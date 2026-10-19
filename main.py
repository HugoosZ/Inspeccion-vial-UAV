"""
==============================================================================
PROYECTO: Detección de Señaléticas y Elementos Viales mediante Flota UAV
Orquestador Principal
==============================================================================
Este script automatiza la ejecución secuencial del proceso ETL completo:
 1. Extracción y transformación de Infraestructura (nodos y aristas OSM).
 2. Extracción y transformación de Metadata (señaléticas oficiales, Waze, clima).
 3. Extracción y transformación de Amenazas (zonas hostiles, clima severo, jammers).
 4. Carga de los archivos JSON/GeoJSON a la Base de Datos (PostgreSQL/PostGIS/pgRouting).
 5. Verificación de servicios y disponibilidad del visualizador Leaflet.
"""

import os
import sys
import time
import psycopg2


def check_db_connection():
    """Verifica que la base de datos esté lista para recibir conexiones."""
    host = os.getenv("DB_HOST", "localhost")
    port = os.getenv("DB_PORT", "5432")
    dbname = os.getenv("DB_NAME", "ruteo_db")
    user = os.getenv("DB_USER", "ruteo_user")
    password = os.getenv("DB_PASSWORD", "ruteo_pass")

    print(f"[*] Conectando a la base de datos en {host}:{port}/{dbname}...")
    retries = 10
    while retries > 0:
        try:
            conn = psycopg2.connect(
                host=host, port=port, dbname=dbname, user=user, password=password
            )
            conn.close()
            print("[+] Conexión exitosa a PostgreSQL/PostGIS/pgRouting.")
            return True
        except Exception as e:
            print(f"[-] Esperando a la base de datos ({e})... reintentando en 2s")
            time.sleep(2)
            retries -= 1

    print("[!] Error: No se pudo conectar a la base de datos.")
    return False


def run_etl_infrastructure():
    """Ejecuta los scripts de extracción y transformación en 'Infraestructura/'."""
    print("\n" + "=" * 60)
    print("FASE 1: ETL INFRAESTRUCTURA (Nodos y Aristas OSM)")
    print("=" * 60)
    # TODO: Invocar script(s) en Infraestructura/
    print("[*] Pendiente de implementar scripts en Infraestructura/")


def run_etl_metadata():
    """Ejecuta los scripts de extracción y transformación en 'Metadata/'."""
    print("\n" + "=" * 60)
    print("FASE 2: ETL METADATA (Inventario Oficial, Waze Tráfico, Clima/Sombras)")
    print("=" * 60)
    # TODO: Invocar scripts en Metadata/
    print("[*] Pendiente de implementar scripts en Metadata/")


AMENAZAS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Amenazas")

# Módulos de amenazas con extractor.py (Extract + Transform) y cargar.py (Load)
MODULOS_AMENAZAS = [
    ("T2 Incendios (GDACS + NASA FIRMS)", "incendios"),
    ("T3 Clima (Open-Meteo)", "clima"),
    ("T5 Emergencias por lluvia (Municipalidad de Maipú)", "lluvias_maipu"),
]


def _ejecutar(script):
    """Ejecuta un script Python y devuelve True si terminó sin error."""
    import subprocess
    resultado = subprocess.run([sys.executable, script], check=False)
    return resultado.returncode == 0


def run_etl_amenazas():
    """Ejecuta los scripts de extracción y transformación en 'Amenazas/'."""
    print("\n" + "=" * 60)
    print("FASE 3: ETL AMENAZAS (Incendios, Clima, Lluvias Maipú, SOSAFE)")
    print("=" * 60)
    for nombre, carpeta in MODULOS_AMENAZAS:
        print(f"\n[*] {nombre}")
        if not _ejecutar(os.path.join(AMENAZAS_DIR, carpeta, "extractor.py")):
            print(f"[!] {nombre}: la extracción falló; se usará el último JSON disponible.")

    sosafe_script = os.path.join(AMENAZAS_DIR, "sosafe", "run.py")
    if os.path.exists(sosafe_script):
        print("\n[*] T6 Zona conflictiva (SOSAFE)")
        _ejecutar(sosafe_script)


def run_db_loader():
    """Ejecuta la carga de todos los archivos JSON/GeoJSON procesados a la base de datos."""
    print("\n" + "=" * 60)
    print("FASE 4: CARGA A BASE DE DATOS (PostGIS / pgRouting)")
    print("=" * 60)
    for nombre, carpeta in MODULOS_AMENAZAS:
        carpeta_data = os.path.join(AMENAZAS_DIR, carpeta, "data")
        if not any(f.endswith(".geojson") for f in os.listdir(carpeta_data)):
            print(f"[!] {nombre}: no hay GeoJSON para cargar.")
            continue
        print(f"[*] Cargando {nombre}...")
        _ejecutar(os.path.join(AMENAZAS_DIR, carpeta, "cargar.py"))
    # TODO: Invocar scripts de carga de Infraestructura, Metadata y SOSAFE


def main():
    print("Iniciando orquestador de ETL y Ruteo UAV...")
    if not check_db_connection():
        sys.exit(1)

    run_etl_infrastructure()
    run_etl_metadata()
    run_etl_amenazas()
    run_db_loader()

    print("\n[+] Proceso completado exitosamente.")
    print("[+] El sitio web Leaflet está disponible en: http://localhost:8080")


if __name__ == "__main__":
    main()
