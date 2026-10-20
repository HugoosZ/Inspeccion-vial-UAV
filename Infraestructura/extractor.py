"""
==============================================================================
PUNTO 1: ETL de Infraestructura (red vial de Maipú -> nodos y aristas)
==============================================================================
Extract:
  - Límite comunal de Maipú desde OpenStreetMap (Nominatim vía OSMnx). Si falla,
    se usa el rectángulo de respaldo AOI_RESPALDO.
  - Red vial vehicular (network_type="drive") dentro del límite, con OSMnx
    sobre la API Overpass. Es la red donde están las señales de tránsito que
    el dron debe inspeccionar.
  - (Opcional) Elevación de cada nodo desde la API de elevación de Open-Meteo
    (Copernicus DEM 90 m), para el costo energético E(e)=k1*d(e)+k2*Δh⁺(e) de
    la fase 3.
Transform:
  - El grafo se convierte en no dirigido: el dron vuela sobre la calle y no
    está sujeto al sentido del tránsito. El atributo oneway se conserva como
    dato, y con INFRA_RESPETAR_SENTIDO=1 las aristas de sentido único reciben
    reverse_cost = -1 (como un vehículo terrestre).
  - Nodos y aristas reciben IDs enteros consecutivos (1..N) compatibles con
    pgRouting; el ID OSM se conserva en osm_id.
  - Atributos normalizados: highway y name (listas -> texto), maxspeed (km/h).
Load (archivos):
  - data/nodos_maipu.geojson    (Point,      EPSG:4326)
  - data/aristas_maipu.geojson  (LineString, EPSG:4326)

Uso:
  python Infraestructura/extractor.py
Variables opcionales:
  INFRA_LUGAR             consulta Nominatim (defecto "Maipú, Provincia de Santiago, Chile")
  INFRA_NETWORK_TYPE      drive (defecto) | drive_service | all
  INFRA_ELEVACION         1 (defecto) consulta Open-Meteo | 0 deja elevación en 0
  INFRA_RESPETAR_SENTIDO  0 (defecto) | 1
"""

import json
import math
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
SALIDA_NODOS = DATA_DIR / "nodos_maipu.geojson"
SALIDA_ARISTAS = DATA_DIR / "aristas_maipu.geojson"

COMUNA = "Maipú"
# Respaldo si Nominatim no responde: rectángulo de la comuna (oeste, sur, este, norte)
AOI_RESPALDO = (-70.92, -33.62, -70.70, -33.46)
ELEVACION_API = "https://api.open-meteo.com/v1/elevation"
VELOCIDAD_DEFECTO = {"motorway": 100, "trunk": 80, "primary": 60, "secondary": 50,
                     "tertiary": 50, "residential": 40, "living_street": 20}


# ------------------------------------------------------------------------------
# Utilidades
# ------------------------------------------------------------------------------
def a_texto(valor):
    """OSMnx deja listas cuando una arista simplificada une varias vías."""
    if valor is None or (isinstance(valor, float) and math.isnan(valor)):
        return None
    if isinstance(valor, (list, tuple, set)):
        valores = [str(v) for v in valor if v is not None]
        return " / ".join(dict.fromkeys(valores)) or None
    return str(valor)


def primer_valor(valor):
    if isinstance(valor, (list, tuple, set)):
        valor = next(iter(valor), None)
    return valor


def velocidad_kmh(maxspeed, highway):
    """Convierte maxspeed OSM ('50', '50 mph', ['50','60']) a km/h entero."""
    valor = primer_valor(maxspeed)
    if valor is not None and not (isinstance(valor, float) and math.isnan(valor)):
        m = re.search(r"\d+(\.\d+)?", str(valor))
        if m:
            v = float(m.group())
            return int(round(v * 1.609 if "mph" in str(valor) else v))
    tipo = str(highway or "").replace("_link", "")
    return VELOCIDAD_DEFECTO.get(tipo, 50)


def es_verdadero(valor):
    valor = primer_valor(valor)
    return str(valor).lower() in ("true", "yes", "1", "-1")


# ------------------------------------------------------------------------------
# Extract
# ------------------------------------------------------------------------------
def descargar_grafo(lugar, network_type):
    import osmnx as ox

    ox.settings.use_cache = True
    ox.settings.cache_folder = str(DATA_DIR / ".cache_osmnx")
    ox.settings.log_console = False

    try:
        limite = ox.geocode_to_gdf(lugar)
        poligono = limite.geometry.iloc[0]
        print(f"[+] Límite comunal obtenido de Nominatim: {limite['display_name'].iloc[0]}")
        G = ox.graph_from_polygon(poligono, network_type=network_type, simplify=True)
        origen_limite = "nominatim"
    except Exception as error:  # noqa: BLE001 - cualquier fallo -> respaldo
        print(f"[!] No se pudo usar el límite de Nominatim ({error}); se usa el rectángulo de respaldo.")
        oeste, sur, este, norte = AOI_RESPALDO
        try:  # OSMnx >= 2.0
            G = ox.graph_from_bbox((oeste, sur, este, norte), network_type=network_type, simplify=True)
        except TypeError:  # OSMnx 1.x
            G = ox.graph_from_bbox(norte, sur, este, oeste, network_type=network_type, simplify=True)
        origen_limite = "bbox_respaldo"

    # Aseguramos el atributo length (OSMnx 1.x lo agrega aparte)
    if not all("length" in d for _, _, d in G.edges(data=True)):
        try:
            G = ox.distance.add_edge_lengths(G)
        except AttributeError:
            G = ox.add_edge_lengths(G)

    # Grafo no dirigido: una arista por tramo de calle
    convertir = getattr(getattr(ox, "convert", None), "to_undirected", None) \
        or getattr(getattr(ox, "utils_graph", None), "get_undirected", None)
    G_nd = convertir(G)
    nodos, aristas = ox.graph_to_gdfs(G_nd, nodes=True, edges=True, fill_edge_geometry=True)
    print(f"[+] Red vial descargada: {len(nodos)} nodos, {len(aristas)} aristas (no dirigida)")
    return nodos, aristas, origen_limite


def elevaciones(coordenadas):
    """Elevación (m) para una lista de (lat, lon) en lotes de 100 vía Open-Meteo."""
    resultado = []
    for i in range(0, len(coordenadas), 100):
        lote = coordenadas[i:i + 100]
        params = {"latitude": ",".join(f"{la:.6f}" for la, _ in lote),
                  "longitude": ",".join(f"{lo:.6f}" for _, lo in lote)}
        for intento in range(3):
            try:
                r = requests.get(ELEVACION_API, params=params, timeout=30)
                r.raise_for_status()
                resultado += r.json()["elevation"]
                break
            except (requests.RequestException, KeyError, ValueError) as error:
                if intento == 2:
                    raise RuntimeError(f"Open-Meteo elevation falló: {error}")
                time.sleep(2 ** (intento + 1))
        if (i // 100) % 20 == 19:
            print(f"    [*] Elevación: {i + len(lote)}/{len(coordenadas)} nodos")
    return resultado


# ------------------------------------------------------------------------------
# Transform + escritura
# ------------------------------------------------------------------------------
def transformar(nodos, aristas, con_elevacion, respetar_sentido):
    osm_a_id = {osm: i for i, osm in enumerate(nodos.index, start=1)}

    elev = [0.0] * len(nodos)
    if con_elevacion:
        print(f"[*] Consultando elevación de {len(nodos)} nodos en Open-Meteo...")
        try:
            elev = elevaciones(list(zip(nodos["y"], nodos["x"])))
        except RuntimeError as error:
            print(f"[!] {error}. Se deja elevación 0.")

    features_nodos = []
    for (osm, fila), z in zip(nodos.iterrows(), elev):
        features_nodos.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [round(fila["x"], 7), round(fila["y"], 7)]},
            "properties": {
                "id": osm_a_id[osm],
                "osm_id": int(osm),
                "commune": COMUNA,
                "elevation_m": round(float(z), 1) if z is not None else 0.0,
                "street_count": int(fila["street_count"]) if "street_count" in fila and not math.isnan(fila["street_count"]) else None,
            },
        })

    features_aristas = []
    for i, ((u, v, _k), fila) in enumerate(aristas.iterrows(), start=1):
        largo = float(fila["length"])
        sentido_unico = es_verdadero(fila.get("oneway"))
        highway = a_texto(fila.get("highway")) or "unclassified"
        features_aristas.append({
            "type": "Feature",
            "geometry": {"type": "LineString",
                         "coordinates": [[round(x, 7), round(y, 7)] for x, y in fila.geometry.coords]},
            "properties": {
                "id": i,
                "osm_id": int(primer_valor(fila.get("osmid"))) if primer_valor(fila.get("osmid")) is not None else None,
                "name": a_texto(fila.get("name")),
                "highway": highway[:50],
                "source": osm_a_id[u],
                "target": osm_a_id[v],
                "length_m": round(largo, 2),
                "cost": round(largo, 2),
                "reverse_cost": -1.0 if (respetar_sentido and sentido_unico) else round(largo, 2),
                "maxspeed_kmh": velocidad_kmh(fila.get("maxspeed"), primer_valor(fila.get("highway"))),
                "oneway": sentido_unico,
                "lanes": a_texto(fila.get("lanes")),
                "commune": COMUNA,
            },
        })
    return features_nodos, features_aristas


def guardar(ruta, nombre, features, metadatos):
    ruta.parent.mkdir(parents=True, exist_ok=True)
    salida = {
        "type": "FeatureCollection",
        "name": nombre,
        "metadata": {"generado_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
                     "comuna": COMUNA, "total_features": len(features), **metadatos},
        "features": features,
    }
    ruta.write_text(json.dumps(salida, ensure_ascii=False), encoding="utf-8")
    print(f"[+] {len(features)} features -> {ruta}")


def main():
    lugar = os.getenv("INFRA_LUGAR", "Maipú, Provincia de Santiago, Chile")
    network_type = os.getenv("INFRA_NETWORK_TYPE", "drive")
    con_elevacion = os.getenv("INFRA_ELEVACION", "1") == "1"
    respetar_sentido = os.getenv("INFRA_RESPETAR_SENTIDO", "0") == "1"

    print("=" * 60)
    print(f"INFRAESTRUCTURA: red vial '{network_type}' de {lugar}")
    print("=" * 60)
    nodos, aristas, origen = descargar_grafo(lugar, network_type)
    f_nodos, f_aristas = transformar(nodos, aristas, con_elevacion, respetar_sentido)

    comunes = {"fuente": "OpenStreetMap vía OSMnx (Overpass API)", "lugar": lugar,
               "origen_limite": origen, "network_type": network_type, "dirigido": False,
               "respetar_sentido": respetar_sentido}
    guardar(SALIDA_NODOS, "infraestructura_nodos_maipu", f_nodos,
            {**comunes, "elevacion": "Open-Meteo Elevation API (Copernicus DEM 90 m)" if con_elevacion else None})
    guardar(SALIDA_ARISTAS, "infraestructura_aristas_maipu", f_aristas,
            {**comunes, "longitud_total_km": round(sum(f["properties"]["length_m"] for f in f_aristas) / 1000, 1)})


if __name__ == "__main__":
    try:
        main()
    except Exception as error:  # noqa: BLE001
        print(f"[-] Error en la extracción de infraestructura: {error}")
        sys.exit(1)
