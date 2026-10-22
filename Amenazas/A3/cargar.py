"""
A3 · Carga Amenazas/A3/data/threats_geofences.geojson en temp_amenazas.

Se carga el archivo final del módulo SOSAFE: por cada amenaza confirmada, el
punto del reporte y su geocerca circular (No-Fly Zone). Los demás JSON de
data/ (raw_reports, filtered_reports, classified_threats, active_threats_graph)
son etapas intermedias o de auditoría del mismo flujo y su contenido ya queda
representado aquí.

Uso:
  python Amenazas/A3/cargar.py            # carga en la BD
  python Amenazas/A3/cargar.py --dry-run  # solo muestra lo que se cargaría
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import comun  # noqa: E402

ARCHIVO = Path(__file__).resolve().parent / "data" / "threats_geofences.geojson"


def mapear(feature):
    p = feature["properties"]
    es_geocerca = feature["geometry"]["type"] != "Point"
    radio = p.get("radio_metros") or p.get("radio_buffer")
    if es_geocerca:
        nombre = f"SOSAFE {p.get('categoria')} #{p.get('id')} (geocerca {radio} m)"
        desc = f"Zona de exclusión de {radio} m alrededor del reporte SOSAFE #{p.get('id')}."
    else:
        nombre = f"SOSAFE {p.get('categoria')} #{p.get('id')} (reporte)"
        desc = f"{p.get('tipo')}: {p.get('descripcion')}. {p.get('justificacion') or ''}".strip()
    return nombre, "zona_conflictiva", p.get("nivel_riesgo") or "ALTO", desc


if __name__ == "__main__":
    comun.cargar_env()
    comun.cargar_features(ARCHIVO, "A3_sosafe", mapear, dry_run="--dry-run" in sys.argv)
