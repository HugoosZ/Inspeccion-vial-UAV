"""
Carga la infraestructura (nodos y aristas) en las tablas de pgRouting.

Entradas:
  Infraestructura/data/nodos_maipu.geojson   -> network_nodes
  Infraestructura/data/aristas_maipu.geojson -> network_edges

Los IDs del GeoJSON se insertan tal cual (1..N), de modo que source/target de
las aristas apuntan directamente a network_nodes.id. Antes de cargar se vacían
network_nodes, network_edges e inspection_routes (las rutas dependen de los
nodos), así que re-ejecutar el script no duplica datos.

Uso:
  python Infraestructura/cargar.py            # carga en la BD
  python Infraestructura/cargar.py --dry-run  # solo muestra lo que se cargaría
"""

import json
import os
import sys
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data"
NODOS = DATA_DIR / "nodos_maipu.geojson"
ARISTAS = DATA_DIR / "aristas_maipu.geojson"
LOTE = 1000

SQL_NODOS = """
INSERT INTO network_nodes (id, osm_id, commune, elevation_m, geom)
SELECT v.id, v.osm_id, v.commune, v.elevation_m,
       ST_SetSRID(ST_GeomFromGeoJSON(v.geojson), 4326)
FROM (VALUES %s) AS v(id, osm_id, commune, elevation_m, geojson)
ON CONFLICT (osm_id) DO NOTHING
"""

SQL_ARISTAS = """
INSERT INTO network_edges (id, osm_id, name, highway, source, target, cost, reverse_cost,
                           length_m, maxspeed_kmh, oneway, commune, geom)
SELECT v.id, v.osm_id, v.name, v.highway, v.source, v.target, v.cost, v.reverse_cost,
       v.length_m, v.maxspeed_kmh, v.oneway, v.commune,
       ST_SetSRID(ST_GeomFromGeoJSON(v.geojson), 4326)
FROM (VALUES %s) AS v(id, osm_id, name, highway, source, target, cost, reverse_cost,
                      length_m, maxspeed_kmh, oneway, commune, geojson)
"""


def leer(ruta):
    if not ruta.exists():
        sys.exit(f"[-] No existe {ruta}. Ejecuta primero Infraestructura/extractor.py")
    return json.loads(ruta.read_text(encoding="utf-8"))["features"]


def tuplas_nodos(features):
    for f in features:
        p = f["properties"]
        yield (p["id"], p["osm_id"], p["commune"], p.get("elevation_m") or 0.0,
               json.dumps(f["geometry"]))


def tuplas_aristas(features):
    for f in features:
        p = f["properties"]
        yield (p["id"], p.get("osm_id"), (p.get("name") or None) and p["name"][:150],
               p["highway"], p["source"], p["target"], p["cost"], p["reverse_cost"],
               p["length_m"], p.get("maxspeed_kmh") or 50, bool(p.get("oneway")),
               p["commune"], json.dumps(f["geometry"]))


def conectar():
    import psycopg2

    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"), port=os.getenv("DB_PORT", "5432"),
        dbname=os.getenv("DB_NAME", "ruteo_db"), user=os.getenv("DB_USER", "ruteo_user"),
        password=os.getenv("DB_PASSWORD", "ruteo_pass"),
    )


def main(dry_run):
    nodos = list(tuplas_nodos(leer(NODOS)))
    aristas = list(tuplas_aristas(leer(ARISTAS)))
    print(f"[*] {len(nodos)} nodos y {len(aristas)} aristas preparados")
    if dry_run:
        print("   ", nodos[0][:4])
        print("   ", aristas[0][:10])
        print("[*] dry-run: no se escribió en la base de datos.")
        return

    from psycopg2.extras import execute_values

    conn = conectar()
    try:
        with conn, conn.cursor() as cur:
            cur.execute("TRUNCATE inspection_routes, network_edges, network_nodes RESTART IDENTITY CASCADE")
            for i in range(0, len(nodos), LOTE):
                execute_values(cur, SQL_NODOS, nodos[i:i + LOTE], page_size=LOTE)
            for i in range(0, len(aristas), LOTE):
                execute_values(cur, SQL_ARISTAS, aristas[i:i + LOTE], page_size=LOTE)
            # Los IDs se insertaron explícitamente: se ajustan las secuencias
            cur.execute("SELECT setval(pg_get_serial_sequence('network_nodes','id'), "
                        "COALESCE((SELECT MAX(id) FROM network_nodes), 1))")
            cur.execute("SELECT setval(pg_get_serial_sequence('network_edges','id'), "
                        "COALESCE((SELECT MAX(id) FROM network_edges), 1))")
            cur.execute("SELECT count(*) FROM network_nodes")
            n = cur.fetchone()[0]
            cur.execute("SELECT count(*), round(sum(length_m)::numeric / 1000, 1) FROM network_edges")
            m, km = cur.fetchone()
        print(f"[+] Cargados {n} nodos y {m} aristas ({km} km de red vial)")
    finally:
        conn.close()


if __name__ == "__main__":
    main(dry_run="--dry-run" in sys.argv)
