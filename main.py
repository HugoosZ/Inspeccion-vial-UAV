"""
==============================================================================
PROYECTO: Detección de Señaléticas y Elementos Viales mediante Flota UAV
Orquestador principal
==============================================================================
Un solo comando, ejecutado desde la raíz del repositorio, deja la plataforma
funcionando de punta a punta:

    python3 main.py

  1. Docker      Construye y levanta los contenedores db (PostGIS + pgRouting) y etl.
  2. Descarga    Dentro del contenedor etl ejecuta los extractores (Extract + Transform)
                 de Infraestructura, Metadata y Amenazas, que escriben los JSON/GeoJSON
                 en las carpetas data/ de cada módulo.
  3. Carga       Ejecuta un cargar.py por cada JSON/GeoJSON y los importa a la BD.
  4. Verificación Cuenta las filas cargadas por tabla y por fuente.
  5. Web         Levanta el contenedor web (FastAPI + Leaflet), espera a que responda
                 y abre http://localhost:8080 en el navegador.

Opciones:
    python3 main.py --sin-descarga   Omite la descarga y carga los JSON ya presentes
                                     en el repositorio (útil sin internet o para ir rápido).
    python3 main.py --solo-web       Solo levanta los contenedores y verifica la web.
    python3 main.py --no-abrir       No abre el navegador al terminar.

En el computador solo se necesita Docker y Python 3 (biblioteca estándar). Todo el
procesamiento corre dentro de los contenedores. Dentro del contenedor etl el mismo
archivo ejecuta el pipeline (python main.py --pipeline), que es lo que invoca el paso 2.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser

RAIZ = os.path.dirname(os.path.abspath(__file__))
URL_WEB = os.getenv("WEB_URL", "http://localhost:8080")

# ------------------------------------------------------------------------------
# Catálogo del pipeline
# ------------------------------------------------------------------------------
# Extractores (Extract + Transform). Cada uno escribe su JSON/GeoJSON en el repositorio.
# requiere: variable de entorno o archivo sin el cual el paso se omite y se usa el JSON
# existente. cwd: carpeta desde donde debe ejecutarse (scripts con rutas relativas).
EXTRACTORES = [
    {"grupo": "Infraestructura", "nombre": "Red vial OSM + elevación",
     "script": "Infraestructura/extractor.py", "timeout": 1800},
    {"grupo": "Metadata", "nombre": "M1 Paraderos (ArcGIS DTPM)",
     "script": "Metadata/M1/extractor.py", "cwd": "Metadata/M1"},
    {"grupo": "Metadata", "nombre": "M3 Controles viales (ohsome)",
     "script": "Metadata/M3/controles_viales.py", "requiere_env": "OHSOME_API_KEY"},
    {"grupo": "Metadata", "nombre": "M5 Censo 2024 por manzana",
     "script": "Metadata/M5/procesar_censo.py", "cwd": "Metadata/M5",
     "requiere_archivo": "Metadata/M5/Cartografia_censo2024_Pais.gpkg",
     "nota": "usa el ZIP versionado manzanas_maipu_completo.zip"},
    {"grupo": "Amenazas", "nombre": "T2 Incendios (GDACS + NASA FIRMS)",
     "script": "Amenazas/incendios/extractor.py"},
    {"grupo": "Amenazas", "nombre": "T3 Clima (Open-Meteo)",
     "script": "Amenazas/clima/extractor.py"},
    {"grupo": "Amenazas", "nombre": "T5 Emergencias por lluvia (Maipú)",
     "script": "Amenazas/lluvias_maipu/extractor.py"},
    {"grupo": "Amenazas", "nombre": "T6 SOSAFE + clasificación",
     "script": "Amenazas/sosafe/run.py"},
]
# M6 Aeródromos (aerodromos.geojson, DGAC) es una fuente estática versionada en el repositorio.

# Cargadores (Load): un cargar.py por cada JSON/GeoJSON.
CARGADORES = [
    {"grupo": "Infraestructura", "nombre": "Nodos y aristas", "script": "Infraestructura/cargar.py",
     "critico": True},
    {"grupo": "Metadata", "nombre": "M1 Paraderos", "script": "Metadata/M1/cargar.py"},
    {"grupo": "Metadata", "nombre": "M3 Controles viales", "script": "Metadata/M3/cargar.py"},
    {"grupo": "Metadata", "nombre": "M5 Censo 2024", "script": "Metadata/M5/cargar.py"},
    {"grupo": "Metadata", "nombre": "M6 Aeródromos", "script": "Metadata/M6/cargar.py"},
    {"grupo": "Amenazas", "nombre": "T2 Incendios", "script": "Amenazas/incendios/cargar.py"},
    {"grupo": "Amenazas", "nombre": "T3 Clima", "script": "Amenazas/clima/cargar.py"},
    {"grupo": "Amenazas", "nombre": "T5 Lluvias", "script": "Amenazas/lluvias_maipu/cargar.py"},
    {"grupo": "Amenazas", "nombre": "T6 SOSAFE", "script": "Amenazas/sosafe/cargar.py"},
]


# ------------------------------------------------------------------------------
# Utilidades comunes
# ------------------------------------------------------------------------------
def titulo(texto):
    print("\n" + "=" * 70 + f"\n{texto}\n" + "=" * 70, flush=True)


def cargar_env(ruta=os.path.join(RAIZ, ".env")):
    """Lee .env (KEY=VALOR) sin pisar variables ya definidas. Los subprocesos lo heredan."""
    if not os.path.exists(ruta):
        return
    with open(ruta, encoding="utf-8") as f:
        for linea in f:
            linea = linea.strip()
            if not linea or linea.startswith("#") or "=" not in linea:
                continue
            clave, valor = linea.split("=", 1)
            clave = clave.replace("export ", "").strip()
            valor = valor.strip().strip('"').strip("'")
            if clave and clave not in os.environ:
                os.environ[clave] = valor


def en_contenedor():
    return os.getenv("RUTEO_EN_CONTENEDOR") == "1" or os.path.exists("/.dockerenv")


def duracion(segundos):
    return f"{segundos:.0f} s" if segundos < 90 else f"{segundos / 60:.1f} min"


def imprimir_tabla(filas, encabezado):
    anchos = [max(len(str(x)) for x in col) for col in zip(encabezado, *filas)]
    linea = "  ".join("{:<%d}" % a for a in anchos)
    print(linea.format(*encabezado))
    print("  ".join("-" * a for a in anchos))
    for fila in filas:
        print(linea.format(*[str(x) for x in fila]))


# ------------------------------------------------------------------------------
# Modo pipeline (dentro del contenedor etl)
# ------------------------------------------------------------------------------
def esperar_bd(intentos=30):
    import psycopg2

    params = dict(
        host=os.getenv("DB_HOST", "localhost"), port=os.getenv("DB_PORT", "5432"),
        dbname=os.getenv("DB_NAME", "ruteo_db"), user=os.getenv("DB_USER", "ruteo_user"),
        password=os.getenv("DB_PASSWORD", "ruteo_pass"),
    )
    print(f"[*] Conectando a {params['host']}:{params['port']}/{params['dbname']}...")
    for _ in range(intentos):
        try:
            conn = psycopg2.connect(**params)
            print("[+] Base de datos lista (PostgreSQL + PostGIS + pgRouting).")
            return conn
        except Exception as e:  # noqa: BLE001
            print(f"[-] Esperando a la base de datos ({str(e).strip()[:80]})...")
            time.sleep(2)
    return None


def ejecutar_paso(paso):
    """Ejecuta un script del pipeline y devuelve (estado, segundos, detalle)."""
    if paso.get("requiere_env") and not os.getenv(paso["requiere_env"]):
        return "OMITIDO", 0, f"falta {paso['requiere_env']} en .env; se usa el JSON existente"
    if paso.get("requiere_archivo") and not os.path.exists(os.path.join(RAIZ, paso["requiere_archivo"])):
        return "OMITIDO", 0, paso.get("nota", "falta el archivo de entrada")

    script = os.path.join(RAIZ, paso["script"])
    cwd = os.path.join(RAIZ, paso.get("cwd", ""))
    inicio = time.time()
    try:
        r = subprocess.run([sys.executable, script], cwd=cwd, timeout=paso.get("timeout", 900))
        estado = "OK" if r.returncode == 0 else "FALLÓ"
        detalle = "" if r.returncode == 0 else f"código {r.returncode}"
    except subprocess.TimeoutExpired:
        estado, detalle = "FALLÓ", "tiempo agotado"
    return estado, time.time() - inicio, detalle


def ejecutar_etapa(nombre_etapa, pasos, aviso_fallo):
    titulo(nombre_etapa)
    resultados = []
    for paso in pasos:
        print(f"\n[*] {paso['grupo']} · {paso['nombre']}  ({paso['script']})", flush=True)
        estado, seg, detalle = ejecutar_paso(paso)
        if estado == "FALLÓ":
            print(f"[!] {paso['nombre']}: {aviso_fallo}")
        elif estado == "OMITIDO":
            print(f"[~] Omitido: {detalle}")
        resultados.append((paso, estado, seg, detalle))
    return resultados


def resumen_bd(conn):
    titulo("VERIFICACIÓN DE LA BASE DE DATOS")
    with conn.cursor() as cur:
        cur.execute("""
            SELECT 'network_nodes', 'Infraestructura', count(*) FROM network_nodes
            UNION ALL SELECT 'network_edges', 'Infraestructura', count(*) FROM network_edges
            UNION ALL SELECT 'temp_metadata', COALESCE(propiedades->>'fuente_etl', '(sin fuente)'), count(*)
                      FROM temp_metadata GROUP BY 2
            UNION ALL SELECT 'temp_amenazas', COALESCE(propiedades->>'fuente_etl', '(sin fuente)'), count(*)
                      FROM temp_amenazas GROUP BY 2
            ORDER BY 1, 2
        """)
        filas = cur.fetchall()
    imprimir_tabla(filas, ("Tabla", "Fuente", "Filas"))
    return {(t, f): n for t, f, n in filas}


def modo_pipeline(sin_descarga):
    cargar_env()
    conn = esperar_bd()
    if conn is None:
        print("[!] No se pudo conectar a la base de datos.")
        return 1

    resultados = []
    if sin_descarga:
        print("\n[~] --sin-descarga: se cargan los JSON que ya están en el repositorio.")
    else:
        resultados += ejecutar_etapa("1/2 · DESCARGA Y TRANSFORMACIÓN (Extract + Transform)", EXTRACTORES,
                                     "la extracción falló; se usará el último JSON disponible.")
    resultados += ejecutar_etapa("2/2 · CARGA A LA BASE DE DATOS (Load)", CARGADORES,
                                 "la carga falló (¿existe el JSON de entrada?).")

    conteos = resumen_bd(conn)
    conn.close()

    titulo("RESUMEN DEL PIPELINE")
    imprimir_tabla([(p["grupo"], p["nombre"], e, duracion(s) if s else "-", d)
                    for p, e, s, d in resultados],
                   ("Grupo", "Paso", "Estado", "Tiempo", "Detalle"))

    criticos = [p["nombre"] for p, e, _, _ in resultados if p.get("critico") and e != "OK"]
    if criticos or not conteos.get(("network_edges", "Infraestructura")):
        print("\n[!] La red vial no quedó cargada: el ruteo no puede funcionar.")
        return 1
    fallidos = sum(1 for _, e, _, _ in resultados if e == "FALLÓ")
    print(f"\n[+] Pipeline terminado ({fallidos} paso(s) con advertencias).")
    return 0


# ------------------------------------------------------------------------------
# Modo anfitrión (en el computador): orquesta Docker y habilita la web
# ------------------------------------------------------------------------------
def comando_compose():
    if shutil.which("docker") is None:
        sys.exit("[!] No se encontró Docker. Instala Docker Desktop y vuelve a ejecutar.")
    if subprocess.run(["docker", "info"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
        sys.exit("[!] Docker está instalado pero no está corriendo. Abre Docker Desktop y reintenta.")
    if subprocess.run(["docker", "compose", "version"], stdout=subprocess.DEVNULL,
                      stderr=subprocess.DEVNULL).returncode == 0:
        return ["docker", "compose"]
    if shutil.which("docker-compose"):
        return ["docker-compose"]
    sys.exit("[!] No se encontró Docker Compose.")


def compose(base, *args):
    r = subprocess.run(base + list(args), cwd=RAIZ)
    return r.returncode == 0


def http_json(ruta, timeout=5):
    with urllib.request.urlopen(URL_WEB + ruta, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def esperar_web(limite_s=120):
    print(f"[*] Esperando a que la web responda en {URL_WEB}/health ...", flush=True)
    fin = time.time() + limite_s
    while time.time() < fin:
        try:
            estado = http_json("/health")
            if estado.get("status") == "ok":
                return True
        except (urllib.error.URLError, ConnectionError, OSError, ValueError):
            pass
        time.sleep(2)
    return False


def resumen_web():
    try:
        capas = http_json("/api/capas", timeout=30)
        red = capas.get("infraestructura") or {}
        print(f"[+] Red vial disponible: {red.get('nodos', 0)} nodos, {red.get('aristas', 0)} aristas, "
              f"{red.get('km', 0)} km")
        for grupo in ("metadata", "amenazas"):
            por_fuente = {}
            for fila in capas.get(grupo, []):
                por_fuente[fila["fuente_etl"]] = por_fuente.get(fila["fuente_etl"], 0) + int(fila["total"])
            texto = ", ".join(f"{f} ({n})" for f, n in sorted(por_fuente.items(), key=lambda x: str(x[0])))
            print(f"[+] Capas de {grupo}: {texto or 'ninguna'}")
        http_json("/api/rutas/preferencias")
        print("[+] API de ruteo (pgr_dijkstra + TDRPP-GA) disponible.")
    except Exception as e:  # noqa: BLE001
        print(f"[!] La web responde, pero no se pudo leer el catálogo de capas: {e}")


def modo_anfitrion(args):
    cargar_env()
    inicio = time.time()
    base = comando_compose()

    titulo("PASO 1 · CONTENEDORES (db + etl)")
    if not compose(base, "up", "-d", "--build", "db", "etl"):
        sys.exit("[!] No se pudieron levantar los contenedores db y etl.")

    codigo = 0
    if not args.solo_web:
        titulo("PASO 2 · DESCARGA E IMPORTACIÓN A LA BD (contenedor etl)")
        cmd = ["exec", "-T", "etl", "python", "main.py", "--pipeline"]
        if args.sin_descarga:
            cmd.append("--sin-descarga")
        if not compose(base, *cmd):
            codigo = 1
            print("[!] El pipeline terminó con errores; se levanta la web igual para revisar.")

    titulo("PASO 3 · HABILITACIÓN DE LA WEB (FastAPI + Leaflet)")
    # Se recrea el contenedor para que la API lea los datos recién cargados.
    if not compose(base, "up", "-d", "--build", "--force-recreate", "web"):
        sys.exit("[!] No se pudo levantar el contenedor web.")
    if not esperar_web():
        print(f"[!] La web no respondió a tiempo. Revisa: {' '.join(base)} logs web")
        return 1
    resumen_web()

    titulo("PLATAFORMA LISTA")
    print(f"  Visualizador:  {URL_WEB}")
    print(f"  API (docs):    {URL_WEB}/docs")
    print(f"  Tiempo total:  {duracion(time.time() - inicio)}")
    print(f"  Detener:       {' '.join(base)} down")
    if not args.no_abrir:
        webbrowser.open(URL_WEB)
    return codigo


# ------------------------------------------------------------------------------
def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

    parser = argparse.ArgumentParser(description="Orquestador: Docker + descarga + carga a la BD + web.")
    parser.add_argument("--sin-descarga", action="store_true",
                        help="no descarga; carga los JSON ya presentes en el repositorio")
    parser.add_argument("--solo-web", action="store_true", help="solo levanta los contenedores y la web")
    parser.add_argument("--no-abrir", action="store_true", help="no abre el navegador al terminar")
    parser.add_argument("--pipeline", action="store_true",
                        help="(uso interno) ejecuta descarga y carga dentro del contenedor etl")
    args = parser.parse_args()

    if args.pipeline or en_contenedor():
        sys.exit(modo_pipeline(args.sin_descarga))
    sys.exit(modo_anfitrion(args))


if __name__ == "__main__":
    main()
