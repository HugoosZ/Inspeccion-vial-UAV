"""
Carga data/clima_maipu.geojson en la tabla temp_amenazas.

Uso:
  python cargar.py            # carga en la BD (variables DB_HOST, DB_PORT, ...)
  python cargar.py --dry-run  # solo muestra lo que se cargaría
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import comun  # noqa: E402

ARCHIVO = Path(__file__).resolve().parent / "data" / "clima_maipu.geojson"


def mapear(feature):
    p = feature["properties"]
    nombre = f"Clima severo celda {p['id_celda']} {p['valido_desde'][:16]}"
    desc = f"{', '.join(p['motivos'])}. Válido {p['valido_desde']} a {p['valido_hasta']}."
    return nombre, p["tipo_amenaza"], p["nivel_riesgo"], desc


if __name__ == "__main__":
    comun.cargar_env()
    comun.cargar_features(ARCHIVO, "T3_clima", mapear, dry_run="--dry-run" in sys.argv)
