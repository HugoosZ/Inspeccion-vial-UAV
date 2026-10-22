"""
Carga data/incendios_maipu.geojson en la tabla temp_amenazas.

Uso:
  python cargar.py            # carga en la BD (variables DB_HOST, DB_PORT, ...)
  python cargar.py --dry-run  # solo muestra lo que se cargaría
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import comun  # noqa: E402

ARCHIVO = Path(__file__).resolve().parent / "data" / "incendios_maipu.geojson"


def mapear(feature):
    p = feature["properties"]
    if p["fuente"] == "GDACS":
        nombre = f"Incendio forestal GDACS {p.get('glide') or p['id_amenaza']}"
        desc = (f"Polígono oficial GDACS, alerta {p.get('alerta_gdacs')}, "
                f"{p.get('area_afectada_ha')} ha, {p.get('valido_desde')} a {p.get('valido_hasta')}.")
    else:
        nombre = f"Foco de calor FIRMS {p.get('valido_desde')}"
        desc = (f"Píxel {p.get('lado_pixel_m')} m + {p.get('radio_exclusion_m')} m de exclusión, "
                f"confianza {p.get('confianza')}, FRP {p.get('frp_mw')} MW.")
    return nombre, p["tipo_amenaza"], p["nivel_riesgo"], desc


if __name__ == "__main__":
    comun.cargar_env()
    comun.cargar_features(ARCHIVO, "A1_incendios", mapear, dry_run="--dry-run" in sys.argv)
