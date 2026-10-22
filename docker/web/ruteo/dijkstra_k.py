"""
Generación de k rutas distintas con pgr_dijkstra sobre pesos resilientes.

Peso de cada arista (ver README, sección 2):
    w(e) = d(e)[km] · ρ(e) · π(e) · φ(e, t0)
  y las aristas bloqueadas (amenazas duras o cercanas a aeródromos) quedan con
  costo -1, que pgr_dijkstra interpreta como arista inexistente.

Método de penalización iterativa para obtener k alternativas:
  1. Ruta 1 = pgr_dijkstra con cost = w(e).
  2. Las aristas ya usadas multiplican su costo por FACTOR por cada ruta que
     las usó, y se vuelve a ejecutar pgr_dijkstra.
  3. Se repite hasta tener k rutas distintas (o hasta que no aparezcan
     alternativas nuevas).

Los factores se guardan en una tabla temporal de la sesión (pesos_ruteo) para
que la consulta interna de pgr_dijkstra los lea con un JOIN.
"""

from psycopg2.extras import execute_values

FACTOR = 3.0
INTENTOS_EXTRA = 3

SQL_ARISTAS = """
    SELECT e.id, e.source, e.target,
           CASE WHEN p.factor < 0 THEN -1
                ELSE e.length_m / 1000.0 * COALESCE(p.factor, 1) * power({f}, COALESCE(p.uso, 0)) END AS cost,
           CASE WHEN p.factor < 0 OR e.reverse_cost < 0 THEN -1
                ELSE e.length_m / 1000.0 * COALESCE(p.factor, 1) * power({f}, COALESCE(p.uso, 0)) END AS reverse_cost
    FROM network_edges e LEFT JOIN pesos_ruteo p ON p.id = e.id
    WHERE e.geom && ST_MakeEnvelope({xmin}, {ymin}, {xmax}, {ymax}, 4326)
"""


def _preparar_pesos(cur, bloqueadas, factores):
    cur.execute("""CREATE TEMP TABLE IF NOT EXISTS pesos_ruteo
                   (id BIGINT PRIMARY KEY, factor DOUBLE PRECISION NOT NULL DEFAULT 1, uso INTEGER NOT NULL DEFAULT 0)""")
    cur.execute("TRUNCATE pesos_ruteo")
    filas = [(e, -1.0) for e in bloqueadas] + [(e, f) for e, f in factores.items()
                                                if e not in bloqueadas and f != 1.0]
    if filas:
        execute_values(cur, "INSERT INTO pesos_ruteo (id, factor) VALUES %s", filas)


def k_rutas(conn, inicio, objetivo, k, bbox, bloqueadas=(), factores=None):
    """k rutas de pgr_dijkstra dentro de bbox.

    bloqueadas: aristas eliminadas del grafo; factores: edge -> multiplicador de w(e).
    """
    bloqueadas = set(bloqueadas)
    sql = SQL_ARISTAS.format(f=FACTOR, xmin=bbox[0], ymin=bbox[1], xmax=bbox[2], ymax=bbox[3])
    rutas, vistas = [], set()
    with conn.cursor() as cur:
        _preparar_pesos(cur, bloqueadas, factores or {})
        for _ in range(k + INTENTOS_EXTRA):
            cur.execute("""
                SELECT d.edge FROM pgr_dijkstra(%s, %s, %s, directed := true) AS d
                WHERE d.edge <> -1 ORDER BY d.seq""", (sql, inicio, objetivo))
            aristas = [fila[0] for fila in cur.fetchall()]
            if not aristas:
                break
            firma = tuple(aristas)
            if firma not in vistas:
                vistas.add(firma)
                rutas.append(aristas)
                if len(rutas) == k:
                    break
            execute_values(cur, """
                INSERT INTO pesos_ruteo (id, uso) VALUES %s
                ON CONFLICT (id) DO UPDATE SET uso = pesos_ruteo.uso + 1""", [(e, 1) for e in aristas])
    conn.rollback()  # descarta la tabla temporal de esta consulta
    return rutas
