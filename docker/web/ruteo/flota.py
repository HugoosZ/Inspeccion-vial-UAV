"""
Planificación por área con una flota de n drones.

En vez de unir un inicio y un objetivo, el operador elige un área (o toda la
comuna) y la cantidad de drones. Cada dron recibe una parte del área, sale de
su base, inspecciona las calles con señalética de esa parte y vuelve a la base
cada vez que se le acaba la batería.

El enfoque sigue a Zhong et al. (2023), "A Route Planning Method for UAV Swarm
Inspection of Roads Fusing Distributed Droneport Site Selection" (Sensors
23(20), 8479):

  - Número fijo de drones, cada uno con su base (droneport) y sus tramos.
  - Regla de construcción de rutas del paper: desde su posición, el dron
    inspecciona el tramo pendiente más cercano, y así sucesivamente.
  - Objetivos: minimizar el kilometraje total (Dis) y equilibrar el uso de los
    drones (varianza de los kilometrajes); el tiempo de inspección global es
    el del dron que más tarda (T = max T_i).
  - Indicadores: utilización equilibrada beta = min dis_i / max dis_i,
    eficiencia eta = km a inspeccionar / Dis y tasa de repetición rho_i
    (el paper la limita a 18 %).

Pasos:
  1. Tareas: cada señalética M1 de los tipos elegidos dentro del área se asocia
     a su calle más cercana; cada calle con señal es una tarea. Las tareas sobre
     calles bloqueadas (incendio, SOSAFE, cierres de TomTom) se postergan.
  2. Partición equilibrada: k-medias con capacidad, donde la carga de cada
     tarea es su largo más una estimación del traslado hasta la siguiente.
  3. Bases: una por área, en un recinto público (M2: salud, educación,
     Carabineros o Bomberos). Se elige el recinto que minimiza la suma de
     distancias a las calles del área, ponderadas por su largo (1-mediana
     discreta sobre sitios candidatos, como la selección de droneports de
     Zhong et al.), sin repetir recinto entre drones. El dron despega desde la
     calle más cercana al recinto. Con bases = centralizada hay un solo recinto.
  4. Ruta de cada dron:
       - distancias entre la base y los extremos de sus tareas con
         pgr_dijkstraCostMatrix,
       - orden con la regla del tramo pendiente más cercano, respetando la
         batería (si la siguiente tarea no alcanza, vuelve a la base y empieza
         otra salida) y mejora 2-opt dentro de cada salida,
       - trazado de cada traslado sobre la red con pgr_dijkstra.
  5. Tiempos: vuelo a velocidad de crucero + recarga entre salidas; avisos de
     las salidas que coinciden con horas de mal tiempo (A2).

Técnicas (punto 6, ruteo/tecnicas.py): los pasos 1 a 3 forman una instancia
común (calles, partición, bases y sectores) y el paso 4 se resuelve con cada
técnica elegida: T1 pgr_dijkstra por distancia, T2 MILP con Gurobi, T3
pgr_dijkstra resiliente (el procedimiento descrito arriba) y T4 DA-DPSO. El
riesgo de cada plan se evalúa con todas las amenazas.
"""

import json
import math
import time
from collections import Counter, defaultdict
from datetime import timedelta

import numpy as np
from psycopg2.extras import execute_values

from . import datos, dpso, milp, preferencias
from .tecnicas import (COSTO, NOMBRES, constructiva, costo_salida, factible,  # noqa: F401
                       factor_resiliente, minutos_sector)


class ErrorFlota(ValueError):
    pass


# Tope de vértices por matriz de costos; si un dron tiene más tareas, su área
# se divide en sectores que se recorren uno tras otro.
MAX_NODOS_MATRIZ = 1000
# Margen alrededor de cada sector para que los traslados puedan usar calles
# vecinas; si una tarea queda sin camino se reintenta con MARGEN_MAX_M
MARGEN_RED_M = 1000.0
MARGEN_MAX_M = 4000.0
# Distancia máxima entre una señal y la calle a la que se asigna
RADIO_ASIGNACION_M = 30.0
# Factor de rodeo de la red respecto de la distancia en línea recta
FACTOR_RODEO = 1.3
# Repeticiones del ciclo partición-ruteo para equilibrar los tiempos de los drones
REBALANCEOS = 2

SQL_ARISTAS = """
    SELECT e.id, e.source, e.target,
           CASE WHEN p.factor < 0 THEN -1 ELSE e.length_m * COALESCE(p.factor, 1) END AS cost,
           CASE WHEN p.factor < 0 OR e.reverse_cost < 0 THEN -1
                ELSE e.length_m * COALESCE(p.factor, 1) END AS reverse_cost
    FROM network_edges e LEFT JOIN pesos_flota p ON p.id = e.id
    WHERE e.geom && ST_MakeEnvelope({xmin}, {ymin}, {xmax}, {ymax}, 4326)
"""
# T1: costo = largo de la calle, sin amenazas
SQL_ARISTAS_DISTANCIA = """
    SELECT e.id, e.source, e.target, e.length_m AS cost,
           CASE WHEN e.reverse_cost < 0 THEN -1 ELSE e.length_m END AS reverse_cost
    FROM network_edges e
    WHERE e.geom && ST_MakeEnvelope({xmin}, {ymin}, {xmax}, {ymax}, 4326)
"""
# Tope de calles por sector para el modelo exacto (T2): con 200 calles el
# modelo tiene ~160.000 arcos antes de la reducción; sobre eso el sector
# conserva la solución de T3 y se informa.
MAX_TAREAS_MILP = 200
# Avisos de clima por salida (A2): probabilidad de que el escenario sea no apto
# para el Mavic 3E en las horas de la salida. Con la mitad o más de los
# escenarios del ensamble no aptos se sugiere reprogramar (mayoría de escenarios).
UMBRAL_CLIMA_REPROGRAMAR = 0.5
UMBRAL_CLIMA_AVISO = 0.2


# ------------------------------------------------------------------------------
# Geometría plana local (metros) para agrupar
# ------------------------------------------------------------------------------
class Proyeccion:
    def __init__(self, lat0):
        self.lat0 = lat0
        self.kx = 111_320 * math.cos(math.radians(lat0))
        self.ky = 110_574

    def xy(self, lon, lat):
        return np.column_stack([np.asarray(lon, float) * self.kx, np.asarray(lat, float) * self.ky])

    def lonlat(self, x, y):
        return float(x / self.kx), float(y / self.ky)


def expandir_bbox(bbox, margen_m):
    lat = (bbox[1] + bbox[3]) / 2
    dlat = margen_m / 111_320
    dlon = margen_m / (111_320 * math.cos(math.radians(lat)))
    return (bbox[0] - dlon, bbox[1] - dlat, bbox[2] + dlon, bbox[3] + dlat)


def validar_area(area):
    """Acepta un Polygon GeoJSON (dict o texto) o None (toda la red)."""
    if area in (None, "", {}):
        return None, None
    if isinstance(area, str):
        area = json.loads(area)
    if area.get("type") == "Feature":
        area = area.get("geometry") or {}
    if area.get("type") != "Polygon" or not area.get("coordinates"):
        raise ErrorFlota("El área debe ser un polígono GeoJSON.")
    anillo = [[float(x), float(y)] for x, y in (p[:2] for p in area["coordinates"][0])]
    if len(anillo) < 3:
        raise ErrorFlota("El área necesita al menos 3 vértices.")
    if any(not (-180 <= x <= 180 and -90 <= y <= 90) for x, y in anillo):
        raise ErrorFlota("Coordenadas del área fuera de rango (se esperan longitud, latitud).")
    if anillo[0] != anillo[-1]:
        anillo.append(anillo[0])
    poligono = {"type": "Polygon", "coordinates": [anillo]}
    xs, ys = [p[0] for p in anillo], [p[1] for p in anillo]
    return poligono, (min(xs), min(ys), max(xs), max(ys))


# ------------------------------------------------------------------------------
# Partición equilibrada (k-medias con capacidad)
# ------------------------------------------------------------------------------
def particion_equilibrada(P, pesos, k, iteraciones=30, holgura=0.04, semilla=7):
    """Divide los puntos P (n x 2, metros) en k grupos de carga parecida.

    Cada iteración asigna primero los puntos con mayor diferencia entre su
    centro más cercano y el segundo más cercano, al centro más cercano que
    todavía tenga capacidad; luego recalcula los centros.
    """
    n = len(P)
    k = max(1, min(k, n))
    if k == 1:
        return np.zeros(n, int)
    pesos = np.asarray(pesos, float)
    rng = np.random.default_rng(semilla)
    # inicio tipo k-medias++ determinista
    centros = [P[int(np.argmax(pesos))]]
    for _ in range(1, k):
        d2 = np.min(((P[:, None, :] - np.array(centros)[None, :, :]) ** 2).sum(-1), axis=1)
        prob = d2 * pesos
        centros.append(P[int(rng.choice(n, p=prob / prob.sum()))] if prob.sum() > 0 else P[int(rng.integers(n))])
    C = np.array(centros, float)
    capacidad = pesos.sum() / k * (1 + holgura)
    etiquetas = np.zeros(n, int)
    for _ in range(iteraciones):
        D = np.sqrt(((P[:, None, :] - C[None, :, :]) ** 2).sum(-1))
        orden_c = np.argsort(D, axis=1)
        mejor = D[np.arange(n), orden_c[:, 0]]
        segundo = D[np.arange(n), orden_c[:, 1]]
        carga = np.zeros(k)
        nuevas = np.empty(n, int)
        for i in np.argsort(-(segundo - mejor)):
            for c in orden_c[i]:
                if carga[c] + pesos[i] <= capacidad:
                    break
            else:
                c = int(np.argmin(carga))
            nuevas[i] = c
            carga[c] += pesos[i]
        C_nuevo = np.array([np.average(P[nuevas == c], axis=0, weights=pesos[nuevas == c])
                            if np.any(nuevas == c) else C[c] for c in range(k)])
        estable = np.array_equal(nuevas, etiquetas)
        etiquetas = nuevas
        if estable and np.allclose(C_nuevo, C):
            break
        C = C_nuevo
    return etiquetas


def mediana_geometrica(P, pesos, iteraciones=60):
    """Punto que minimiza la suma ponderada de distancias (Weiszfeld)."""
    pesos = np.asarray(pesos, float)
    z = np.average(P, axis=0, weights=pesos)
    for _ in range(iteraciones):
        d = np.maximum(np.hypot(*(P - z).T), 1.0)
        w = pesos / d
        z_nuevo = (P * w[:, None]).sum(0) / w.sum()
        if np.hypot(*(z_nuevo - z)) < 0.5:
            return z_nuevo
        z = z_nuevo
    return z


def envolvente(puntos):
    """Envolvente convexa (cadena monótona) de una lista de (lon, lat)."""
    pts = sorted(set((round(x, 6), round(y, 6)) for x, y in puntos))
    if len(pts) < 3:
        return pts

    def cruz(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    inferior, superior = [], []
    for p in pts:
        while len(inferior) >= 2 and cruz(inferior[-2], inferior[-1], p) <= 0:
            inferior.pop()
        inferior.append(p)
    for p in reversed(pts):
        while len(superior) >= 2 and cruz(superior[-2], superior[-1], p) <= 0:
            superior.pop()
        superior.append(p)
    casco = inferior[:-1] + superior[:-1]
    return casco + [casco[0]]


# ------------------------------------------------------------------------------
# pgRouting
# ------------------------------------------------------------------------------
class Red:
    """Grafo con pesos de la consulta: aristas bloqueadas (-1) y factores.

    solo_distancia=True es la red de T1: costo = largo, sin bloqueos."""

    def __init__(self, bloqueadas=(), factores=None, solo_distancia=False):
        self.solo_distancia = solo_distancia
        self.bloqueadas = set() if solo_distancia else set(bloqueadas)
        self.factores = {} if solo_distancia else {
            e: f for e, f in (factores or {}).items() if e not in self.bloqueadas and f != 1.0}

    def factor(self, e):
        return -1.0 if e in self.bloqueadas else self.factores.get(e, 1.0)

    def sql(self, bbox):
        plantilla = SQL_ARISTAS_DISTANCIA if self.solo_distancia else SQL_ARISTAS
        return plantilla.format(xmin=bbox[0], ymin=bbox[1], xmax=bbox[2], ymax=bbox[3])


def preparar_pesos(conn, red):
    """Carga los factores en una tabla temporal que leen las consultas de pgRouting."""
    with conn.cursor() as cur:
        cur.execute("""CREATE TEMP TABLE IF NOT EXISTS pesos_flota
                       (id BIGINT PRIMARY KEY, factor DOUBLE PRECISION NOT NULL)""")
        cur.execute("TRUNCATE pesos_flota")
        filas = [(e, -1.0) for e in red.bloqueadas] + list(red.factores.items())
        if filas:
            execute_values(cur, "INSERT INTO pesos_flota (id, factor) VALUES %s", filas)


def matriz_costos(conn, red, bbox, vids):
    """Costos (metros ponderados) entre todos los vids con pgr_dijkstraCostMatrix.

    Devuelve una matriz numpy en el orden de vids; inf si no hay camino.
    """
    indice = {v: i for i, v in enumerate(vids)}
    M = np.full((len(vids), len(vids)), np.inf)
    np.fill_diagonal(M, 0.0)
    with conn.cursor() as cur:
        cur.execute("""
            SELECT start_vid, array_agg(end_vid) AS fin, array_agg(agg_cost) AS costo
            FROM pgr_dijkstraCostMatrix(%s, %s::BIGINT[], directed := true)
            GROUP BY start_vid""", (red.sql(bbox), list(vids)))
        for inicio, fines, costos in cur.fetchall():
            i = indice.get(inicio)
            if i is None:
                continue
            cols = [indice[f] for f in fines if f in indice]
            M[i, cols] = [float(c) for f, c in zip(fines, costos) if f in indice]
    return M


def tramos(conn, red, bbox, pares):
    """Camino (lista de aristas) de cada par (origen, destino) con pgr_dijkstra."""
    pares = sorted({(int(a), int(b)) for a, b in pares if a != b})
    caminos = defaultdict(list)
    if not pares:
        return caminos
    valores = ", ".join(f"({a}, {b})" for a, b in pares)
    combinaciones = f"SELECT source, target FROM (VALUES {valores}) AS t(source, target)"
    with conn.cursor() as cur:
        cur.execute("""
            SELECT start_vid, end_vid, edge
            FROM pgr_dijkstra(%s, %s, directed := true)
            WHERE edge <> -1
            ORDER BY start_vid, end_vid, path_seq""", (red.sql(bbox), combinaciones))
        for a, b, e in cur.fetchall():
            caminos[(int(a), int(b))].append(int(e))
    return caminos


# ------------------------------------------------------------------------------
# Planificación
# ------------------------------------------------------------------------------
def _carga_estimada(P, largos):
    """Carga de cada tarea: su largo + traslado estimado al vecino más cercano."""
    n = len(P)
    if n == 1:
        return np.asarray(largos, float)
    vecino = np.full(n, np.inf)
    for i in range(0, n, 500):
        D = np.sqrt(((P[i:i + 500, None, :] - P[None, :, :]) ** 2).sum(-1))
        D[np.arange(D.shape[0]), np.arange(i, i + D.shape[0])] = np.inf
        vecino[i:i + 500] = D.min(1)
    return np.asarray(largos, float) + FACTOR_RODEO * np.minimum(vecino, 1000.0)


def _sectores(P, largos, capacidad, n_tareas):
    """Divide las tareas de un dron en sectores compactos: cada sector debe caber
    en una matriz de costos y quedar a una distancia que la batería permita ir,
    inspeccionar y volver desde la base del sector."""
    radio_max = 0.35 * capacidad / FACTOR_RODEO
    m = max(1, math.ceil((2 * n_tareas + 1) / MAX_NODOS_MATRIZ))
    while True:
        etiquetas = particion_equilibrada(P, largos, m) if m > 1 else np.zeros(len(P), int)
        ok = True
        for s in range(m):
            Ps = P[etiquetas == s]
            if not len(Ps):
                continue
            z = mediana_geometrica(Ps, largos[etiquetas == s])
            if (2 * len(Ps) + 1 > MAX_NODOS_MATRIZ) or np.hypot(*(Ps - z).T).max() > radio_max:
                ok = False
                break
        if ok or m >= min(15, len(P)):
            return etiquetas
        m += 1


def a_salidas(ctx, orden, M, Lt):
    """Convierte las salidas de una técnica (listas de (tarea, entrada, salida))
    en pasos ("tramo", origen, destino) / ("tarea", arista); los tramos se
    trazan después con pgr_dijkstra (materializar)."""
    vids, grupo = ctx["vids"], ctx["grupo"]
    salidas = []
    for seq in orden:
        pasos, anterior = [], 0
        for t, e, s in seq:
            pasos.append(("tramo", vids[anterior], vids[e]))
            pasos.append(("tarea", grupo[t]["id"]))
            anterior = s
        pasos.append(("tramo", vids[anterior], vids[0]))
        salidas.append({"tipo": "inspeccion", "pasos": pasos, "bbox": ctx["bbox"],
                        "tareas": [grupo[t]["id"] for t, _, _ in seq],
                        "metros": costo_salida(M, 0, seq, Lt)})
    return salidas


def _rutear_sector(conn, red, base, grupo, capacidad, margen=MARGEN_RED_M):
    """Salidas de un sector desde su base con la técnica T3 (constructiva
    sobre costos resilientes), que define la instancia común.

    Devuelve (salidas, inalcanzables, contextos). Cada contexto guarda la
    matriz de costos y las tareas del sector para que T1, T2 y T4 resuelvan
    exactamente el mismo subproblema.

    La red se recorta al rectángulo del sector más un margen; si alguna tarea
    queda sin camino (el acceso pasa por fuera del recorte), se reintenta con
    un margen mayor."""
    t0 = time.perf_counter()
    vids = [base["id"]] + sorted({v for t in grupo for v in (t["source"], t["target"])} - {base["id"]})
    idx = {v: i for i, v in enumerate(vids)}
    lon = [t["lon"] for t in grupo] + [base["lon"]]
    lat = [t["lat"] for t in grupo] + [base["lat"]]
    bbox = expandir_bbox((min(lon), min(lat), max(lon), max(lat)), margen)
    M = matriz_costos(conn, red, bbox, vids)
    t_matriz = time.perf_counter() - t0
    U = np.array([idx[t["source"]] for t in grupo])
    V = np.array([idx[t["target"]] for t in grupo])
    Lt = np.array([t["length_m"] * max(red.factor(t["id"]), 1.0) for t in grupo])
    orden, fuera = constructiva(M, 0, U, V, Lt, capacidad)
    ctx = {"base": base, "grupo": grupo, "vids": vids, "bbox": bbox, "M": M, "U": U, "V": V,
           "Lt": Lt, "fuera": fuera, "orden": orden, "t_matriz": t_matriz,
           "t_orden": time.perf_counter() - t0 - t_matriz}
    salidas = a_salidas(ctx, orden, M, Lt)
    if fuera and margen < MARGEN_MAX_M:
        otras, fuera_ids, ctxs = _rutear_sector(conn, red, base, [grupo[j] for j in fuera], capacidad, MARGEN_MAX_M)
        for c in ctxs:
            c["reintento"] = True      # mismo sector, red recortada con más margen
        return salidas + otras, fuera_ids, [ctx] + ctxs
    return salidas, [grupo[j]["id"] for j in fuera], [ctx]


def materializar(conn, red, salidas):
    """Traza los tramos de las salidas con pgr_dijkstra (una consulta por recorte
    de red) y agrega a cada salida la lista de aristas recorridas."""
    por_caja = defaultdict(set)
    for s in salidas:
        for p in s["pasos"]:
            if p[0] == "tramo" and p[1] != p[2]:
                por_caja[s["bbox"]].add((p[1], p[2]))
    caminos = {caja: tramos(conn, red, caja, pares) for caja, pares in por_caja.items()}
    for s in salidas:
        aristas = []
        completo = True
        for p in s["pasos"]:
            if p[0] == "tarea":
                aristas.append(p[1])
            elif p[1] != p[2]:
                camino = caminos[s["bbox"]].get((p[1], p[2]), [])
                completo = completo and bool(camino)
                aristas += camino
        s["aristas"] = aristas
        s["camino_completo"] = completo
    return salidas


class UbicadorBases:
    """Elige las bases entre los lugares públicos (M2).

    Para un grupo de tareas elige el recinto j que minimiza
    sum_i largo_i * ||p_i - c_j|| (1-mediana discreta). La base queda asociada al
    nodo válido de la red más cercano al recinto, desde donde el dron se une a
    las calles. Sin lugares públicos cargados se usa el nodo más cercano a la
    mediana geométrica de las tareas.
    """

    def __init__(self, conn, red, proy, lugares):
        self.conn, self.red, self.proy, self.lugares = conn, red, proy, lugares
        self.P = (proy.xy([l["lon"] for l in lugares], [l["lat"] for l in lugares])
                  if lugares else None)
        self._bases = {}

    def elegir(self, Psub, pesos, excluir=()):
        if not self.lugares:
            x, y = mediana_geometrica(Psub, pesos)
            b = datos.nodo_valido_mas_cercano(self.conn, *self.proy.lonlat(x, y), self.red.bloqueadas)
            if b is None:
                raise ErrorFlota("No se encontró un nodo válido para ubicar la base.")
            return {**b, "lugar": None, "distancia_m": 0}
        dist = np.hypot(Psub[:, None, 0] - self.P[None, :, 0], Psub[:, None, 1] - self.P[None, :, 1])
        costo = (np.asarray(pesos, float)[:, None] * dist).sum(0)
        orden = [int(j) for j in np.argsort(costo)]
        j = next((j for j in orden if self.lugares[j]["id"] not in excluir), orden[0])
        return self._base(self.lugares[j])

    def _base(self, lugar):
        if lugar["id"] not in self._bases:
            nodo = datos.nodo_valido_mas_cercano(self.conn, lugar["lon"], lugar["lat"], self.red.bloqueadas)
            if nodo is None:
                raise ErrorFlota(f"No hay una calle utilizable junto a {lugar['nombre']}.")
            a, b = self.proy.xy([lugar["lon"], nodo["lon"]], [lugar["lat"], nodo["lat"]])
            self._bases[lugar["id"]] = {
                **nodo, "distancia_m": round(float(np.hypot(*(a - b)))),
                "lugar": {k: lugar.get(k) for k in ("id", "nombre", "categoria", "dependencia",
                                                     "direccion", "lon", "lat")}}
        return self._bases[lugar["id"]]


def _rutear_dron(conn, red, base, tareas, capacidad, proy, droneports=True, ubicar=None, excluir=()):
    """Plan de un dron. Si su área es grande, se divide en sectores; con
    droneports=True cada sector tiene su propia base (droneport distribuido,
    como en Zhong et al.) y el dron vuela de una base a la siguiente al
    terminar un sector. Con droneports=False todos los sectores usan la misma base.

    ubicar: UbicadorBases.elegir, para la base de cada sector (excluir: recintos
    que ya son base de otros drones).
    Devuelve (salidas, inalcanzables, bases_usadas, pasos). Las salidas son de
    tipo 'inspeccion' o 'traslado' (vuelo de una base a otra); pasos es la
    secuencia ("sector", contexto) / ("traslado", salida) con la que las otras
    técnicas rearman el plan del dron sobre los mismos sectores.
    """
    if not tareas:
        return [], [], [base], []
    P = proy.xy([t["lon"] for t in tareas], [t["lat"] for t in tareas])
    largos = np.array([t["length_m"] for t in tareas])
    if droneports:
        etiquetas = _sectores(P, largos, capacidad, len(tareas))
    else:
        m = max(1, math.ceil((2 * len(tareas) + 1) / MAX_NODOS_MATRIZ))
        etiquetas = particion_equilibrada(P, largos, m) if m > 1 else np.zeros(len(tareas), int)
    restantes = sorted(set(etiquetas.tolist()))
    un_sector = len(restantes) == 1
    salidas, inalcanzables, bases_usadas, pasos = [], [], [], []
    posicion = proy.xy([base["lon"]], [base["lat"]])[0]
    base_actual = None
    while restantes:
        # siguiente sector: el más cercano a la posición actual del dron
        s = min(restantes, key=lambda s: float(np.hypot(*(P[etiquetas == s].mean(0) - posicion))))
        restantes.remove(s)
        grupo = [t for t, e in zip(tareas, etiquetas) if e == s]
        if not droneports or un_sector:
            base_sector = base
        else:
            base_sector = ubicar(P[etiquetas == s], largos[etiquetas == s], excluir)
        if base_actual is not None and base_sector["id"] != base_actual["id"]:
            caja = expandir_bbox((min(base_actual["lon"], base_sector["lon"]), min(base_actual["lat"], base_sector["lat"]),
                                  max(base_actual["lon"], base_sector["lon"]), max(base_actual["lat"], base_sector["lat"])),
                                 MARGEN_MAX_M)
            d = float(np.hypot(*(proy.xy([base_sector["lon"]], [base_sector["lat"]])[0] - posicion)))
            traslado = {"tipo": "traslado", "pasos": [("tramo", base_actual["id"], base_sector["id"])],
                        "bbox": caja, "tareas": [], "metros": FACTOR_RODEO * d}
            salidas.append(traslado)
            pasos.append(("traslado", traslado))
        if not bases_usadas or bases_usadas[-1]["id"] != base_sector["id"]:
            bases_usadas.append(base_sector)
        sal, fuera, ctxs = _rutear_sector(conn, red, base_sector, grupo, capacidad)
        pasos += [("sector", c) for c in ctxs]
        salidas += sal
        inalcanzables += fuera
        base_actual = base_sector
        posicion = proy.xy([base_sector["lon"]], [base_sector["lat"]])[0]
    return salidas, inalcanzables, bases_usadas, pasos


def _resolver_tecnicas(conn, plan, tecs, red_dist, prefs, capacidad):
    """Ejecuta cada técnica sobre los sectores de la instancia común.

    Devuelve {codigo: {"ordenes": {id(ctx): (salidas, M, Lt)}, "info": {...}}}."""
    v, recarga = prefs["velocidad_crucero_ms"], prefs["tiempo_recarga_min"]
    conv = 1.0 / (60.0 * v)
    ctxs = [c for (_, _, _, pasos) in plan for tipo, c in pasos if tipo == "sector"]
    for c in ctxs:
        fuera = set(c["fuera"])
        c["R"] = [j for j in range(len(c["grupo"])) if j not in fuera]
    total = sum(len(c["R"]) for c in ctxs) or 1
    resultados = {}
    for cod in tecs:
        t_ini = time.perf_counter()
        ordenes, info = {}, {"sectores": len(ctxs), "minutos_modelo": 0.0}
        segundos_matriz = 0.0
        for c in ctxs:
            R = c["R"]
            pos = {j: k for k, j in enumerate(R)}
            U, V = c["U"][R], c["V"][R]
            semilla = [[(pos[t], e, s) for t, e, s in seq] for seq in c["orden"]]

            def volver(salidas):
                return [[(R[t], e, s) for t, e, s in seq] for seq in salidas]

            if not R:
                ordenes[id(c)] = ([], c["M"], c["Lt"])
                continue
            if cod == "T3":
                M, Lt, orden = c["M"], c["Lt"], c["orden"]
                segundos_matriz += c["t_matriz"] + c["t_orden"]
            elif cod == "T1":
                t_m = time.perf_counter()
                M = matriz_costos(conn, red_dist, c["bbox"], c["vids"])
                Lt = np.array([t["length_m"] for t in c["grupo"]])
                orden_local, _ = constructiva(M, 0, U, V, Lt[R], capacidad)
                orden = volver(orden_local)
                segundos_matriz += time.perf_counter() - t_m
            elif cod == "T2":
                M, Lt = c["M"], c["Lt"]
                segundos_matriz += c["t_matriz"] + c["t_orden"]   # matriz + solución inicial T3
                if len(R) > MAX_TAREAS_MILP:
                    orden = c["orden"]
                    info.setdefault("sectores_sin_milp", 0)
                    info["sectores_sin_milp"] += 1
                else:
                    limite = max(2.0, prefs["limite_milp_s"] * len(R) / total)
                    r = milp.resolver(M, 0, U, V, Lt[R], capacidad, conv, recarga,
                                      inicio=semilla, limite_s=limite)
                    orden = volver(r["salidas"]) if r.get("salidas") else c["orden"]
                    if not r.get("salidas") or not factible(M, 0, orden, Lt, capacidad):
                        orden = c["orden"]
                        info["nota"] = "Un sector quedó con la solución de T3: el solver no entregó una solución factible."
                    info["solver"] = r["solver"]
                    info["estados"] = info.get("estados", []) + [r["estado"]]
                    info["valor"] = info.get("valor", 0.0) + (r["valor"] or 0.0)
                    info["cota"] = info.get("cota", 0.0) + (r["cota"] if r.get("cota") is not None else 0.0)
                    info["variables"] = info.get("variables", 0) + r["variables"]
                    info["binarias"] = info.get("binarias", 0) + r["binarias"]
                    info["restricciones"] = info.get("restricciones", 0) + r["restricciones"]
                    if r.get("nota"):
                        info["nota"] = r["nota"]
            elif cod == "T4":
                M, Lt = c["M"], c["Lt"]
                segundos_matriz += c["t_matriz"] + c["t_orden"]   # matriz + solución inicial T3
                limite = max(1.0, prefs["limite_dpso_s"] * len(R) / total)
                Ml = M.tolist()
                r = dpso.resolver(Ml, 0, U.tolist(), V.tolist(), Lt[R].tolist(), capacidad, conv, recarga,
                                  semilla_perm=[t for seq in semilla for t, _, _ in seq], limite_s=limite)
                orden = volver(r["salidas"]) if r["salidas"] else c["orden"]
                info["iteraciones"] = info.get("iteraciones", 0) + r["iteraciones"]
                info["evaluaciones"] = info.get("evaluaciones", 0) + r["evaluaciones"]
                info["busquedas_locales"] = info.get("busquedas_locales", 0) + r["busquedas_locales"]
                info["valor_inicial"] = info.get("valor_inicial", 0.0) + minutos_sector(
                    M, 0, c["orden"], Lt, v, recarga)          # objetivo de T3 en el sector
                info["valor"] = info.get("valor", 0.0) + r["valor"]
            ordenes[id(c)] = (orden, M, Lt)
            info["minutos_modelo"] += minutos_sector(M, 0, orden, Lt, v, recarga)
        # T1 calcula su propia matriz dentro de este tiempo; T2, T3 y T4 reutilizan la
        # matriz resiliente de la instancia, cuyo tiempo (segundos_matriz) se les suma después.
        info["segundos_solver"] = time.perf_counter() - t_ini
        info["segundos_matriz"] = segundos_matriz
        resultados[cod] = {"ordenes": ordenes, "info": info}
    return resultados


def _plan_tecnica(plan, ordenes):
    """Plan de cada dron con las salidas de una técnica (mismos sectores y traslados)."""
    nuevo = []
    for salidas_t3, fuera, bases_d, pasos in plan:
        salidas = []
        for tipo, obj in pasos:
            if tipo == "traslado":
                salidas.append({k: v for k, v in obj.items() if k != "aristas"})
            else:
                orden, M, Lt = ordenes[id(obj)]
                salidas += a_salidas(obj, orden, M, Lt)
        nuevo.append((salidas, fuera, bases_d))
    return nuevo


def _clima_salida(aristas, ini, fin, clima):
    """Clima de una salida (A2): la mayor probabilidad de clima no apto entre las
    celdas que recorre, en las horas que dura. Sin ensamble, el nivel determinista."""
    celda_por_arista, horas = clima
    p, nivel, orden = None, None, {"BAJO": 0, "MEDIO": 1, "ALTO": 2, "CRITICO": 3}
    for celda in {celda_por_arista.get(e) for e in aristas} - {None}:
        for desde, hasta, ph, nh in horas.get(celda, ()):
            if desde < fin and hasta > ini:
                if ph is not None:
                    p = max(p or 0.0, ph)
                if nh and orden.get(nh, 0) > orden.get(nivel, -1):
                    nivel = nh
    return p, nivel


def _evaluar_tecnica(cod, plan_t, etiquetas, tareas, tarea_por_id, fuera_set, geoms, prob_eval,
                     bloqueadas_eval, clima, prefs, capacidad, t0):
    """Indicadores, tiempos y rutas (GeoJSON) del plan de una técnica.

    El riesgo se evalúa siempre con TODAS las amenazas (prob_eval,
    bloqueadas_eval), aunque la técnica haya ruteado con menos o con ninguna:
    así T1 muestra el riesgo que corre al ignorarlas."""
    v, recarga = prefs["velocidad_crucero_ms"], prefs["tiempo_recarga_min"]
    largo = {e: g["length_m"] for e, g in geoms.items()}
    drones, rutas_fc, avisos = [], [], []
    cruzadas, riesgo_m, metros_tot, p_climas = set(), 0.0, 0.0, []
    for d, (salidas, _, bases_d) in enumerate(plan_t):
        conteo = Counter(e for s in salidas for e in s["aristas"])
        dis = sum(largo.get(e, 0) * c for e, c in conteo.items())
        esperado = sum(largo.get(e, 0) * c * factor_resiliente(prob_eval.get(e, 0.0)) for e, c in conteo.items())
        repetido = sum(largo.get(e, 0) * (c - 1) for e, c in conteo.items() if c > 1)
        ids_dron = [t["id"] for t, e in zip(tareas, etiquetas) if e == d and t["id"] not in fuera_set]
        km_insp = sum(tarea_por_id[i]["length_m"] for i in ids_dron)
        minutos, detalle, n_insp = 0.0, [], 0
        salidas = [s for s in salidas if s["aristas"] or not s.get("camino_completo", True)]
        for s, sal in enumerate(salidas, start=1):
            aristas = sal["aristas"]
            km = sum(largo.get(e, 0) for e in aristas) / 1000
            if sal["tipo"] == "traslado" and km * 1000 > capacidad:
                avisos.append(f"El traslado del dron {d + 1} entre bases ({km:.1f} km) supera la batería: "
                              "debe hacerse por tierra o con una recarga intermedia.")
            vuelo = km * 1000 / v / 60
            ini = t0 + timedelta(minutes=minutos)
            fin = ini + timedelta(minutes=vuelo)
            p_clima, nivel_clima = _clima_salida(aristas, ini.timestamp(), fin.timestamp(), clima)
            aviso = None
            if p_clima is not None:
                if p_clima >= UMBRAL_CLIMA_REPROGRAMAR:
                    aviso = f"clima no apto en el {100 * p_clima:.0f} % de los escenarios: reprogramar"
                elif p_clima >= UMBRAL_CLIMA_AVISO:
                    aviso = f"clima no apto en el {100 * p_clima:.0f} % de los escenarios"
            elif nivel_clima in ("ALTO", "CRITICO"):
                aviso = "mal tiempo pronosticado (riesgo alto): reprogramar"
            elif nivel_clima == "MEDIO":
                aviso = "lluvia, viento o visibilidad sobre la política de vuelo"
            if p_clima is not None:
                p_climas.append(p_clima)
            # Riesgo medio de la salida: probabilidad de falla media por metro recorrido.
            # (La probabilidad de completar la salida no es el producto de las p(e):
            # el clima o un incidente afectan a muchas calles a la vez; eso se trata
            # con eventos correlacionados en la simulación de fallas.)
            metros = sum(largo.get(e, 0) for e in aristas)
            p_media = (sum(largo.get(e, 0) * prob_eval.get(e, 0.0) for e in aristas) / metros) if metros else 0.0
            bloq = set(aristas) & bloqueadas_eval
            cruzadas |= bloq
            riesgo_m += sum(largo.get(e, 0) * prob_eval.get(e, 0.0) for e in aristas)
            metros_tot += metros
            n_insp += sal["tipo"] == "inspeccion"
            detalle.append({"salida": s, "tipo": sal["tipo"], "km": round(km, 2), "vuelo_min": round(vuelo, 1),
                            "tareas": len(sal["tareas"]), "inicio": ini.isoformat(), "fin": fin.isoformat(),
                            "clima": aviso, "p_clima": None if p_clima is None else round(p_clima, 3),
                            "p_media": round(p_media, 4), "aristas_bloqueadas": len(bloq)})
            rutas_fc.append({"type": "Feature",
                             "geometry": {"type": "MultiLineString",
                                          "coordinates": [geoms[e]["coords"] for e in aristas if e in geoms]},
                             "properties": {"tecnica": cod, "dron": d + 1, "salida": s, "tipo": sal["tipo"],
                                            "km": round(km, 2), "vuelo_min": round(vuelo, 1),
                                            "tareas": len(sal["tareas"]), "inicio": ini.isoformat(),
                                            "fin": (ini + timedelta(minutes=vuelo)).isoformat(),
                                            "aristas": list(aristas),
                                            "tarea_ids": list(sal["tareas"]),
                                            "metros_recorrido": km * 1000,
                                            "camino_completo": sal.get("camino_completo", True),
                                            "retorno_base": sal["tipo"] != "inspeccion" or (
                                                bool(sal["pasos"]) and sal["pasos"][0][0] == "tramo"
                                                and sal["pasos"][-1][0] == "tramo"
                                                and sal["pasos"][0][1] == sal["pasos"][-1][2]),
                                            "clima": aviso, "p_media": round(p_media, 4),
                                            "p_clima": None if p_clima is None else round(p_clima, 3),
                                            "aristas_bloqueadas": len(bloq)}})
            minutos += vuelo + (recarga if s < len(salidas) else 0)
        drones.append({
            "dron": d + 1, "bases": bases_d, "tareas": len(ids_dron),
            "puntos": sum(tarea_por_id[i]["n_puntos"] for i in ids_dron),
            "km_total": round(dis / 1000, 2), "km_inspeccion": round(km_insp / 1000, 2),
            "km_esperados": round(esperado / 1000, 2),
            "km_repetidos": round(repetido / 1000, 2),
            "repeticion_rho": round(repetido / dis, 3) if dis else 0.0,
            "salidas": n_insp, "traslados": len(salidas) - n_insp,
            "tiempo_min": round(minutos, 1), "detalle_salidas": detalle,
        })
    activos = [x for x in drones if x["km_total"] > 0]
    Dis = sum(x["km_total"] for x in drones)
    L_insp = sum(x["km_inspeccion"] for x in drones)
    T = max((x["tiempo_min"] for x in drones), default=0.0)
    kms = [x["km_total"] for x in activos]
    if T / 60 > prefs["jornada_h"]:
        avisos.append(f"La inspección toma {T / 60:.1f} h y supera la jornada de {prefs['jornada_h']:g} h.")
    n_clima = sum(1 for x in drones for s in x["detalle_salidas"] if s["clima"] and "reprogramar" in s["clima"])
    if n_clima:
        avisos.append(f"{n_clima} salida(s) caen en horas con clima no apto para el Mavic 3E (A2): conviene "
                      "otra hora de inicio.")
    if cruzadas:
        avisos.append(f"Cruza {len(cruzadas)} calle(s) bloqueadas por amenazas activas.")
    resumen = {
        "tiempo_global_min": round(T, 1),
        "km_total_Dis": round(Dis, 2), "km_a_inspeccionar": round(L_insp, 2),
        "km_esperados": round(sum(x["km_esperados"] for x in drones), 2),
        "salidas": sum(x["salidas"] for x in drones),
        "utilizacion_beta": round(min(kms) / max(kms), 3) if kms else None,
        "eficiencia_eta": round(L_insp / Dis, 3) if Dis else None,
        "repeticion_rho_media": round(float(np.mean([x["repeticion_rho"] for x in activos])), 3) if activos else None,
        "p_media_km": round(riesgo_m / metros_tot, 4) if metros_tot else None,
        "p_clima_max": round(max(p_climas), 3) if p_climas else None,
        "salidas_reprogramar": n_clima,
        "aristas_bloqueadas": len(cruzadas),
    }
    return drones, rutas_fc, resumen, avisos


METODOS = {
    "T1": "Costo = largo de la calle (pgr_dijkstraCostMatrix y pgr_dijkstra), sin amenazas. Orden: tramo pendiente "
          "más cercano con batería y 2-opt (Zhong et al., 2023). Línea base.",
    "T2": "MILP de ruteo multi-salida con batería: cada calle una vez y en un sentido (copias orientadas), "
          "flujo de energía de una mercancía (Gavish y Graves, 1978) y número de salidas libre. Minimiza el "
          "tiempo con costo resiliente. Arranque en caliente con T3; se informa la brecha certificada.",
    "T3": "Costo resiliente c(e) = largo / (1 - p(e)) con la probabilidad de falla del punto 5 y aristas "
          "bloqueadas eliminadas (pgr_dijkstra). Mismo orden constructivo que T1.",
    "T4": "DA-DPSO: permutaciones de calles, mutación con inercia adaptativa (diversidad y estancamiento) y "
          "cruce de orden con la mejor propia y la del enjambre (Pan et al., 2008); split óptimo (Prins, 2004), "
          "búsqueda local lamarckiana y reinicios de búsqueda local iterada. Costo resiliente.",
}


def planificar_area(conn, area, entrada_prefs):
    prefs, por_defecto = preferencias.normalizar(
        entrada_prefs, preferencias.PREFERENCIAS_FLOTA, preferencias.LIMITES_FLOTA)
    t0 = preferencias.hora_salida(prefs)
    avisos = []

    poligono, bbox_area = validar_area(area)
    if bbox_area is None:
        bbox_area = datos.extension_red(conn)
        if bbox_area is None:
            raise ErrorFlota("La red vial está vacía: ejecuta Infraestructura/cargar.py")
    bbox_red = expandir_bbox(bbox_area, MARGEN_RED_M + 2000)

    # Amenazas vigentes durante la jornada: las elegidas por el usuario guían el
    # ruteo; todas se usan para evaluar el riesgo de cada técnica.
    # Bloqueos (A1 incendio activo, A3 alerta peligrosa, A4 accidente) desde
    # temp_amenazas; probabilidad de las calles desde la tabla del punto 5, sin
    # A2: el clima es casi uniforme a la escala de la grilla, no cambia las rutas
    # y se evalúa por hora de cada salida (ver _clima_salida).
    todas = list(preferencias.AMENAZAS_VALIDAS)
    amen = datos.amenazas_region(conn, bbox_red, t0, prefs["jornada_h"], prefs["considerar_clima"],
                                 prefs["amenazas"])
    amen_eval = datos.amenazas_region(conn, bbox_red, t0, prefs["jornada_h"], True, todas)
    prob, ventana = datos.probabilidades_aristas(conn, bbox_red, [a for a in prefs["amenazas"] if a != "A2"])
    prob_eval, _ = datos.probabilidades_aristas(conn, bbox_red, [a for a in todas if a != "A2"])
    if ventana is None:
        avisos.append("No está la tabla de indisponibilidad del punto 5: el costo de las calles queda igual a su "
                      "largo (solo se aplican los bloqueos). Ejecuta Probabilidades/generar.py.")
    elif not (ventana[0] <= t0 <= ventana[1]):
        avisos.append("Las probabilidades de falla se calcularon para otra jornada "
                      f"({ventana[0]:%d-%m %H:%M} a {ventana[1]:%d-%m %H:%M} UTC): vuelve a ejecutar main.py.")
    red = Red(amen.bloqueadas, {e: factor_resiliente(p) for e, p in prob.items()})
    red_dist = Red(solo_distancia=True)
    clima = (datos.clima_celdas(conn, bbox_red, t0, t0 + timedelta(hours=24))
             if prefs["considerar_clima"] and "A2" in prefs["amenazas"] else ({}, {}))

    # 1. Tareas: calles con señal dentro del área
    puntos = datos.puntos_inspeccion(conn, json.dumps(poligono) if poligono else None,
                                     bbox_area, prefs, RADIO_ASIGNACION_M)
    if not puntos:
        raise ErrorFlota("No hay señales de los tipos elegidos dentro del área.")
    por_arista = defaultdict(list)
    for p in puntos:
        por_arista[p["edge_id"]].append(p)
    tareas, postergadas = [], []
    for e, ps in por_arista.items():
        t = {"id": int(e), "source": int(ps[0]["source"]), "target": int(ps[0]["target"]),
             "length_m": max(float(ps[0]["length_m"]), 0.1), "name": ps[0]["name"],
             "lon": float(np.mean([p["lon"] for p in ps])), "lat": float(np.mean([p["lat"] for p in ps])),
             "n_puntos": len(ps), "tipos": dict(Counter(p["tipo"] for p in ps))}
        if e in amen.bloqueadas:
            postergadas.append({**t, "motivo": "amenaza activa en la jornada"})
        else:
            tareas.append(t)
    if not tareas:
        raise ErrorFlota("Todas las calles con señal del área están bloqueadas por amenazas.")

    proy = Proyeccion((bbox_area[1] + bbox_area[3]) / 2)
    P = proy.xy([t["lon"] for t in tareas], [t["lat"] for t in tareas])
    largos = np.array([t["length_m"] for t in tareas])

    # 2-4. Instancia común: partición equilibrada, bases y sectores. Se repite
    # reponderando la carga de cada área según el tiempo que resultó con T3, para
    # equilibrar a los drones (Zhong et al.) y se conserva la mejor partición.
    n = min(prefs["n_drones"], len(tareas))
    if n < prefs["n_drones"]:
        avisos.append(f"Solo hay {len(tareas)} calles a inspeccionar: se usan {n} drones.")
    capacidad = (prefs["velocidad_crucero_ms"] * prefs["autonomia_min"] * 60
                 * (1 - prefs["reserva_bateria_pct"] / 100))
    v = prefs["velocidad_crucero_ms"]
    recarga = prefs["tiempo_recarga_min"]
    centralizada = prefs["bases"] == "centralizada"

    lugares = datos.lugares_publicos(conn, bbox_red, prefs["categorias_base"])
    if not lugares:
        avisos.append("No hay lugares públicos (M2) cargados para las categorías elegidas: las bases "
                      "quedan en la calle más cercana al centro de cada área.")
    ubicador = UbicadorBases(conn, red, proy, lugares)

    def minutos_estimados(salidas):
        return sum(s["metros"] for s in salidas) / v / 60 + recarga * max(0, len(salidas) - 1)

    preparar_pesos(conn, red)
    t_instancia = time.perf_counter()
    carga = _carga_estimada(P, largos)
    mejor = None
    for _ in range(1 + REBALANCEOS):
        etiquetas = particion_equilibrada(P, carga, n)
        if centralizada:
            bases = [ubicador.elegir(P, largos)] * n
        else:
            bases, usados = [None] * n, set()
            for d in sorted(range(n), key=lambda d: -float(largos[etiquetas == d].sum())):
                bases[d] = ubicador.elegir(P[etiquetas == d], largos[etiquetas == d], usados)
                if bases[d]["lugar"]:
                    usados.add(bases[d]["lugar"]["id"])
        plan, tiempos = [], []
        for d in range(n):
            grupo = [t for t, e in zip(tareas, etiquetas) if e == d]
            otros = {b["lugar"]["id"] for i, b in enumerate(bases) if i != d and b["lugar"]}
            salidas, fuera, usadas_b, pasos = _rutear_dron(conn, red, bases[d], grupo, capacidad, proy,
                                                           droneports=not centralizada,
                                                           ubicar=ubicador.elegir, excluir=otros)
            plan.append((salidas, fuera, usadas_b, pasos))
            tiempos.append(minutos_estimados(salidas))
        T_est = max(tiempos)
        if mejor is None or T_est < mejor[0]:
            mejor = (T_est, etiquetas, plan)
        activos_t = [x for x in tiempos if x > 0]
        if n == 1 or not activos_t or min(activos_t) / max(activos_t) >= 0.9:
            break
        media = float(np.mean(tiempos))
        ajuste = np.array([x / media if media else 1.0 for x in tiempos]) ** 0.8
        carga = carga * ajuste[etiquetas]
    _, etiquetas, plan = mejor
    t_instancia = time.perf_counter() - t_instancia

    # 5. Técnicas sobre la instancia común
    tecs = [t for t in preferencias.TECNICAS_VALIDAS if t in prefs["tecnicas"]]
    resultados = _resolver_tecnicas(conn, plan, tecs, red_dist, prefs, capacidad)
    planes = {}
    for cod in tecs:
        t_m = time.perf_counter()
        planes[cod] = _plan_tecnica(plan, resultados[cod]["ordenes"])
        for salidas, _, _ in planes[cod]:
            materializar(conn, red_dist if cod == "T1" else red, salidas)
        resultados[cod]["info"]["segundos_trazado"] = time.perf_counter() - t_m
    conn.rollback()  # descarta la tabla temporal pesos_flota

    # 6. Indicadores y capas
    usadas = {e for p in planes.values() for salidas, _, _ in p for s in salidas for e in s["aristas"]}
    geoms = datos.geometrias(conn, list(usadas | {t["id"] for t in tareas} | {t["id"] for t in postergadas}))
    tarea_por_id = {t["id"]: t for t in tareas}
    inalcanzables = [i for _, fuera, _, _ in plan for i in fuera]
    fuera_set = set(inalcanzables)

    salida_tecnicas = {}
    for cod in tecs:
        drones_t, rutas_t, resumen_t, avisos_t = _evaluar_tecnica(
            cod, planes[cod], etiquetas, tareas, tarea_por_id, fuera_set, geoms, prob_eval,
            amen_eval.bloqueadas, clima, prefs, capacidad, t0)
        info = resultados[cod]["info"]
        tiempo = info["segundos_solver"] + info["segundos_trazado"]
        if cod != "T1":
            tiempo += info["segundos_matriz"]      # matriz resiliente (y orden constructivo en T3)
        detalle = {"minutos_modelo": round(info["minutos_modelo"], 1), "sectores": info["sectores"]}
        if cod == "T2":
            valor, cota = info.get("valor"), info.get("cota")
            detalle.update({
                "solver": info.get("solver"), "estados": sorted(set(info.get("estados", []))),
                "variables": info.get("variables"), "binarias": info.get("binarias"),
                "restricciones": info.get("restricciones"),
                "cota_inferior_min": round(cota, 1) if cota else None,
                "gap": round((valor - cota) / valor, 4) if valor and cota is not None else None,
                "limite_s": prefs["limite_milp_s"], "sectores_sin_milp": info.get("sectores_sin_milp", 0),
                "nota": info.get("nota")})
        elif cod == "T4":
            ini, fin = info.get("valor_inicial"), info.get("valor")
            detalle.update({"iteraciones": info.get("iteraciones"), "evaluaciones": info.get("evaluaciones"),
                            "busquedas_locales": info.get("busquedas_locales"),
                            "mejora_sobre_T3": round((ini - fin) / ini, 4) if ini else None,
                            "limite_s": prefs["limite_dpso_s"], "poblacion": dpso.POBLACION,
                            "semilla": dpso.SEMILLA})
        nombre = NOMBRES[cod]
        if cod == "T2" and info.get("solver") and info["solver"] != "Gurobi":
            nombre = f"Modelo MILP ({info['solver']}, sin licencia de Gurobi)"
        salida_tecnicas[cod] = {
            "codigo": cod, "nombre": nombre, "costo": COSTO[cod], "metodo": METODOS[cod],
            "tiempo_calculo_s": round(tiempo, 2), "resumen": resumen_t, "detalle": detalle,
            "drones": drones_t, "rutas": {"type": "FeatureCollection", "features": rutas_t},
            "avisos": avisos_t,
        }

    # Capas comunes (iguales para todas las técnicas): calles, áreas y bases
    tareas_fc, areas_fc, bases_fc = [], [], []
    for d, (_, _, bases_d, _) in enumerate(plan):
        ids_dron = [t["id"] for t, e in zip(tareas, etiquetas) if e == d and t["id"] not in fuera_set]
        coords_area = [c for i in ids_dron if i in geoms for c in geoms[i]["coords"]]
        coords_area += [[b["lon"], b["lat"]] for b in bases_d]
        casco = envolvente(coords_area)
        if len(casco) >= 4:
            areas_fc.append({"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [casco]},
                             "properties": {"dron": d + 1}})
        for orden_b, b in enumerate(bases_d, start=1):
            lugar = b.get("lugar") or {}
            punto = [lugar["lon"], lugar["lat"]] if lugar else [b["lon"], b["lat"]]
            bases_fc.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": punto},
                             "properties": {"dron": d + 1, "orden": orden_b, "nodo": b["id"],
                                            "compartida": centralizada,
                                            "nombre": lugar.get("nombre"), "categoria": lugar.get("categoria"),
                                            "dependencia": lugar.get("dependencia"),
                                            "direccion": lugar.get("direccion"),
                                            "despegue": [b["lon"], b["lat"]], "distancia_m": b["distancia_m"]}})
        for i in ids_dron:
            t = tarea_por_id[i]
            if i in geoms:
                tareas_fc.append({"type": "Feature",
                                  "geometry": {"type": "LineString", "coordinates": geoms[i]["coords"]},
                                  "properties": {"dron": d + 1, "id": i, "nombre": t["name"],
                                                 "puntos": t["n_puntos"], "tipos": t["tipos"],
                                                 "p_falla": round(prob_eval.get(i, 0.0), 4)}})
    if postergadas:
        avisos.append(f"{len(postergadas)} calle(s) con señal quedan postergadas por amenazas.")
    if inalcanzables:
        avisos.append(f"{len(inalcanzables)} calle(s) quedan fuera del alcance de la batería desde su base.")
    postergadas_fc = [{"type": "Feature",
                       "geometry": {"type": "LineString", "coordinates": geoms[t["id"]]["coords"]},
                       "properties": {"id": t["id"], "nombre": t["name"], "motivo": t["motivo"],
                                      "puntos": t["n_puntos"]}}
                      for t in postergadas if t["id"] in geoms]
    postergadas_fc += [{"type": "Feature",
                        "geometry": {"type": "LineString", "coordinates": geoms[i]["coords"]},
                        "properties": {"id": i, "nombre": tarea_por_id[i]["name"],
                                       "motivo": "fuera del alcance de la batería desde su base",
                                       "puntos": tarea_por_id[i]["n_puntos"]}}
                       for i in sorted(fuera_set) if i in geoms]
    bases_dron = [{"dron": d + 1, "bases": bases_d, "tareas": int(sum(
        1 for t, e in zip(tareas, etiquetas) if e == d and t["id"] not in fuera_set))}
        for d, (_, _, bases_d, _) in enumerate(plan)]
    fc = lambda feats: {"type": "FeatureCollection", "features": feats}  # noqa: E731
    return {
        "area": poligono, "hora_salida": t0.isoformat(),
        "preferencias": prefs, "preferencias_por_defecto": por_defecto,
        "resumen": {
            "drones": n, "puntos": len(puntos), "calles_a_inspeccionar": len(tareas) - len(inalcanzables),
            "km_a_inspeccionar": round(sum(t["length_m"] for t in tareas if t["id"] not in fuera_set) / 1000, 2),
            "bases": len({b["id"] for _, _, bs, _ in plan for b in bs}),
            "sectores": sum(1 for _, _, _, pasos in plan for tipo, c in pasos
                            if tipo == "sector" and not c.get("reintento")),
            "capacidad_salida_km": round(capacidad / 1000, 2),
            "postergadas": len(postergadas), "fuera_de_alcance": len(inalcanzables),
            "amenazas_en_jornada": amen.n_amenazas, "amenazas_consideradas": prefs["amenazas"],
            "segundos_instancia": round(t_instancia, 2),
        },
        "tecnicas": salida_tecnicas,
        "servicio_requerido": {
            "unidad": "senales",
            "tareas": [{"id": t["id"], "senales": int(t["n_puntos"])} for t in tareas + postergadas],
        },
        "drones": bases_dron,
        "capas": {"tareas": fc(tareas_fc), "postergadas": fc(postergadas_fc),
                  "areas": fc(areas_fc), "bases": fc(bases_fc)},
        "metodo": ("Instancia común para las técnicas: partición equilibrada del área entre drones, bases en "
                   "recintos públicos (M2) y sectores (Zhong et al., 2023). Cada técnica rutea los mismos sectores."),
        "avisos": avisos,
    }
