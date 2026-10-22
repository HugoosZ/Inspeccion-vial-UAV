"""
==============================================================================
MÓDULO AMENAZAS: Extractor Waze Live Map (Calles Cerradas y Construcción)
==============================================================================
Realiza la consulta automatizada a la API GeoRSS de Waze Live Map
(https://www.waze.com/live-map/api/georss) para obtener alertas en tiempo real
centradas en la comuna de Maipú.

Filtra eventos que constituyen una amenaza crítica para la operación de un UAV
que vuela a baja cota (~20 m) en inspección de señalética vial:
  1. Calles cerradas (ROAD_CLOSED / cortes de tránsito / desvíos).
  2. Obras viales, faenas y reparaciones en calzada (HAZARD_ON_ROAD_CONSTRUCTION).

No requiere librerías externas (utiliza urllib de la biblioteca estándar).
Si Waze bloquea la petición por control perimetral (HTTP 403 Forbidden), el
extractor emite instrucciones claras y recurre automáticamente al conjunto de
datos verificado de respaldo en data/waze_raw.json.
==============================================================================
"""

import os
import json
import urllib.request
import urllib.parse
import urllib.error
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
DATA_DIR = CURRENT_DIR / "data"

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
    """Carga variables desde .env local o raíz sin requerir python-dotenv."""
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


def fetch_waze_alerts():
    """
    Consulta la API GeoRSS de Waze Live Map para la caja delimitadora de Maipú.
    Retorna el diccionario con la respuesta o None si la petición es bloqueada.
    """
    load_env_file()

    # Bounding box centrado en Maipú
    top = os.getenv("WAZE_TOP", "-33.4600")
    bottom = os.getenv("WAZE_BOTTOM", "-33.5800")
    left = os.getenv("WAZE_LEFT", "-70.8200")
    right = os.getenv("WAZE_RIGHT", "-70.7000")
    env = os.getenv("WAZE_ENV", "row")
    types = os.getenv("WAZE_TYPES", "alerts,traffic")
    cookie = os.getenv("WAZE_COOKIE", "").strip()
    user_agent = os.getenv(
        "WAZE_USER_AGENT",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
    )

    query_params = urllib.parse.urlencode({
        "top": top,
        "bottom": bottom,
        "left": left,
        "right": right,
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

    print(f"[*] Consultando Waze Live Map API (Maipú: N={top}, S={bottom}, O={left}, E={right})...")
    req = urllib.request.Request(url, headers=headers, method="GET")

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            raw_path = DATA_DIR / "waze_raw.json"
            with open(raw_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            n_alerts = len(data.get("alerts", []))
            print(f"[+] Waze API respondiendo OK: {n_alerts} alertas descargadas en {raw_path}")
            return data
    except urllib.error.HTTPError as e:
        status_code = e.code
        print("\n" + "!" * 70)
        print(f"[!] Waze Live Map API respondió HTTP {status_code} ({e.reason}).")
        if status_code in (401, 403):
            print("    Motivo: Control perimetral WAF / Cloud Armor de Waze detectó solicitud automatizada.")
            print("    Para actualizar en vivo:")
            print("      1. Abra https://www.waze.com/live-map en su navegador.")
            print("      2. En Herramientas de Desarrollador (F12) -> Red, copie la cabecera Cookie.")
            print("      3. Péguela en Amenazas/waze/.env en la variable WAZE_COOKIE='...'")
        print("!" * 70 + "\n")
        return None
    except Exception as e:
        print(f"[-] Error de conexión con Waze API: {e}")
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

    # Límites espaciales de Maipú
    lat_min, lat_max = -33.62, -33.46
    lon_min, lon_max = -70.92, -70.70

    for a in alerts:
        loc = a.get("location", {})
        lon = loc.get("x")
        lat = loc.get("y")

        # 1. Filtro espacial
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
        street = (a.get("street") or "")

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
