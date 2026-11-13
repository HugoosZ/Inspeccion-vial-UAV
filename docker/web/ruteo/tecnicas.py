"""
Técnicas de ruteo comparadas (punto 6 del enunciado).

Las cuatro técnicas resuelven el MISMO subproblema en cada sector: dada una
base (droneport en un recinto público M2), un conjunto de calles a inspeccionar
y la batería del dron, decidir las salidas (base -> calles -> base), el orden de
las calles y el sentido en que se recorre cada una. La partición del área entre
drones y la elección de bases se calculan una sola vez y se comparten, para que
las diferencias medidas se deban solo a la técnica de ruteo.

  T1  pgr_dijkstra por distancia: costo = largo de la calle. Ignora amenazas
      y metadata de riesgo (línea base). Orden con la regla del tramo pendiente
      más cercano + batería + 2-opt (Zhong et al., 2023).
  T2  Modelo formal (MILP) resuelto con Gurobi: minimiza el tiempo total del
      dron con costos resilientes, restricción de batería por salida y número
      de salidas libre. Ver ruteo/milp.py.
  T3  pgr_dijkstra con amenazas, metadata y restricciones del usuario: costo
      resiliente c(e) = largo / (1 - p(e)) y aristas bloqueadas eliminadas.
      Mismo orden constructivo que T1.
  T4  Metaheurística DA-DPSO (PSO discreta con adaptación dinámica) sobre
      los mismos costos resilientes. Ver ruteo/dpso.py.

Notación de un sector: M es la matriz de costos entre vértices (índice ib =
base); cada tarea t tiene extremos U[t], V[t] y costo de recorrido Lt[t].
Una salida es una lista de (t, entrada, salida) con los índices del vértice
por donde entra y sale el dron.
"""

import math

import numpy as np

NOMBRES = {
    "T1": "pgr_dijkstra por distancia",
    "T2": "Modelo MILP (Gurobi)",
    "T3": "pgr_dijkstra resiliente",
    "T4": "Metaheurística DA-DPSO",
}
COSTO = {"T1": "distancia", "T2": "resiliente", "T3": "resiliente", "T4": "resiliente"}

# p(e) se acota para que c(e) = largo / (1 - p) sea finito: con p = 0,99 la
# calle cuesta 100 veces su largo (queda en el grafo como último recurso).
P_MAX = 0.99


def factor_resiliente(p):
    """Factor de costo de una arista con probabilidad de falla p.

    Si cada intento de cruzar la arista falla con probabilidad p (y el dron
    debe repetirlo), el número de intentos es geométrico con media 1 / (1 - p):
    el costo esperado de la arista es largo / (1 - p)."""
    return 1.0 / (1.0 - min(max(p, 0.0), P_MAX))


def prob_desde_eventos(eventos):
    """Probabilidad de falla por arista a partir de las amenazas vigentes
    (respaldo si aún no existe la tabla del punto 5):
    p(e) = 1 - prod_a (1 - p_a), con p_a según el nivel de la amenaza."""
    return {e: 1.0 - math.prod(1.0 - p for p in ev.values()) for e, ev in eventos.items()}


# ------------------------------------------------------------------------------
# Construcción de salidas: regla del tramo pendiente más cercano + batería
# ------------------------------------------------------------------------------
def ordenar_tareas(M, ib, U, V, Lt, capacidad):
    """Arma las salidas de un dron (T1 y T3).

    Desde su posición, el dron inspecciona la tarea pendiente más cercana que
    todavía le permite volver a la base; si ninguna cabe, vuelve y empieza otra
    salida (regla de construcción de Zhong et al., 2023).
    Devuelve (salidas, inalcanzables)."""
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
    """Costo (metros, o metros esperados con costo resiliente) de una salida."""
    if not seq:
        return 0.0
    c = M[ib, seq[0][1]] + M[seq[-1][2], ib]
    for (_, _, s), (_, e2, _) in zip(seq, seq[1:]):
        c += M[s, e2]
    return float(c + sum(Lt[t] for t, _, _ in seq))


def dos_opt(M, ib, seq, max_pasadas=20):
    """Mejora 2-opt de una salida: invierte tramos de la secuencia (y el sentido
    de recorrido de cada tarea invertida) si eso acorta los traslados.
    Supone costos simétricos (la red se carga no dirigida). Como el costo de la
    salida nunca aumenta, la salida sigue cabiendo en la batería."""
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


def constructiva(M, ib, U, V, Lt, capacidad):
    """T1 / T3: regla del tramo más cercano con batería y 2-opt por salida."""
    orden, fuera = ordenar_tareas(M, ib, U, V, Lt, capacidad)
    finito = np.isfinite(M)
    Mf = np.where(finito, M, 0)
    if np.allclose(Mf, Mf.T, rtol=1e-6, atol=1e-3):
        orden = [dos_opt(M, ib, seq) for seq in orden]
    return orden, fuera


# ------------------------------------------------------------------------------
# Función objetivo común
# ------------------------------------------------------------------------------
def minutos_sector(M, ib, salidas, Lt, velocidad_ms, recarga_min):
    """Tiempo del dron en el sector: vuelo (costo / velocidad) + cambio de
    batería entre salidas consecutivas. Es la función objetivo de T2 y T4, y
    con ella se comparan las cuatro técnicas."""
    vuelo = sum(costo_salida(M, ib, s, Lt) for s in salidas) / velocidad_ms / 60
    return vuelo + recarga_min * max(0, len(salidas) - 1)


# ------------------------------------------------------------------------------
# Split óptimo (Prins, 2004) con sentido de recorrido óptimo
# ------------------------------------------------------------------------------
def split(perm, M, ib, U, V, Lt, capacidad, conv, recarga):
    """Divide una secuencia de tareas en salidas de forma ÓPTIMA.

    Dada la secuencia perm (orden de visita), elige dónde cortar en salidas y
    el sentido de cada calle para minimizar
        sum_salidas costo * conv + recarga * (salidas - 1),
    con costo de cada salida <= capacidad. Es el procedimiento Split de Prins
    (2004): camino mínimo sobre un grafo acíclico cuyos arcos (i, j) son las
    salidas que cubren perm[i..j]. Dentro de cada salida, el sentido de las
    calles se elige con programación dinámica de dos estados (entra por U o
    por V), exacta para una secuencia fija.

    Como M cumple la desigualdad triangular, el costo de cerrar una salida no
    disminuye al agregarle tareas, y la búsqueda de j se corta en cuanto deja
    de caber en la batería.

    Devuelve (valor, salidas) o (inf, None) si alguna tarea no cabe sola.
    M es una lista de listas (más rápida que numpy para acceso escalar).
    """
    n = len(perm)
    INF = float("inf")
    mejor = [0.0] + [INF] * n
    previo = [-1] * (n + 1)
    Mb = M[ib]
    for i in range(n):
        base_i = mejor[i]
        if base_i == INF:
            continue
        t = perm[i]
        u, v, lt = U[t], V[t], Lt[t]
        g0 = Mb[u] + lt          # entra por U, sale por V
        g1 = Mb[v] + lt          # entra por V, sale por U
        j = i
        while True:
            t = perm[j]
            u, v = U[t], V[t]
            cierre = min(g0 + M[v][ib], g1 + M[u][ib])
            if cierre > capacidad:
                break
            val = base_i + cierre * conv + recarga
            if val < mejor[j + 1]:
                mejor[j + 1] = val
                previo[j + 1] = i
            if j + 1 == n:
                break
            nt = perm[j + 1]
            un, vn, ltn = U[nt], V[nt], Lt[nt]
            Mv, Mu = M[v], M[u]
            n0 = min(g0 + Mv[un], g1 + Mu[un]) + ltn
            n1 = min(g0 + Mv[vn], g1 + Mu[vn]) + ltn
            g0, g1 = n0, n1
            j += 1
    if mejor[n] == INF:
        return INF, None
    cortes, k = [], n
    while k > 0:
        cortes.append((previo[k], k))
        k = previo[k]
    salidas = [_orientar(perm[a:b], M, ib, U, V, Lt) for a, b in reversed(cortes)]
    return mejor[n] - recarga, salidas


def _orientar(tareas, M, ib, U, V, Lt):
    """Sentido óptimo de cada calle de una salida (programación dinámica)."""
    g = [M[ib][U[tareas[0]]] + Lt[tareas[0]], M[ib][V[tareas[0]]] + Lt[tareas[0]]]
    atras = []
    for prev, t in zip(tareas, tareas[1:]):
        sal = (V[prev], U[prev])       # vértice de salida según el sentido 0 / 1
        ent = (U[t], V[t])             # vértice de entrada según el sentido 0 / 1
        nuevo, punteros = [], []
        for o in (0, 1):
            c0, c1 = g[0] + M[sal[0]][ent[o]], g[1] + M[sal[1]][ent[o]]
            punteros.append(0 if c0 <= c1 else 1)
            nuevo.append(min(c0, c1) + Lt[t])
        g = nuevo
        atras.append(punteros)
    ultimo = tareas[-1]
    o = 0 if g[0] + M[V[ultimo]][ib] <= g[1] + M[U[ultimo]][ib] else 1
    sentidos = [o]
    for punteros in reversed(atras):
        o = punteros[o]
        sentidos.append(o)
    sentidos.reverse()
    return [(t, U[t], V[t]) if o == 0 else (t, V[t], U[t]) for t, o in zip(tareas, sentidos)]


def factible(M, ib, salidas, Lt, capacidad, tol=1e-6):
    """Comprueba que cada salida cabe en la batería."""
    return all(costo_salida(M, ib, s, Lt) <= capacidad + tol for s in salidas)
