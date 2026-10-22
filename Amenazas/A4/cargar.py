"""
A4 · Carga Amenazas/A4/data/waze_threats_geofences.geojson en temp_amenazas.

Carga el archivo final del módulo Waze: por cada evento confirmado de calle
cerrada u obra de construcción en Maipú, el punto del incidente y su geocerca
circular de exclusión aérea (No-Fly Zone).

Uso:
  python Amenazas/A4/cargar.py            # carga en la BD
  python Amenazas/A4/cargar.py --dry-run  # solo muestra lo que se cargaría
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import comun  # noqa: E402

ARCHIVO = Path(__file__).resolve().parent / "data" / "waze_threats_geofences.geojson"


def mapear(feature):
    p = feature["properties"]
    es_geocerca = feature["geometry"]["type"] != "Point"
    radio = p.get("radio_buffer") or p.get("radio_metros") or 50
    calle = p.get("calle") or "Vía urbana Maipú"
    cat = p.get("categoria") or "CONSTRUCCION_VIAL"

    if es_geocerca:
        nombre = f"Waze {cat} en {calle} (geocerca {radio} m)"
        desc = f"Zona de exclusión aérea de {radio} m por {cat.lower()} en {calle}."
    else:
        nombre = f"Waze {cat} en {calle} (punto)"
        desc = f"{p.get('tipo')}: {p.get('descripcion')}. {p.get('justificacion') or ''}".strip()

    return nombre, p.get("tipo_amenaza") or "construccion_vial", p.get("nivel_riesgo") or "ALTO", desc


if __name__ == "__main__":
    comun.cargar_env()
    comun.cargar_features(ARCHIVO, "A4_waze", mapear, dry_run="--dry-run" in sys.argv)
