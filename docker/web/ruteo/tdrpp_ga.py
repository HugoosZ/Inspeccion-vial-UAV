"""
Algoritmo genético para el Time-Dependent Rural Postman Problem
(TDRPP-GA), adaptado a k drones que salen de un inicio y llegan a un objetivo.

Base: Xin, Yu, D'Ariano, Wang y Wang (2022). "Time-dependent rural postman
problem: time-space network formulation and genetic algorithm". Operational
Research 22(3):2943-2972. DOI 10.1007/s12351-021-00639-0.
  - Una solución alterna aristas requeridas y caminos de transición entre ellas.
  - Con tiempos FIFO, los caminos de transición se toman como caminos de costo
    mínimo; el genético solo decide el ORDEN y el SENTIDO de las aristas
    requeridas (propiedad de optimalidad del paper).
Extensiones del equipo:
  - k drones: el cromosoma también reparte las aristas requeridas entre drones
    (cortes sobre la permutación). Cada dron va de inicio a objetivo.
  - Dependencia del tiempo: el costo de una arista depende de la hora a la que
    el dron la cruza (amenazas de clima A2 por hora).
  - Restricción de autonomía (batería) por dron y balance entre drones.
  - Mejora local 2-Opt sobre la mejor solución (Croes, 1958).
  - Arquitectura offline/online: los caminos de transición se calculan una vez
    (offline) y se reutilizan; ante una amenaza nueva se vuelve a ejecutar
    sobre el tramo restante (online).

Función objetivo (minimizar), en km equivalentes:
    F = Σ_i C(P_i) + β·max_i C(P_i) + λ_bat·Σ_i max(0, L(P_i) − L_max) + λ_vac·(#drones sin aristas)
    C(P_i) = Σ_{e∈P_i} w(e)·φ(e, t_e)      con φ = factor horario del clima
"""

import math
import random
import time

import networkx as nx

BETA = 0.5          # peso del dron más cargado (balance / makespan)
LAMBDA_BAT = 5.0    # penalización por km sobre la autonomía
LAMBDA_VACIO = 50.0 # penalización por dron sin aristas requeridas
INFACTIBLE = 1e6


class TDRPPGA:
    def __init__(self, H, inicio, objetivo, requeridas, amenazas, t0_epoch, k,
                 v_crucero, v_inspeccion, autonomia_km, semilla=42):
        self.H, self.s, self.g = H, inicio, objetivo
        self.amen, self.t0, self.k = amenazas, t0_epoch, k
        self.vc, self.vi = v_crucero, v_inspeccion
        self.L_max = autonomia_km * 1000.0
        self.rnd = random.Random(semilla)
        self.avisos = []
        self.req = []
        for r in requeridas:
            u, v = r["source"], r["target"]
            if u in H and v in H:
                factor = H[u][v]["w"] / max(H[u][v]["w0"], 1e-9) if H.has_edge(u, v) else 1.0
                self.req.append({**r, "w": r["length_m"] / 1000.0 * factor})
        self._transiciones()

    # --------------------------------------------------------------------------
    # Fase offline: caminos de transición de costo mínimo entre extremos
    # --------------------------------------------------------------------------
    def _transiciones(self):
        nodos = {self.s, self.g}
        for r in self.req:
            nodos.update((r["source"], r["target"]))
        self.T = {}
        for a in nodos:
            dist, caminos = nx.single_source_dijkstra(self.H, a, weight="w")
            self.T[a] = {}
            for b in nodos:
                if b not in caminos:
                    continue
                camino = caminos[b]
                largo, temporales, recorrido = 0.0, [], 0.0
                for x, y in zip(camino, camino[1:]):
                    datos = self.H[x][y]
                    if datos["id"] in self.amen.temporales:
                        temporales.append((recorrido, datos["id"], datos["w"]))
                    recorrido += datos["d"]
                    largo += datos["d"]
                self.T[a][b] = (dist[b], largo, temporales, camino)
        alcanzables = [r for r in self.req
                       if r["source"] in self.T[self.s] and r["target"] in self.T[self.s]
                       and self.g in self.T.get(r["source"], {})]
        if len(alcanzables) < len(self.req):
            self.avisos.append(f"{len(self.req) - len(alcanzables)} aristas requeridas quedaron "
                               "aisladas por amenazas o restricciones y se omitieron.")
        self.req = alcanzables
        self.m = len(self.req)

    # --------------------------------------------------------------------------
    # Evaluación de una ruta (dependiente del tiempo)
    # --------------------------------------------------------------------------
    def _tramo(self, a, b, t, acumulado):
        tr = self.T.get(a, {}).get(b)
        if tr is None:
            return None
        costo, largo, temporales, _ = tr
        for desplazamiento, eid, w in temporales:
            f = self.amen.factor_en(eid, t + desplazamiento / self.vc)
            costo += w * (f - 1.0)
        acumulado[0] += costo
        acumulado[1] += largo
        return t + largo / self.vc

    def costo_ruta(self, secuencia, dirs):
        """secuencia: índices de aristas requeridas; dirs[i]: True = source→target."""
        acumulado, t, pos = [0.0, 0.0], self.t0, self.s
        for i in secuencia:
            r = self.req[i]
            u, v = (r["source"], r["target"]) if dirs[i] else (r["target"], r["source"])
            t = self._tramo(pos, u, t, acumulado)
            if t is None:
                return INFACTIBLE, 0.0, 0.0
            acumulado[0] += r["w"] * self.amen.factor_en(r["id"], t)
            acumulado[1] += r["length_m"]
            t += r["length_m"] / self.vi
            pos = v
        t = self._tramo(pos, self.g, t, acumulado)
        if t is None:
            return INFACTIBLE, 0.0, 0.0
        return acumulado[0], acumulado[1], t - self.t0

    # --------------------------------------------------------------------------
    # Cromosoma = (permutación, sentidos, cortes)
    # --------------------------------------------------------------------------
    def segmentos(self, ind):
        perm, _, cortes = ind
        limites = [0] + cortes + [self.m]
        return [perm[limites[i]:limites[i + 1]] for i in range(self.k)]

    def aptitud(self, ind):
        costos, total, penal = [], 0.0, 0.0
        for seg in self.segmentos(ind):
            c, largo, _ = self.costo_ruta(seg, ind[1])
            costos.append(c)
            total += c
            penal += LAMBDA_BAT * max(0.0, largo - self.L_max) / 1000.0
            if not seg and self.m >= self.k:
                penal += LAMBDA_VACIO
        return total + BETA * max(costos) + penal

    def _cortes_parejos(self):
        return [round(self.m * (i + 1) / self.k) for i in range(self.k - 1)]

    def _aleatorio(self):
        perm = list(range(self.m))
        self.rnd.shuffle(perm)
        dirs = [self.rnd.random() < 0.5 for _ in range(self.m)]
        cortes = sorted(self.rnd.randint(0, self.m) for _ in range(self.k - 1))
        return (perm, dirs, cortes)

    def _semilla_proyeccion(self):
        """Ordena las aristas por su proyección sobre la recta inicio→objetivo."""
        H = self.H
        sx, sy, gx, gy = H.nodes[self.s]["x"], H.nodes[self.s]["y"], H.nodes[self.g]["x"], H.nodes[self.g]["y"]
        dx, dy = gx - sx, gy - sy
        norma = dx * dx + dy * dy or 1e-12

        def proy(n):
            return ((H.nodes[n]["x"] - sx) * dx + (H.nodes[n]["y"] - sy) * dy) / norma

        perm = sorted(range(self.m), key=lambda i: proy(self.req[i]["source"]) + proy(self.req[i]["target"]))
        dirs = [proy(self.req[i]["source"]) <= proy(self.req[i]["target"]) for i in range(self.m)]
        return (perm, dirs, self._cortes_parejos())

    def _torneo(self, poblacion, aptitudes, tam=3):
        elegidos = self.rnd.sample(range(len(poblacion)), tam)
        return poblacion[min(elegidos, key=lambda i: aptitudes[i])]

    def _cruce_ox(self, p1, p2):
        """Order crossover (OX) sobre la permutación; sentidos uniformes; cortes de un padre."""
        n = self.m
        if n < 2:
            return (list(p1[0]), list(p1[1]), list(p1[2]))
        a, b = sorted(self.rnd.sample(range(n), 2))
        hijo = [None] * n
        hijo[a:b + 1] = p1[0][a:b + 1]
        usados = set(hijo[a:b + 1])
        resto = [x for x in p2[0] if x not in usados]
        j = 0
        for i in range(n):
            if hijo[i] is None:
                hijo[i] = resto[j]
                j += 1
        dirs = [p1[1][i] if self.rnd.random() < 0.5 else p2[1][i] for i in range(n)]
        cortes = list(p1[2] if self.rnd.random() < 0.5 else p2[2])
        return (hijo, dirs, cortes)

    def _mutar(self, ind, prob=0.3):
        perm, dirs, cortes = list(ind[0]), list(ind[1]), list(ind[2])
        n = self.m
        if n >= 2 and self.rnd.random() < prob:                 # intercambio
            i, j = self.rnd.sample(range(n), 2)
            perm[i], perm[j] = perm[j], perm[i]
        if n >= 2 and self.rnd.random() < prob:                 # inversión
            i, j = sorted(self.rnd.sample(range(n), 2))
            perm[i:j + 1] = reversed(perm[i:j + 1])
        if n >= 1 and self.rnd.random() < prob:                 # cambio de sentido
            i = self.rnd.randrange(n)
            dirs[i] = not dirs[i]
        if cortes and self.rnd.random() < prob:                 # mover un corte
            i = self.rnd.randrange(len(cortes))
            cortes[i] = max(0, min(n, cortes[i] + self.rnd.randint(-2, 2)))
            cortes.sort()
        return (perm, dirs, cortes)

    def _dos_opt(self, ind, limite_s):
        """2-Opt dentro de cada dron: invierte subsecuencias y sus sentidos."""
        mejor, f_mejor = ind, self.aptitud(ind)
        fin = time.time() + limite_s
        mejora = True
        while mejora and time.time() < fin:
            mejora = False
            limites = [0] + mejor[2] + [self.m]
            for d in range(self.k):
                a0, b0 = limites[d], limites[d + 1]
                for i in range(a0, b0 - 1):
                    for j in range(i + 1, b0):
                        perm, dirs = list(mejor[0]), list(mejor[1])
                        perm[i:j + 1] = reversed(perm[i:j + 1])
                        for x in perm[i:j + 1]:
                            dirs[x] = not dirs[x]
                        cand = (perm, dirs, list(mejor[2]))
                        f = self.aptitud(cand)
                        if f + 1e-9 < f_mejor:
                            mejor, f_mejor, mejora = cand, f, True
                    if time.time() > fin:
                        break
        return mejor, f_mejor

    # --------------------------------------------------------------------------
    def resolver(self, tam_poblacion=80, generaciones=400, limite_s=8.0, elite=2):
        if self.m == 0:
            return None
        inicio = time.time()
        poblacion = [self._semilla_proyeccion()] + [self._aleatorio() for _ in range(tam_poblacion - 1)]
        aptitudes = [self.aptitud(x) for x in poblacion]
        historia, gen = [], 0
        for gen in range(generaciones):
            orden = sorted(range(len(poblacion)), key=lambda i: aptitudes[i])
            nueva = [poblacion[i] for i in orden[:elite]]
            while len(nueva) < tam_poblacion:
                p1, p2 = self._torneo(poblacion, aptitudes), self._torneo(poblacion, aptitudes)
                hijo = self._cruce_ox(p1, p2) if self.rnd.random() < 0.9 else (list(p1[0]), list(p1[1]), list(p1[2]))
                nueva.append(self._mutar(hijo))
            poblacion = nueva
            aptitudes = [self.aptitud(x) for x in poblacion]
            historia.append(round(min(aptitudes), 4))
            if time.time() - inicio > limite_s * 0.8:
                break
        mejor = poblacion[min(range(len(poblacion)), key=lambda i: aptitudes[i])]
        f_antes = min(aptitudes)
        mejor, f_mejor = self._dos_opt(mejor, max(0.5, limite_s - (time.time() - inicio)))
        return self._salida(mejor, {
            "generaciones": gen + 1, "poblacion": tam_poblacion, "aptitud_ga": round(f_antes, 4),
            "aptitud_final_2opt": round(f_mejor, 4), "segundos": round(time.time() - inicio, 2),
            "convergencia": historia,
        })

    def _salida(self, ind, info):
        rutas = []
        for seg in self.segmentos(ind):
            aristas, pos = [], self.s
            for i in seg:
                r = self.req[i]
                u, v = (r["source"], r["target"]) if ind[1][i] else (r["target"], r["source"])
                aristas += self._aristas_camino(self.T[pos][u][3])
                aristas.append(r["id"])
                pos = v
            aristas += self._aristas_camino(self.T[pos][self.g][3])
            costo, largo, duracion = self.costo_ruta(seg, ind[1])
            rutas.append({"aristas": aristas, "requeridas": [self.req[i]["id"] for i in seg],
                          "costo": round(costo, 4), "longitud_m": round(largo, 1),
                          "duracion_min": round(duracion / 60, 1),
                          "excede_autonomia": largo > self.L_max})
        return {"rutas": rutas, "info": info, "avisos": self.avisos}

    def _aristas_camino(self, camino):
        return [self.H[x][y]["id"] for x, y in zip(camino, camino[1:])]


def construir_grafo_resiliente(G, bbox, amenazas, eliminadas_metadata, factores_metadata):
    """Subgrafo de la región con pesos resilientes estáticos w(e).

    w(e) = d(e)[km] · ρ_amenaza(e) · π_metadata(e); se eliminan las aristas bloqueadas.
    """
    xmin, ymin, xmax, ymax = bbox
    nodos = [n for n, a in G.nodes(data=True) if xmin <= a["x"] <= xmax and ymin <= a["y"] <= ymax]
    H = nx.Graph()
    for n in nodos:
        H.add_node(n, **G.nodes[n])
    conjunto = set(nodos)
    for u in nodos:
        for v, datos in G[u].items():
            if v not in conjunto or H.has_edge(u, v):
                continue
            e = datos["id"]
            if e in amenazas.bloqueadas or e in eliminadas_metadata:
                continue
            w = datos["w0"] * amenazas.factor_estatico.get(e, 1.0) * factores_metadata.get(e, 1.0)
            H.add_edge(u, v, id=e, d=datos["d"], w0=datos["w0"], w=w)
    return H


def distancia_m(a, b):
    """Distancia aproximada en metros entre dos dicts con lon/lat."""
    lat = math.radians((a["lat"] + b["lat"]) / 2)
    dx = (a["lon"] - b["lon"]) * 111_320 * math.cos(lat)
    dy = (a["lat"] - b["lat"]) * 111_320
    return math.hypot(dx, dy)
