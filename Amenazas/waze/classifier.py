"""
==============================================================================
MÓDULO AMENAZAS: Clasificador Operacional UAV y Geocercas Waze
==============================================================================
Transforma las alertas filtradas de Waze (calles cerradas y obras en calzada)
en entidades espaciales y restricciones de navegación para la flota de drones:

1. Asignación de Severidad Operacional UAV (Cota 20m):
   - CALLE_CERRADA (CRÍTICO, radio 60m): Desvío total de tránsito vehicular y
     peatonal. Invalida la inspección de señalética y presenta obstáculos físicos
     a baja cota.
   - CONSTRUCCION_VIAL (ALTO, radio 50m): Faenas activas, maquinaria pesada
     (grúas, retroexcavadoras) y suspensión de polvo que daña y ciega las cámaras
     de inspección a 20m de altura.

2. Generación Espacial (WGS84 EPSG:4326):
   - Punto de incidente (amenaza_punto).
   - Polígono circular de exclusión aérea (geocerca_exclusion) compatible con
     Leaflet, PostGIS temp_amenazas y ST_Intersects con network_edges.

3. Exportación de Formatos:
   - data/waze_threats_geofences.geojson: Capa espacial para Leaflet y PostGIS.
   - data/active_waze_threats_graph.json: Lista estructurada para Dijkstra / pgRouting.
==============================================================================
"""

import json
import math
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
DATA_DIR = CURRENT_DIR / "data"


def load_env():
    """Carga variables desde .env local o raíz."""
    for ruta in [CURRENT_DIR / ".env", CURRENT_DIR.parent.parent / ".env"]:
        if ruta.exists():
            for linea in ruta.read_text(encoding="utf-8").splitlines():
                linea = linea.strip()
                if linea and not linea.startswith("#") and "=" in linea:
                    k, v = linea.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def ahora_utc_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def create_circular_polygon(lon, lat, radius_meters, num_points=32):
    """Genera coordenadas GeoJSON de un polígono circular alrededor de un punto."""
    coords = []
    r_lat = radius_meters / 111320.0
    r_lon = radius_meters / (111320.0 * math.cos(math.radians(lat)))

    for i in range(num_points + 1):
        angle = 2 * math.pi * i / num_points
        d_lon = r_lon * math.cos(angle)
        d_lat = r_lat * math.sin(angle)
        coords.append([round(lon + d_lon, 6), round(lat + d_lat, 6)])

    return [coords]


def clasificar_alerta(alerta):
    """
    Determina la categoría operacional de la amenaza para un UAV a 20m.
    """
    tipo = (alerta.get("type") or "").upper()
    subtipo = (alerta.get("subtype") or "").upper()

    if tipo == "ROAD_CLOSED" or "ROAD_CLOSED" in subtipo:
        categoria = "CALLE_CERRADA"
        nivel = "CRITICO"
        radio = 60
        justificacion = (
            "Vía cerrada al tránsito por obras o eventos mayores. Representa una "
            "zona de exclusión para vuelo a baja cota (20m) e invalida la inspección "
            "de señalética por posible retiro, daño u obstrucción de la infraestructura."
        )
        marker_color = "#d90429"  # Rojo
    else:
        categoria = "CONSTRUCCION_VIAL"
        nivel = "ALTO"
        radio = 50
        justificacion = (
            "Faenas viales activas y maquinaria pesada operando en calzada. Riesgo de "
            "colisión a baja cota (20m) y emisión de polvo/partículas que degradan "
            "los sensores ópticos de detección de señalética."
        )
        marker_color = "#f77f00"  # Naranja

    return {
        "categoria": categoria,
        "nivel_riesgo": nivel,
        "radio_buffer": radio,
        "justificacion": justificacion,
        "marker_color": marker_color,
    }


def generar_geocercas_y_grafo():
    """
    Procesa data/waze_filtered_construction.json y genera los archivos de salida.
    """
    filtered_path = DATA_DIR / "waze_filtered_construction.json"
    if not filtered_path.exists():
        print(f"[-] No se encontró {filtered_path}. Ejecute extractor.py primero.")
        return False

    with open(filtered_path, "r", encoding="utf-8") as f:
        alertas = json.load(f)

    if not alertas:
        print("[!] No hay alertas filtradas para procesar.")
        return False

    ahora = datetime.now(timezone.utc).replace(microsecond=0)
    valido_desde = ahora.isoformat()
    valido_hasta = (ahora + timedelta(hours=24)).isoformat()

    features = []
    amenazas_grafo = []

    for a in alertas:
        loc = a.get("location", {})
        lon = float(loc.get("x"))
        lat = float(loc.get("y"))
        id_alerta = str(a.get("id") or a.get("uuid"))
        street = a.get("street") or "Vía urbana Maipú"
        desc = a.get("reportDescription") or "Trabajos y cierre vial reportado en Waze"

        c = clasificar_alerta(a)

        # 1. Objeto para algoritmo de ruteo (Grafo)
        amenazas_grafo.append({
            "id": id_alerta,
            "uuid": a.get("uuid") or id_alerta,
            "sector": a.get("sector", "Maipú"),
            "categoria": c["categoria"],
            "nivel_riesgo": c["nivel_riesgo"],
            "latitud": lat,
            "longitud": lon,
            "coordenadas": [lat, lon],
            "radio_buffer_metros": c["radio_buffer"],
            "accion_grafo": "BLOQUEAR_ARISTAS",
            "factor_penalizacion": "INF",
            "calle": street,
            "descripcion": desc,
            "justificacion": c["justificacion"],
        })

        # Propiedades comunes para GeoJSON
        props_base = {
            "id_amenaza": f"WAZE_{id_alerta}",
            "id": id_alerta,
            "uuid": a.get("uuid") or id_alerta,
            "fuente": "Waze Live Map",
            "sector": a.get("sector", "Maipú"),
            "tipo": a.get("type"),
            "subtipo": a.get("subtype"),
            "tipo_amenaza": c["categoria"].lower(),
            "categoria": c["categoria"],
            "nivel_riesgo": c["nivel_riesgo"],
            "accion_grafo": "BLOQUEAR_ARISTAS",
            "factor_penalizacion": "INF",
            "calle": street,
            "comuna": "Maipú",
            "descripcion": desc,
            "justificacion": c["justificacion"],
            "radio_buffer": c["radio_buffer"],
            "radio_metros": c["radio_buffer"],
            "marker_color": c["marker_color"],
            "confianza": a.get("confidence", 5),
            "valido_desde": valido_desde,
            "valido_hasta": valido_hasta,
            "activa": True,
        }

        # 2. Entidad Punto (Marcador)
        features.append({
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [round(lon, 6), round(lat, 6)],
            },
            "properties": {
                **props_base,
                "tipo_geometria": "amenaza_punto",
            },
        })

        # 3. Entidad Polígono (Geocerca de exclusión circular)
        coords_anillo = create_circular_polygon(lon, lat, c["radio_buffer"], num_points=32)
        features.append({
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": coords_anillo,
            },
            "properties": {
                **props_base,
                "tipo_geometria": "geocerca_exclusion",
            },
        })

    # Guardar GeoJSON
    geojson_path = DATA_DIR / "waze_threats_geofences.geojson"
    geojson_data = {
        "type": "FeatureCollection",
        "name": "uav_threat_geofences_waze_maipu",
        "metadata": {
            "generado_utc": ahora_utc_iso(),
            "comuna": "Maipú",
            "fuente": "Waze Live Map",
            "total_features": len(features),
            "total_amenazas": len(amenazas_grafo),
            "cota_vuelo_m": 20,
        },
        "features": features,
    }
    with open(geojson_path, "w", encoding="utf-8") as f:
        json.dump(geojson_data, f, ensure_ascii=False, indent=2)
    print(f"[+] GeoJSON con geocercas generado: {geojson_path} ({len(features)} features)")

    # Guardar JSON para Grafo
    graph_path = DATA_DIR / "active_waze_threats_graph.json"
    with open(graph_path, "w", encoding="utf-8") as f:
        json.dump(amenazas_grafo, f, ensure_ascii=False, indent=2)
    print(f"[+] Archivo estructurado para grafo: {graph_path} ({len(amenazas_grafo)} amenazas)")

    return True


if __name__ == "__main__":
    load_env()
    generar_geocercas_y_grafo()
