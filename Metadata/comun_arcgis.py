"""
==============================================================================
Consultas a servicios ArcGIS REST (FeatureServer / MapServer)
==============================================================================
Lo usan los extractores de M2 (lugares públicos), M3 (puentes), M4 (siniestros)
y A1 (incendios CONAF). Solo depende de la biblioteca estándar de Python.

- consultar(): descarga todas las filas de una capa, paginando con
  resultOffset cuando el servicio limita la cantidad por respuesta.
- a_geojson(): convierte la geometría de ArcGIS (x/y, paths, rings) a GeoJSON.
- limite_maipu(): polígono oficial de la comuna (MINVU, División Político
  Administrativa) para filtrar los elementos que caen dentro de Maipú.
- dentro_de(): prueba punto-en-polígono (regla par-impar, admite agujeros).
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

METADATA_DIR = Path(__file__).resolve().parent
COMUNA = "Maipú"
CUT_MAIPU = "13119"

# Rectángulo que contiene la comuna, incluida la zona rural poniente (Rinconada).
# Orden: oeste, sur, este, norte.
AOI_MAIPU = (-70.92, -33.62, -70.70, -33.46)

# MINVU · División Político Administrativa, capa Comunas (fecha del recurso 2023-09-29)
URL_LIMITES = ("https://services3.arcgis.com/cTnMkBRk4HWkUCRo/arcgis/rest/services/"
               "División_Político_Administrativa_Comunas/FeatureServer/0")
ARCHIVO_LIMITE = METADATA_DIR / "limite_maipu.geojson"

AGENTE = "Inspeccion-vial-UAV/1.0 (UDP CIT3435)"


# ------------------------------------------------------------------------------
# HTTP
# ------------------------------------------------------------------------------
def get_json(url, params=None, intentos=3, timeout=60):
    """GET con reintentos y espera exponencial. Lanza RuntimeError si falla."""
    consulta = url + ("?" + urllib.parse.urlencode(params) if params else "")
    ultimo = None
    for intento in range(1, intentos + 1):
        try:
            req = urllib.request.Request(consulta, headers={"User-Agent": AGENTE})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                datos = json.loads(resp.read().decode("utf-8"))
            if isinstance(datos, dict) and "error" in datos:
                raise RuntimeError(f"ArcGIS respondió error: {datos['error']}")
            return datos
        except (urllib.error.URLError, TimeoutError, ValueError, RuntimeError) as error:
            ultimo = error
            print(f"    [-] Intento {intento}/{intentos} falló: {error}")
            if intento < intentos:
                time.sleep(2 ** intento)
    raise RuntimeError(f"No se pudo consultar {url}: {ultimo}")


def consultar(url_capa, where="1=1", campos="*", bbox=None, por_pagina=None, extra=None):
    """Descarga todas las filas de una capa ArcGIS en EPSG:4326.

    - url_capa: URL de la capa, terminada en /FeatureServer/<id> o /MapServer/<id>.
    - bbox: (oeste, sur, este, norte) para filtrar por rectángulo, opcional.
    - por_pagina: tamaño de página; por defecto el maxRecordCount de la capa.
    Devuelve la lista de features en formato ArcGIS (attributes + geometry).
    """
    info = get_json(url_capa, {"f": "json"})
    por_pagina = por_pagina or int(info.get("maxRecordCount") or 1000)
    params = {"where": where, "outFields": campos, "returnGeometry": "true",
              "outSR": "4326", "f": "json"}
    if bbox:
        params.update({
            "geometry": json.dumps({"xmin": bbox[0], "ymin": bbox[1], "xmax": bbox[2],
                                    "ymax": bbox[3], "spatialReference": {"wkid": 4326}}),
            "geometryType": "esriGeometryEnvelope", "inSR": "4326",
            "spatialRel": "esriSpatialRelIntersects"})
    params.update(extra or {})
    pagina_soportada = (info.get("advancedQueryCapabilities") or {}).get("supportsPagination", False)
    campo_orden = info.get("objectIdField") or "OBJECTID"

    filas, offset = [], 0
    while True:
        p = dict(params)
        if pagina_soportada:
            p.update({"resultOffset": offset, "resultRecordCount": por_pagina,
                      "orderByFields": campo_orden})
        datos = get_json(url_capa + "/query", p)
        lote = datos.get("features", [])
        filas += lote
        if not pagina_soportada or not datos.get("exceededTransferLimit") and len(lote) < por_pagina:
            break
        if not lote:
            break
        offset += len(lote)
    return filas


# ------------------------------------------------------------------------------
# Geometría
# ------------------------------------------------------------------------------
def a_geojson(geometria):
    """Geometría ArcGIS -> GeoJSON (Point, MultiLineString o Polygon/MultiPolygon)."""
    if not geometria:
        return None
    if "x" in geometria and "y" in geometria:
        if geometria["x"] is None or geometria["y"] is None:
            return None
        return {"type": "Point", "coordinates": [round(geometria["x"], 6), round(geometria["y"], 6)]}
    if "paths" in geometria:
        return {"type": "MultiLineString",
                "coordinates": [[[round(x, 6), round(y, 6)] for x, y, *_ in p] for p in geometria["paths"]]}
    if "rings" in geometria:
        anillos = [[[round(x, 6), round(y, 6)] for x, y, *_ in r] for r in geometria["rings"]]
        exteriores, agujeros = [], []
        for anillo in anillos:  # ArcGIS: exteriores en sentido horario (área con signo negativo)
            (exteriores if _area_signada(anillo) < 0 else agujeros).append(anillo)
        if not exteriores:
            exteriores, agujeros = anillos, []
        poligonos = [[e] for e in exteriores]
        for h in agujeros:
            destino = next((p for p in poligonos if _punto_en_anillo(h[0][0], h[0][1], p[0])), poligonos[0])
            destino.append(h)
        if len(poligonos) == 1:
            return {"type": "Polygon", "coordinates": poligonos[0]}
        return {"type": "MultiPolygon", "coordinates": poligonos}
    return None


def _area_signada(anillo):
    return sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(anillo, anillo[1:])) / 2


def _punto_en_anillo(x, y, anillo):
    dentro = False
    for (x1, y1), (x2, y2) in zip(anillo, anillo[1:]):
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            dentro = not dentro
    return dentro


def dentro_de(lon, lat, geometria):
    """Punto en Polygon/MultiPolygon GeoJSON (los agujeros quedan fuera)."""
    if geometria["type"] == "Polygon":
        poligonos = [geometria["coordinates"]]
    elif geometria["type"] == "MultiPolygon":
        poligonos = geometria["coordinates"]
    else:
        return False
    return any(sum(_punto_en_anillo(lon, lat, a) for a in poligono) % 2 == 1 for poligono in poligonos)


def limite_maipu():
    """Polígono de Maipú (MINVU). Si el servicio no responde, usa la copia guardada."""
    try:
        filas = consultar(URL_LIMITES, where=f"cod_com='{CUT_MAIPU}'", campos="cod_com,nom_com")
        if not filas:
            raise RuntimeError("el servicio no devolvió la comuna")
        geom = a_geojson(filas[0]["geometry"])
        ARCHIVO_LIMITE.write_text(json.dumps({
            "type": "Feature", "properties": {**filas[0]["attributes"], "fuente": "MINVU · División Político Administrativa",
                                              "url": URL_LIMITES, "descargado_utc": ahora_utc_iso()},
            "geometry": geom}, ensure_ascii=False), encoding="utf-8")
        return geom
    except RuntimeError as error:
        if ARCHIVO_LIMITE.exists():
            print(f"    [!] Límite comunal no disponible ({error}); se usa {ARCHIVO_LIMITE.name}")
            return json.loads(ARCHIVO_LIMITE.read_text(encoding="utf-8"))["geometry"]
        raise


# ------------------------------------------------------------------------------
# Salida
# ------------------------------------------------------------------------------
def ahora_utc_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def fecha_iso(epoch_ms):
    """Fecha de ArcGIS (milisegundos desde 1970, UTC) -> ISO 8601, o None."""
    if epoch_ms in (None, ""):
        return None
    return datetime.fromtimestamp(int(epoch_ms) / 1000, tz=timezone.utc).replace(microsecond=0).isoformat()


def texto(valor):
    """Texto sin espacios sobrantes; cadenas vacías -> None."""
    if valor is None:
        return None
    valor = " ".join(str(valor).split())
    return valor or None


def guardar_geojson(ruta, nombre, features, metadatos, indent=1):
    """Escribe un FeatureCollection con un bloque 'metadata' de trazabilidad.

    indent=None deja el archivo compacto (para capas con miles de puntos).
    """
    ruta = Path(ruta)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    salida = {
        "type": "FeatureCollection",
        "name": nombre,
        "metadata": {"generado_utc": ahora_utc_iso(), "comuna": COMUNA,
                     "total_features": len(features), **metadatos},
        "features": features,
    }
    ruta.write_text(json.dumps(salida, ensure_ascii=False, indent=indent), encoding="utf-8")
    print(f"    [+] {len(features)} features -> {ruta}")
    return salida
