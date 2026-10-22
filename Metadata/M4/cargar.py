"""
M4 · Carga Metadata/M4/aerodromos.geojson en temp_metadata.

Cada aeródromo (DGAC, exportado desde KMZ) se carga como Point con tipo
'aerodromo'. La coordenada Z (altura 0 del KML) se elimina con ST_Force2D.
Se cargan todos los aeródromos del archivo: en la fase 3 se filtran con
ST_DWithin según el radio de exclusión que se adopte.

Uso:
  python Metadata/M4/cargar.py            # carga en la BD
  python Metadata/M4/cargar.py --dry-run  # solo muestra lo que se cargaría
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import comun_bd  # noqa: E402

ARCHIVO = Path(__file__).resolve().parent / "aerodromos.geojson"


def filas():
    datos = json.loads(ARCHIVO.read_text(encoding="utf-8"))
    for feat in datos.get("features", []):
        p = feat.get("properties") or {}
        g = feat.get("geometry")
        if not g:
            continue
        yield {
            "nombre": p.get("name") or "Aeródromo sin nombre",
            "tipo": "aerodromo",
            "fuente": "DGAC (KMZ de aeródromos)",
            "descripcion": p.get("description") or f"{p.get('category')} - {p.get('name')}",
            "propiedades": {k: p.get(k) for k in ("name", "layer", "category", "folder")},
            "geometry": g,
        }


if __name__ == "__main__":
    comun_bd.cargar_env()
    comun_bd.cargar_filas(list(filas()), "M4_aerodromos", dry_run="--dry-run" in sys.argv)
