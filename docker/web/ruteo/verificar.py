"""
Verificación de las técnicas T2 (MILP) y T4 (DA-DPSO) contra la fuerza bruta.

En instancias pequeñas el óptimo se obtiene enumerando todas las permutaciones
de calles y decodificando cada una con split (que es exacto para una
permutación fija, incluido el sentido de cada calle). Se comprueba que:
  - T2 entrega ese óptimo y lo certifica (cota = valor),
  - T4 lo alcanza,
  - las soluciones cubren cada calle una vez y cada salida cabe en la batería.
También compara las formulaciones de flujo y MTZ del MILP.

Uso (con el sistema levantado):
  docker compose exec web python /app/server/ruteo/verificar.py
Sin la licencia WLS de Gurobi en el .env, el MILP se resuelve con HiGHS.
"""

import itertools
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ruteo import dpso, milp, tecnicas  # noqa: E402

CONV, RECARGA, V = 1 / 600, 15.0, 10.0


def instancia(n, semilla, holgura):
    """Calles al azar en un cuadrado de 6 km (distancias euclidianas, que
    cumplen la desigualdad triangular como las de pgr_dijkstraCostMatrix)."""
    rng = np.random.default_rng(semilla)
    pts, U, Vv = [np.zeros(2)], [], []
    for q in range(n):
        a = rng.uniform(-3000, 3000, 2)
        pts += [a, a + rng.uniform(-300, 300, 2)]
        U.append(2 * q + 1)
        Vv.append(2 * q + 2)
    P = np.array(pts)
    M = np.hypot(*(P[:, None, :] - P[None, :, :]).transpose(2, 0, 1))
    U, Vv = np.array(U), np.array(Vv)
    Lt = M[U, Vv] * rng.uniform(1.0, 1.2, n)
    ida = np.minimum(M[0, U] + Lt + M[Vv, 0], M[0, Vv] + Lt + M[U, 0])
    return M, U, Vv, Lt, max(ida.max(), holgura * ida.mean())


def cubre(salidas, n):
    return sorted(t for s in salidas for t, _, _ in s) == list(range(n))


def main():
    print("Solver del modelo T2:", milp.estado_solver())
    print()
    print(f"{'n':>3} {'óptimo':>9} {'T3':>9} {'T2':>9} {'cota T2':>9} {'s T2':>6} {'T4':>9} {'s T4':>5}  estado")
    fallas = 0
    for n, sem, h in [(5, 2, 2.5), (6, 3, 3.0), (6, 4, 1.5), (7, 5, 2.5), (7, 6, 4.0), (8, 7, 2.5)]:
        M, U, Vv, Lt, cap = instancia(n, sem, h)
        Ml = M.tolist()
        optimo = min(tecnicas.split(list(p), Ml, 0, U.tolist(), Vv.tolist(), Lt.tolist(), cap, CONV, RECARGA)[0]
                     for p in itertools.permutations(range(n)))
        orden, _ = tecnicas.constructiva(M, 0, U, Vv, Lt, cap)
        v3 = tecnicas.minutos_sector(M, 0, orden, Lt, V, RECARGA)
        t = time.perf_counter()
        r2 = milp.resolver(M, 0, U, Vv, Lt, cap, CONV, RECARGA, inicio=orden, limite_s=120)
        s2 = time.perf_counter() - t
        t = time.perf_counter()
        r4 = dpso.resolver(Ml, 0, U.tolist(), Vv.tolist(), Lt.tolist(), cap, CONV, RECARGA,
                           semilla_perm=[q for s in orden for q, _, _ in s], limite_s=5)
        s4 = time.perf_counter() - t
        ok = (abs(r2["valor"] - optimo) < 1e-4 and abs(r4["valor"] - optimo) < 1e-4
              and cubre(r2["salidas"], n) and cubre(r4["salidas"], n)
              and tecnicas.factible(M, 0, r2["salidas"], Lt, cap) and tecnicas.factible(M, 0, r4["salidas"], Lt, cap))
        fallas += not ok
        cota = r2["cota"] if r2["cota"] is not None else float("nan")
        print(f"{n:>3} {optimo:9.3f} {v3:9.3f} {r2['valor']:9.3f} {cota:9.3f} {s2:6.1f} {r4['valor']:9.3f} {s4:5.1f}"
              f"  {r2['estado']}{'' if ok else '  <-- FALLA'}")
    print()
    print("Formulaciones del MILP (tiempo hasta certificar el óptimo, límite 60 s):")
    for n, sem, h in [(6, 3, 3.0), (7, 5, 2.5), (8, 7, 2.5), (10, 8, 2.5)]:
        M, U, Vv, Lt, cap = instancia(n, sem, h)
        orden, _ = tecnicas.constructiva(M, 0, U, Vv, Lt, cap)
        linea = f"  n={n:>2}"
        for f in ("flujo", "mtz"):
            t = time.perf_counter()
            r = milp.resolver(M, 0, U, Vv, Lt, cap, CONV, RECARGA, inicio=orden, limite_s=60, formulacion=f)
            linea += f" | {f}: {r['valor']:.3f} ({r['estado']}, {time.perf_counter() - t:.1f} s)"
        print(linea)
    print()
    print("Todo correcto." if not fallas else f"{fallas} instancia(s) con diferencias.")


if __name__ == "__main__":
    main()
