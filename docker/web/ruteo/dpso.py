"""
T4 · Metaheurística DA-DPSO (PSO discreta con adaptación dinámica).

Adaptación del prototipo del grupo (Tarea3/da_dpso_entrega) al planificador:

Representación
    Cada partícula es una permutación de las calles del sector (orden de
    visita). El decodificador split (ruteo/tecnicas.py, Prins 2004) la
    convierte, de forma óptima para esa permutación, en salidas que respetan la
    batería y elige el sentido de cada calle. Así toda partícula es factible y
    la búsqueda se concentra en el orden, que es la parte combinatoria difícil.

Movimiento (PSO discreta de Pan, Tasgetiren y Liang, 2008)
    En PSO continua la nueva posición combina inercia, memoria propia y memoria
    del enjambre. En la versión discreta cada término es un operador:
        X <- c2 (x) CR( c1 (x) CR( w (x) MUT(X), P_i ), G )
    MUT: mutación (intercambio, inserción o inversión de un tramo), aplicada
         con probabilidad w (inercia / exploración).
    CR:  cruce de orden (OX, Davis 1985) con la mejor posición propia P_i
         (probabilidad c1, componente cognitiva) y con la mejor del enjambre G
         (probabilidad c2, componente social). OX conserva el orden relativo de
         las calles del guía, que es la información útil en un recorrido.

Adaptación dinámica ("DA")
    w no es fijo: aumenta cuando el enjambre pierde diversidad o se estanca,
    como en el PSO guiado por diversidad de Riget y Vesterstrøm (2002):
        w = clip(W_MIN + 0,45 (1 - diversidad) + 0,15 min(1, estancamiento / 25), W_MIN, W_MAX)
    diversidad = fracción media de posiciones en que cada partícula difiere de G.

Búsqueda local (componente memética)
    Las soluciones prometedoras se decodifican, se mejoran y se vuelven a
    codificar concatenando las salidas: la permutación hereda la mejora
    (aprendizaje lamarckiano, esquema de Prins 2004 para algoritmos evolutivos
    con split). Movimientos, con primera mejora: invertir el sentido de una
    calle, reubicar una calle, mover cadenas de 2 o 3 calles consecutivas
    (or-opt), intercambiar calles entre salidas y 2-opt dentro de cada salida.
    Se aplica al líder inicial (T3) y a cada mejor personal que quede a menos
    de UMBRAL_LOCAL del líder (búsqueda local selectiva, para no gastarla en
    partículas malas).

Reinicios (búsqueda local iterada)
    Tras ESTANCAMIENTO_REINICIO iteraciones sin mejorar, cada partícula se
    reemplaza por una perturbación fuerte del líder (n/5 mutaciones) seguida de
    búsqueda local, como en la búsqueda local iterada de Lourenço, Martin y
    Stützle (2003). La búsqueda termina por tiempo o tras REINICIOS_MAX
    reinicios seguidos sin mejora.

Inicialización
    Una partícula parte de la solución constructiva T3 y el resto al azar,
    igual que Pan et al. (2008) siembran el enjambre con la heurística NEH.
    Como G solo cambia si mejora, T4 nunca es peor que T3.

Función objetivo
    Tiempo del dron en el sector (vuelo con costo resiliente + cambio de
    batería entre salidas), la misma de T2. El prototipo usaba un orden
    lexicográfico (pendientes, exposición al viento, makespan...); aquí el
    riesgo ya está dentro del costo resiliente c(e) = largo / (1 - p(e)).

Cambios respecto del prototipo (problemas detectados en la revisión)
    - Distancias precalculadas con pgr_dijkstraCostMatrix (el prototipo
      corría Dijkstra en cada decodificación: ~0,16 s por partícula).
    - Aristas identificadas por su id de la red (el DiGraph del prototipo
      perdía las aristas paralelas).
    - Split óptimo en vez del prefijo factible voraz, que además marcaba
      como pendientes todas las tareas siguientes a una infactible.
"""

import random
import time

from .tecnicas import split

POBLACION = 30          # rango habitual 20-50; la calibración (README C.6) muestra poca sensibilidad
W_MIN, W_MAX = 0.15, 0.70
C1, C2 = 0.5, 0.5       # simetría cognitiva / social (c1 = c2 del PSO clásico); calibrado en README C.6
ESTANCAMIENTO_REINICIO = 50  # iteraciones sin mejora antes de regenerar el enjambre
REINICIOS_MAX = 3            # reinicios seguidos sin mejora para terminar antes del límite de tiempo
UMBRAL_LOCAL = 0.02          # búsqueda local a los mejores personales a menos de 2 % del líder
ITERACIONES_MAX = 5000
SEMILLA = 7             # reproducibilidad


def _mutar(perm, rng):
    p = list(perm)
    n = len(p)
    if n < 2:
        return p
    i, j = rng.randrange(n), rng.randrange(n)
    op = rng.randrange(3)
    if op == 0:
        p[i], p[j] = p[j], p[i]
    elif op == 1:
        p.insert(j, p.pop(i))
    else:
        a, b = min(i, j), max(i, j)
        p[a:b + 1] = reversed(p[a:b + 1])
    return p


def _cruce_ox(perm, guia, rng):
    """Cruce de orden: copia un tramo del guía en las mismas posiciones y
    completa con las calles restantes en el orden de la partícula."""
    n = len(perm)
    if n < 2:
        return list(perm)
    a, b = sorted((rng.randrange(n), rng.randrange(n)))
    tramo = guia[a:b + 1]
    usadas = set(tramo)
    resto = [t for t in perm if t not in usadas]
    return resto[:a] + tramo + resto[a:]


def busqueda_local(salidas, M, ib, U, V, Lt, capacidad, conv, recarga, max_pasadas=20):
    """Mejora una solución decodificada (Prins, 2004). Primera mejora con:
      - invertir el sentido de una calle en su lugar,
      - reubicar una calle en la mejor posición y sentido de cualquier salida
        (si una salida queda vacía se ahorra además un cambio de batería),
      - intercambiar dos calles de salidas distintas,
      - 2-opt dentro de cada salida.
    Toda salida resultante cabe en la batería. Cada movimiento se evalúa en O(1)
    con la diferencia de los arcos que cambian.
    Devuelve las salidas mejoradas como listas de (t, entrada, salida)."""
    rutas = [list(s) for s in salidas if s]
    eps = 1e-6

    def costo(seq):
        c, prev = 0.0, ib
        for t, e, s in seq:
            c += M[prev][e] + Lt[t]
            prev = s
        return c + M[prev][ib]

    def vecinos(seq, i):
        return (ib if i == 0 else seq[i - 1][2]), (ib if i == len(seq) - 1 else seq[i + 1][1])

    cargas = [costo(s) for s in rutas]
    for _ in range(max_pasadas):
        mejoro = False
        # invertir el sentido en su lugar
        for r, seq in enumerate(rutas):
            for i, (t, e, s) in enumerate(seq):
                p, q = vecinos(seq, i)
                if M[p][s] + M[e][q] < M[p][e] + M[s][q] - eps:
                    seq[i] = (t, s, e)
                    mejoro = True
            cargas[r] = costo(seq)
        # reubicar
        for r in range(len(rutas)):
            i = 0
            while i < len(rutas[r]):
                t, e, s = rutas[r][i]
                prev, sig = vecinos(rutas[r], i)
                ahorro = M[prev][e] + Lt[t] + M[s][sig] - M[prev][sig]
                bono = recarga / conv if len(rutas[r]) == 1 else 0.0
                mejor = (eps, None)
                for r2, seq in enumerate(rutas):
                    for k in range(len(seq) + 1):
                        if r2 == r and k in (i, i + 1):
                            continue
                        p = ib if k == 0 else seq[k - 1][2]
                        q = ib if k == len(seq) else seq[k][1]
                        for ent, sal in ((U[t], V[t]), (V[t], U[t])):
                            extra = M[p][ent] + Lt[t] + M[sal][q] - M[p][q]
                            if cargas[r2] + extra - (ahorro if r2 == r else 0.0) > capacidad + 1e-9:
                                continue
                            if ahorro + bono - extra > mejor[0]:
                                mejor = (ahorro + bono - extra, (r2, k, ent, sal))
                if mejor[1] is None:
                    i += 1
                    continue
                r2, k, ent, sal = mejor[1]
                del rutas[r][i]
                if r2 == r and k > i:
                    k -= 1
                rutas[r2].insert(k, (t, ent, sal))
                cargas[r], cargas[r2] = costo(rutas[r]), costo(rutas[r2])
                mejoro = True
        for r in reversed([r for r in range(len(rutas)) if not rutas[r]]):
            del rutas[r], cargas[r]
        # or-opt: mover cadenas de 2 o 3 calles consecutivas, en cualquier sentido
        for largo in (2, 3):
            for r in range(len(rutas)):
                i = 0
                while i + largo <= len(rutas[r]):
                    seq = rutas[r]
                    cad = seq[i:i + largo]
                    prev = ib if i == 0 else seq[i - 1][2]
                    sig = ib if i + largo == len(seq) else seq[i + largo][1]
                    interno = sum(Lt[t] for t, _, _ in cad) + sum(M[cad[k][2]][cad[k + 1][1]] for k in range(largo - 1))
                    ahorro = M[prev][cad[0][1]] + interno + M[cad[-1][2]][sig] - M[prev][sig]
                    bono = recarga / conv if len(seq) == largo else 0.0
                    mejor = (eps, None)
                    for r2, otra in enumerate(rutas):
                        for k in range(len(otra) + 1):
                            if r2 == r and i <= k <= i + largo:
                                continue
                            p = ib if k == 0 else otra[k - 1][2]
                            q = ib if k == len(otra) else otra[k][1]
                            for invertir in (False, True):
                                ent, sal = (cad[-1][2], cad[0][1]) if invertir else (cad[0][1], cad[-1][2])
                                extra = M[p][ent] + interno + M[sal][q] - M[p][q]
                                if cargas[r2] + extra - (ahorro if r2 == r else 0.0) > capacidad + 1e-9:
                                    continue
                                if ahorro + bono - extra > mejor[0]:
                                    mejor = (ahorro + bono - extra, (r2, k, invertir))
                    if mejor[1] is None:
                        i += 1
                        continue
                    r2, k, invertir = mejor[1]
                    del seq[i:i + largo]
                    if r2 == r and k > i:
                        k -= largo
                    nueva = [(t, x, e) for t, e, x in reversed(cad)] if invertir else cad
                    rutas[r2][k:k] = nueva
                    cargas[r], cargas[r2] = costo(rutas[r]), costo(rutas[r2])
                    mejoro = True
            for r in reversed([r for r in range(len(rutas)) if not rutas[r]]):
                del rutas[r], cargas[r]
        # intercambiar calles entre salidas distintas
        for r1 in range(len(rutas)):
            for r2 in range(r1 + 1, len(rutas)):
                a = 0
                while a < len(rutas[r1]):
                    s1 = rutas[r1]
                    t1, e1, x1 = s1[a]
                    p1, q1 = vecinos(s1, a)
                    hecho = False
                    for b in range(len(rutas[r2])):
                        s2 = rutas[r2]
                        t2, e2, x2 = s2[b]
                        p2, q2 = vecinos(s2, b)
                        quita1 = M[p1][e1] + Lt[t1] + M[x1][q1]
                        quita2 = M[p2][e2] + Lt[t2] + M[x2][q2]
                        pone1 = min(M[p1][U[t2]] + M[V[t2]][q1], M[p1][V[t2]] + M[U[t2]][q1]) + Lt[t2]
                        pone2 = min(M[p2][U[t1]] + M[V[t1]][q2], M[p2][V[t1]] + M[U[t1]][q2]) + Lt[t1]
                        d1, d2 = pone1 - quita1, pone2 - quita2
                        if d1 + d2 < -eps and cargas[r1] + d1 <= capacidad + 1e-9 and cargas[r2] + d2 <= capacidad + 1e-9:
                            o1 = (t2, U[t2], V[t2]) if M[p1][U[t2]] + M[V[t2]][q1] <= M[p1][V[t2]] + M[U[t2]][q1] \
                                else (t2, V[t2], U[t2])
                            o2 = (t1, U[t1], V[t1]) if M[p2][U[t1]] + M[V[t1]][q2] <= M[p2][V[t1]] + M[U[t1]][q2] \
                                else (t1, V[t1], U[t1])
                            s1[a], s2[b] = o1, o2
                            cargas[r1], cargas[r2] = costo(s1), costo(s2)
                            mejoro = hecho = True
                            break
                    if not hecho:
                        a += 1
        # 2-opt dentro de cada salida
        for r, seq in enumerate(rutas):
            n = len(seq)
            for i in range(n - 1):
                for j in range(i + 1, n):
                    prev = ib if i == 0 else seq[i - 1][2]
                    sig = ib if j == n - 1 else seq[j + 1][1]
                    if M[prev][seq[j][2]] + M[seq[i][1]][sig] < M[prev][seq[i][1]] + M[seq[j][2]][sig] - eps:
                        seq[i:j + 1] = [(t, s, e) for t, e, s in reversed(seq[i:j + 1])]
                        mejoro = True
            cargas[r] = costo(seq)
        if not mejoro:
            break
    return rutas


def _perturbar(perm, rng, movimientos):
    p = list(perm)
    for _ in range(movimientos):
        p = _mutar(p, rng)
    return p


def _diversidad(enjambre, lider):
    n = len(lider)
    if n == 0:
        return 0.0
    return sum(sum(1 for x, y in zip(p, lider) if x != y) for p in enjambre) / (len(enjambre) * n)


def resolver(M, ib, U, V, Lt, capacidad, conv, recarga, semilla_perm=None,
             limite_s=10.0, poblacion=POBLACION, semilla=SEMILLA, c1=C1, c2=C2,
             w_fijo=None, local=True, reinicios=True):
    """DA-DPSO sobre un sector.

    M: lista de listas de costos; U, V, Lt: listas por tarea; conv: minutos por
    unidad de costo (1 / (60 v)); recarga: minutos entre salidas.
    semilla_perm: permutación inicial (orden de T3).
    c1, c2, w_fijo (inercia constante en vez de adaptativa), local (búsqueda
    local) y reinicios solo se cambian en la calibración (README del ruteo, sección C.6).
    Devuelve dict con valor, salidas y estadísticas de la búsqueda."""
    t_ini = time.perf_counter()
    rng = random.Random(semilla)
    n = len(U)
    tareas = list(range(n))
    cache = {}
    stats = {"busquedas_locales": 0, "reinicios": 0}

    def evaluar(perm):
        clave = tuple(perm)
        if clave not in cache:
            cache[clave] = split(perm, M, ib, U, V, Lt, capacidad, conv, recarga)
        return cache[clave]

    def mejorar(perm):
        """Lamarckiano: decodifica, aplica búsqueda local y vuelve a codificar
        concatenando las salidas (la permutación hereda la mejora)."""
        if not local:
            return list(perm), evaluar(perm)[0]
        stats["busquedas_locales"] += 1
        sal = busqueda_local(evaluar(perm)[1], M, ib, U, V, Lt, capacidad, conv, recarga)
        nueva = [t for seq in sal for t, _, _ in seq]
        return nueva, evaluar(nueva)[0]

    enjambre = [list(semilla_perm)] if semilla_perm else []
    while len(enjambre) < poblacion:
        p = tareas[:]
        rng.shuffle(p)
        enjambre.append(p)
    v_inicial = evaluar(enjambre[0])[0]
    mejores = [list(p) for p in enjambre]
    v_mejores = [evaluar(p)[0] for p in enjambre]
    g = min(range(poblacion), key=lambda k: v_mejores[k])
    lider, v_lider = mejorar(mejores[g])
    mejores[g], v_mejores[g], enjambre[g] = list(lider), v_lider, list(lider)
    estancamiento, reinicios_sin_mejora, iteracion, w = 0, 0, 0, W_MIN
    historia = [round(v_lider, 3)]

    while (time.perf_counter() - t_ini < limite_s and iteracion < ITERACIONES_MAX
           and reinicios_sin_mejora <= REINICIOS_MAX and n > 1):
        iteracion += 1
        w = w_fijo if w_fijo is not None else min(
            W_MAX, max(W_MIN, W_MIN + 0.45 * (1 - _diversidad(enjambre, lider)) + 0.15 * min(1.0, estancamiento / 25)))
        mejoro = False
        for k in range(poblacion):
            x = enjambre[k]
            if rng.random() < w:
                x = _mutar(x, rng)
            if rng.random() < c1:
                x = _cruce_ox(x, mejores[k], rng)
            if rng.random() < c2:
                x = _cruce_ox(x, lider, rng)
            val = evaluar(x)[0]
            if val < v_mejores[k] - 1e-9:
                # búsqueda local selectiva: solo en mejores personales prometedores
                if val < v_lider * (1 + UMBRAL_LOCAL):
                    x, val = mejorar(x)
                mejores[k], v_mejores[k] = list(x), val
                if val < v_lider - 1e-9:
                    lider, v_lider, mejoro = list(x), val, True
            enjambre[k] = x
        if mejoro:
            estancamiento, reinicios_sin_mejora = 0, 0
        else:
            estancamiento += 1
        if reinicios and estancamiento >= ESTANCAMIENTO_REINICIO:
            # reinicio (búsqueda local iterada): cada partícula pasa a ser una
            # perturbación fuerte del líder seguida de búsqueda local
            mov = max(2, n // 5)
            for k in range(poblacion):
                if time.perf_counter() - t_ini >= limite_s:
                    break
                if mejores[k] == lider:
                    continue
                x, val = mejorar(_perturbar(lider, rng, mov))
                enjambre[k], mejores[k], v_mejores[k] = x, list(x), val
                if val < v_lider - 1e-9:
                    lider, v_lider, reinicios_sin_mejora = list(x), val, -1
            estancamiento = 0
            reinicios_sin_mejora += 1
            stats["reinicios"] += 1
        elif not reinicios and estancamiento >= ESTANCAMIENTO_REINICIO * (REINICIOS_MAX + 1):
            break
        if iteracion % 10 == 0:
            historia.append(round(v_lider, 3))

    valor, salidas = evaluar(lider)
    return {"valor": valor, "salidas": salidas, "iteraciones": iteracion, "evaluaciones": len(cache),
            "valor_inicial": v_inicial, **stats, "w_final": round(w, 3),
            "segundos": time.perf_counter() - t_ini, "historia": historia}
