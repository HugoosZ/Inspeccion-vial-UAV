"""
T2 · Modelo formal (MILP) del ruteo de un dron en un sector, resuelto con Gurobi.

Problema: desde la base (droneport) el dron debe recorrer todas las calles
requeridas del sector; cada salida parte y termina en la base y no puede
consumir más que la batería disponible; entre salidas se cambia la batería.
Es un problema de ruteo de arcos capacitado con múltiples viajes, que se
transforma en un problema de ruteo de nodos representando cada calle q por
dos copias, una por sentido (q+ : U->V, q- : V->U), como en las
transformaciones CARP -> VRP (Longo, Poggi de Aragão y Uchoa, 2006).

Conjuntos
    Q          calles a inspeccionar del sector
    A          copias orientadas (2 por calle); 0 = la base
    ent(a), sal(a)   vértice de entrada y de salida de la copia a
Parámetros
    c_ij       costo resiliente del camino mínimo (pgr_dijkstraCostMatrix)
               entre la salida de i y la entrada de j (metros esperados)
    L_a        costo resiliente de recorrer la calle de a
    B          batería por salida (metros): v * autonomía * (1 - reserva)
    kappa      minutos por metro = 1 / (60 v)
    tau        minutos de cambio de batería entre salidas
Variables
    x_ij in {0,1}  el dron va de i a j (i, j en A U {0})
    z_a  in {0,1}  la calle de a se recorre en el sentido de a
    g_ij >= 0      energía (metros) ya gastada en la salida al dejar i rumbo a j
Modelo (formulación de flujo de una mercancía, Gavish y Graves, 1978)
    min  kappa (sum_ij c_ij x_ij + sum_a L_a z_a) + tau (sum_a x_0a - 1)
    s.a. z_q+ + z_q- = 1                                    para toda q (cada calle una vez)
         sum_j x_aj = z_a ,  sum_j x_ja = z_a               para toda a (flujo)
         sum_k g_bk = sum_a g_ab + sum_a c_ab x_ab + L_b z_b para toda b (balance de energía)
         g_0b = 0                                           (la salida empieza con la batería llena)
         (c_0a + L_a) x_ab <= g_ab <= (B - c_ab - L_b - c_b0) x_ab   para todo arco a->b
         g_a0 <= (B - c_a0) x_a0                            (alcanza para volver)
         sum_a x_0a >= ceil(sum_q L_q / B)                  (desigualdad válida)
El número de salidas, sum_a x_0a, es una variable: el modelo decide cuántas
recargas hacer. El balance impide ciclos que no pasen por la base, porque la
energía crece estrictamente a lo largo de una salida (L_b > 0).

Alternativa MTZ (FORMULACION = "mtz"): f_b >= f_a + c_ab + L_b - M_ab (1 - x_ab)
con f_a la energía al terminar a. Tiene menos variables, pero su relajación
lineal es mucho más débil: en las pruebas (README del ruteo) el modelo de flujo
demuestra el óptimo de 6 a 8 calles en 1 a 25 s, mientras MTZ no lo logra en
60 s con 7 calles.

Reducción exacta (no cambia el óptimo): se eliminan las copias que no caben
solas en una salida y los arcos a->b con c_0a + L_a + c_ab + L_b > B - c_b0.

Arranque en caliente: la solución de T3 se entrega como solución inicial, así
Gurobi parte de una cota superior y nunca devuelve algo peor que T3. Con
límite de tiempo, se informa la brecha (gap) certificada entre la mejor
solución y la cota inferior.

Solver: Gurobi (licencia WLS académica: WLSACCESSID, WLSSECRET y LICENSEID en
el .env). Si gurobipy o la licencia no están disponibles se usa HiGHS (vía
scipy) como respaldo, y el resultado lo indica.
"""

import math
import os
import time

import numpy as np
from scipy import sparse

_ENV = None
_ENV_ERROR = None
FORMULACION = "flujo"   # "flujo" (Gavish y Graves) o "mtz"; ver pruebas en el README


def _entorno_gurobi():
    """Entorno de Gurobi con la licencia WLS del .env (se crea una sola vez)."""
    global _ENV, _ENV_ERROR
    if _ENV is not None or _ENV_ERROR is not None:
        return _ENV
    try:
        import gurobipy as gp
        params = {"OutputFlag": 0}
        if os.getenv("WLSACCESSID") and os.getenv("WLSSECRET") and os.getenv("LICENSEID"):
            params.update({"WLSACCESSID": os.getenv("WLSACCESSID").strip(),
                           "WLSSECRET": os.getenv("WLSSECRET").strip(),
                           "LICENSEID": int(os.getenv("LICENSEID").strip())})
        _ENV = gp.Env(params=params)
    except Exception as e:  # noqa: BLE001
        _ENV_ERROR = f"{type(e).__name__}: {e}"
    return _ENV


def estado_solver():
    """Qué solver se usará y por qué (para la API y la leyenda)."""
    env = _entorno_gurobi()
    if env is not None:
        return {"solver": "Gurobi", "detalle": "licencia WLS" if os.getenv("WLSACCESSID") else "licencia local"}
    return {"solver": "HiGHS", "detalle": f"Gurobi no disponible ({_ENV_ERROR})"}


# ------------------------------------------------------------------------------
def construir(M, ib, U, V, Lt, capacidad, conv, recarga, formulacion=None):
    """Arma el modelo en forma matricial: min c x  s.a.  bl <= A x <= bu."""
    formulacion = formulacion or FORMULACION
    n = len(U)
    ent = np.empty(2 * n, int)
    sal = np.empty(2 * n, int)
    L = np.empty(2 * n)
    ent[0::2], sal[0::2] = U, V
    ent[1::2], sal[1::2] = V, U
    L[0::2] = L[1::2] = Lt
    lb = M[ib, ent] + L                       # energía mínima al terminar la copia
    ub = capacidad - M[sal, ib]               # energía máxima para poder volver
    ok = lb <= ub + 1e-9

    arcos = [("D", a) for a in range(2 * n) if ok[a]] + [(a, "D") for a in range(2 * n) if ok[a]]
    copias = np.where(ok)[0]
    if len(copias):
        C = M[np.ix_(sal[copias], ent[copias])]                       # c_ab
        posible = lb[copias][:, None] + C + L[copias][None, :] <= ub[copias][None, :] + 1e-9
        misma = (copias[:, None] // 2) == (copias[None, :] // 2)
        ii, jj = np.where(posible & ~misma)
        arcos += list(zip(copias[ii].tolist(), copias[jj].tolist()))
    na = len(arcos)
    iz = na                 # z_a
    i_f = na + 2 * n        # f_a (MTZ) o g_ab (flujo)
    nv = na + 4 * n if formulacion == "mtz" else na + 2 * n + na

    costo = np.zeros(nv)
    for k, (a, b) in enumerate(arcos):
        if a == "D":
            costo[k] = M[ib, ent[b]] * conv + recarga
        elif b == "D":
            costo[k] = M[sal[a], ib] * conv
        else:
            costo[k] = M[sal[a], ent[b]] * conv
    costo[iz:iz + 2 * n] = L * conv

    filas, cols, vals, bl, bu = [], [], [], [], []
    r = 0

    def fila(coefs, inf, sup):
        nonlocal r
        for c, v in coefs:
            filas.append(r); cols.append(c); vals.append(v)
        bl.append(inf); bu.append(sup)
        r += 1

    for q in range(n):                                   # cada calle una vez
        fila([(iz + 2 * q, 1.0), (iz + 2 * q + 1, 1.0)], 1.0, 1.0)
    salen = [[] for _ in range(2 * n)]
    entran = [[] for _ in range(2 * n)]
    for k, (a, b) in enumerate(arcos):
        if a != "D":
            salen[a].append(k)
        if b != "D":
            entran[b].append(k)
    for a in range(2 * n):                               # flujo
        fila([(k, 1.0) for k in salen[a]] + [(iz + a, -1.0)], 0.0, 0.0)
        fila([(k, 1.0) for k in entran[a]] + [(iz + a, -1.0)], 0.0, 0.0)
    if formulacion == "mtz":
        for k, (a, b) in enumerate(arcos):               # energía (MTZ)
            if a == "D" or b == "D":
                continue
            cab = M[sal[a], ent[b]]
            big = ub[a] + cab + L[b] - lb[b]
            # f_b - f_a - big x_ab >= cab + L_b - big
            fila([(i_f + b, 1.0), (i_f + a, -1.0), (k, -big)], cab + L[b] - big, np.inf)
    else:
        # Flujo de energía de una mercancía (Gavish y Graves, 1978): g_ab es la
        # energía ya gastada al dejar a rumbo a b. En cada copia b:
        #   sum_k g_bk = sum_a g_ab + sum_a c_ab x_ab + L_b z_b   (g_0b = 0)
        # y en cada arco: (c_0a + L_a) x_ab <= g_ab <= (B - c_ab - L_b - c_b0) x_ab
        for b_ in range(2 * n):
            coefs = [(i_f + k, 1.0) for k in salen[b_]]
            coefs += [(i_f + k, -1.0) for k in entran[b_] if arcos[k][0] != "D"]
            for k in entran[b_]:
                a = arcos[k][0]
                coefs.append((k, -(M[ib, ent[b_]] if a == "D" else M[sal[a], ent[b_]])))
            coefs.append((iz + b_, -L[b_]))
            fila(coefs, 0.0, 0.0)
        for k, (a, b_) in enumerate(arcos):
            if a == "D":
                fila([(i_f + k, 1.0)], 0.0, 0.0)
                continue
            fila([(i_f + k, 1.0), (k, -lb[a])], 0.0, np.inf)
            tope = ub[a] if b_ == "D" else capacidad - M[sal[a], ent[b_]] - L[b_] - M[sal[b_], ib]
            fila([(i_f + k, 1.0), (k, -tope)], -np.inf, 0.0)
    k_min = math.ceil(float(Lt.sum()) / capacidad - 1e-9) if n else 0
    fila([(k, 1.0) for k, (a, _) in enumerate(arcos) if a == "D"], max(1, k_min), np.inf)

    A = sparse.csr_matrix((vals, (filas, cols)), shape=(r, nv))
    lo = np.zeros(nv)
    hi = np.ones(nv)
    if formulacion == "mtz":
        lo[i_f:] = np.where(ok, lb, 0.0)
        hi[i_f:] = np.where(ok, ub, capacidad)
    else:
        hi[i_f:] = capacidad
    hi[iz:iz + 2 * n] = np.where(ok, 1.0, 0.0)
    entero = np.ones(nv, bool)
    entero[i_f:] = False
    return {"A": A, "bl": np.array(bl), "bu": np.array(bu), "c": costo, "const": -recarga,
            "lo": lo, "hi": hi, "entero": entero, "arcos": arcos, "ent": ent, "sal": sal,
            "L": L, "n": n, "iz": iz, "if": i_f, "nv": nv, "formulacion": formulacion}


def _vector_inicial(mod, salidas, M, ib):
    """Solución de T3 expresada en las variables del modelo."""
    x = np.zeros(mod["nv"])
    indice = {arco: k for k, arco in enumerate(mod["arcos"])}
    for seq in salidas:
        copias = [2 * t + (0 if mod["ent"][2 * t] == e else 1) for t, e, _ in seq]
        energia = 0.0
        previo = "D"
        for a in copias:
            k = indice.get((previo, a))
            if k is None:
                return None
            x[k] = 1
            x[mod["iz"] + a] = 1
            if mod["formulacion"] == "flujo":
                x[mod["if"] + k] = energia          # energía al dejar el nodo anterior
            energia += (M[ib, mod["ent"][a]] if previo == "D" else M[mod["sal"][previo], mod["ent"][a]]) + mod["L"][a]
            if mod["formulacion"] == "mtz":
                x[mod["if"] + a] = energia
            previo = a
        k = indice.get((previo, "D"))
        if k is None:
            return None
        x[k] = 1
        if mod["formulacion"] == "flujo":
            x[mod["if"] + k] = energia
    if mod["formulacion"] == "mtz":
        # las copias no usadas quedan con f en su cota inferior
        no_usadas = np.where(x[mod["iz"]:mod["iz"] + 2 * mod["n"]] == 0)[0]
        x[mod["if"] + no_usadas] = mod["lo"][mod["if"] + no_usadas]
    return x


def _salidas(mod, x):
    """Reconstruye las salidas a partir de los x_ij = 1."""
    siguiente, inicios = {}, []
    for k, (a, b) in enumerate(mod["arcos"]):
        if x[k] > 0.5:
            if a == "D":
                inicios.append(b)
            else:
                siguiente[a] = b
    salidas = []
    for a in inicios:
        seq, pasos = [], 0
        while a != "D" and pasos <= 2 * mod["n"]:
            seq.append((a // 2, int(mod["ent"][a]), int(mod["sal"][a])))
            a = siguiente.get(a, "D")
            pasos += 1
        salidas.append(seq)
    return salidas


def resolver(M, ib, U, V, Lt, capacidad, conv, recarga, inicio=None, limite_s=60.0, gap=1e-4, formulacion=None):
    """Resuelve el MILP de un sector.

    inicio: salidas de T3 para el arranque en caliente.
    Devuelve dict con salidas, valor, cota, gap, estado, solver y tiempos."""
    t0 = time.perf_counter()
    U, V, Lt = np.asarray(U), np.asarray(V), np.asarray(Lt, float)
    mod = construir(M, ib, U, V, Lt, capacidad, conv, recarga, formulacion)
    t_modelo = time.perf_counter() - t0
    x0 = _vector_inicial(mod, inicio, M, ib) if inicio else None
    info = {"formulacion": mod["formulacion"], "variables": mod["nv"], "binarias": int(mod["entero"].sum()),
            "restricciones": int(mod["A"].shape[0]), "segundos_modelo": round(t_modelo, 2)}

    env = _entorno_gurobi()
    if env is not None:
        try:
            return _resolver_gurobi(env, mod, x0, info, limite_s, gap)
        except Exception as e:  # noqa: BLE001  (p. ej., licencia restringida para modelos grandes)
            info["nota"] = f"Gurobi no pudo resolver ({type(e).__name__}: {e}); se usó HiGHS."
    return _resolver_highs(mod, x0, info, inicio, limite_s, gap, t0, t_modelo)


def _resolver_gurobi(env, mod, x0, info, limite_s, gap):
    import gurobipy as gp
    GRB = gp.GRB
    m = gp.Model("ruteo_sector", env=env)
    try:
        vtype = np.where(mod["entero"], GRB.BINARY, GRB.CONTINUOUS)
        x = m.addMVar(mod["nv"], lb=mod["lo"], ub=mod["hi"], vtype=vtype)
        m.setObjective(mod["c"] @ x + mod["const"], GRB.MINIMIZE)
        A = mod["A"]
        finito_inf, finito_sup = np.isfinite(mod["bl"]), np.isfinite(mod["bu"])
        igual = finito_inf & finito_sup & (mod["bl"] == mod["bu"])
        mayor = finito_inf & ~igual
        menor = finito_sup & ~igual
        if igual.any():
            m.addConstr(A[igual] @ x == mod["bl"][igual])
        if mayor.any():
            m.addConstr(A[mayor] @ x >= mod["bl"][mayor])
        if menor.any():
            m.addConstr(A[menor] @ x <= mod["bu"][menor])
        m.Params.TimeLimit = max(1.0, limite_s)
        m.Params.MIPGap = gap
        if x0 is not None:
            x.Start = x0
        m.optimize()
        estados = {GRB.OPTIMAL: "óptimo", GRB.TIME_LIMIT: "límite de tiempo",
                   GRB.INFEASIBLE: "infactible", GRB.INTERRUPTED: "interrumpido"}
        res = {**info, "solver": "Gurobi", "estado": estados.get(m.Status, f"estado {m.Status}"),
               "segundos_solver": round(m.Runtime, 2)}
        if m.SolCount:
            res.update(valor=float(m.ObjVal), cota=float(m.ObjBound), gap=float(m.MIPGap),
                       salidas=_salidas(mod, x.X), nodos_bb=int(m.NodeCount))
        else:
            res.update(valor=None, cota=float(m.ObjBound) if m.Status != GRB.INFEASIBLE else None,
                       gap=None, salidas=None)
        return res
    finally:
        m.dispose()


def _resolver_highs(mod, x0, info, inicio, limite_s, gap, t0, t_modelo):
    """Respaldo: HiGHS (scipy). No admite solución inicial."""
    from scipy.optimize import Bounds, LinearConstraint, milp
    r = milp(mod["c"], integrality=mod["entero"].astype(int),
             bounds=Bounds(mod["lo"], mod["hi"]),
             constraints=LinearConstraint(mod["A"], mod["bl"], mod["bu"]),
             options={"time_limit": max(1.0, limite_s), "mip_rel_gap": gap, "disp": False})
    estado = {0: "óptimo", 1: "límite de tiempo", 2: "infactible"}.get(r.status, f"estado {r.status}")
    res = {**info, "solver": "HiGHS", "estado": estado,
           "segundos_solver": round(time.perf_counter() - t0 - t_modelo, 2)}
    if r.x is not None:
        valor = float(r.fun + mod["const"])
        cota = getattr(r, "mip_dual_bound", None)
        cota = float(cota + mod["const"]) if cota is not None and np.isfinite(cota) else None
        res.update(valor=valor, cota=cota,
                   gap=float(r.mip_gap) if getattr(r, "mip_gap", None) is not None else None,
                   salidas=_salidas(mod, r.x))
    else:
        res.update(valor=None, cota=None, gap=None, salidas=None)
    # si el respaldo no mejora a T3 dentro del límite, se conserva T3 (equivale al arranque en caliente)
    if inicio and (res["valor"] is None or (x0 is not None and res["valor"] > float(mod["c"] @ x0 + mod["const"]) + 1e-6)):
        res.update(valor=float(mod["c"] @ x0 + mod["const"]) if x0 is not None else None,
                   salidas=inicio, nota="HiGHS no superó la solución inicial (T3) en el límite de tiempo")
    return res
