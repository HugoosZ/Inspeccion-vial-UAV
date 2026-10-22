"""
==============================================================================
FLUJO INTEGRAL ATÓMICO: SOSAFE (Extracción y Clasificación Semántica)
==============================================================================
1. Intenta la extracción en vivo vía HTTP GET a SOSAFE API V3 con las credenciales
   definidas en .env. Si las credenciales expiraron o no están configuradas, utiliza
   los datos locales existentes en data/filtered_reports.json sin detener la ejecución.
2. Ejecuta la clasificación semántica mediante IA (Google Gemini 3.8 Flash) o su
   heurística de respaldo de seguridad operacional UAV.
3. Genera las No-Fly Zones en formato GeoJSON para Leaflet y el JSON limpio
   con las amenazas activas estructuradas para el grafo de navegación (Dijkstra/pgRouting).
"""

import sys
from pathlib import Path
from extractor import fetch_sosafe_reports
from classifier import run_classification

CURRENT_DIR = Path(__file__).resolve().parent
DATA_DIR = CURRENT_DIR / "data"


def main():
    print("\n" + "=" * 65)
    print("MÓDULO AMENAZAS: SOSAFE (EXTRACCIÓN & CLASIFICACIÓN IA)")
    print("=" * 65)

    # Paso 1: Extracción en vivo
    print("\n--- PASO 1: Consulta a SOSAFE API V3 ---")
    reports = fetch_sosafe_reports()

    filtered_file = DATA_DIR / "filtered_reports.json"
    if reports is None:
        print("[!] No se pudo obtener respuesta en vivo de SOSAFE.")
        if filtered_file.exists():
            print(f"[*] Utilizando conjunto de datos local existente: {filtered_file}")
        else:
            print("[-] No se encontró un archivo local previo para continuar.")
            sys.exit(1)
    else:
        print("[+] Extracción completada exitosamente.")

    # Paso 2: Clasificación de Amenazas Aeronáuticas
    print("\n--- PASO 2: Clasificación Semántica de Amenazas Aeronáuticas ---")
    classified = run_classification()

    if classified is None:
        print("[-] Falló el proceso de clasificación.")
        sys.exit(1)

    print("\n" + "=" * 65)
    print("FLUJO DE AMENAZAS SOSAFE FINALIZADO CON ÉXITO")
    print(f"  - Reportes filtrados:     {DATA_DIR / 'filtered_reports.json'}")
    print(f"  - Clasificación general:  {DATA_DIR / 'classified_threats.json'}")
    print(f"  - Geocercas (GeoJSON):     {DATA_DIR / 'threats_geofences.geojson'}")
    print(f"  - Amenazas para el Grafo: {DATA_DIR / 'active_threats_graph.json'}")
    print("=" * 65 + "\n")


if __name__ == "__main__":
    main()
