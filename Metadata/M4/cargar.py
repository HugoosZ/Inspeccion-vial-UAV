"""
M4 · Carga Metadata/M4/data/zonas_siniestros_maipu.geojson en temp_metadata.

Cada celda de 100 x 100 m queda con tipo = zona_siniestros, su nivel (CRITICA,
ALTA, MEDIA) y los conteos de siniestros. fuente_etl = M4_siniestros.
Los puntos individuales (siniestros_maipu.geojson) no se cargan: quedan en el
repositorio como respaldo de cada conteo.

Uso:
  python Metadata/M4/cargar.py            # carga en la BD
  python Metadata/M4/cargar.py --dry-run  # solo muestra lo que se cargaría
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import comun_bd  # noqa: E402

ARCHIVO = Path(__file__).resolve().parent / "data" / "zonas_siniestros_maipu.geojson"


def filas():
    datos = json.loads(ARCHIVO.read_text(encoding="utf-8"))
    for feat in datos.get("features", []):
        p = feat["properties"]
        lugar = p.get("ubicacion_principal") or f"celda {p['id_celda']}"
        yield {
            "nombre": f"Zona de siniestros {p['nivel'].lower()} · {lugar}",
            "tipo": "zona_siniestros",
            "fuente": "CONASET · Base de siniestros 2020-2025",
            "descripcion": f"{p['n_siniestros']} siniestros {p.get('anios') or ''} "
                           f"({p['n_desobediencia_senal']} por desobedecer una señal, "
                           f"{p['n_senal_defectuosa']} por señal defectuosa).",
            "propiedades": p,
            "geometry": feat["geometry"],
        }


if __name__ == "__main__":
    comun_bd.cargar_env()
    comun_bd.cargar_filas(list(filas()), "M4_siniestros", dry_run="--dry-run" in sys.argv)
