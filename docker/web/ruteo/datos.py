"""
Acceso a PostGIS para el ruteo: nodos, amenazas por arista, aristas
requeridas (señaléticas M1 y zonas de siniestros M4) y geometrías.
"""

import json
import math
from datetime import timedelta

# Probabilidad de que una amenaza se materialice y corte la ruta, por nivel
# (supuesto de diseño usado por la métrica de resiliencia).
PROB_NIVEL = {"BAJO": 0.05, "MEDIO": 0.2, "ALTO": 0.5, "CRITICO": 0.8}

# Factor sobre el costo de una arista según la acción de la amenaza
FACTOR_ACCION = {"PENALIZAR_ARISTAS": 1.5, "POSTERGAR_ARISTAS": 2.0}
# El clima ALTO/CRÍTICO se trata como "bloqueo blando": la arista sigue en el
# grafo pero cruzarla en esa hora cuesta 10 veces más. Así pgr_dijkstra la
# esquiva si hay alternativa, en vez de quedarse sin solución.
FACTOR_CLIMA_BLOQUEO = 10.0

# ------------------------------------------------------------------------------
def _filas(conn, sql, params=()):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        columnas = [c[0] for c in cur.description]
        return [dict(zip(columnas, fila)) for fila in cur.fetchall()]


def nodo_mas_cercano(conn, lon, lat):
    fila = _filas(conn, """
        SELECT id, ST_X(geom) AS lon, ST_Y(geom) AS lat
        FROM network_nodes
        ORDER BY geom <-> ST_SetSRID(ST_Point(%s, %s), 4326)
        LIMIT 1""", (lon, lat))
    return fila[0] if fila else None


def bbox_region(a, b, margen_m):
    """Rectángulo que contiene los dos puntos más un margen en metros."""
    lat_media = (a["lat"] + b["lat"]) / 2
    dlat = margen_m / 111_320
    dlon = margen_m / (111_320 * math.cos(math.radians(lat_media)))
    return (min(a["lon"], b["lon"]) - dlon, min(a["lat"], b["lat"]) - dlat,
            max(a["lon"], b["lon"]) + dlon, max(a["lat"], b["lat"]) + dlat)


# ------------------------------------------------------------------------------
# Amenazas por arista
# ------------------------------------------------------------------------------
SQL_AMENAZAS_ARISTA = """
WITH amen AS (
    SELECT id, nivel_riesgo, geom,
           propiedades->>'fuente_etl' AS fuente,
           propiedades->>'id_celda'   AS id_celda,
           NULLIF(propiedades->>'valido_desde', '')::timestamptz AS desde,
           NULLIF(propiedades->>'valido_hasta', '')::timestamptz AS hasta,
           COALESCE(propiedades->>'accion_grafo', 'BLOQUEAR_ARISTAS') AS accion,
           COALESCE(NULLIF(propiedades->>'radio_afectacion_m', '')::float, 0) AS radio
    FROM temp_amenazas
    WHERE COALESCE(propiedades->>'accion_grafo', 'BLOQUEAR_ARISTAS') <> 'NINGUNA'
      AND geom && ST_MakeEnvelope(%(xmin)s, %(ymin)s, %(xmax)s, %(ymax)s, 4326)
)
SELECT e.id AS edge_id, a.id AS amenaza_id, a.fuente, a.id_celda, a.nivel_riesgo,
       a.accion, EXTRACT(EPOCH FROM a.desde) AS desde, EXTRACT(EPOCH FROM a.hasta) AS hasta
FROM amen a
JOIN network_edges e
  ON e.geom && ST_Expand(a.geom, 0.003)
 AND CASE WHEN a.radio > 0 THEN ST_DWithin(e.geom::geography, a.geom::geography, a.radio)
          ELSE ST_Intersects(e.geom, a.geom) END
WHERE (a.hasta IS NULL OR a.hasta > %(t0)s)
  AND (a.desde IS NULL OR a.desde < %(t1)s)
  AND e.geom && ST_MakeEnvelope(%(xmin)s, %(ymin)s, %(xmax)s, %(ymax)s, 4326)
"""


class Amenazas:
    """Amenazas que afectan a cada arista dentro de la ventana [t0, t1].

    - bloqueadas: aristas con una amenaza dura (A1 incendio, A3 SOSAFE,
      A4 TomTom) -> se eliminan del grafo de pgr_dijkstra.
    - factor_estatico: penalización permanente en la ventana (PENALIZAR, POSTERGAR).
    - temporales: intervalos (desde, hasta, factor) del clima (A2), que dependen
      de la hora a la que el dron cruza la arista.
    - eventos: para la métrica, cada arista -> {clave_evento: probabilidad}.
    """

    def __init__(self, filas, considerar_clima=True, amenazas=None):
        self.bloqueadas, self.factor_estatico, self.temporales, self.eventos = set(), {}, {}, {}
        if amenazas is not None:
            # solo las amenazas elegidas por el usuario (fuente_etl A1_..., A2_..., ...)
            filas = [f for f in filas if (f["fuente"] or "")[:2] in amenazas]
        self.n_amenazas = len({f["amenaza_id"] for f in filas})
        for f in filas:
            e, nivel, accion = f["edge_id"], f["nivel_riesgo"] or "MEDIO", f["accion"]
            clave = ("A2", f["id_celda"]) if f["fuente"] == "A2_clima" else ("A", f["amenaza_id"])
            p = PROB_NIVEL.get(nivel, 0.2)
            self.eventos.setdefault(e, {})
            self.eventos[e][clave] = max(p, self.eventos[e].get(clave, 0))

            if f["fuente"] == "A2_clima":
                if not considerar_clima:
                    continue
                factor = FACTOR_CLIMA_BLOQUEO if accion == "BLOQUEAR_ARISTAS" else FACTOR_ACCION.get(accion, 1.5)
                self.temporales.setdefault(e, []).append(
                    (float(f["desde"] or 0), float(f["hasta"] or 1e12), factor))
            elif accion == "BLOQUEAR_ARISTAS":
                self.bloqueadas.add(e)
            else:
                self.factor_estatico[e] = max(self.factor_estatico.get(e, 1.0),
                                              FACTOR_ACCION.get(accion, 1.5))

    def factor_en(self, edge_id, t_epoch):
        """Factor horario del clima para cruzar la arista en el instante t."""
        factor = 1.0
        for desde, hasta, f in self.temporales.get(edge_id, ()):
            if desde <= t_epoch < hasta:
                factor = max(factor, f)
        return factor


def amenazas_region(conn, bbox, t0, horizonte_h, considerar_clima, amenazas=None):
    t1 = t0 + timedelta(hours=horizonte_h)
    filas = _filas(conn, SQL_AMENAZAS_ARISTA, {
        "xmin": bbox[0], "ymin": bbox[1], "xmax": bbox[2], "ymax": bbox[3], "t0": t0, "t1": t1})
    return Amenazas(filas, considerar_clima, amenazas)


def probabilidades_aristas(conn, bbox, amenazas):
    """Probabilidad de falla de cada arista del rectángulo (punto 5, tabla
    prob_falla_aristas), combinando solo las amenazas elegidas:
        p(e) = 1 - prod_{k elegidas} (1 - p_k(e))
    Devuelve (dict edge_id -> p, ventana (t0, t1) en que se calculó) o
    ({}, None) si la tabla no existe o está vacía."""
    if not amenazas:
        return {}, None
    columnas = " * ".join(f"(1 - COALESCE(p.p_{a.lower()}, 0))" for a in amenazas)
    try:
        filas = _filas(conn, f"""
            SELECT p.edge_id, 1 - {columnas} AS p, p.t0, p.t1
            FROM prob_falla_aristas p JOIN network_edges e ON e.id = p.edge_id
            WHERE e.geom && ST_MakeEnvelope(%(xmin)s, %(ymin)s, %(xmax)s, %(ymax)s, 4326)""",
                       {"xmin": bbox[0], "ymin": bbox[1], "xmax": bbox[2], "ymax": bbox[3]})
    except Exception:  # noqa: BLE001  (tabla aún no creada: se ejecuta antes Probabilidades/generar.py)
        conn.rollback()
        return {}, None
    if not filas:
        return {}, None
    ventana = (filas[0]["t0"], filas[0]["t1"])
    return {int(f["edge_id"]): float(f["p"]) for f in filas if f["p"] and f["p"] > 0}, ventana


def aristas_requeridas(conn, inicio, objetivo, prefs, excluidas):
    """Aristas a inspeccionar (E_R) dentro del corredor inicio-objetivo.

    Una arista es candidata si tiene al menos una señal de los tipos elegidos
    (M1) a menos de 15 m. Se ordenan por puntaje = n° de señales (+2 si cruza
    una zona de siniestros CRITICA o ALTA de M4 y la preferencia está activa) y
    se toman las max_requeridas mejores que no estén bloqueadas.
    """
    radio = float(prefs["radio_corredor_m"])
    filas = _filas(conn, """
        WITH linea AS (
            SELECT ST_MakeLine(ST_SetSRID(ST_Point(%(lon1)s, %(lat1)s), 4326),
                               ST_SetSRID(ST_Point(%(lon2)s, %(lat2)s), 4326)) AS g
        ),
        cand AS (
            SELECT e.id, e.source, e.target, e.length_m, e.name, e.geom
            FROM network_edges e, linea
            WHERE e.geom && ST_Expand(linea.g, %(grados)s)
              AND ST_DWithin(e.geom::geography, linea.g::geography, %(radio)s)
        )
        SELECT c.id, c.source, c.target, c.length_m, c.name,
               (SELECT count(*) FROM temp_metadata s
                 WHERE s.propiedades->>'fuente_etl' = 'M1_senaleticas'
                   AND s.tipo = ANY(%(tipos)s)
                   AND s.geom && ST_Expand(c.geom, 0.0002)
                   AND ST_DWithin(c.geom::geography, s.geom::geography, 15)) AS n_senales,
               (SELECT max(CASE m.propiedades->>'nivel' WHEN 'CRITICA' THEN 3 WHEN 'ALTA' THEN 2 ELSE 1 END)
                  FROM temp_metadata m
                 WHERE m.propiedades->>'fuente_etl' = 'M4_siniestros'
                   AND m.geom && c.geom
                   AND ST_Intersects(c.geom, m.geom)) AS nivel_siniestros
        FROM cand c
    """, {"lon1": inicio["lon"], "lat1": inicio["lat"], "lon2": objetivo["lon"], "lat2": objetivo["lat"],
          "radio": radio, "grados": radio / 90_000, "tipos": list(prefs["tipos_senal"])})

    candidatas = []
    for f in filas:
        if f["n_senales"] == 0 or f["id"] in excluidas:
            continue
        nivel = {3: "CRITICA", 2: "ALTA", 1: "MEDIA"}.get(f["nivel_siniestros"])
        prioritaria = prefs["priorizar_siniestros"] and nivel in ("CRITICA", "ALTA")
        candidatas.append({**f, "length_m": float(f["length_m"]), "puntaje": f["n_senales"] + (2 if prioritaria else 0),
                           "nivel_siniestros": nivel})
    candidatas.sort(key=lambda r: (-r["puntaje"], -r["length_m"]))
    return candidatas[: int(prefs["max_requeridas"])]


# ------------------------------------------------------------------------------
# Geometrías
# ------------------------------------------------------------------------------
def geometrias(conn, edge_ids):
    if not edge_ids:
        return {}
    filas = _filas(conn, """
        SELECT id, name, length_m, ST_AsGeoJSON(geom, 6) AS g
        FROM network_edges WHERE id = ANY(%s)""", (list(set(edge_ids)),))
    return {f["id"]: {"coords": json.loads(f["g"])["coordinates"], "name": f["name"],
                      "length_m": float(f["length_m"])} for f in filas}


# ------------------------------------------------------------------------------
# Planificación por área (ruteo/flota.py)
# ------------------------------------------------------------------------------
def extension_red(conn):
    """Rectángulo (xmin, ymin, xmax, ymax) que contiene toda la red vial."""
    fila = _filas(conn, """
        SELECT ST_XMin(x) AS xmin, ST_YMin(x) AS ymin, ST_XMax(x) AS xmax, ST_YMax(x) AS ymax
        FROM (SELECT ST_Extent(geom) AS x FROM network_edges) t""")
    f = fila[0] if fila else None
    if not f or f["xmin"] is None:
        return None
    return (float(f["xmin"]), float(f["ymin"]), float(f["xmax"]), float(f["ymax"]))


SQL_PUNTOS_INSPECCION = """
WITH pts AS (
    SELECT m.id, m.tipo, m.propiedades->>'fuente_etl' AS fuente, m.geom
    FROM temp_metadata m
    WHERE m.geom && ST_MakeEnvelope(%(xmin)s, %(ymin)s, %(xmax)s, %(ymax)s, 4326)
      AND m.propiedades->>'fuente_etl' = 'M1_senaleticas' AND m.tipo = ANY(%(tipos)s)
      AND (%(area)s::text IS NULL
           OR ST_Intersects(m.geom, ST_SetSRID(ST_GeomFromGeoJSON(%(area)s::text), 4326)))
)
SELECT p.id AS punto_id, p.tipo, p.fuente,
       ST_X(ST_PointOnSurface(p.geom)) AS lon, ST_Y(ST_PointOnSurface(p.geom)) AS lat,
       e.id AS edge_id, e.source, e.target, e.length_m, e.name, e.d
FROM pts p
CROSS JOIN LATERAL (
    SELECT n.id, n.source, n.target, n.length_m, n.name,
           ST_Distance(n.geom::geography, p.geom::geography) AS d
    FROM network_edges n
    ORDER BY n.geom <-> p.geom
    LIMIT 1
) e
WHERE e.d <= %(radio)s
"""


def puntos_inspeccion(conn, area_geojson, bbox, prefs, radio_m=30.0):
    """Señaléticas (M1) de los tipos elegidos dentro del área, cada una asociada
    a la calle (arista) más cercana a menos de radio_m metros.

    area_geojson: texto GeoJSON del polígono, o None para toda la red.
    """
    filas = _filas(conn, SQL_PUNTOS_INSPECCION, {
        "xmin": bbox[0], "ymin": bbox[1], "xmax": bbox[2], "ymax": bbox[3],
        "tipos": list(prefs["tipos_senal"]),
        "area": area_geojson, "radio": float(radio_m)})
    for f in filas:
        f["length_m"] = float(f["length_m"])
        f["lon"], f["lat"], f["d"] = float(f["lon"]), float(f["lat"]), float(f["d"])
    return filas


def lugares_publicos(conn, bbox, categorias):
    """Recintos públicos (M2) de las categorías elegidas dentro del rectángulo:
    son los sitios candidatos para instalar las bases de los drones."""
    return [{**f, "lon": float(f["lon"]), "lat": float(f["lat"])} for f in _filas(conn, """
        SELECT id, nombre, tipo AS categoria,
               propiedades->>'dependencia' AS dependencia,
               propiedades->>'direccion'   AS direccion,
               ST_X(geom) AS lon, ST_Y(geom) AS lat
        FROM temp_metadata
        WHERE propiedades->>'fuente_etl' = 'M2_lugares_publicos'
          AND tipo = ANY(%(categorias)s)
          AND geom && ST_MakeEnvelope(%(xmin)s, %(ymin)s, %(xmax)s, %(ymax)s, 4326)
        ORDER BY id""", {"categorias": list(categorias), "xmin": bbox[0], "ymin": bbox[1],
                         "xmax": bbox[2], "ymax": bbox[3]})]


def nodo_valido_mas_cercano(conn, lon, lat, excluidas=()):
    """Nodo más cercano a (lon, lat) que tenga al menos una arista utilizable."""
    fila = _filas(conn, """
        SELECT n.id, ST_X(n.geom) AS lon, ST_Y(n.geom) AS lat
        FROM network_nodes n
        WHERE EXISTS (SELECT 1 FROM network_edges e
                      WHERE (e.source = n.id OR e.target = n.id)
                        AND NOT (e.id = ANY(%(excl)s::BIGINT[])))
        ORDER BY n.geom <-> ST_SetSRID(ST_Point(%(lon)s, %(lat)s), 4326)
        LIMIT 1""", {"lon": lon, "lat": lat, "excl": list(excluidas)})
    if not fila:
        return None
    f = fila[0]
    return {"id": int(f["id"]), "lon": float(f["lon"]), "lat": float(f["lat"])}
