"""
Planificación y comparación de rutas (rúbrica 8 a 11).

planificar() recibe el par (inicio, objetivo) y las preferencias del usuario y
devuelve, para el mismo E_R y las mismas amenazas:
  - k rutas de pgr_dijkstra por distancia (peor caso, rúbrica 8),
  - k rutas del TDRPP-GA con pesos resilientes (rúbrica 9),
  - la métrica de resiliencia de cada conjunto y su diferencial (rúbrica 10 y 11.2).
"""

from . import datos, dijkstra_k, metrica, preferencias
from .tdrpp_ga import TDRPPGA, construir_grafo_resiliente, distancia_m


class ErrorRuteo(ValueError):
    pass


def _coleccion(rutas, geoms, algoritmo, metricas_ruta):
    features = []
    for i, (aristas, m) in enumerate(zip(rutas, metricas_ruta), start=1):
        lineas = [geoms[e]["coords"] for e in aristas if e in geoms]
        features.append({
            "type": "Feature",
            "geometry": {"type": "MultiLineString", "coordinates": lineas},
            "properties": {"algoritmo": algoritmo, "ruta": i, "aristas": len(aristas), **m},
        })
    return {"type": "FeatureCollection", "features": features}


def planificar(conn, lon_inicio, lat_inicio, lon_objetivo, lat_objetivo, entrada_prefs):
    prefs, por_defecto = preferencias.normalizar(entrada_prefs)
    t0 = preferencias.hora_salida(prefs)
    avisos = []

    inicio = datos.nodo_mas_cercano(conn, lon_inicio, lat_inicio)
    objetivo = datos.nodo_mas_cercano(conn, lon_objetivo, lat_objetivo)
    if not inicio or not objetivo:
        raise ErrorRuteo("La red vial está vacía: ejecuta Infraestructura/cargar.py")
    if inicio["id"] == objetivo["id"]:
        raise ErrorRuteo("El inicio y el objetivo caen en el mismo nodo; elige puntos más separados.")

    # Región de trabajo: rectángulo inicio-objetivo con margen
    margen = max(1500.0, prefs["radio_corredor_m"] * 3, 0.3 * distancia_m(inicio, objetivo))
    bbox = datos.bbox_region(inicio, objetivo, margen)

    # Amenazas (5.2) y restricciones de metadata (6.1)
    amen = datos.amenazas_region(conn, bbox, t0, prefs["horizonte_h"], prefs["considerar_clima"])
    eliminadas, factores = datos.restricciones_metadata(conn, bbox, prefs)

    # Aristas a inspeccionar (mismo E_R para ambos algoritmos)
    req = datos.aristas_requeridas(conn, inicio, objetivo, prefs, amen.bloqueadas | eliminadas)
    if not req:
        avisos.append("No hay señales de los tipos elegidos en el corredor: amplía el radio o agrega tipos.")

    # --- Rúbrica 8: k rutas con pgr_dijkstra (solo distancia) ---
    rutas_dij = dijkstra_k.k_rutas(conn, inicio["id"], objetivo["id"], prefs["k"])
    if not rutas_dij:
        raise ErrorRuteo("pgr_dijkstra no encontró camino entre los puntos (red desconectada).")
    if len(rutas_dij) < prefs["k"]:
        avisos.append(f"Dijkstra solo encontró {len(rutas_dij)} ruta(s) distinta(s).")

    # --- Rúbrica 9: k rutas con TDRPP-GA (pesos resilientes) ---
    G = datos.grafo_completo(conn)
    H = construir_grafo_resiliente(G, bbox, amen, eliminadas, factores)
    if inicio["id"] not in H or objetivo["id"] not in H:
        raise ErrorRuteo("El inicio u objetivo quedó dentro de una zona bloqueada (amenaza o aeródromo).")
    ga = TDRPPGA(H, inicio["id"], objetivo["id"], req, amen, t0.timestamp(), prefs["k"],
                 prefs["velocidad_crucero_ms"], prefs["velocidad_inspeccion_ms"], prefs["autonomia_km"])
    resultado_ga = ga.resolver() if req else None
    avisos += ga.avisos
    rutas_ga = [r["aristas"] for r in resultado_ga["rutas"]] if resultado_ga else []

    # --- Rúbrica 10 / 11.2: métrica y diferencial ---
    todas = {e for r in rutas_dij + rutas_ga for e in r} | {r["id"] for r in req}
    geoms = datos.geometrias(conn, list(todas))
    largos = {e: g["length_m"] for e, g in geoms.items()}
    req_largos = {r["id"]: r["length_m"] for r in req}
    m_dij = metrica.resiliencia(rutas_dij, amen.eventos, largos, req_largos)
    m_ga = metrica.resiliencia(rutas_ga, amen.eventos, largos, req_largos)

    extra_ga = [{"duracion_min": r["duracion_min"], "costo_resiliente": r["costo"],
                 "excede_autonomia": r["excede_autonomia"]} for r in resultado_ga["rutas"]] if resultado_ga else []
    por_ruta_ga = [{**a, **b} for a, b in zip(m_ga["por_ruta"], extra_ga)]

    return {
        "inicio": inicio, "objetivo": objetivo, "hora_salida": t0.isoformat(),
        "preferencias": prefs, "preferencias_por_defecto": por_defecto,
        "contexto": {"amenazas_en_region": amen.n_amenazas, "aristas_bloqueadas": len(amen.bloqueadas),
                     "aristas_eliminadas_metadata": len(eliminadas),
                     "aristas_penalizadas_metadata": len(factores), "aristas_requeridas": len(req)},
        "aristas_requeridas": {"type": "FeatureCollection", "features": [{
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": geoms[r["id"]]["coords"]},
            "properties": {"id": r["id"], "nombre": r["name"], "senales": r["n_senales"],
                           "vulnerabilidad": r["vulnerabilidad"], "puntaje": r["puntaje"]},
        } for r in req if r["id"] in geoms]},
        "dijkstra": {"rutas": _coleccion(rutas_dij, geoms, "pgr_dijkstra", m_dij["por_ruta"]),
                     "metricas": {k: v for k, v in m_dij.items() if k != "por_ruta"},
                     "metodo": f"pgr_dijkstra por distancia (km) con penalización ×{dijkstra_k.FACTOR} "
                               "de aristas ya usadas"},
        "tdrpp_ga": {"rutas": _coleccion(rutas_ga, geoms, "TDRPP-GA", por_ruta_ga),
                     "metricas": {k: v for k, v in m_ga.items() if k != "por_ruta"},
                     "info": resultado_ga["info"] if resultado_ga else None},
        "diferencial": metrica.diferencial(m_dij, m_ga),
        "avisos": avisos,
    }
