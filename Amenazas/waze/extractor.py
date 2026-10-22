"""
==============================================================================
MÓDULO AMENAZAS: Extractor Waze Live Map (Multi-Sector Maipú)
==============================================================================
Realiza consultas sectorizadas a la API GeoRSS de Waze Live Map
(https://www.waze.com/live-map/api/georss).

Para evitar que la API trunque o restrinja los resultados por volumen en un
área amplia, la comuna de Maipú se divide en 10 sectores geográficos
(sub-bounding boxes). Las consultas se realizan de forma secuencial con una
pausa configurable (por defecto 3 segundos) para proteger la cuota, no saturar
la sesión y evitar bloqueos por tasa de peticiones (Rate Limiting).

Filtra eventos de amenaza crítica para vuelo UAV a 20m:
  1. Calles cerradas (ROAD_CLOSED / desvíos / cortes totales).
  2. Faenas viales y obras en calzada (HAZARD_ON_ROAD_CONSTRUCTION).

Si la API responde con 403 Forbidden (control perimetral WAF de Google/Waze),
el extractor informa el procedimiento para agregar la Cookie de sesión en .env
y continúa inmediatamente con el conjunto de datos verificado de respaldo en
data/waze_raw.json.
==============================================================================
"""

import os
import json
import time
import urllib.request
import urllib.parse
import urllib.error
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
DATA_DIR = CURRENT_DIR / "data"

# División de Maipú en 10 sectores operativos para consulta granular
SECTORES_MAIPU = [
    {
        "id": "SEC_01",
        "nombre": "Pajaritos Norte / Del Sol / Monte Tabor",
        "top": -33.4600, "bottom": -33.4900, "left": -70.7600, "right": -70.7200,
        "descripcion": "Corredor norte de Pajaritos, autopista Vespucio y estaciones de metro."
    },
    {
        "id": "SEC_02",
        "nombre": "Plaza de Maipú / Centro Cívico",
        "top": -33.4950, "bottom": -33.5200, "left": -70.7700, "right": -70.7400,
        "descripcion": "Casco histórico, Av. 5 de Abril, Alberto Llona y eje comercial."
    },
    {
        "id": "SEC_03",
        "nombre": "Esquina Blanca / Camino Melipilla Ote / Ferrocarril",
        "top": -33.4950, "bottom": -33.5250, "left": -70.7400, "right": -70.7050,
        "descripcion": "Zona industrial, obras de paso bajo nivel y cruces ferroviarios."
    },
    {
        "id": "SEC_04",
        "nombre": "Rinconada / Hospital El Carmen",
        "top": -33.4850, "bottom": -33.5150, "left": -70.7950, "right": -70.7600,
        "descripcion": "Eje vial de acceso a recintos de salud y poniente comunal."
    },
    {
        "id": "SEC_05",
        "nombre": "El Olimpo / Los Presidentes",
        "top": -33.5100, "bottom": -33.5350, "left": -70.7850, "right": -70.7550,
        "descripcion": "Sector residencial denso con colectores y mantención de calzada."
    },
    {
        "id": "SEC_06",
        "nombre": "Las Naciones / Cuatro Poniente",
        "top": -33.5150, "bottom": -33.5400, "left": -70.8200, "right": -70.7800,
        "descripcion": "Extremo poniente, obras sanitarias SMAPA y recarpeteos."
    },
    {
        "id": "SEC_07",
        "nombre": "Pajaritos Sur / Nueva San Martín",
        "top": -33.5200, "bottom": -33.5450, "left": -70.7650, "right": -70.7350,
        "descripcion": "Conexión con Vespucio Sur, eje sur de Pajaritos y San Martín."
    },
    {
        "id": "SEC_08",
        "nombre": "El Abrazo / Tres Poniente",
        "top": -33.5300, "bottom": -33.5550, "left": -70.7800, "right": -70.7450,
        "descripcion": "Barrio El Abrazo, colectores primarios y cruce Camino a Melipilla."
    },
    {
        "id": "SEC_09",
        "nombre": "Ciudad Satélite / Camino Melipilla Poniente",
        "top": -33.5450, "bottom": -33.5750, "left": -70.8100, "right": -70.7700,
        "descripcion": "Acceso poniente a la Región Metropolitana, faenas viales mayores."
    },
    {
        "id": "SEC_10",
        "nombre": "Loma Blanca / Lumen / Segunda Transversal",
        "top": -33.4800, "bottom": -33.5100, "left": -70.7350, "right": -70.7050,
        "descripcion": "Zona residencial oriente limítrofe con Cerrillos, pavimentación."
    },
]

# Palabras clave en español para detectar faenas viales y cortes
KEYWORDS_CONSTRUCCION = [
    "construccion", "construcción",
    "obra", "obras",
    "trabajo", "trabajos",
    "reparacion", "reparación",
    "pavimentacion", "pavimentación",
    "recarpeteo",
    "desvio", "desvío",
    "arreglo", "arreglos",
    "cierre", "cerrada", "cortada", "corte",
    "faena", "faenas",
    "bacheo", "alcantarillado", "colector"
]


def load_env_file():
    """Carga variables desde .env local o raíz sin requerir librerías externas."""
    env_paths = [
        CURRENT_DIR / ".env",
        CURRENT_DIR.parent.parent / ".env",
        CURRENT_DIR / ".env.example",
    ]
    for p in env_paths:
        if p.exists():
            with open(p, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        k_clean = k.strip()
                        v_clean = v.strip().strip('"').strip("'")
                        if k_clean not in os.environ:
                            os.environ[k_clean] = v_clean
            break


def query_waze_sector(top, bottom, left, right, env="row", types="alerts,traffic", cookie="", user_agent=""):
    """Realiza una petición GET a la API GeoRSS para una ventana espacial."""
    query_params = urllib.parse.urlencode({
        "top": str(top),
        "bottom": str(bottom),
        "left": str(left),
        "right": str(right),
        "env": env,
        "types": types,
    })
    url = f"https://www.waze.com/live-map/api/georss?{query_params}"

    headers = {
        "User-Agent": user_agent,
        "Referer": "https://www.waze.com/live-map/",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "es-419,es;q=0.9,en;q=0.8",
        "X-Requested-With": "XMLHttpRequest",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
    }
    if cookie:
        headers["Cookie"] = cookie

    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_waze_alerts():
    """
    Consulta Waze Live Map dividiendo Maipú en 10 sectores geográficos.
    Aplica una pausa configurable (3s) entre consultas para no saturar la API.
    Si se detecta 403 Forbidden, interrumpe el intento en vivo y recurre a los
    datos locales verificados.
    """
    load_env_file()

    use_multi_sector = os.getenv("WAZE_MULTI_SECTOR", "true").lower() == "true"
    delay_s = float(os.getenv("WAZE_REQUEST_DELAY", "3.0"))
    env = os.getenv("WAZE_ENV", "row")
    types = os.getenv("WAZE_TYPES", "alerts,traffic")
    cookie = os.getenv("WAZE_COOKIE", "").strip()
    user_agent = os.getenv(
        "WAZE_USER_AGENT",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
    )

    sectores = SECTORES_MAIPU if use_multi_sector else [{
        "id": "SEC_GLOBAL",
        "nombre": "Maipú Completo (Global)",
        "top": float(os.getenv("WAZE_TOP", "-33.4600")),
        "bottom": float(os.getenv("WAZE_BOTTOM", "-33.5800")),
        "left": float(os.getenv("WAZE_LEFT", "-70.8200")),
        "right": float(os.getenv("WAZE_RIGHT", "-70.7000")),
    }]

    print(f"[*] Modo de extracción: {'Multi-sector (10 zonas de Maipú)' if use_multi_sector else 'Caja global única'}")
    print(f"[*] Pausa entre solicitudes: {delay_s:.1f}s por sector.")

    alertas_dedup = {}
    total_sectores = len(sectores)

    for idx, sec in enumerate(sectores, start=1):
        print(f"[*] [{idx}/{total_sectores}] Consultando sector '{sec['nombre']}'...")
        try:
            resp = query_waze_sector(
                top=sec["top"],
                bottom=sec["bottom"],
                left=sec["left"],
                right=sec["right"],
                env=env,
                types=types,
                cookie=cookie,
                user_agent=user_agent
            )
            alerts = resp.get("alerts", [])
            for a in alerts:
                aid = a.get("id") or a.get("uuid")
                if aid and aid not in alertas_dedup:
                    a["sector"] = sec["nombre"]
                    alertas_dedup[aid] = a

            print(f"    [+] {len(alerts)} alertas recibidas (acumulado único: {len(alertas_dedup)})")

            if idx < total_sectores and delay_s > 0:
                time.sleep(delay_s)

        except urllib.error.HTTPError as e:
            status_code = e.code
            print("\n" + "!" * 70)
            print(f"[!] Waze Live Map API respondió HTTP {status_code} ({e.reason}) en sector {idx}.")
            if status_code in (401, 403):
                print("    Motivo: Control perimetral WAF / Cloud Armor de Waze detectó solicitud automatizada.")
                print("    Para actualizar en vivo:")
                print("      1. Abra https://www.waze.com/live-map en su navegador.")
                print("      2. En Herramientas de Desarrollador (F12) -> Red, copie la cabecera Cookie.")
                print("      3. Péguela en Amenazas/waze/.env en la variable WAZE_COOKIE='...'")
                print("    Interrumpiendo peticiones en vivo para evitar pausas innecesarias...")
            print("!" * 70 + "\n")
            return None
        except Exception as e:
            print(f"    [-] Error en sector {sec['nombre']}: {e}")
            return None

    if alertas_dedup:
        data_combinada = {"alerts": list(alertas_dedup.values()), "jams": []}
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        raw_path = DATA_DIR / "waze_raw.json"
        with open(raw_path, "w", encoding="utf-8") as f:
            json.dump(data_combinada, f, ensure_ascii=False, indent=2)
        print(f"[+] Total alertas únicas extraídas en Maipú: {len(alertas_dedup)}")
        return data_combinada

    return None


def filter_construction_and_closures(raw_data=None):
    """
    Filtra los eventos de Waze dejando únicamente los relevantes para la inspección
    vial UAV: calles cerradas y obras de construcción / reparación dentro de Maipú.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    raw_path = DATA_DIR / "waze_raw.json"

    if raw_data is None:
        if raw_path.exists():
            print(f"[*] Cargando datos locales desde {raw_path}...")
            with open(raw_path, "r", encoding="utf-8") as f:
                raw_data = json.load(f)
        else:
            print(f"[-] No se encontró {raw_path} para procesar.")
            return []

    alerts = raw_data.get("alerts", [])
    filtered = []

    # Límites espaciales de la comuna de Maipú
    lat_min, lat_max = -33.62, -33.46
    lon_min, lon_max = -70.92, -70.70

    for a in alerts:
        loc = a.get("location", {})
        lon = loc.get("x")
        lat = loc.get("y")

        # 1. Filtro espacial dentro de Maipú
        if lon is None or lat is None:
            continue
        if not (lon_min <= lon <= lon_max and lat_min <= lat <= lat_max):
            continue

        city = (a.get("city") or "").strip().lower()
        if city and city not in ("maipú", "maipu"):
            continue

        tipo = (a.get("type") or "").upper()
        subtipo = (a.get("subtype") or "").upper()
        desc = (a.get("reportDescription") or "").lower()

        # 2. Criterio de inclusión: Calles cerradas o faenas de construcción
        es_calle_cerrada = tipo == "ROAD_CLOSED" or "ROAD_CLOSED" in subtipo
        es_obra_construccion = "CONSTRUCTION" in subtipo or any(k in desc for k in KEYWORDS_CONSTRUCCION)

        if not (es_calle_cerrada or es_obra_construccion):
            continue

        filtered.append(a)

    print(f"[*] Total alertas Waze evaluadas: {len(alerts)} | En Maipú (cerradas/obras): {len(filtered)}")

    filtered_path = DATA_DIR / "waze_filtered_construction.json"
    with open(filtered_path, "w", encoding="utf-8") as f:
        json.dump(filtered, f, ensure_ascii=False, indent=2)
    print(f"[+] Archivo guardado: {filtered_path}")

    return filtered


def run_extraction():
    """Ejecuta el flujo completo de extracción y filtrado inicial."""
    data = fetch_waze_alerts()
    return filter_construction_and_closures(data)


if __name__ == "__main__":
    run_extraction()
