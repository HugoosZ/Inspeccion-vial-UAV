"""Recuperación de un escenario conocido antes del despegue, con bases fijas.

No simula decisiones a mitad de vuelo. Los cierres A1/A3 se conservan toda la
jornada; A2 suspende salidas que solapan horas no aptas, sin desvío espacial.
"""

from copy import deepcopy
from datetime import datetime
import time

import numpy as np

from . import datos, flota


def epoch(iso):
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def replanificar(conn, registro, modelo, eventos):
    inicio = time.perf_counter()
    instancia, prefs = registro["instancia"], registro["preferencias"]
    activos = [e for e in eventos if e["ocurre"]]
    cierres = {a for e in activos if e["fuente"] in ("A1", "A3") for a in e["aristas"]}
    demanda = {a for e in activos if e["fuente"] == "A4" for a in e["aristas"]}
    clima = [e for e in activos if e["fuente"] == "A2"]
    tareas = {t["id"]: dict(t) for t in instancia["tareas"]}
    codigos = list(registro["rutas"])
    resultados = {}
    # Sin cambios espaciales, conservar EXACTAMENTE los recorridos; no volver
    # a ejecutar un solver con límite de tiempo y alterar un plan por azar.
    usadas_originales = {e for rutas in registro["rutas"].values() for r in rutas for e in r["aristas"]}
    if not (cierres & usadas_originales) and not demanda:
        resultados = deepcopy(registro["tecnicas_originales"])
        for t in resultados.values():
            t["tiempo_recalculo_s"] = 0.0
    else:
        for e in demanda - tareas.keys():
            g = modelo["aristas"][e]
            coords = g["geom"]["coordinates"]
            lon, lat = coords[len(coords)//2][:2]
            tareas[e] = {"id": e, "source": g["source"], "target": g["target"],
                         "length_m": max(g["length_m"], 0.1), "name": g["name"],
                         "lon": lon, "lat": lat, "n_puntos": 0, "tipos": {"inspeccion_A4": 1}}
        # Retener bases, asignaciones y secuencia de sectores del plan inicial.
        sectores, por_tarea = [], {}
        for d, (_, _, _, pasos) in enumerate(instancia["plan"]):
            for tipo, ctx in pasos:
                if tipo != "sector" or ctx.get("reintento"):
                    continue
                s = {"dron": d, "base": ctx["base"], "ids": set()}
                sectores.append(s)
                for t in ctx["grupo"]:
                    por_tarea[t["id"]] = s
        if not sectores:
            raise ValueError("El plan no conserva sectores para recuperar.")
        for e, t in tareas.items():
            if e in cierres:
                continue
            s = por_tarea.get(e)
            if s is None:
                s = min(sectores, key=lambda s: (s["base"]["lon"]-t["lon"])**2 + (s["base"]["lat"]-t["lat"])**2)
            s["ids"].add(e)
        red = flota.Red(cierres, {e: flota.factor_resiliente(p) for e, p in instancia["prob"].items()})
        red_dist = flota.Red(solo_distancia=True, cierres_obligatorios=cierres)
        flota.preparar_pesos(conn, red)
        plan = []
        asignacion = {}
        for d, (_, _, bases, pasos_originales) in enumerate(instancia["plan"]):
            salidas, fuera, pasos = [], [], []
            it = iter(s for s in sectores if s["dron"] == d)
            for tipo, obj in pasos_originales:
                if tipo == "traslado":
                    traslado = deepcopy(obj)
                    salidas.append(traslado)
                    pasos.append((tipo, traslado))
                elif not obj.get("reintento"):
                    s = next(it)
                    # A4 se atiende primero al llegar a su base/sector; no se
                    # inventa una señal en una calle sin inventario.
                    for ids in (s["ids"] & demanda, s["ids"] - demanda):
                        if not ids:
                            continue
                        grupo = [tareas[e] for e in sorted(ids)]
                        asignacion.update({e: d for e in ids})
                        sal, no, ctxs = flota._rutear_sector(conn, red, s["base"], grupo, registro["capacidad_m"])
                        salidas += sal
                        fuera += no
                        pasos += [("sector", c) for c in ctxs]
            plan.append((salidas, fuera, bases, pasos))
        comun_s = time.perf_counter() - inicio
        soluciones = flota._resolver_tecnicas(conn, plan, codigos, red_dist, prefs, registro["capacidad_m"])
        lista = list(tareas.values())
        etiquetas = np.array([asignacion.get(t["id"], -1) for t in lista])
        for cod in codigos:
            ti = time.perf_counter()
            p = flota._plan_tecnica(plan, soluciones[cod]["ordenes"])
            for salidas, _, _ in p:
                flota.materializar(conn, red_dist if cod == "T1" else red, salidas)
            usadas = {e for salidas, _, _ in p for s in salidas for e in s["aristas"]}
            geoms = datos.geometrias(conn, list(usadas | tareas.keys()))
            fuera = {e for _, ids, _ in p for e in ids} | cierres
            drones, features, resumen, avisos = flota._evaluar_tecnica(
                cod, p, etiquetas, lista, tareas, fuera, geoms, instancia["prob"],
                cierres, ({}, {}), prefs, registro["capacidad_m"], registro["t0"])
            info = soluciones[cod]["info"]
            resultados[cod] = {"rutas": {"type": "FeatureCollection", "features": features},
                               "resumen": resumen, "drones": drones, "detalle": info, "avisos": avisos,
                               "tiempo_recalculo_s": round(comun_s + info["segundos_solver"] + time.perf_counter()-ti, 3)}
        conn.rollback()
    t0, t1 = epoch(modelo["t0"]), epoch(modelo["t1"])
    total = sum(registro["requeridas"].values())
    for cod, resultado in resultados.items():
        atendidas, detenidos = set(), set()
        suspensiones, pendientes = 0, []
        for f in sorted(resultado["rutas"]["features"], key=lambda f: (f["properties"]["dron"], f["properties"]["salida"])):
            p = f["properties"]
            ini, fin = epoch(p["inicio"]), epoch(p["fin"])
            metros = sum(modelo["aristas"][e]["length_m"] for e in p["aristas"])
            meteorologia = any(e["desde"] < fin and e["hasta"] > ini for e in clima)
            estado = ("dependencia_suspendida" if p["dron"] in detenidos else
                      "suspendida_A2" if meteorologia else
                      "bloqueada" if set(p["aristas"]) & cierres else
                      "inviable" if not p["camino_completo"] or not p["retorno_base"] or metros > registro["capacidad_m"]+1e-6 else
                      "fuera_jornada" if ini < t0 or fin > t1 else "ejecutable")
            p["estado_recuperacion"] = estado
            p["tareas_A4"] = sorted(set(p["tarea_ids"]) & demanda)
            if estado == "ejecutable":
                atendidas.update(p["tarea_ids"])
            else:
                pendientes.append({"dron": p["dron"], "salida": p["salida"], "motivo": estado})
                suspensiones += estado == "suspendida_A2"
                if p["tipo"] == "traslado":
                    detenidos.add(p["dron"])
        originales = atendidas & registro["requeridas"].keys()
        cumplidas = sum(registro["requeridas"][e] for e in originales)
        nuevas_ok = demanda & atendidas
        resultado["recuperacion"] = {
            "tareas_originales_pendientes": sorted(registro["requeridas"].keys()-atendidas),
            "tareas_A4": sorted(demanda), "tareas_A4_atendidas": sorted(nuevas_ok),
            "tareas_A4_pendientes": sorted(demanda-atendidas),
            "cumplimiento_A4": len(nuevas_ok)/len(demanda) if demanda else None,
            "salidas_suspendidas_A2": suspensiones, "salidas_no_ejecutables": pendientes,
            "senales_atendidas": cumplidas, "R": cumplidas/total if total else None,
            "km_adicionales": round(resultado["resumen"]["km_total_Dis"]-registro["tecnicas_originales"][cod]["resumen"]["km_total_Dis"], 3),
        }
    return resultados
