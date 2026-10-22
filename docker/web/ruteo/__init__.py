"""
Planificación de rutas resilientes con pgr_dijkstra.

planificar() recibe el par (inicio, objetivo) y las preferencias del usuario y
devuelve:
  - k rutas de pgr_dijkstra sobre pesos resilientes (amenazas + metadata),
  - las aristas a inspeccionar E_R del corredor,
  - la métrica de resiliencia del conjunto de rutas.
"""

import math

from . import datos, dijkstra_k, metrica, preferencias


class ErrorRuteo(ValueError):
    pass


def distancia_m(a, b):
    """Distancia aproximada en metros entre dos dicts con lon/lat."""
    lat = math.radians((a["lat"] + b["lat"]) / 2)
    dx = (a["lon"] - b["lon"]) * 111_320 * math.cos(lat)
    dy = (a["lat"] - b["lat"]) * 111_320
    return math.hypot(dx, dy)


def _coleccion(rutas, geoms, metricas_ruta):
    features = []
    for i, (aristas, m) in enumerate(zip(rutas, metricas_ruta), start=1):
        lineas = [geoms[e]["coords"] for e in aristas if e in geoms]
        features.append({
            "type": "Feature",
            "geometry": {"type": "MultiLineString", "coordinates": lineas},
            "properties": {"algoritmo": "pgr_dijkstra", "ruta": i, "aristas": len(aristas), **m},
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

    # Amenazas y restricciones de metadata -> pesos resilientes w(e)
    amen = datos.amenazas_region(conn, bbox, t0, prefs["horizonte_h"], prefs["considerar_clima"])
    eliminadas, factores_meta = datos.restricciones_metadata(conn, bbox, prefs)
    bloqueadas = amen.bloqueadas | eliminadas
    factores = {}
    for e in set(amen.factor_estatico) | set(factores_meta) | set(amen.temporales):
        factores[e] = (amen.factor_estatico.get(e, 1.0) * factores_meta.get(e, 1.0)
                       * amen.factor_en(e, t0.timestamp()))

    # Aristas a inspeccionar en el corredor (para la cobertura de la métrica)
    req = datos.aristas_requeridas(conn, inicio, objetivo, prefs, bloqueadas)
    if not req:
        avisos.append("No hay señales de los tipos elegidos en el corredor: amplía el radio o agrega tipos.")

    # k rutas con pgr_dijkstra sobre w(e)
    rutas = dijkstra_k.k_rutas(conn, inicio["id"], objetivo["id"], prefs["k"], bbox, bloqueadas, factores)
    if not rutas:
        raise ErrorRuteo("pgr_dijkstra no encontró camino: el inicio u objetivo queda aislado por "
                         "amenazas o aeródromos, o la red está desconectada.")
    if len(rutas) < prefs["k"]:
        avisos.append(f"pgr_dijkstra solo encontró {len(rutas)} ruta(s) distinta(s).")

    # Métrica de resiliencia
    todas = {e for r in rutas for e in r} | {r["id"] for r in req}
    geoms = datos.geometrias(conn, list(todas))
    largos = {e: g["length_m"] for e, g in geoms.items()}
    req_largos = {r["id"]: r["length_m"] for r in req}
    m = metrica.resiliencia(rutas, amen.eventos, largos, req_largos)

    por_ruta = []
    for aristas, p in zip(rutas, m["por_ruta"]):
        costo = sum(largos.get(e, 0) / 1000.0 * factores.get(e, 1.0) for e in aristas)
        por_ruta.append({**p, "costo_resiliente": round(costo, 3),
                         "duracion_min": round(p["longitud_km"] * 1000 / prefs["velocidad_crucero_ms"] / 60, 1),
                         "excede_autonomia": p["longitud_km"] > prefs["autonomia_km"]})

    return {
        "inicio": inicio, "objetivo": objetivo, "hora_salida": t0.isoformat(),
        "preferencias": prefs, "preferencias_por_defecto": por_defecto,
        "contexto": {"amenazas_en_region": amen.n_amenazas, "aristas_bloqueadas": len(amen.bloqueadas),
                     "aristas_eliminadas_metadata": len(eliminadas),
                     "aristas_penalizadas": len(factores), "aristas_requeridas": len(req)},
        "aristas_requeridas": {"type": "FeatureCollection", "features": [{
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": geoms[r["id"]]["coords"]},
            "properties": {"id": r["id"], "nombre": r["name"], "senales": r["n_senales"],
                           "vulnerabilidad": r["vulnerabilidad"], "puntaje": r["puntaje"]},
        } for r in req if r["id"] in geoms]},
        "dijkstra": {"rutas": _coleccion(rutas, geoms, por_ruta),
                     "metricas": {k: v for k, v in m.items() if k != "por_ruta"},
                     "metodo": f"pgr_dijkstra sobre pesos resilientes w(e), con penalización "
                               f"×{dijkstra_k.FACTOR:g} de aristas ya usadas"},
        "avisos": avisos,
    }
