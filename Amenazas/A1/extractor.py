"""
==============================================================================
AMENAZA A1: Incendios forestales y de pastizales (GDACS + NASA FIRMS)
==============================================================================
Extract:
  - GDACS (sin autenticación): eventos de incendio forestal (WF) en Chile y el
    polígono oficial de cada episodio.
  - NASA FIRMS (MAP_KEY gratuita, se pide con un correo): focos de calor VIIRS
    detectados en los últimos días dentro del rectángulo de Maipú.
Transform:
  - GDACS: se conservan solo los polígonos que se cruzan con la Región
    Metropolitana (o todo Chile si GDACS_ALCANCE=pais).
  - FIRMS: cada foco es un píxel de scan x track km; se convierte en un cuadrado
    expandido por un radio de exclusión (INCENDIOS_RADIO_M, por defecto 2000 m).
Load (archivo):
  - data/incendios_maipu.geojson  (polígonos de exclusión, EPSG:4326)

Uso:
  python extractor.py
Variables de entorno opcionales:
  FIRMS_MAP_KEY        clave FIRMS; sin ella se omite FIRMS y se usa solo GDACS
  FIRMS_SOURCE         VIIRS_SNPP_NRT (defecto), VIIRS_NOAA20_NRT, MODIS_NRT ...
  FIRMS_DIAS           días hacia atrás, 1 a 5 (defecto 5)
  GDACS_DESDE          fecha inicial AAAA-MM-DD (defecto: 365 días atrás)
  GDACS_ALCANCE        region (defecto) | pais
  INCENDIOS_RADIO_M    radio de exclusión alrededor del fuego (defecto 2000)
"""

import csv
import io
import os
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import comun  # noqa: E402

DATA_DIR = Path(__file__).resolve().parent / "data"
SALIDA = DATA_DIR / "incendios_maipu.geojson"

GDACS_LISTA = "https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH"
GDACS_GEOMETRIA = "https://www.gdacs.org/gdacsapi/api/polygons/getgeometry"
FIRMS_AREA = "https://firms.modaps.eosdis.nasa.gov/api/area/csv/{key}/{source}/{bbox}/{dias}"

NIVEL_GDACS = {"Red": "CRITICO", "Orange": "ALTO", "Green": "MEDIO"}


# ------------------------------------------------------------------------------
# GDACS
# ------------------------------------------------------------------------------
def extraer_gdacs(desde, hasta, alcance, radio_m):
    print("[*] GDACS: consultando incendios forestales en Chile...")
    eventos = comun.http_get(GDACS_LISTA, params={
        "eventlist": "WF", "country": "Chile",
        "fromDate": desde.isoformat(), "toDate": hasta.isoformat(),
    })
    lista = eventos.get("features", []) if isinstance(eventos, dict) else []
    print(f"    [+] {len(lista)} eventos WF en Chile desde {desde}")

    zona = comun.AOI_REGION_METROPOLITANA
    features = []
    for ev in lista:
        p = ev.get("properties", {})
        try:
            geo = comun.http_get(GDACS_GEOMETRIA, params={
                "eventtype": "WF", "eventid": p["eventid"], "episodeid": p["episodeid"],
            })
        except RuntimeError as error:
            print(f"    [-] Sin geometría para evento {p.get('eventid')}: {error}")
            continue

        for poly in geo.get("features", []):
            g = poly.get("geometry") or {}
            if g.get("type") not in ("Polygon", "MultiPolygon"):
                continue  # se descartan centroides y puntos de etiqueta
            bbox = comun.bbox_geometria(g)
            dentro = comun.bbox_se_cruzan(bbox, zona)
            if alcance == "region" and not dentro:
                continue
            sev = p.get("severitydata") or {}
            features.append({
                "type": "Feature",
                "geometry": g,
                "properties": {
                    "id_amenaza": f"GDACS-{p['eventid']}-{p['episodeid']}",
                    "fuente": "GDACS",
                    "tipo_amenaza": "incendio_forestal",
                    "nivel_riesgo": NIVEL_GDACS.get(p.get("alertlevel"), "MEDIO"),
                    "accion_grafo": "BLOQUEAR_ARISTAS",
                    "factor_penalizacion": "INF",
                    "valido_desde": p.get("fromdate"),
                    "valido_hasta": p.get("todate"),
                    "es_actual": str(p.get("iscurrent", "false")).lower() == "true",
                    "en_region_metropolitana": dentro,
                    "alerta_gdacs": p.get("alertlevel"),
                    "area_afectada_ha": sev.get("severity"),
                    "clase_poligono": (poly.get("properties") or {}).get("Class"),
                    "glide": p.get("glide"),
                    "url_reporte": (p.get("url") or {}).get("report"),
                    "radio_exclusion_m": 0,
                },
            })
    print(f"    [+] {len(features)} polígonos GDACS conservados (alcance={alcance})")
    return features


# ------------------------------------------------------------------------------
# NASA FIRMS
# ------------------------------------------------------------------------------
def nivel_firms(confianza, frp):
    """Nivel de riesgo según confianza VIIRS (l/n/h) y potencia radiativa (MW)."""
    conf = str(confianza).lower()
    if conf in ("h", "high") or (frp or 0) >= 20:
        return "CRITICO"
    if conf in ("n", "nominal"):
        return "ALTO"
    return "MEDIO"


def extraer_firms(map_key, source, dias, radio_m):
    print(f"[*] FIRMS: focos de calor {source}, últimos {dias} días, rectángulo de Maipú...")
    bbox = ",".join(str(v) for v in comun.AOI_MAIPU)
    url = FIRMS_AREA.format(key=map_key, source=source, bbox=bbox, dias=dias)
    texto = comun.http_get(url, como_json=False)
    if texto.lstrip().lower().startswith(("invalid", "error")):
        raise RuntimeError(f"FIRMS respondió: {texto[:200]}")

    features = []
    for fila in csv.DictReader(io.StringIO(texto)):
        lat, lon = float(fila["latitude"]), float(fila["longitude"])
        if not comun.punto_en_bbox(lon, lat, comun.AOI_MAIPU):
            continue
        # scan y track = tamaño del píxel en km (VIIRS ~0,375 km, MODIS ~1 km)
        lado_m = 1000 * max(float(fila.get("scan") or 0.375), float(fila.get("track") or 0.375))
        frp = float(fila.get("frp") or 0)
        hora = str(fila.get("acq_time", "0000")).zfill(4)
        features.append({
            "type": "Feature",
            "geometry": comun.cuadrado_con_margen(lon, lat, lado_m, radio_m),
            "properties": {
                "id_amenaza": f"FIRMS-{fila['acq_date']}-{hora}-{lat:.4f}-{lon:.4f}",
                "fuente": "NASA FIRMS",
                "tipo_amenaza": "foco_de_calor",
                "nivel_riesgo": nivel_firms(fila.get("confidence"), frp),
                "accion_grafo": "BLOQUEAR_ARISTAS",
                "factor_penalizacion": "INF",
                "valido_desde": f"{fila['acq_date']}T{hora[:2]}:{hora[2:]}:00Z",
                "valido_hasta": None,
                "latitud_foco": lat,
                "longitud_foco": lon,
                "lado_pixel_m": round(lado_m),
                "radio_exclusion_m": radio_m,
                "confianza": fila.get("confidence"),
                "frp_mw": frp,
                "satelite": fila.get("satellite"),
                "instrumento": fila.get("instrument"),
                "dia_noche": fila.get("daynight"),
            },
        })
    print(f"    [+] {len(features)} focos FIRMS dentro de Maipú")
    return features


# ------------------------------------------------------------------------------
def main():
    comun.cargar_env()
    radio_m = int(os.getenv("INCENDIOS_RADIO_M", "2000"))
    alcance = os.getenv("GDACS_ALCANCE", "region").lower()
    hasta = date.today()
    desde = date.fromisoformat(os.getenv("GDACS_DESDE", (hasta - timedelta(days=365)).isoformat()))

    features, fuentes_ok = [], []
    try:
        features += extraer_gdacs(desde, hasta, alcance, radio_m)
        fuentes_ok.append("GDACS")
    except RuntimeError as error:
        print(f"[!] GDACS no disponible: {error}")

    map_key = os.getenv("FIRMS_MAP_KEY", "").strip()
    if map_key:
        try:
            dias = max(1, min(5, int(os.getenv("FIRMS_DIAS", "5"))))
            features += extraer_firms(map_key, os.getenv("FIRMS_SOURCE", "VIIRS_SNPP_NRT"),
                                      dias, radio_m)
            fuentes_ok.append("FIRMS")
        except RuntimeError as error:
            print(f"[!] FIRMS no disponible: {error}")
    else:
        print("[!] FIRMS_MAP_KEY no definida: se omite FIRMS (pídela en "
              "https://firms.modaps.eosdis.nasa.gov/api/map_key).")

    if not fuentes_ok:
        print("[-] Ninguna fuente respondió; se conserva el archivo anterior si existe.")
        sys.exit(1)

    comun.guardar_geojson(SALIDA, "amenaza_a1_incendios_maipu", features, {
        "amenaza": "A1 - Incendios forestales y de pastizales",
        "fuentes_consultadas": fuentes_ok,
        "gdacs_desde": desde.isoformat(),
        "gdacs_alcance": alcance,
        "radio_exclusion_m": radio_m,
        "aoi_maipu_bbox": list(comun.AOI_MAIPU),
    })


if __name__ == "__main__":
    main()
