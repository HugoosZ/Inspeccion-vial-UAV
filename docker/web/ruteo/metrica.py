"""
Métrica de resiliencia de un conjunto de k rutas y
diferencial entre algoritmos.

Fallas: cada amenaza activa en la ventana [t0, t0 + horizonte] es un evento
independiente a que se materializa con probabilidad p_a según su nivel
(BAJO 0,05 · MEDIO 0,2 · ALTO 0,5 · CRÍTICO 0,8). Las celdas de clima A2 se
agrupan por celda (un evento por celda, con el nivel máximo de la ventana).

  A(P)      = amenazas que toca la ruta P
  S(P)      = Π_{a∈A(P)} (1 − p_a)                      supervivencia de una ruta
  R_sup     = P(al menos una ruta sobrevive)
            = Σ_{∅≠Q⊆{1..k}} (−1)^{|Q|+1} Π_{a ∈ ∪_{i∈Q} A(P_i)} (1 − p_a)
              (exacta: si dos rutas comparten una amenaza, fallan juntas)
  C         = largo de E_R cubierto / largo total de E_R       cobertura de inspección
  J         = largo compartido / largo de la unión (promedio entre pares)   solapamiento
  R         = R_sup · C                                        índice de resiliencia
  ΔR        = R_TDRPP-GA − R_Dijkstra
"""

from itertools import combinations


def _amenazas_ruta(aristas, eventos):
    resultado = {}
    for e in aristas:
        for clave, p in eventos.get(e, {}).items():
            resultado[clave] = max(p, resultado.get(clave, 0.0))
    return resultado


def _supervivencia(amenazas):
    s = 1.0
    for p in amenazas.values():
        s *= (1.0 - p)
    return s


def resiliencia(rutas, eventos, largos, requeridas):
    """rutas: listas de edge ids; eventos: edge -> {evento: p}; largos: edge -> metros;
    requeridas: dict edge_id -> largo (E_R común a ambos algoritmos)."""
    if not rutas:
        return {"R": 0.0, "R_supervivencia": 0.0, "cobertura": 0.0, "solapamiento_J": None,
                "por_ruta": []}
    amen = [_amenazas_ruta(r, eventos) for r in rutas]

    r_sup = 0.0
    for tam in range(1, len(rutas) + 1):
        for grupo in combinations(range(len(rutas)), tam):
            union = {}
            for i in grupo:
                for clave, p in amen[i].items():
                    union[clave] = max(p, union.get(clave, 0.0))
            r_sup += (-1) ** (tam + 1) * _supervivencia(union)

    cubiertas = set().union(*map(set, rutas)) & set(requeridas)
    total_req = sum(requeridas.values())
    cobertura = sum(requeridas[e] for e in cubiertas) / total_req if total_req else 0.0

    js = []
    for a, b in combinations(range(len(rutas)), 2):
        sa, sb = set(rutas[a]), set(rutas[b])
        union = sum(largos.get(e, 0) for e in sa | sb)
        js.append(sum(largos.get(e, 0) for e in sa & sb) / union if union else 0.0)

    por_ruta = []
    for r, a in zip(rutas, amen):
        por_ruta.append({
            "longitud_km": round(sum(largos.get(e, 0) for e in r) / 1000, 3),
            "amenazas_cruzadas": len(a),
            "supervivencia": round(_supervivencia(a), 4),
            "requeridas_cubiertas": len(set(r) & set(requeridas)),
        })
    return {
        "R": round(r_sup * cobertura, 4),
        "R_supervivencia": round(r_sup, 4),
        "cobertura": round(cobertura, 4),
        "solapamiento_J": round(sum(js) / len(js), 4) if js else None,
        "longitud_total_km": round(sum(p["longitud_km"] for p in por_ruta), 3),
        "por_ruta": por_ruta,
    }


def diferencial(m_dijkstra, m_tdrpp):
    return {
        "delta_R": round(m_tdrpp["R"] - m_dijkstra["R"], 4),
        "delta_R_supervivencia": round(m_tdrpp["R_supervivencia"] - m_dijkstra["R_supervivencia"], 4),
        "delta_cobertura": round(m_tdrpp["cobertura"] - m_dijkstra["cobertura"], 4),
        "delta_solapamiento_J": None if m_dijkstra["solapamiento_J"] is None or m_tdrpp["solapamiento_J"] is None
        else round(m_tdrpp["solapamiento_J"] - m_dijkstra["solapamiento_J"], 4),
    }
