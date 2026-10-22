"""
==============================================================================
Utilidades compartidas por los módulos ETL de Amenazas
==============================================================================
- Área de estudio (Maipú) y grilla de celdas.
- Lectura de variables de entorno desde .env sin dependencias externas.
- Descarga HTTP con reintentos.
- Geometría simple en WGS84 (círculos, cuadrados, bbox) sin shapely.
- Escritura de GeoJSON y carga genérica a la tabla temp_amenazas.
"""

import json
import math
import os
import time
from datetime import datetime, timezone
from pathlib import Path

# import requests se realiza de forma local en http_get() para permitir
# importar utilidades de comun.py sin dependencias externas.

AMENAZAS_DIR = Path(__file__).resolve().parent
PROJECT_DIR = AMENAZAS_DIR.parent

# Rectángulo que cubre la comuna de Maipú, incluida la zona rural poniente
# (Rinconada). Orden: oeste, sur, este, norte (lon_min, lat_min, lon_max, lat_max).
AOI_MAIPU = (-70.92, -33.62, -70.70, -33.46)
COMUNA = "Maipú"

# Rectángulo aproximado de la Región Metropolitana (para amenazas regionales
# como incendios forestales grandes, cuyo humo afecta a toda la cuenca).
AOI_REGION_METROPOLITANA = (-71.72, -34.30, -69.76, -32.92)

METROS_POR_GRADO_LAT = 111_320.0


# ------------------------------------------------------------------------------
# Configuración
# ------------------------------------------------------------------------------
def cargar_env():
    """Carga variables desde .env (raíz del proyecto y carpeta Amenazas) si existen.

    No sobrescribe variables ya definidas en el entorno (por ejemplo, las que
    entrega docker-compose).
    """
    for ruta in (PROJECT_DIR / ".env", AMENAZAS_DIR / ".env"):
        if not ruta.exists():
            continue
        for linea in ruta.read_text(encoding="utf-8").splitlines():
            linea = linea.strip()
            if not linea or linea.startswith("#") or "=" not in linea:
                continue
            clave, valor = linea.split("=", 1)
            os.environ.setdefault(clave.strip(), valor.strip().strip('"').strip("'"))


def ahora_utc_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# ------------------------------------------------------------------------------
# HTTP
# ------------------------------------------------------------------------------
def http_get(url, params=None, intentos=3, timeout=60, como_json=True):
    """GET con reintentos y espera exponencial. Devuelve JSON o texto."""
    import requests  # import local

    ultimo_error = None
    for intento in range(1, intentos + 1):
        try:
            resp = requests.get(
                url,
                params=params,
                timeout=timeout,
                headers={"User-Agent": "Inspeccion-vial-UAV/1.0 (UDP CIT3435)"},
            )
            resp.raise_for_status()
            return resp.json() if como_json else resp.text
        except (requests.RequestException, ValueError) as error:
            ultimo_error = error
            print(f"    [-] Intento {intento}/{intentos} falló: {error}")
            time.sleep(2 ** intento)
    raise RuntimeError(f"No se pudo descargar {url}: {ultimo_error}")


# ------------------------------------------------------------------------------
# Geometría en WGS84 (aproximación local, suficiente a escala comunal)
# ------------------------------------------------------------------------------
def metros_a_grados(metros, lat):
    """Convierte una distancia en metros a (dlon, dlat) en grados a una latitud dada."""
    dlat = metros / METROS_POR_GRADO_LAT
    dlon = metros / (METROS_POR_GRADO_LAT * math.cos(math.radians(lat)))
    return dlon, dlat


def circulo(lon, lat, radio_m, segmentos=32):
    """Polígono GeoJSON aproximando un círculo de radio_m metros."""
    dlon, dlat = metros_a_grados(radio_m, lat)
    anillo = []
    for i in range(segmentos):
        ang = 2 * math.pi * i / segmentos
        anillo.append([round(lon + dlon * math.cos(ang), 6),
                       round(lat + dlat * math.sin(ang), 6)])
    anillo.append(anillo[0])
    return {"type": "Polygon", "coordinates": [anillo]}


def rectangulo(lon_min, lat_min, lon_max, lat_max):
    """Polígono GeoJSON rectangular."""
    anillo = [
        [round(lon_min, 6), round(lat_min, 6)],
        [round(lon_max, 6), round(lat_min, 6)],
        [round(lon_max, 6), round(lat_max, 6)],
        [round(lon_min, 6), round(lat_max, 6)],
        [round(lon_min, 6), round(lat_min, 6)],
    ]
    return {"type": "Polygon", "coordinates": [anillo]}


def cuadrado_con_margen(lon, lat, lado_m, margen_m):
    """Cuadrado centrado de lado_m metros, expandido margen_m metros por lado."""
    semilado = lado_m / 2 + margen_m
    dlon, dlat = metros_a_grados(semilado, lat)
    return rectangulo(lon - dlon, lat - dlat, lon + dlon, lat + dlat)


def grilla(aoi, filas, columnas):
    """Divide un bbox en filas x columnas celdas. Devuelve lista de dicts."""
    lon_min, lat_min, lon_max, lat_max = aoi
    paso_lon = (lon_max - lon_min) / columnas
    paso_lat = (lat_max - lat_min) / filas
    celdas = []
    for f in range(filas):
        for c in range(columnas):
            x0 = lon_min + c * paso_lon
            y0 = lat_min + f * paso_lat
            celdas.append({
                "id_celda": f"F{f}C{c}",
                "centro_lon": round(x0 + paso_lon / 2, 5),
                "centro_lat": round(y0 + paso_lat / 2, 5),
                "geometry": rectangulo(x0, y0, x0 + paso_lon, y0 + paso_lat),
            })
    return celdas


def _coordenadas_planas(geometry):
    """Itera todos los pares [lon, lat] de una geometría GeoJSON."""
    def recorrer(obj):
        if isinstance(obj, (list, tuple)) and obj and isinstance(obj[0], (int, float)):
            yield obj
        elif isinstance(obj, (list, tuple)):
            for sub in obj:
                yield from recorrer(sub)
    yield from recorrer(geometry.get("coordinates", []))


def bbox_geometria(geometry):
    coords = list(_coordenadas_planas(geometry))
    lons = [c[0] for c in coords]
    lats = [c[1] for c in coords]
    return (min(lons), min(lats), max(lons), max(lats))


def bbox_se_cruzan(a, b):
    return not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1])


def punto_en_bbox(lon, lat, bbox):
    return bbox[0] <= lon <= bbox[2] and bbox[1] <= lat <= bbox[3]


# ------------------------------------------------------------------------------
# Salida
# ------------------------------------------------------------------------------
def guardar_geojson(ruta, nombre, features, metadatos):
    """Escribe un FeatureCollection con un bloque 'metadata' de trazabilidad."""
    ruta = Path(ruta)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    salida = {
        "type": "FeatureCollection",
        "name": nombre,
        "metadata": {"generado_utc": ahora_utc_iso(), "comuna": COMUNA,
                     "total_features": len(features), **metadatos},
        "features": features,
    }
    ruta.write_text(json.dumps(salida, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"    [+] {len(features)} features -> {ruta}")
    return salida


def guardar_json(ruta, datos):
    ruta = Path(ruta)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"    [+] JSON -> {ruta}")


# ------------------------------------------------------------------------------
# Carga a la base de datos (tabla temp_amenazas de database/init.sql)
# ------------------------------------------------------------------------------
def conectar_bd():
    import psycopg2  # import local: la extracción no requiere la BD

    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=os.getenv("DB_PORT", "5432"),
        dbname=os.getenv("DB_NAME", "ruteo_db"),
        user=os.getenv("DB_USER", "ruteo_user"),
        password=os.getenv("DB_PASSWORD", "ruteo_pass"),
    )


SQL_INSERT = """
INSERT INTO temp_amenazas (nombre, tipo, nivel_riesgo, comuna, descripcion, propiedades, geom)
VALUES (%s, %s, %s, %s, %s, %s::jsonb, ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326))
"""


def cargar_features(ruta_geojson, fuente, mapear, dry_run=False):
    """Carga un GeoJSON a temp_amenazas de forma idempotente.

    - fuente: valor que se guarda en propiedades->>'fuente_etl'. Antes de insertar
      se borran las filas previas de esa misma fuente, para que re-ejecutar el
      pipeline no duplique datos.
    - mapear: función feature -> (nombre, tipo, nivel_riesgo, descripcion).
    """
    datos = json.loads(Path(ruta_geojson).read_text(encoding="utf-8"))
    filas = []
    for feat in datos.get("features", []):
        nombre, tipo, nivel, descripcion = mapear(feat)
        props = dict(feat.get("properties", {}))
        props["fuente_etl"] = fuente
        filas.append((
            (nombre or "")[:150], tipo[:50], nivel, COMUNA, descripcion,
            json.dumps(props, ensure_ascii=False), json.dumps(feat["geometry"]),
        ))

    print(f"    [*] {len(filas)} filas preparadas desde {Path(ruta_geojson).name}")
    if dry_run:
        for fila in filas[:3]:
            print("      ", fila[:4])
        print("    [*] dry-run: no se escribió en la base de datos.")
        return len(filas)

    conn = conectar_bd()
    try:
        with conn, conn.cursor() as cur:
            cur.execute("DELETE FROM temp_amenazas WHERE propiedades->>'fuente_etl' = %s",
                        (fuente,))
            if filas:
                cur.executemany(SQL_INSERT, filas)
        print(f"    [+] {len(filas)} filas cargadas en temp_amenazas (fuente_etl={fuente})")
    finally:
        conn.close()
    return len(filas)
