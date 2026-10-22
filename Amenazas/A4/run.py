"""
==============================================================================
FLUJO INTEGRAL ATÓMICO: Waze Live Map (Extracción, Filtrado y Geocercas)
==============================================================================
1. Intenta la extracción en vivo vía HTTP GET a Waze Live Map API
   (https://www.waze.com/live-map/api/georss) con las coordenadas de Maipú.
   Si la API responde 403 Forbidden (control perimetral WAF de Google/Waze),
   informa al usuario y continúa automáticamente con el conjunto de datos
   verificado de respaldo en data/waze_raw.json.
2. Filtra eventos de calles cerradas (ROAD_CLOSED) y obras viales en calzada
   (HAZARD_ON_ROAD_CONSTRUCTION / reparaciones / faenas).
3. Clasifica la amenaza para la seguridad de vuelo a 20m AGL y genera las No-Fly
   Zones circulares (60m para calles cerradas, 50m para obras en calzada).
4. Genera data/waze_threats_geofences.geojson para Leaflet / PostGIS y
   data/active_waze_threats_graph.json para el grafo de ruteo resiliente.
==============================================================================
"""

import sys
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
DATA_DIR = CURRENT_DIR / "data"

from extractor import fetch_waze_alerts, filter_construction_and_closures  # noqa: E402
from classifier import generar_geocercas_y_grafo  # noqa: E402


def main():
    print("\n" + "=" * 70)
    print("MÓDULO AMENAZAS: WAZE LIVE MAP (CALLES CERRADAS & OBRAS VIALES)")
    print("=" * 70)

    # Paso 1: Extracción en vivo
    print("\n--- PASO 1: Consulta a Waze Live Map API (Maipú) ---")
    data = fetch_waze_alerts()

    raw_file = DATA_DIR / "waze_raw.json"
    if data is None:
        print("[!] No se pudo obtener respuesta en vivo directa de Waze (403 WAF).")
        if raw_file.exists():
            print(f"[*] Utilizando conjunto de datos local verificado: {raw_file}")
        else:
            print("[-] No se encontró un archivo local previo para continuar.")
            sys.exit(1)
    else:
        print("[+] Extracción en vivo completada exitosamente.")

    # Paso 2: Filtrado temático y espacial para Maipú
    print("\n--- PASO 2: Filtrado de Calles Cerradas y Obras en Calzada ---")
    filtered = filter_construction_and_closures(data)

    if not filtered:
        print("[-] No se encontraron alertas válidas de cierre u obras.")
        sys.exit(1)

    # Paso 3: Generación de Geocercas y Archivo de Grafo
    print("\n--- PASO 3: Generación Espacial de Geocercas (No-Fly Zones a 20m) ---")
    ok = generar_geocercas_y_grafo()
    if not ok:
        print("[-] Falló la generación de geocercas.")
        sys.exit(1)

    print("\n" + "=" * 70)
    print("FLUJO DE AMENAZAS WAZE FINALIZADO CON ÉXITO")
    print(f"  - Alertas brutas:         {DATA_DIR / 'waze_raw.json'}")
    print(f"  - Alertas filtradas:      {DATA_DIR / 'waze_filtered_construction.json'}")
    print(f"  - Geocercas (GeoJSON):    {DATA_DIR / 'waze_threats_geofences.geojson'}")
    print(f"  - Amenazas para el Grafo: {DATA_DIR / 'active_waze_threats_graph.json'}")
    print("=" * 70 + "\n")

    # Paso 4 (Opcional): Carga a BD si se solicita vía CLI
    if "--load" in sys.argv or "--dry-run" in sys.argv:
        print("--- PASO 4: Carga en Base de Datos (temp_amenazas) ---")
        import cargar
        dry = "--dry-run" in sys.argv
        cargar.comun.cargar_env()
        cargar.comun.cargar_features(cargar.ARCHIVO, "A4_waze", cargar.mapear, dry_run=dry)


if __name__ == "__main__":
    main()
