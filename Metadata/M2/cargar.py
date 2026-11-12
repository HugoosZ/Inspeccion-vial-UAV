"""
M2 · Carga Metadata/M2/data/lugares_publicos_maipu.geojson en temp_metadata.

Cada lugar público queda con tipo = su categoría (salud_publica,
educacion_publica, carabineros, bomberos) y fuente_etl = M2_lugares_publicos.

Uso:
  python Metadata/M2/cargar.py            # carga en la BD
  python Metadata/M2/cargar.py --dry-run  # solo muestra lo que se cargaría
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import comun_bd  # noqa: E402

ARCHIVO = Path(__file__).resolve().parent / "data" / "lugares_publicos_maipu.geojson"

ETIQUETAS = {"salud_publica": "Salud pública", "educacion_publica": "Educación pública",
             "carabineros": "Carabineros", "bomberos": "Bomberos"}


def filas():
    datos = json.loads(ARCHIVO.read_text(encoding="utf-8"))
    for feat in datos.get("features", []):
        p = feat["properties"]
        yield {
            "nombre": p.get("nombre"),
            "tipo": p["categoria"],
            "fuente": p.get("fuente"),
            "descripcion": f"{ETIQUETAS.get(p['categoria'], p['categoria'])} "
                           f"(dependencia {p.get('dependencia')}), candidato a droneport. "
                           f"Dirección: {p.get('direccion') or 'sin dato'}.",
            "propiedades": {k: v for k, v in p.items() if v is not None},
            "geometry": feat["geometry"],
        }


if __name__ == "__main__":
    comun_bd.cargar_env()
    comun_bd.cargar_filas(list(filas()), "M2_lugares_publicos", dry_run="--dry-run" in sys.argv)
