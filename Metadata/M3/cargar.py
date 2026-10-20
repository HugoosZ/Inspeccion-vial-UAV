"""
M3 · Carga Metadata/M3/data/controles_viales_maipu.geojson en temp_metadata.

Cada elemento OSM (PARE, Ceda el paso, semáforo o cruce peatonal) se carga con
un tipo según su etiqueta `highway`:
  stop -> 'senal_pare' · give_way -> 'senal_ceda_el_paso'
  traffic_signals -> 'semaforo' · crossing -> 'cruce_peatonal'
Las columnas `tag_*` con valor nulo no se guardan.

Uso:
  python Metadata/M3/cargar.py            # carga en la BD
  python Metadata/M3/cargar.py --dry-run  # solo muestra lo que se cargaría
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import comun_bd  # noqa: E402

ARCHIVO = Path(__file__).resolve().parent / "data" / "controles_viales_maipu.geojson"

TIPOS = {
    "stop": ("senal_pare", "Señal PARE"),
    "give_way": ("senal_ceda_el_paso", "Señal Ceda el paso"),
    "traffic_signals": ("semaforo", "Semáforo"),
    "crossing": ("cruce_peatonal", "Cruce peatonal"),
}
# Se omiten datos del editor de OSM (usuario, changeset), que no aportan al ruteo
OMITIR = {"user_id", "user_name", "changeset_id", "valid_to_timestamp", "clipped"}


def filas():
    datos = json.loads(ARCHIVO.read_text(encoding="utf-8"))
    for feat in datos.get("features", []):
        p = feat.get("properties") or {}
        tipo, etiqueta = TIPOS.get(p.get("tag_highway"), ("control_vial", "Control vial"))
        props = {k: v for k, v in p.items() if v is not None and k not in OMITIR}
        yield {
            "nombre": f"{etiqueta} OSM {p.get('osm_type')}/{p.get('osm_id')}",
            "tipo": tipo,
            "fuente": "OpenStreetMap vía ohsome API",
            "descripcion": f"{etiqueta} (highway={p.get('tag_highway')}), "
                           f"última edición OSM {p.get('edit_timestamp')}.",
            "propiedades": props,
            "geometry": feat.get("geometry"),
        }


if __name__ == "__main__":
    comun_bd.cargar_env()
    comun_bd.cargar_filas(list(filas()), "M3_controles_viales", dry_run="--dry-run" in sys.argv)
