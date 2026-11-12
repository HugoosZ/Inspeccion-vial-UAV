"""
A4 · Carga Amenazas/A4/data/tomtom_incidentes_maipu.geojson en temp_amenazas.

Cada incidente de TomTom (accidente, calle cerrada, obras, carril cerrado,
condición peligrosa o vehículo detenido) se carga con su geometría original
(punto o línea) y fuente_etl = A4_tomtom. El ruteo bloquea o penaliza las
aristas a menos de radio_afectacion_m del incidente.

Uso:
  python Amenazas/A4/cargar_tomtom.py            # carga en la BD
  python Amenazas/A4/cargar_tomtom.py --dry-run  # solo muestra lo que se cargaría
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import comun  # noqa: E402

ARCHIVO = Path(__file__).resolve().parent / "data" / "tomtom_incidentes_maipu.geojson"


def mapear(feature):
    p = feature["properties"]
    lugar = p.get("tramo") or (", ".join(p["vias"]) if p.get("vias") else "Maipú")
    nombre = f"{p['categoria']} · {lugar}"
    desc = f"{p.get('descripcion')}. Vigente desde {p.get('valido_desde') or 'sin dato'}"
    if p.get("valido_hasta"):
        desc += f" hasta {p['valido_hasta']}"
    desc += f". Radio de afectación {p['radio_afectacion_m']} m."
    if p.get("revisar_senaletica"):
        desc += " Revisar la señalética del tramo después del accidente."
    return nombre, p["tipo_amenaza"], p["nivel_riesgo"], desc


if __name__ == "__main__":
    comun.cargar_env()
    if not ARCHIVO.exists():
        print(f"    [!] No existe {ARCHIVO.name}: ejecuta antes Amenazas/A4/tomtom.py (requiere TOMTOM_API_KEY).")
        sys.exit(0)
    comun.cargar_features(ARCHIVO, "A4_tomtom", mapear, dry_run="--dry-run" in sys.argv)
