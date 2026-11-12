"""
Carga data/incendios_maipu.geojson (CONAF) en la tabla temp_amenazas.

Tipos que se cargan:
  incendio_activo         polígono de exclusión (bloquea o penaliza aristas)
  incendio_historico      punto de un incendio extinguido (sin efecto en el grafo)
  probabilidad_ignicion   celda de 1 km con la probabilidad de ignición del día

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
    tipo = p["tipo_amenaza"]
    if tipo == "probabilidad_ignicion":
        nombre = f"Probabilidad de ignición {p['clase_pi']} · {p['id_celda']}"
        desc = (f"Pronóstico CONAF para el {p['valido_desde'][:10]}: probabilidad de ignición "
                f"{p['clase_pi']} en esta celda de 1 km.")
    elif tipo == "incendio_activo":
        nombre = f"Incendio {p.get('nombre_incendio') or p['id_conaf']} ({p['estado'].lower()})"
        desc = (f"CONAF, comuna {p.get('comuna_incendio')}, {p.get('superficie_ha')} ha, "
                f"inicio {p.get('inicio')}. Exclusión de {p['radio_exclusion_m']} m.")
    else:
        nombre = f"Incendio extinguido {p.get('nombre_incendio') or p['id_conaf']}"
        desc = (f"CONAF, temporada {p['temporada']}, comuna {p.get('comuna_incendio')}, "
                f"{p.get('superficie_ha')} ha, inicio {p.get('inicio')}.")
    return nombre, tipo, p["nivel_riesgo"], desc


if __name__ == "__main__":
    comun.cargar_env()
    comun.cargar_features(ARCHIVO, "A1_incendios", mapear, dry_run="--dry-run" in sys.argv)
