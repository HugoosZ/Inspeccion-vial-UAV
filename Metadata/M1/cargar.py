"""
M1 · Carga Metadata/M1/paraderos_maipu.json en temp_metadata.

El JSON trae, por paradero, los atributos originales de la capa ArcGIS
(`attributes`) y una geometría propia con `latitud` y `longitud` en WGS84.
Cada paradero se carga como un Point con tipo 'paradero'.

Uso:
  python Metadata/M1/cargar.py            # carga en la BD
  python Metadata/M1/cargar.py --dry-run  # solo muestra lo que se cargaría
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import comun_bd  # noqa: E402

ARCHIVO = Path(__file__).resolve().parent / "paraderos_maipu.json"

# Atributos útiles para el ruteo (se omiten campos administrativos vacíos)
CAMPOS = ["codigo", "simt", "tipo", "clasificac", "eje", "desde", "hacia", "sentido",
          "nombre_ust", "estado", "tipo_rts", "zona_paga", "corredor"]


def reparar_texto(valor):
    """Corrige textos de la capa original guardados en CP850 y leídos como Latin-1
    (por ejemplo 'BORGO¥O' -> 'BORGOÑO', 'MAIPé' -> 'MAIPÚ')."""
    if not isinstance(valor, str):
        return valor
    try:
        return valor.encode("latin-1").decode("cp850")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return valor


def filas():
    datos = json.loads(ARCHIVO.read_text(encoding="utf-8"))
    for feat in datos.get("features", []):
        g = feat.get("geometry") or {}
        a = {k: reparar_texto(v) for k, v in (feat.get("attributes") or {}).items()}
        lat, lon = g.get("latitud"), g.get("longitud")
        if lat is None or lon is None:
            continue
        props = {c: a.get(c) for c in CAMPOS}
        props["id_ruteo"] = feat.get("ID_Ruteo")
        yield {
            "nombre": a.get("nombre_ust") or a.get("codigo"),
            "tipo": "paradero",
            "fuente": "SIG Tránsito Maipú / Paraderos Transantiago",
            "descripcion": f"Paradero {a.get('codigo')} en {a.get('eje')} "
                           f"({a.get('sentido')}), estado {a.get('estado')}.",
            "propiedades": props,
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
        }


if __name__ == "__main__":
    comun_bd.cargar_env()
    comun_bd.cargar_filas(list(filas()), "M1_paraderos", dry_run="--dry-run" in sys.argv)
