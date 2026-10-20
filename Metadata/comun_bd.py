"""
==============================================================================
Carga genérica de Metadata a la base de datos (tabla temp_metadata)
==============================================================================
Lo usan los scripts Metadata/M*/cargar.py. Cada script transforma su archivo a
una lista de filas y llama a cargar_filas(), que:
  - borra las filas anteriores de la misma fuente (carga idempotente),
  - inserta en lotes con execute_values,
  - transforma la geometría a EPSG:4326 si viene en otro sistema (p. ej. 4674).

Fila esperada (dict):
  nombre, tipo, fuente, descripcion, propiedades (dict), geometry (dict GeoJSON),
  srid (opcional, por defecto 4326)
"""

import json
import math
import os
from pathlib import Path

METADATA_DIR = Path(__file__).resolve().parent
PROJECT_DIR = METADATA_DIR.parent
COMUNA = "Maipú"
LOTE = 500


def cargar_env():
    """Carga el .env de la raíz sin depender de python-dotenv (no sobrescribe)."""
    ruta = PROJECT_DIR / ".env"
    if not ruta.exists():
        return
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if linea and not linea.startswith("#") and "=" in linea:
            clave, valor = linea.split("=", 1)
            os.environ.setdefault(clave.strip(), valor.strip().strip('"').strip("'"))


def limpiar(valor):
    """Deja un valor serializable a JSON válido para jsonb (NaN/inf -> null)."""
    if isinstance(valor, float) and (math.isnan(valor) or math.isinf(valor)):
        return None
    if isinstance(valor, dict):
        return {str(k): limpiar(v) for k, v in valor.items()}
    if isinstance(valor, (list, tuple)):
        return [limpiar(v) for v in valor]
    if hasattr(valor, "item"):            # tipos numpy
        return limpiar(valor.item())
    if hasattr(valor, "isoformat"):       # fechas / timestamps
        return valor.isoformat()
    return valor


def conectar_bd():
    import psycopg2

    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=os.getenv("DB_PORT", "5432"),
        dbname=os.getenv("DB_NAME", "ruteo_db"),
        user=os.getenv("DB_USER", "ruteo_user"),
        password=os.getenv("DB_PASSWORD", "ruteo_pass"),
    )


def _tupla(fila, fuente_etl):
    props = limpiar(dict(fila.get("propiedades") or {}))
    props["fuente_etl"] = fuente_etl
    return (
        (fila.get("nombre") or "")[:150],
        fila["tipo"][:50],
        (fila.get("fuente") or "")[:100],
        COMUNA,
        fila.get("descripcion"),
        json.dumps(props, ensure_ascii=False),
        json.dumps(fila["geometry"]),
        int(fila.get("srid", 4326)),
    )


SQL_INSERT = """
INSERT INTO temp_metadata (nombre, tipo, fuente, comuna, descripcion, propiedades, geom)
SELECT v.nombre, v.tipo, v.fuente, v.comuna, v.descripcion, v.props::jsonb,
       ST_Force2D(ST_Transform(ST_SetSRID(ST_GeomFromGeoJSON(v.geojson), v.srid), 4326))
FROM (VALUES %s) AS v(nombre, tipo, fuente, comuna, descripcion, props, geojson, srid)
"""


def cargar_filas(filas, fuente_etl, dry_run=False):
    """Inserta las filas en temp_metadata. Devuelve la cantidad cargada."""
    tuplas = [_tupla(f, fuente_etl) for f in filas if f.get("geometry")]
    print(f"    [*] {len(tuplas)} filas preparadas (fuente_etl={fuente_etl})")
    if dry_run:
        for t in tuplas[:3]:
            print("      ", t[:4], "| srid", t[7])
        print("    [*] dry-run: no se escribió en la base de datos.")
        return len(tuplas)

    from psycopg2.extras import execute_values

    conn = conectar_bd()
    try:
        with conn, conn.cursor() as cur:
            cur.execute("DELETE FROM temp_metadata WHERE propiedades->>'fuente_etl' = %s",
                        (fuente_etl,))
            for i in range(0, len(tuplas), LOTE):
                execute_values(cur, SQL_INSERT, tuplas[i:i + LOTE], page_size=LOTE)
        print(f"    [+] {len(tuplas)} filas cargadas en temp_metadata")
    finally:
        conn.close()
    return len(tuplas)
