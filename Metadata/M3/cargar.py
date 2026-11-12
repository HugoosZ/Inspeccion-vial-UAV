"""
M3 · Carga Metadata/M3/data/puentes_maipu.geojson en temp_metadata.

Cada estructura queda con tipo = puente, pasarela o enlace y
fuente_etl = M3_puentes.

Uso:
  python Metadata/M3/cargar.py            # carga en la BD
  python Metadata/M3/cargar.py --dry-run  # solo muestra lo que se cargaría
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import comun_bd  # noqa: E402

ARCHIVO = Path(__file__).resolve().parent / "data" / "puentes_maipu.geojson"
ETIQUETAS = {"puente": "Puente", "pasarela": "Pasarela peatonal", "enlace": "Enlace a desnivel"}


def filas():
    datos = json.loads(ARCHIVO.read_text(encoding="utf-8"))
    for feat in datos.get("features", []):
        p = feat["properties"]
        detalle = ", ".join(x for x in (
            f"cruza {p['cruza']}" if p.get("cruza") else None,
            f"largo {p['largo_m']} m" if p.get("largo_m") else None,
            f"camino {p['camino']}" if p.get("camino") else None) if x)
        yield {
            "nombre": p.get("nombre"),
            "tipo": p["tipo"],
            "fuente": p.get("fuente"),
            "descripcion": f"{ETIQUETAS.get(p['tipo'], p['tipo'])}" + (f": {detalle}." if detalle else "."),
            "propiedades": p,
            "geometry": feat["geometry"],
        }


if __name__ == "__main__":
    comun_bd.cargar_env()
    comun_bd.cargar_filas(list(filas()), "M3_puentes", dry_run="--dry-run" in sys.argv)
