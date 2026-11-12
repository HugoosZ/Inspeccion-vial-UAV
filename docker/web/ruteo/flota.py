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
     calles bloqueadas (incendio, SOSAFE, Waze) se postergan.
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
"""

import json
import math
from collections import Counter, defaultdict
from datetime import timedelta

import numpy as np
from psycopg2.extras import execute_values

from . import datos, preferencias


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
    """Grafo con pesos de la consulta: aristas bloqueadas (-1) y factores."""

    def __init__(self, bloqueadas, factores):
        self.bloqueadas = set(bloqueadas)
        self.factores = {e: f for e, f in factores.items() if e not in self.bloqueadas and f != 1.0}

    def factor(self, e):
        return -1.0 if e in self.bloqueadas else self.factores.get(e, 1.0)

    @staticmethod
    def sql(bbox):
        return SQL_ARISTAS.format(xmin=bbox[0], ymin=bbox[1], xmax=bbox[2], ymax=bbox[3])


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
# Orden de visita de un dron (regla del tramo pendiente más cercano + batería)
# ------------------------------------------------------------------------------
def ordenar_tareas(M, ib, U, V, Lt, capacidad):
    """Arma las salidas de un dron.

    M: matriz de costos; ib: índice de la base; U, V, Lt: índices de los
    extremos y costo de cada tarea; capacidad: metros por salida.
    Devuelve (salidas, inalcanzables). Cada salida es una lista de
    (tarea, indice_entrada, indice_salida).
    """
    T = len(U)
    ida_vuelta = np.minimum(M[ib, U] + Lt + M[V, ib], M[ib, V] + Lt + M[U, ib])
    inalcanzables = [int(j) for j in np.where(~(ida_vuelta <= capacidad))[0]]
    pendiente = ida_vuelta <= capacidad
    salidas = []
    while pendiente.any():
        actual, usado, seq = ib, 0.0, []
        while True:
            cand = np.where(pendiente)[0]
            if not len(cand):
                break
            dU, dV = M[actual, U[cand]], M[actual, V[cand]]
            # entrar por U y salir por V, o al revés; debe alcanzar para volver
            okU = usado + dU + Lt[cand] + M[V[cand], ib] <= capacidad
            okV = usado + dV + Lt[cand] + M[U[cand], ib] <= capacidad
            clave = np.minimum(np.where(okU, dU, np.inf), np.where(okV, dV, np.inf))
            if not np.isfinite(clave).any():
                break
            j = int(np.argmin(clave))
            t = int(cand[j])
            if okU[j] and (not okV[j] or dU[j] <= dV[j]):
                entrada, salida, d = int(U[t]), int(V[t]), dU[j]
            else:
                entrada, salida, d = int(V[t]), int(U[t]), dV[j]
            usado += d + Lt[t]
            seq.append((t, entrada, salida))
            pendiente[t] = False
            actual = salida
        if not seq:
            break
        salidas.append(seq)
    return salidas, inalcanzables


def costo_salida(M, ib, seq, Lt):
    if not seq:
        return 0.0
    c = M[ib, seq[0][1]] + M[seq[-1][2], ib]
    for (t, e, s), (_, e2, _) in zip(seq, seq[1:]):
        c += M[s, e2]
    return c + sum(Lt[t] for t, _, _ in seq)


def dos_opt(M, ib, seq, max_pasadas=20):
    """Mejora 2-opt de una salida: invierte tramos de la secuencia (y el sentido
    de recorrido de cada tarea invertida) si eso acorta los traslados.
    Supone costos simétricos (la red se carga no dirigida)."""
    seq = list(seq)
    n = len(seq)
    if n < 3:
        return seq
    for _ in range(max_pasadas):
        mejoro = False
        for i in range(n - 1):
            previo = ib if i == 0 else seq[i - 1][2]
            for j in range(i + 1, n):
                siguiente = ib if j == n - 1 else seq[j + 1][1]
                delta = (M[previo, seq[j][2]] + M[seq[i][1], siguiente]
                         - M[previo, seq[i][1]] - M[seq[j][2], siguiente])
                if delta < -1e-6:
                    seq[i:j + 1] = [(t, s, e) for t, e, s in reversed(seq[i:j + 1])]
                    mejoro = True
                    previo = ib if i == 0 else seq[i - 1][2]
        if not mejoro:
            break
    return seq


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


def _rutear_sector(conn, red, base, grupo, capacidad, margen=MARGEN_RED_M):
    """Salidas de un sector desde su base.

    Devuelve (salidas, inalcanzables). Cada salida es un dict con la secuencia
    de pasos ("tramo", origen, destino) / ("tarea", arista); los tramos se
    trazan después con pgr_dijkstra (materializar) solo para el plan elegido.

    La red se recorta al rectángulo del sector más un margen; si alguna tarea
    queda sin camino (el acceso pasa por fuera del recorte), se reintenta con
    un margen mayor."""
    vids = [base["id"]] + sorted({v for t in grupo for v in (t["source"], t["target"])} - {base["id"]})
    idx = {v: i for i, v in enumerate(vids)}
    lon = [t["lon"] for t in grupo] + [base["lon"]]
    lat = [t["lat"] for t in grupo] + [base["lat"]]
    bbox = expandir_bbox((min(lon), min(lat), max(lon), max(lat)), margen)
    M = matriz_costos(conn, red, bbox, vids)
    U = np.array([idx[t["source"]] for t in grupo])
    V = np.array([idx[t["target"]] for t in grupo])
    Lt = np.array([t["length_m"] * max(red.factor(t["id"]), 1.0) for t in grupo])
    orden, fuera = ordenar_tareas(M, 0, U, V, Lt, capacidad)
    finito = np.isfinite(M)
    Mf = np.where(finito, M, 0)
    if np.allclose(Mf, Mf.T, rtol=1e-6, atol=1e-3):
        orden = [dos_opt(M, 0, seq) for seq in orden]
    salidas = []
    for seq in orden:
        pasos, anterior = [], 0
        for t, e, s in seq:
            pasos.append(("tramo", vids[anterior], vids[e]))
            pasos.append(("tarea", grupo[t]["id"]))
            anterior = s
        pasos.append(("tramo", vids[anterior], vids[0]))
        salidas.append({"tipo": "inspeccion", "pasos": pasos, "bbox": bbox,
                        "tareas": [grupo[t]["id"] for t, _, _ in seq],
                        "metros": float(costo_salida(M, 0, seq, Lt))})
    if fuera and margen < MARGEN_MAX_M:
        otras, fuera_ids = _rutear_sector(conn, red, base, [grupo[j] for j in fuera], capacidad, MARGEN_MAX_M)
        return salidas + otras, fuera_ids
    return salidas, [grupo[j]["id"] for j in fuera]


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
        for p in s["pasos"]:
            if p[0] == "tarea":
                aristas.append(p[1])
            elif p[1] != p[2]:
                aristas += caminos[s["bbox"]].get((p[1], p[2]), [])
        s["aristas"] = aristas
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
    Devuelve (salidas, inalcanzables, bases_usadas). Las salidas son de tipo
    'inspeccion' o 'traslado' (vuelo de una base a otra).
    """
    if not tareas:
        return [], [], [base]
    P = proy.xy([t["lon"] for t in tareas], [t["lat"] for t in tareas])
    largos = np.array([t["length_m"] for t in tareas])
    if droneports:
        etiquetas = _sectores(P, largos, capacidad, len(tareas))
    else:
        m = max(1, math.ceil((2 * len(tareas) + 1) / MAX_NODOS_MATRIZ))
        etiquetas = particion_equilibrada(P, largos, m) if m > 1 else np.zeros(len(tareas), int)
    restantes = sorted(set(etiquetas.tolist()))
    un_sector = len(restantes) == 1
    salidas, inalcanzables, bases_usadas = [], [], []
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
            salidas.append({"tipo": "traslado", "pasos": [("tramo", base_actual["id"], base_sector["id"])],
                            "bbox": caja, "tareas": [], "metros": FACTOR_RODEO * d})
        if not bases_usadas or bases_usadas[-1]["id"] != base_sector["id"]:
            bases_usadas.append(base_sector)
        sal, fuera = _rutear_sector(conn, red, base_sector, grupo, capacidad)
        salidas += sal
        inalcanzables += fuera
        base_actual = base_sector
        posicion = proy.xy([base_sector["lon"]], [base_sector["lat"]])[0]
    return salidas, inalcanzables, bases_usadas


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

    # Amenazas vigentes durante la jornada
    amen = datos.amenazas_region(conn, bbox_red, t0, prefs["jornada_h"], prefs["considerar_clima"])
    red = Red(set(amen.bloqueadas), amen.factor_estatico)

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
             "length_m": ps[0]["length_m"], "name": ps[0]["name"],
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

    # 2-4. Partición equilibrada, bases y rutas. Se repite reponderando la carga
    # de cada área según el tiempo que resultó, para equilibrar a los drones
    # (objetivo de varianza mínima de Zhong et al.) y se conserva el mejor plan.
    n = min(prefs["n_drones"], len(tareas))
    if n < prefs["n_drones"]:
        avisos.append(f"Solo hay {len(tareas)} calles a inspeccionar: se usan {n} drones.")
    capacidad = (prefs["velocidad_crucero_ms"] * prefs["autonomia_min"] * 60
                 * (1 - prefs["reserva_bateria_pct"] / 100))
    v = prefs["velocidad_crucero_ms"]
    recarga = prefs["tiempo_recarga_min"]
    centralizada = prefs["bases"] == "centralizada"

    # Sitios candidatos para las bases: lugares públicos (M2) de las categorías elegidas
    lugares = datos.lugares_publicos(conn, bbox_red, prefs["categorias_base"])
    if not lugares:
        avisos.append("No hay lugares públicos (M2) cargados para las categorías elegidas: las bases "
                      "quedan en la calle más cercana al centro de cada área.")
    ubicador = UbicadorBases(conn, red, proy, lugares)

    def minutos_estimados(salidas, distancias):
        vuelo = sum(m for m in distancias) / v / 60
        return vuelo + recarga * max(0, len(salidas) - 1)

    preparar_pesos(conn, red)
    carga = _carga_estimada(P, largos)
    mejor = None
    for _ in range(1 + REBALANCEOS):
        etiquetas = particion_equilibrada(P, carga, n)
        if centralizada:
            bases = [ubicador.elegir(P, largos)] * n
        else:
            # sin repetir recinto: eligen primero las áreas con más calles
            bases, usados = [None] * n, set()
            for d in sorted(range(n), key=lambda d: -float(largos[etiquetas == d].sum())):
                bases[d] = ubicador.elegir(P[etiquetas == d], largos[etiquetas == d], usados)
                if bases[d]["lugar"]:
                    usados.add(bases[d]["lugar"]["id"])
        plan, tiempos = [], []
        for d in range(n):
            grupo = [t for t, e in zip(tareas, etiquetas) if e == d]
            otros = {b["lugar"]["id"] for i, b in enumerate(bases) if i != d and b["lugar"]}
            salidas, fuera, usadas_b = _rutear_dron(conn, red, bases[d], grupo, capacidad, proy,
                                                     droneports=not centralizada,
                                                     ubicar=ubicador.elegir, excluir=otros)
            plan.append((salidas, fuera, usadas_b))
            # metros estimados (costo ponderado); los traslados entre bases, por distancia recta
            tiempos.append(minutos_estimados(salidas, [s["metros"] for s in salidas]))
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
    for salidas, _, _ in plan:
        materializar(conn, red, salidas)
    conn.rollback()  # descarta la tabla temporal pesos_flota

    # 5. Métricas, tiempos y geometrías
    usadas = {e for salidas, _, _ in plan for s in salidas for e in s["aristas"]}
    geoms = datos.geometrias(conn, list(usadas | {t["id"] for t in tareas} | {t["id"] for t in postergadas}))
    largo = {e: g["length_m"] for e, g in geoms.items()}
    tarea_por_id = {t["id"]: t for t in tareas}
    inalcanzables = [i for _, fuera, _ in plan for i in fuera]
    fuera_set = set(inalcanzables)

    drones, rutas_fc, tareas_fc, areas_fc, bases_fc = [], [], [], [], []
    for d, (salidas, _, bases_d) in enumerate(plan):
        conteo = Counter(e for s in salidas for e in s["aristas"])
        dis = sum(largo.get(e, 0) * c for e, c in conteo.items())
        repetido = sum(largo.get(e, 0) * (c - 1) for e, c in conteo.items() if c > 1)
        ids_dron = [t["id"] for t, e in zip(tareas, etiquetas) if e == d and t["id"] not in fuera_set]
        km_insp = sum(tarea_por_id[i]["length_m"] for i in ids_dron)
        minutos, detalle, n_insp = 0.0, [], 0
        salidas = [s for s in salidas if s["aristas"]]
        for s, sal in enumerate(salidas, start=1):
            aristas = sal["aristas"]
            km = sum(largo.get(e, 0) for e in aristas) / 1000
            if sal["tipo"] == "traslado" and km * 1000 > capacidad:
                avisos.append(f"El traslado del dron {d + 1} entre bases ({km:.1f} km) supera la batería: "
                              "debe hacerse por tierra o con una recarga intermedia.")
            vuelo = km * 1000 / v / 60
            ini = (t0 + timedelta(minutes=minutos)).replace(microsecond=0)
            fin = (ini + timedelta(minutes=vuelo)).replace(microsecond=0)
            clima = 0.0
            for e in set(aristas):
                for desde, hasta, f in amen.temporales.get(e, ()):
                    if desde < fin.timestamp() and hasta > ini.timestamp():
                        clima = max(clima, f)
            aviso = None
            if clima >= datos.FACTOR_CLIMA_BLOQUEO:
                aviso = "mal tiempo (riesgo alto): reprogramar"
            elif clima > 1:
                aviso = "lluvia o viento moderado"
            n_insp += sal["tipo"] == "inspeccion"
            detalle.append({"salida": s, "tipo": sal["tipo"], "km": round(km, 2), "vuelo_min": round(vuelo, 1),
                            "tareas": len(sal["tareas"]), "inicio": ini.isoformat(), "fin": fin.isoformat(),
                            "clima": aviso})
            rutas_fc.append({"type": "Feature",
                             "geometry": {"type": "MultiLineString",
                                          "coordinates": [geoms[e]["coords"] for e in aristas if e in geoms]},
                             "properties": {"dron": d + 1, "salida": s, "tipo": sal["tipo"], "km": round(km, 2),
                                            "vuelo_min": round(vuelo, 1), "tareas": len(sal["tareas"]),
                                            "inicio": ini.isoformat(), "clima": aviso}})
            minutos += vuelo + (recarga if s < len(salidas) else 0)
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
                                                 "puntos": t["n_puntos"], "tipos": t["tipos"]}})
        drones.append({
            "dron": d + 1, "bases": bases_d, "tareas": len(ids_dron),
            "puntos": sum(tarea_por_id[i]["n_puntos"] for i in ids_dron),
            "km_total": round(dis / 1000, 2), "km_inspeccion": round(km_insp / 1000, 2),
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
        avisos.append(f"La inspección completa toma {T / 60:.1f} h y supera la jornada de "
                      f"{prefs['jornada_h']:g} h: agrega drones o reduce el área.")
    n_clima = sum(1 for x in drones for s in x["detalle_salidas"] if s["clima"])
    if n_clima:
        avisos.append(f"{n_clima} salida(s) coinciden con horas de mal tiempo pronosticado (A2).")
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
    fc = lambda feats: {"type": "FeatureCollection", "features": feats}  # noqa: E731
    return {
        "area": poligono, "hora_salida": t0.isoformat(),
        "preferencias": prefs, "preferencias_por_defecto": por_defecto,
        "resumen": {
            "drones": n, "puntos": len(puntos), "calles_a_inspeccionar": len(tareas),
            "km_a_inspeccionar": round(L_insp, 2), "km_total_Dis": round(Dis, 2),
            "tiempo_global_min": round(T, 1),
            "salidas": sum(x["salidas"] for x in drones),
            "bases": len({b["id"] for x in drones for b in x["bases"]}),
            "capacidad_salida_km": round(capacidad / 1000, 2),
            "utilizacion_beta": round(min(kms) / max(kms), 3) if kms else None,
            "eficiencia_eta": round(L_insp / Dis, 3) if Dis else None,
            "repeticion_rho_media": round(float(np.mean([x["repeticion_rho"] for x in activos])), 3) if activos else None,
            "postergadas": len(postergadas), "fuera_de_alcance": len(inalcanzables),
            "amenazas_en_jornada": amen.n_amenazas,
        },
        "drones": drones,
        "capas": {"rutas": fc(rutas_fc), "tareas": fc(tareas_fc), "postergadas": fc(postergadas_fc),
                  "areas": fc(areas_fc), "bases": fc(bases_fc)},
        "metodo": ("Partición equilibrada del área entre drones con bases distribuidas y, para cada dron, "
                   "regla del tramo pendiente más cercano con límite de batería y mejora 2-opt "
                   "(Zhong et al., 2023); distancias con pgr_dijkstraCostMatrix y trazado con pgr_dijkstra."),
        "avisos": avisos,
    }
