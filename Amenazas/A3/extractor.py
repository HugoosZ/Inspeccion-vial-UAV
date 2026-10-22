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
    Ejecuta la petición GET a la API de SOSAFE.
    Retorna la lista de reportes en crudo o None si falla la autenticación.
    """
    load_env_file()

    user_uuid = os.getenv("SOSAFE_USER_UUID")
    access_token = os.getenv("SOSAFE_ACCESS_TOKEN")
    session_token = os.getenv("SOSAFE_SESSION")
    device_id = os.getenv("SOSAFE_DEVICE_ID")

    lat = os.getenv("SOSAFE_LATITUDE", "-33.5108")
    lon = os.getenv("SOSAFE_LONGITUDE", "-70.7573")
    radius = os.getenv("SOSAFE_RADIUS", "6500")
    report_types = os.getenv("SOSAFE_REPORTS_TYPES", "19,17,14,0,1,146,41,42,29")
    target_commune = os.getenv("TARGET_COMMUNE", "Maipú").strip().lower()

    if not all([user_uuid, access_token, session_token, device_id]):
        print("[-] Error: Faltan credenciales en .env (SOSAFE_USER_UUID, ACCESS_TOKEN, SESSION, DEVICE_ID).")
        return None

    query_params = urllib.parse.urlencode({
        "reportsTypes": report_types,
        "latitude": lat,
        "longitude": lon,
        "radius": radius,
        "userLatitude": lat,
        "userLongitude": lon,
    })

    url = f"https://api-v3.sosafeapp.com/app-reports/near/{user_uuid}?{query_params}"

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

    print(f"[*] Consultando SOSAFE API: centro ({lat}, {lon}) con radio {radius}m...")
    req = urllib.request.Request(url, headers=headers, method="GET")

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            status_code = resp.status
            body = resp.read().decode("utf-8")
            data = json.loads(body)
    except urllib.error.HTTPError as e:
        status_code = e.code
        err_body = e.read().decode("utf-8", errors="ignore")
        if status_code == 401 or "UNAUTHORIZED" in err_body:
            print("\n" + "!" * 70)
            print("[!] ERROR 401: TOKEN DE SESIÓN EXPIRADO O INVÁLIDO")
            print("    El token de SOSAFE ha caducado.")
            print("    Pasos para renovar:")
            print("    1. Abre Proxyman en tu iPhone y abre la app SOSAFE.")
            print("    2. Copia los nuevos valores de X-SOSAFE-ACCESSTOKEN y X-SOSAFE-SESSION.")
            print("    3. Actualízalos en tu archivo .env.")
            print("!" * 70 + "\n")
            return None
        print(f"[!] Error HTTP ({status_code}): {err_body[:200]}")
        return None
    except Exception as e:
        print(f"[!] Error de conexión de red: {e}")
        return None

    reports = data.get("reports", [])

    # Crear directorio data/ si no existe
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Guardar JSON en crudo
    raw_path = DATA_DIR / "raw_reports.json"
    with open(raw_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"[+] Guardado crudo: {raw_path} ({len(reports)} reportes totales)")

    # 2. Filtrar exclusivamente por la comuna objetivo
    filtered_reports = [
        r for r in reports
        if target_commune in (r.get("location") or "").lower()
    ]

    filtered_data = {
        "success": True,
        "commune": target_commune,
        "total_filtered": len(filtered_reports),
        "reports": filtered_reports,
    }

    filtered_path = DATA_DIR / "filtered_reports.json"
    with open(filtered_path, "w", encoding="utf-8") as f:
        json.dump(filtered_data, f, indent=2, ensure_ascii=False)

    print(f"[+] Guardado filtrado para '{target_commune}': {filtered_path} ({len(filtered_reports)} reportes)")

    return filtered_reports


if __name__ == "__main__":
    fetch_sosafe_reports()
