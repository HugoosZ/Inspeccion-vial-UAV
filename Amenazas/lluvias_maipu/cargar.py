"""
Carga data/lluvias_maipu.geojson en la tabla temp_amenazas.

Uso:
  python cargar.py            # carga en la BD (variables DB_HOST, DB_PORT, ...)
  python cargar.py --dry-run  # solo muestra lo que se cargaría
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import comun  # noqa: E402

ARCHIVO = Path(__file__).resolve().parent / "data" / "lluvias_maipu.geojson"


def mapear(feature):
    p = feature["properties"]
    nombre = f"Emergencia lluvia {p.get('barrio') or ''} - {p.get('calle') or 's/calle'}".strip()
    estado = "activa" if p["activa"] else "histórica"
    desc = (f"{p['estado']} ({estado}), punto crítico: {'sí' if p['punto_critico'] else 'no'}, "
            f"última edición {p.get('fecha_edicion')}.")
    return nombre, p["tipo_amenaza"], p["nivel_riesgo"], desc


if __name__ == "__main__":
    comun.cargar_env()
    comun.cargar_features(ARCHIVO, "T5_lluvias_maipu", mapear, dry_run="--dry-run" in sys.argv)
