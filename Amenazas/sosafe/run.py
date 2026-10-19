"""
==============================================================================
EXTRACCIÓN ATÓMICA: SOSAFE
==============================================================================
Ejecuta la consulta GET a la API V3 de SOSAFE utilizando las credenciales
del archivo .env y guarda:
1. data/raw_reports.json (todos los reportes devueltos por la API)
2. data/filtered_reports.json (únicamente los eventos de la comuna objetivo)
"""

import sys
from pathlib import Path
from extractor import fetch_sosafe_reports

CURRENT_DIR = Path(__file__).resolve().parent
DATA_DIR = CURRENT_DIR / "data"


def main():
    print("\n" + "=" * 60)
    print("EXTRACCIÓN DE REPORTES SOSAFE")
    print("=" * 60)

    reports = fetch_sosafe_reports()

    if reports is None:
        print("[!] No se pudo completar la extracción en vivo (credenciales faltantes o expiradas).")
        sys.exit(1)

    print("\n[+] Extracción y guardado completados con éxito.")
    print(f"    - Crudo:    {DATA_DIR / 'raw_reports.json'}")
    print(f"    - Filtrado: {DATA_DIR / 'filtered_reports.json'}\n")


if __name__ == "__main__":
    main()
