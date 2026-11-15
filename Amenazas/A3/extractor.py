"""
==============================================================================
MÓDULO: Extractor de Reportes Ciudadanos SOSAFE
==============================================================================
Realiza la consulta automatizada a la API V3 de SOSAFE utilizando las
credenciales registradas en el archivo .env, guarda el JSON en crudo y genera
el archivo filtrado para la comuna objetivo (por defecto Maipú).
Funciona sin dependencias externas (utiliza urllib de la librería estándar).
"""

import os
import json
import urllib.request
import urllib.parse
import urllib.error
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
DATA_DIR = CURRENT_DIR / "data"


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
                        if k.strip() not in os.environ:
                            os.environ[k.strip()] = v.strip()
            break


def fetch_sosafe_reports():
    """
    Ejecuta consultas a la API de SOSAFE para múltiples sectores de la comuna
    y acumula incrementalmente los reportes nuevos sin borrar los existentes.
    Retorna la lista acumulada de reportes filtrados para la comuna objetivo.
    """
    import time

    load_env_file()

    user_uuid = os.getenv("SOSAFE_USER_UUID")
    access_token = os.getenv("SOSAFE_ACCESS_TOKEN")
    session_token = os.getenv("SOSAFE_SESSION")
    device_id = os.getenv("SOSAFE_DEVICE_ID")

    report_types = os.getenv("SOSAFE_REPORTS_TYPES", "117,106,152,116,95,84,47,124,87,129,57,71,14,127,49,149,36,19,63,61,126,37,94,11,24,64,43,16,75,151,22,10,110,21,59,58,54,56,38,136,142,155,26,12,88,130,17,55,31,0,39,115,28,141,34,20,128,132,148,137,66,135,1,108,99,13,118,33,104,32,103,8,113,5,30,144,9,52,41,18,139,67,23,83,147,105,7,3,6,145,2,93,98,156,133,143,125,109,29,40,119,25,27,60,101,86,50,4,102,140,150,131,146,89,53,82,46,112,51,42,97,107,35,157,175,188,185,187,186,189")
    target_commune = os.getenv("TARGET_COMMUNE", "Maipú").strip().lower()

    if not all([user_uuid, access_token, session_token, device_id]):
        print("[-] Error: Faltan credenciales en .env (SOSAFE_USER_UUID, ACCESS_TOKEN, SESSION, DEVICE_ID).")
        return None

    # Sectores de cobertura geográfica sobre la comuna de Maipú
    sectores = [
        {"nombre": "Centro / Plaza de Maipú", "lat": -33.5108, "lon": -70.7573, "rad": 6500},
        {"nombre": "Norte / Pajaritos / Monte Tabor", "lat": -33.4750, "lon": -70.7350, "rad": 5000},
        {"nombre": "Poniente / Ciudad Satélite / El Abrazo", "lat": -33.5400, "lon": -70.7800, "rad": 5000},
        {"nombre": "Sur / Rinconada / Tres Poniente", "lat": -33.5200, "lon": -70.8000, "rad": 5000},
        {"nombre": "Oriente / Vespucio Sur / Autopista del Sol", "lat": -33.5000, "lon": -70.7150, "rad": 4500},
    ]

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    raw_path = DATA_DIR / "raw_reports.json"
    filtered_path = DATA_DIR / "filtered_reports.json"

    # Cargar datos crudos previos para acumulación incremental
    raw_accumulated = {}
    if raw_path.exists():
        try:
            with open(raw_path, "r", encoding="utf-8") as f:
                prev_raw = json.load(f)
                prev_list = prev_raw.get("reports", []) if isinstance(prev_raw, dict) else prev_raw
                for r in prev_list:
                    if "id" in r:
                        raw_accumulated[r["id"]] = r
        except Exception as e:
            print(f"[!] Aviso al leer {raw_path}: {e}")

    # Cargar datos filtrados previos
    filtered_accumulated = {}
    if filtered_path.exists():
        try:
            with open(filtered_path, "r", encoding="utf-8") as f:
                prev_filtered = json.load(f)
                prev_list = prev_filtered.get("reports", []) if isinstance(prev_filtered, dict) else prev_filtered
                for r in prev_list:
                    if "id" in r:
                        filtered_accumulated[r["id"]] = r
        except Exception as e:
            print(f"[!] Aviso al leer {filtered_path}: {e}")

    prev_filtered_count = len(filtered_accumulated)
    print(f"[*] Registros previos en memoria: {len(raw_accumulated)} crudos, {prev_filtered_count} en {target_commune.capitalize()}")

    headers = {
        "Host": "api-v3.sosafeapp.com",
        "X-SOSAFE-LOCALE": "es-CL",
        "Accept": "application/json",
        "X-SOSAFE-ACCESSTOKEN": access_token,
        "Accept-Language": "es-419,es;q=0.9",
        "X-SOSAFE-DEVICEID": device_id,
        "User-Agent": "SOSAFE/3 CFNetwork/3896.100.1.2.1 Darwin/27.0.0",
        "Connection": "keep-alive",
        "X-SOSAFE-PROTO-HEADER": "x256",
        "X-SOSAFE-SESSION": session_token,
        "Content-Type": "application/json",
    }

    nuevos_crudos = 0
    nuevos_filtrados = 0

    for sec in sectores:
        lat = sec["lat"]
        lon = sec["lon"]
        rad = sec["rad"]
        nombre = sec["nombre"]

        query_params = urllib.parse.urlencode({
            "reportsTypes": report_types,
            "latitude": lat,
            "longitude": lon,
            "radius": rad,
            "userLatitude": lat,
            "userLongitude": lon,
        })
        url = f"https://api-v3.sosafeapp.com/app-reports/near/{user_uuid}?{query_params}"
        print(f"[*] Consultando SOSAFE en sector '{nombre}' (radio {rad}m)...")
        req = urllib.request.Request(url, headers=headers, method="GET")

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            status_code = e.code
            err_body = e.read().decode("utf-8", errors="ignore")
            if status_code == 401 or "UNAUTHORIZED" in err_body:
                print("\n" + "!" * 70)
                print("[!] ERROR 401: TOKEN DE SESIÓN EXPIRADO O INVÁLIDO")
                print("!" * 70 + "\n")
                if not filtered_accumulated:
                    return None
                break
            print(f"[!] Error HTTP ({status_code}) en {nombre}: {err_body[:100]}")
            continue
        except Exception as e:
            print(f"[!] Error de conexión en {nombre}: {e}")
            continue

        reports = data.get("reports", [])
        for r in reports:
            rid = r.get("id")
            if not rid:
                continue
            if rid not in raw_accumulated:
                nuevos_crudos += 1
            raw_accumulated[rid] = r

            if target_commune in (r.get("location") or "").lower():
                if rid not in filtered_accumulated:
                    nuevos_filtrados += 1
                filtered_accumulated[rid] = r

        time.sleep(0.4)

    # 1. Guardar crudo acumulado
    raw_data = {
        "success": True,
        "total_reports": len(raw_accumulated),
        "reports": list(raw_accumulated.values()),
    }
    with open(raw_path, "w", encoding="utf-8") as f:
        json.dump(raw_data, f, indent=2, ensure_ascii=False)
    print(f"[+] Guardado crudo acumulado: {raw_path} ({len(raw_accumulated)} reportes totales, +{nuevos_crudos} nuevos)")

    # 2. Guardar filtrado acumulado para la comuna
    filtered_list = list(filtered_accumulated.values())
    filtered_data = {
        "success": True,
        "commune": target_commune,
        "total_filtered": len(filtered_list),
        "reports": filtered_list,
    }
    with open(filtered_path, "w", encoding="utf-8") as f:
        json.dump(filtered_data, f, indent=2, ensure_ascii=False)
    print(f"[+] Guardado filtrado acumulado para '{target_commune}': {filtered_path} ({len(filtered_list)} reportes totales, +{nuevos_filtrados} nuevos)")

    return filtered_list


if __name__ == "__main__":
    fetch_sosafe_reports()
