"""
M5 · Carga las manzanas del Censo 2024 de Maipú en temp_metadata.

Entrada: Metadata/M5/manzanas_maipu_completo.zip, que contiene
manzanas_maipu_completo.geojson (5.875 MultiPolygon, EPSG:4674 SIRGAS-Chile).
Se lee directamente desde el ZIP (no hace falta descomprimirlo).

Transformación:
  - Se conservan solo las variables útiles para el ruteo (identificación de la
    manzana y los insumos del índice de vulnerabilidad).
  - Se calcula el índice de vulnerabilidad por manzana:
        V_m = 1/3 [ z(-escolaridad) + z(hacinadas/viviendas) + z(desocupados/(ocupados+desocupados)) ]
    donde z es la puntuación estándar sobre todas las manzanas con dato.
    V_m > 0 indica una manzana más vulnerable que el promedio comunal.
  - La geometría se transforma de EPSG:4674 a EPSG:4326 en PostGIS.

Uso:
  python Metadata/M5/cargar.py            # carga en la BD
  python Metadata/M5/cargar.py --dry-run  # solo muestra lo que se cargaría
"""

import json
import math
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import comun_bd  # noqa: E402

ZIP = Path(__file__).resolve().parent / "manzanas_maipu_completo.zip"
MIEMBRO = "manzanas_maipu_completo.geojson"
SRID_ORIGEN = 4674

CAMPOS = {
    "MANZENT": "manzent",
    "DISTRITO": "distrito",
    "AREA_C": "area",
    "LOCALIDAD": "localidad",
    "n_per_x": "personas",
    "n_hog_x": "hogares",
    "n_vp_x": "viviendas",
    "prom_escolaridad18_x": "escolaridad_prom_18",
    "n_viv_hacinadas_x": "viviendas_hacinadas",
    "n_ocupado_x": "ocupados",
    "n_desocupado_x": "desocupados",
}


def _num(v):
    try:
        v = float(v)
        return None if math.isnan(v) else v
    except (TypeError, ValueError):
        return None


def leer_manzanas():
    """Devuelve lista de (propiedades_dict, geometria_geojson)."""
    try:
        import geopandas as gpd
        from shapely.geometry import mapping

        gdf = gpd.read_file(f"zip://{ZIP}!{MIEMBRO}", columns=list(CAMPOS))
        print(f"    [*] {len(gdf)} manzanas leídas con geopandas")
        return [({c: row.get(c) for c in CAMPOS}, mapping(row.geometry))
                for _, row in gdf.iterrows() if row.geometry is not None]
    except ImportError:
        print("    [!] geopandas no disponible: lectura JSON directa (usa más memoria)")
        with zipfile.ZipFile(ZIP) as z:
            datos = json.loads(z.read(MIEMBRO))
        return [({c: (f.get("properties") or {}).get(c) for c in CAMPOS}, f["geometry"])
                for f in datos["features"] if f.get("geometry")]


def zscores(valores):
    datos = [v for v in valores if v is not None]
    if len(datos) < 2:
        return [None] * len(valores)
    media = sum(datos) / len(datos)
    desv = math.sqrt(sum((v - media) ** 2 for v in datos) / (len(datos) - 1)) or 1.0
    return [None if v is None else (v - media) / desv for v in valores]


def filas():
    manzanas = leer_manzanas()
    props = [{CAMPOS[k]: v for k, v in p.items()} for p, _ in manzanas]

    esc, hac, des = [], [], []
    for p in props:
        e, viv, hz = _num(p["escolaridad_prom_18"]), _num(p["viviendas"]), _num(p["viviendas_hacinadas"])
        oc, de = _num(p["ocupados"]), _num(p["desocupados"])
        esc.append(-e if e is not None else None)
        hac.append(hz / viv if viv and hz is not None else None)
        des.append(de / (oc + de) if oc is not None and de is not None and (oc + de) > 0 else None)
        p["tasa_hacinamiento"] = None if hac[-1] is None else round(hac[-1], 4)
        p["tasa_desocupacion"] = None if des[-1] is None else round(des[-1], 4)

    z_esc, z_hac, z_des = zscores(esc), zscores(hac), zscores(des)
    for p, a, b, c in zip(props, z_esc, z_hac, z_des):
        componentes = [x for x in (a, b, c) if x is not None]
        # Se exige al menos 2 de los 3 componentes para no sesgar el índice
        p["indice_vulnerabilidad"] = round(sum(componentes) / len(componentes), 4) \
            if len(componentes) >= 2 else None
        if isinstance(p.get("manzent"), float):
            p["manzent"] = str(int(p["manzent"]))

    for p, (_, geom) in zip(props, manzanas):
        v = p["indice_vulnerabilidad"]
        yield {
            "nombre": f"Manzana {p.get('manzent')}",
            "tipo": "manzana_censo",
            "fuente": "INE Censo 2024 (manzanas Maipú)",
            "descripcion": f"Distrito {p.get('distrito')}, {p.get('personas')} personas, "
                           f"índice de vulnerabilidad {v if v is not None else 's/d'}.",
            "propiedades": p,
            "geometry": geom,
            "srid": SRID_ORIGEN,
        }


if __name__ == "__main__":
    comun_bd.cargar_env()
    comun_bd.cargar_filas(list(filas()), "M5_censo_manzanas", dry_run="--dry-run" in sys.argv)
