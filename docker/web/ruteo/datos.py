"""
Acceso a PostGIS para el ruteo: grafo, amenazas por arista (rúbrica 5.2),
restricciones de metadata (6.1), aristas requeridas y geometrías.
"""

import json
import math
import time
from datetime import timedelta

import networkx as nx

# Probabilidad de que una amenaza se materialice y corte la ruta, por nivel
# (supuesto de diseño usado por la métrica de resiliencia).
PROB_NIVEL = {"BAJO": 0.05, "MEDIO": 0.2, "ALTO": 0.5, "CRITICO": 0.8}

# Factor sobre el costo de una arista según la acción de la amenaza (rúbrica 5.2)
FACTOR_ACCION = {"PENALIZAR_ARISTAS": 1.5, "POSTERGAR_ARISTAS": 2.0}
# El clima ALTO/CRÍTICO se trata como "bloqueo blando": la arista sigue en el
# grafo pero cruzarla en esa hora cuesta 10 veces más. Así el genético puede
# esquivar la hora mala reordenando la ruta en vez de quedarse sin solución.
FACTOR_CLIMA_BLOQUEO = 10.0

_CACHE = {"grafo": None, "cargado": 0.0}
VIGENCIA_CACHE_S = 600


# ------------------------------------------------------------------------------
def _filas(conn, sql, params=()):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        columnas = [c[0] for c in cur.description]
        return [dict(zip(columnas, fila)) for fila in cur.fetchall()]


def grafo_completo(conn, forzar=False):
    """Grafo no dirigido de toda la red (cacheado 10 min).

    Nodo: id de network_nodes, con atributos x, y.
    Arista: id, d (metros), w0 (km). Si hay aristas paralelas se conserva la más corta.
    """
    if not forzar and _CACHE["grafo"] is not None and time.time() - _CACHE["cargado"] < VIGENCIA_CACHE_S:
        return _CACHE["grafo"]
    G = nx.Graph()
    for n in _filas(conn, "SELECT id, ST_X(geom) AS x, ST_Y(geom) AS y FROM network_nodes"):
        G.add_node(n["id"], x=n["x"], y=n["y"])
    for e in _filas(conn, "SELECT id, source, target, length_m FROM network_edges"):
        u, v, d = e["source"], e["target"], float(e["length_m"])
        if G.has_edge(u, v) and G[u][v]["d"] <= d:
            continue
        G.add_edge(u, v, id=e["id"], d=d, w0=d / 1000.0)
    _CACHE.update(grafo=G, cargado=time.time())
    return G


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
# Amenazas por arista (rúbrica 5.1 / 5.2)
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
      AND (propiedades->>'fuente_etl' <> 'T5_lluvias_maipu'
           OR COALESCE((propiedades->>'activa')::boolean, false))
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

    - bloqueadas: aristas con una amenaza dura (T2 incendio, T5 activa ALTO/CRÍTICO,
      T6 SOSAFE) -> se eliminan del grafo del algoritmo resiliente.
    - factor_estatico: penalización permanente en la ventana (T5 POSTERGAR, etc.).
    - temporales: intervalos (desde, hasta, factor) del clima (T3), que dependen
      de la hora a la que el dron cruza la arista.
    - eventos: para la métrica, cada arista -> {clave_evento: probabilidad}.
    """

    def __init__(self, filas, considerar_clima=True):
        self.bloqueadas, self.factor_estatico, self.temporales, self.eventos = set(), {}, {}, {}
        self.n_amenazas = len({f["amenaza_id"] for f in filas})
        for f in filas:
            e, nivel, accion = f["edge_id"], f["nivel_riesgo"] or "MEDIO", f["accion"]
            clave = ("T3", f["id_celda"]) if f["fuente"] == "T3_clima" else ("A", f["amenaza_id"])
            p = PROB_NIVEL.get(nivel, 0.2)
            self.eventos.setdefault(e, {})
            self.eventos[e][clave] = max(p, self.eventos[e].get(clave, 0))

            if f["fuente"] == "T3_clima":
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


def amenazas_region(conn, bbox, t0, horizonte_h, considerar_clima):
    t1 = t0 + timedelta(hours=horizonte_h)
    filas = _filas(conn, SQL_AMENAZAS_ARISTA, {
        "xmin": bbox[0], "ymin": bbox[1], "xmax": bbox[2], "ymax": bbox[3], "t0": t0, "t1": t1})
    return Amenazas(filas, considerar_clima)


# ------------------------------------------------------------------------------
# Restricciones derivadas de la metadata (rúbrica 6.1)
# ------------------------------------------------------------------------------
def restricciones_metadata(conn, bbox, prefs):
    """Devuelve (aristas_eliminadas, factores) según las preferencias del usuario."""
    caja = {"xmin": bbox[0], "ymin": bbox[1], "xmax": bbox[2], "ymax": bbox[3]}
    eliminadas, factores = set(), {}
    if prefs["evitar_aerodromos"] and prefs["radio_aerodromo_m"] > 0:
        radio = float(prefs["radio_aerodromo_m"])
        for f in _filas(conn, """
            SELECT DISTINCT e.id FROM network_edges e
            JOIN temp_metadata m ON m.propiedades->>'fuente_etl' = 'M6_aerodromos'
             AND m.geom && ST_Expand(e.geom, %(grados)s)
             AND ST_DWithin(e.geom::geography, m.geom::geography, %(radio)s)
            WHERE e.geom && ST_MakeEnvelope(%(xmin)s, %(ymin)s, %(xmax)s, %(ymax)s, 4326)
        """, {**caja, "radio": radio, "grados": radio / 90_000}):
            eliminadas.add(f["id"])
    if prefs["evitar_paraderos"]:
        for f in _filas(conn, """
            SELECT DISTINCT e.id FROM network_edges e
            JOIN temp_metadata m ON m.propiedades->>'fuente_etl' = 'M1_paraderos'
             AND m.geom && ST_Expand(e.geom, 0.0004)
             AND ST_DWithin(e.geom::geography, m.geom::geography, 25)
            WHERE e.geom && ST_MakeEnvelope(%(xmin)s, %(ymin)s, %(xmax)s, %(ymax)s, 4326)
        """, caja):
            factores[f["id"]] = float(prefs["factor_paradero"])
    return eliminadas, factores


def aristas_requeridas(conn, inicio, objetivo, prefs, excluidas):
    """Aristas a inspeccionar (E_R) dentro del corredor inicio-objetivo.

    Una arista es candidata si tiene al menos una señal de los tipos elegidos
    (M3) a menos de 15 m. Se ordenan por puntaje = n° de señales (+2 si está
    junto a una manzana vulnerable y la preferencia está activa) y se toman las
    max_requeridas mejores que no estén bloqueadas.
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
                 WHERE s.propiedades->>'fuente_etl' = 'M3_controles_viales'
                   AND s.tipo = ANY(%(tipos)s)
                   AND s.geom && ST_Expand(c.geom, 0.0002)
                   AND ST_DWithin(c.geom::geography, s.geom::geography, 15)) AS n_senales,
               (SELECT max(NULLIF(m.propiedades->>'indice_vulnerabilidad', '')::float)
                  FROM temp_metadata m
                 WHERE m.propiedades->>'fuente_etl' = 'M5_censo_manzanas'
                   AND m.geom && ST_Expand(c.geom, 0.0002)
                   AND ST_DWithin(c.geom::geography, m.geom::geography, 15)) AS vulnerabilidad
        FROM cand c
    """, {"lon1": inicio["lon"], "lat1": inicio["lat"], "lon2": objetivo["lon"], "lat2": objetivo["lat"],
          "radio": radio, "grados": radio / 90_000, "tipos": list(prefs["tipos_senal"])})

    candidatas = []
    for f in filas:
        if f["n_senales"] == 0 or f["id"] in excluidas:
            continue
        vuln = f["vulnerabilidad"]
        puntaje = f["n_senales"] + (2 if prefs["priorizar_vulnerables"] and vuln is not None and vuln > 0 else 0)
        candidatas.append({**f, "length_m": float(f["length_m"]), "puntaje": puntaje,
                           "vulnerabilidad": None if vuln is None else round(float(vuln), 3)})
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
