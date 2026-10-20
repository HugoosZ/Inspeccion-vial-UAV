"""
Rúbrica 8: k rutas distintas usando solo pgr_dijkstra y la distancia en km.

Método de penalización iterativa:
  1. Ruta 1 = pgr_dijkstra con cost = length_m / 1000 (km).
  2. Las aristas ya usadas multiplican su costo por FACTOR por cada ruta que
     las usó, y se vuelve a ejecutar pgr_dijkstra.
  3. Se repite hasta tener k rutas distintas (o hasta que no aparezcan
     alternativas nuevas).
Todas las rutas salen de pgr_dijkstra; la penalización solo sirve para que la
siguiente ejecución explore un camino diferente.
"""

FACTOR = 3.0
INTENTOS_EXTRA = 3


def _sql_aristas(uso):
    """Consulta de aristas para pgr_dijkstra con las penalizaciones vigentes."""
    if uso:
        casos = " ".join(f"WHEN {int(e)} THEN {FACTOR ** n:.6f}" for e, n in uso.items())
        factor = f"(CASE id {casos} ELSE 1 END)"
    else:
        factor = "1"
    return (
        "SELECT id, source, target, "
        f"length_m / 1000.0 * {factor} AS cost, "
        f"CASE WHEN reverse_cost < 0 THEN -1 ELSE length_m / 1000.0 * {factor} END AS reverse_cost "
        "FROM network_edges"
    )


def k_rutas(conn, inicio, objetivo, k):
    rutas, uso, vistas = [], {}, set()
    for _ in range(k + INTENTOS_EXTRA):
        with conn.cursor() as cur:
            cur.execute("""
                SELECT d.edge FROM pgr_dijkstra(%s, %s, %s, directed := true) AS d
                WHERE d.edge <> -1 ORDER BY d.seq""", (_sql_aristas(uso), inicio, objetivo))
            aristas = [fila[0] for fila in cur.fetchall()]
        if not aristas:
            break
        firma = tuple(aristas)
        if firma not in vistas:
            vistas.add(firma)
            rutas.append(aristas)
            if len(rutas) == k:
                break
        for e in aristas:
            uso[e] = uso.get(e, 0) + 1
    return rutas
