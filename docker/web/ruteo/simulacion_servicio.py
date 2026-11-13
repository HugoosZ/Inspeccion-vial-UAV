"""Escenarios reproducibles de indisponibilidad aplicados a planes de flota.

El plan calculado se conserva en memoria y el sorteo utiliza un único modelo
para todas sus técnicas. Se conserva el original y se calcula otro plan:
desvíos A1/A3, suspensión A2 y demanda A4. Ver Probabilidades/ESCENARIOS.md.
"""

from collections import OrderedDict, defaultdict
from datetime import datetime
import hashlib
import json
import threading
import time
from uuid import uuid4

from . import resiliencia, simulacion, recuperacion


MAX_PLANES = 4
TTL_PLAN_S = 1800
_planes = OrderedDict()
_cerrojo = threading.RLock()


class PlanNoDisponible(ValueError):
    pass


def _instante(valor):
    t = datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
    if t.tzinfo is None:
        raise ValueError("El horario del plan debe incluir zona horaria.")
    return t


def _firma_geometria(coordenadas):
    # El plan usa ST_AsGeoJSON(..., 6). La misma precisión permite detectar
    # una recarga de red con IDs reutilizados sin comparar números de más cifras.
    redondeadas = [[round(float(v), 6) for v in punto] for punto in coordenadas]
    return hashlib.sha256(json.dumps(redondeadas, separators=(",", ":")).encode()).hexdigest()


def registrar_plan(resultado):
    """Guarda la información mínima para simular el plan durante 30 minutos."""
    rutas, huellas = {}, {}
    for codigo, tecnica in resultado["tecnicas"].items():
        rutas[codigo] = []
        for feature in tecnica["rutas"]["features"]:
            p = feature["properties"]
            coordenadas = feature["geometry"]["coordinates"]
            if len(coordenadas) == len(p["aristas"]):
                for e, coords in zip(p["aristas"], coordenadas):
                    huellas[int(e)] = _firma_geometria(coords)
            rutas[codigo].append({
                "dron": p["dron"], "salida": p["salida"], "tipo": p["tipo"],
                "inicio": p["inicio"], "fin": p["fin"],
                "aristas": tuple(int(e) for e in p["aristas"]),
                "tareas": tuple(int(e) for e in p.get("tarea_ids", [])),
                "camino_completo": bool(p.get("camino_completo", False)),
                "retorno_base": bool(p.get("retorno_base", False)),
            })
    token = uuid4().hex
    registro = {"creado": time.monotonic(), "rutas": rutas, "huellas": huellas,
                "instancia": resultado.pop("_instancia_recuperacion"),
                "preferencias": dict(resultado["preferencias"]),
                "tecnicas_originales": {c: {k: t[k] for k in ("rutas", "resumen", "detalle", "avisos")}
                                         for c, t in resultado["tecnicas"].items()},
                "t0": _instante(resultado["hora_salida"]),
                "horas": float(resultado["preferencias"]["jornada_h"]),
                "requeridas": {int(t["id"]): t["senales"] for t in
                               resultado.get("servicio_requerido", {}).get("tareas", [])},
                "capacidad_m": (float(resultado["preferencias"]["velocidad_crucero_ms"])
                                * float(resultado["preferencias"]["autonomia_min"]) * 60
                                * (1 - float(resultado["preferencias"]["reserva_bateria_pct"]) / 100)),
                "detalle_tecnicas": {c: t.get("detalle", {}) for c, t in resultado["tecnicas"].items()},
                "modelo": resultado.pop("_modelo_probabilidades", None),
                "modelo_preparado": False, "cerrojo": threading.Lock()}
    with _cerrojo:
        ahora = time.monotonic()
        for clave in list(_planes):
            if ahora - _planes[clave]["creado"] > TTL_PLAN_S:
                del _planes[clave]
        _planes[token] = registro
        while len(_planes) > MAX_PLANES:
            _planes.popitem(last=False)
    return token


def _recuperar_plan(plan_id):
    if not isinstance(plan_id, str):
        raise ValueError("plan_id debe identificar un plan calculado por el servidor.")
    with _cerrojo:
        registro = _planes.get(plan_id)
        if registro is None or time.monotonic() - registro["creado"] > TTL_PLAN_S:
            _planes.pop(plan_id, None)
            raise PlanNoDisponible("El plan ya no está disponible. Vuelve a planificar antes de simular.")
        _planes.move_to_end(plan_id)
        return registro


def _evaluar_rutas(rutas, eventos, aristas, t0, t1, solo_ocurridos=True):
    por_arista, por_nodo = defaultdict(list), defaultdict(list)
    for evento in eventos:
        if evento["fuente"] == "A4":
            continue  # Cambia demanda; no invalida las salidas originales.
        if solo_ocurridos and not evento["ocurre"]:
            continue
        for e in evento["aristas"]:
            por_arista[e].append(evento)
        for n in evento["nodos"]:
            por_nodo[n].append(evento)
    salida = {}
    for codigo, lista in rutas.items():
        detalle = []
        for ruta in lista:
            ini, fin = _instante(ruta["inicio"]).timestamp(), _instante(ruta["fin"]).timestamp()
            en_ventana = ini >= t0 and fin <= t1
            evaluada = ini < t1 and fin > t0
            fallas_e, fallas_n = defaultdict(list), defaultdict(list)
            total_m = sum(aristas[e]["length_m"] for e in ruta["aristas"])
            actual = ini
            if evaluada and total_m > 0:
                # Mismo supuesto de velocidad constante que el planificador.
                # No se atribuye a toda la salida una falla meteorológica que
                # solo estuvo activa cuando el dron ya había dejado esa calle.
                segundos_m = (fin - ini) / total_m
                for e in ruta["aristas"]:
                    datos = aristas[e]
                    siguiente = actual + datos["length_m"] * segundos_m

                    def vigentes(candidatos):
                        return [ev for ev in candidatos
                                if ev["desde"] < siguiente and ev["hasta"] > actual]

                    fallas_e[e].extend(vigentes(por_arista[e]))
                    for n in (datos["source"], datos["target"]):
                        fallas_n[n].extend(vigentes(por_nodo[n]))
                    actual = siguiente
            fallas_e = {e: evs for e, evs in fallas_e.items() if evs}
            fallas_n = {n: evs for n, evs in fallas_n.items() if evs}
            ids_eventos = {ev["id"] for conjunto in (fallas_e, fallas_n)
                           for evs in conjunto.values() for ev in evs}
            # A2 no se evita buscando una calle sin clima.
            ids_eventos.update(ev["id"] for ev in eventos if ev["fuente"] == "A2"
                               and (not solo_ocurridos or ev["ocurre"])
                               and ev["desde"] < fin and ev["hasta"] > ini)
            detalle.append({"dron": ruta["dron"], "salida": ruta["salida"], "tipo": ruta["tipo"],
                            "afectada": bool(ids_eventos), "aristas_falladas": len(fallas_e),
                            "nodos_fallados": len(fallas_n), "eventos": sorted(ids_eventos),
                            "en_ventana": en_ventana, "evaluada": evaluada})
        salida[codigo] = {"rutas_afectadas": sum(r["afectada"] for r in detalle),
                          "salidas_evaluadas": sum(r["evaluada"] for r in detalle),
                          "fuera_ventana": sum(not r["en_ventana"] for r in detalle), "detalle": detalle}
    return salida


def _modelo_del_plan(conn, registro):
    """Una única red y un catálogo de eventos para P7 y P8."""
    with registro["cerrojo"]:
        if not registro["modelo_preparado"]:
            if registro["modelo"] is None:
                registro["modelo"] = simulacion.cargar(conn, registro["t0"], registro["horas"])
            modelo = registro["modelo"]
            bbox = registro["instancia"]["bbox"]
            with conn.cursor() as cur:
                area = registro["instancia"]["area"]
                if area:
                    cur.execute("SELECT id FROM network_edges WHERE ST_Intersects(geom, ST_SetSRID(ST_GeomFromGeoJSON(%s),4326))", (json.dumps(area),))
                else:
                    cur.execute("SELECT id FROM network_edges WHERE ST_Intersects(geom, ST_MakeEnvelope(%s,%s,%s,%s,4326))", bbox)
                area_ids = {int(f[0]) for f in cur.fetchall()}
            usadas = {e for rutas in registro["rutas"].values() for r in rutas for e in r["aristas"]}
            seleccion = set(registro["preferencias"]["amenazas"])
            if not registro["preferencias"]["considerar_clima"]:
                seleccion.discard("A2")
            # No perder peligros fuera del recorrido original: un desvío
            # puede necesitar esas calles. A4 sí se limita al área solicitada.
            modelo["eventos"] = [e for e in modelo["eventos"] if e["fuente"] in seleccion
                                  and (e["fuente"] in ("A1", "A3") or
                                       set(e["aristas"]) & (area_ids | usadas))]
            for e in modelo["eventos"]:
                if e["fuente"] == "A4":
                    e["aristas"] = sorted(set(e["aristas"]) & area_ids)
            modelo["eventos"] = [e for e in modelo["eventos"] if e["aristas"]]
            registro["modelo_preparado"] = True
        modelo = registro["modelo"]
    if modelo.get("firma_red") and simulacion.firma_red(conn) != modelo["firma_red"]:
        raise ValueError("La red cambió desde el cálculo del plan. Vuelve a planificar antes de simular.")
    usadas = {e for lista in registro["rutas"].values() for ruta in lista for e in ruta["aristas"]}
    if usadas - set(modelo["aristas"]):
        raise ValueError("La red cambió desde el cálculo del plan. Vuelve a planificar antes de simular.")
    if any(e not in registro["huellas"] or registro["huellas"][e] != _firma_geometria(
            modelo["aristas"][e]["geom"]["coordinates"]) for e in usadas):
        raise ValueError("La geometría de la red cambió desde el cálculo del plan. Vuelve a planificar.")
    return modelo


def probabilidades_plan(plan_id, elemento, minimo, columna):
    """P5 de la misma instantánea y horizonte utilizados por el plan y P7."""
    registro = _recuperar_plan(plan_id)
    modelo = registro["modelo"]
    if modelo is None or "probabilidades" not in modelo:
        raise PlanNoDisponible("El plan no conserva probabilidades. Vuelve a planificar.")
    features = []
    for identificador, fila in modelo["probabilidades"][elemento].items():
        if not fila["bloqueado"] and (fila.get(columna) or 0) <= minimo:
            continue
        datos = modelo[elemento][identificador]
        features.append({"type": "Feature", "geometry": datos["geom"],
                         "properties": {**fila, "nombre": datos.get("name"),
                                        "t0": modelo["t0"], "t1": modelo["t1"]}})
    return {"type": "FeatureCollection", "features": features,
            "metadata": {"id_modelo": modelo["id_modelo"], "t0": modelo["t0"], "t1": modelo["t1"],
                         "fuentes_seleccionadas": modelo.get("fuentes_seleccionadas"),
                         "configuracion_escenarios": modelo["configuracion_escenarios"]}}


def _metricas_servicio(registro, modelo, eventos_ocurridos=None):
    t0, t1 = _instante(modelo["t0"]).timestamp(), _instante(modelo["t1"]).timestamp()
    # El mismo proyector horario de P7, ahora sobre TODOS los eventos. Para la
    # esperanza se utiliza su probabilidad; para un escenario, su ocurrencia.
    expuestas = _evaluar_rutas(registro["rutas"], modelo["eventos"], modelo["aristas"],
                               t0, t1, solo_ocurridos=False)
    probabilidades = {e["id"]: None if e["determinista"] else e["probabilidad"]
                      for e in modelo["eventos"]}
    salida = {}
    for codigo, rutas in registro["rutas"].items():
        inicio = time.perf_counter()
        exposiciones = {(r["dron"], r["salida"]): r["eventos"] for r in expuestas[codigo]["detalle"]}
        dependencias, salidas = defaultdict(list), []
        for ruta in sorted(rutas, key=lambda r: (r["dron"], r["salida"])):
            clave = f"{codigo}:{ruta['dron']}:{ruta['salida']}"
            metros = sum(modelo["aristas"][e]["length_m"] for e in ruta["aristas"])
            ini, fin = _instante(ruta["inicio"]).timestamp(), _instante(ruta["fin"]).timestamp()
            factible = (ruta["camino_completo"] and ruta["retorno_base"]
                        and metros <= registro["capacidad_m"] + 1e-6
                        and ini >= t0 and fin <= t1)
            salidas.append({"id": clave, "dron": ruta["dron"], "tipo": ruta["tipo"],
                            "tareas": list(ruta["tareas"]), "factible": factible,
                            "dependencias": list(dependencias[ruta["dron"]]),
                            "eventos": exposiciones[(ruta["dron"], ruta["salida"])]})
            if ruta["tipo"] == "traslado":
                dependencias[ruta["dron"]].append(clave)
        try:
            metrica = resiliencia.evaluar(registro["requeridas"], salidas, probabilidades, eventos_ocurridos)
        except (ValueError, TypeError, KeyError) as exc:
            metrica = {"estado": "no_disponible", "R": None, "motivo": str(exc),
                       "cobertura_pre": None, "cobertura_esperada": None, "cobertura_escenario": None}
        metrica["tiempo_evaluacion_ms"] = round((time.perf_counter() - inicio) * 1000, 3)
        salida[codigo] = metrica
    comparacion = resiliencia.comparar(salida)
    comparacion["observaciones"] = []
    t2 = registro["detalle_tecnicas"].get("T2", {})
    if t2.get("sectores_sin_milp"):
        comparacion["observaciones"].append("T2 conserva soluciones T3 en algunos sectores; "
                                            "se compara el plan híbrido informado.")
    if t2.get("nota"):
        comparacion["observaciones"].append("T2: " + t2["nota"])
    return salida, comparacion


def evaluar_plan(conn, plan_id):
    """Cobertura esperada bajo el mismo modelo que utilizará el sorteo de P7."""
    registro = _recuperar_plan(plan_id)
    inicio = time.perf_counter()
    try:
        modelo = _modelo_del_plan(conn, registro)
        tiempo_modelo = (time.perf_counter() - inicio) * 1000
        metricas, comparacion = _metricas_servicio(registro, modelo)
        comparacion["tiempo_modelo_ms"] = round(tiempo_modelo, 3)
        return metricas, comparacion
    except Exception as exc:
        # La falta de una evaluación nunca se convierte en riesgo cero ni
        # elimina del resultado el plan que ya pudo calcularse.
        conn.rollback()
        metricas = {c: {"estado": "no_disponible", "R": None,
                       "motivo": f"{type(exc).__name__}: {exc}"} for c in registro["rutas"]}
        comparacion = resiliencia.comparar(metricas)
        comparacion["tiempo_modelo_ms"] = round((time.perf_counter() - inicio) * 1000, 3)
        return metricas, comparacion


def simular_plan(conn, plan_id, semilla):
    registro = _recuperar_plan(plan_id)
    modelo = _modelo_del_plan(conn, registro)
    escenario = simulacion.sortear(modelo, semilla)
    eventos = escenario["eventos"]
    aristas, nodos = modelo["aristas"], modelo["nodos"]
    incidentes = defaultdict(set)
    for e, datos in aristas.items():
        incidentes[int(datos["source"])].add(e)
        incidentes[int(datos["target"])].add(e)
    afectados_e, afectados_n = defaultdict(list), defaultdict(list)
    for indice, evento in enumerate(eventos):
        if not evento["ocurre"]:
            continue
        ids_e = set(evento["aristas"])
        for n in evento["nodos"]:
            afectados_n[n].append(indice)
            if evento["fuente"] in ("A1", "A3"):
                ids_e.update(incidentes[n])
        # Una intersección indisponible también impide usar sus calles incidentes.
        for e in ids_e:
            afectados_e[e].append(indice)
        evento["aristas"] = sorted(ids_e)

    def publico(ev):
        return {k: ev[k] for k in ("id", "fuente", "efecto", "motivo", "probabilidad", "numero_0_100",
                                    "umbral_pct", "ocurre", "determinista", "desde", "hasta")}

    def feature(i, datos, indices):
        return {"type": "Feature", "geometry": datos["geom"],
                "properties": {"id": i, "nombre": datos.get("name"),
                               "estado": "confirmada" if any(eventos[j]["determinista"] for j in indices) else "sorteada",
                               "motivos": sorted({eventos[j]["motivo"] for j in indices}),
                               "efectos": sorted({eventos[j]["efecto"] for j in indices}),
                               "eventos_idx": indices}}

    t0, t1 = _instante(escenario["t0"]).timestamp(), _instante(escenario["t1"]).timestamp()
    tecnicas = _evaluar_rutas(registro["rutas"], eventos, aristas, t0, t1)
    inicio_resiliencia = time.perf_counter()
    metricas, comparacion = _metricas_servicio(
        registro, modelo, {e["id"] for e in eventos if e["ocurre"]})
    recuperadas = recuperacion.replanificar(conn, registro, modelo, eventos)
    for codigo, nueva in recuperadas.items():
        m, rec = metricas[codigo], nueva["recuperacion"]
        antes = m.get("R_escenario")
        m.update({"R": rec["R"], "R_escenario": rec["R"], "tipo": "escenario_recuperado",
                  "estado": "ok" if rec["R"] is not None else "no_disponible",
                  "motivo": None if rec["R"] is not None else "No hay señales originales requeridas.",
                  "R_sin_recuperacion": antes,
                  "ganancia_recuperacion": rec["R"]-antes if rec["R"] is not None and antes is not None else None,
                  "cobertura_escenario": rec["R"], "senales_escenario": rec["senales_atendidas"],
                  "delta_escenario": rec["R"]-m["cobertura_pre"] if rec["R"] is not None and m.get("cobertura_pre") is not None else None,
                  "cumplimiento_A4": rec["cumplimiento_A4"],
                  "supuestos": ["Recuperación antes del despegue con bases y sectores fijos; no reacción en pleno vuelo.",
                                 "A1/A3 excluyen zonas durante la jornada; A2 suspende salidas sin desvío espacial.",
                                 "R conserva como denominador las señales originales, incluidas las pendientes.",
                                 "A4 añade inspecciones prioritarias en su sector; su cumplimiento se informa por separado."]})
        tecnicas[codigo].update(nueva)
    comparacion = resiliencia.comparar(metricas)
    comparacion["observaciones"] = ["Comparación del servicio original recuperado en este escenario; consultar también cumplimiento A4."]
    for codigo, nueva in recuperadas.items():
        detalle = nueva.get("detalle", {})
        if detalle.get("sectores_sin_milp") or detalle.get("nota"):
            comparacion["observaciones"].append(f"{codigo}: " + (detalle.get("nota") or
                f"conserva T3 en {detalle['sectores_sin_milp']} sectores; se compara el plan híbrido."))
    comparacion["semilla"] = escenario["semilla"]
    comparacion["tiempo_evaluacion_ms"] = round((time.perf_counter() - inicio_resiliencia) * 1000, 3)
    for codigo, tecnica in tecnicas.items():
        tecnica["resiliencia"] = metricas[codigo]
    advertencias = list(modelo["advertencias"])
    advertencias.append("Escenario conocido antes de despegar: A1/A3 cierran zonas toda la jornada; A2 suspende por horario. No se simula emergencia en vuelo ni reprogramación al día siguiente.")
    if any(t["fuera_ventana"] for t in tecnicas.values()):
        advertencias.append("Hay salidas total o parcialmente fuera de la jornada simulada; "
                            "los cruces informados corresponden a los eventos de esa ventana.")
    return {"plan_id": plan_id, "semilla": escenario["semilla"],
            "id_modelo": modelo["id_modelo"],
            "t0": escenario["t0"], "t1": escenario["t1"], "regla": "U < 100 × p",
            "advertencias": advertencias,
            "resumen": {"eventos_sorteados": sum(not e["determinista"] for e in eventos),
                        "eventos_ocurridos": sum(e["ocurre"] and not e["determinista"] for e in eventos),
                        "bloqueos_confirmados": sum(e["determinista"] and e["fuente"] != "A4" for e in eventos),
                        "aristas_afectadas": len(afectados_e), "nodos_afectados": len(afectados_n),
                        "rutas_afectadas": sum(t["rutas_afectadas"] for t in tecnicas.values())},
            "eventos": [{**publico(e), "aristas_afectadas": len(e["aristas"]) if e["ocurre"] else 0,
                         "nodos_afectados": len(e["nodos"]) if e["ocurre"] else 0} for e in eventos],
            "capas": {"aristas": {"type": "FeatureCollection", "features": [
                feature(i, aristas[i], evs) for i, evs in sorted(afectados_e.items())]},
                      "nodos": {"type": "FeatureCollection", "features": [
                feature(i, nodos[i], evs) for i, evs in sorted(afectados_n.items())]}},
            "tecnicas": tecnicas, "comparacion_resiliencia": comparacion}
