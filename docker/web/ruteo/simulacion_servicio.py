"""Escenarios reproducibles de indisponibilidad aplicados a planes de flota.

El plan calculado se conserva en memoria y el sorteo utiliza un único modelo
para todas sus técnicas. Los resultados identifican cruces con fallas durante
el horario de cada salida, sin cambiar los recorridos originales.
"""

from collections import OrderedDict, defaultdict
from datetime import datetime
import hashlib
import json
import threading
import time
from uuid import uuid4

from . import simulacion


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
            })
    token = uuid4().hex
    registro = {"creado": time.monotonic(), "rutas": rutas, "huellas": huellas,
                "t0": _instante(resultado["hora_salida"]),
                "horas": float(resultado["preferencias"]["jornada_h"]),
                "modelo": None, "cerrojo": threading.Lock()}
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


def _evaluar_rutas(rutas, eventos, aristas, t0, t1):
    por_arista, por_nodo = defaultdict(list), defaultdict(list)
    for evento in eventos:
        if not evento["ocurre"]:
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
            detalle.append({"dron": ruta["dron"], "salida": ruta["salida"], "tipo": ruta["tipo"],
                            "afectada": bool(ids_eventos), "aristas_falladas": len(fallas_e),
                            "nodos_fallados": len(fallas_n), "eventos": sorted(ids_eventos),
                            "en_ventana": en_ventana, "evaluada": evaluada})
        salida[codigo] = {"rutas_afectadas": sum(r["afectada"] for r in detalle),
                          "salidas_evaluadas": sum(r["evaluada"] for r in detalle),
                          "fuera_ventana": sum(not r["en_ventana"] for r in detalle), "detalle": detalle}
    return salida


def simular_plan(conn, plan_id, semilla):
    registro = _recuperar_plan(plan_id)
    # El modelo queda congelado para comparar semillas sobre las mismas entradas.
    with registro["cerrojo"]:
        if registro["modelo"] is None:
            registro["modelo"] = simulacion.cargar(conn, registro["t0"], registro["horas"])
        modelo = registro["modelo"]
    usadas = {e for lista in registro["rutas"].values() for ruta in lista for e in ruta["aristas"]}
    if usadas - set(modelo["aristas"]):
        raise ValueError("La red cambió desde el cálculo del plan. Vuelve a planificar antes de simular.")
    if any(e not in registro["huellas"] or registro["huellas"][e] != _firma_geometria(
            modelo["aristas"][e]["geom"]["coordinates"]) for e in usadas):
        raise ValueError("La geometría de la red cambió desde el cálculo del plan. Vuelve a planificar.")
    escenario = simulacion.sortear(modelo, semilla)
    eventos = escenario["eventos"]
    aristas, nodos = modelo["aristas"], modelo["nodos"]
    incidentes = defaultdict(set)
    for e, datos in aristas.items():
        incidentes[int(datos["source"])].add(e)
        incidentes[int(datos["target"])].add(e)
    afectados_e, afectados_n = defaultdict(list), defaultdict(list)
    for evento in eventos:
        if not evento["ocurre"]:
            continue
        ids_e = set(evento["aristas"])
        for n in evento["nodos"]:
            afectados_n[n].append(evento)
            ids_e.update(incidentes[n])
        # Una intersección indisponible también impide usar sus calles incidentes.
        for e in ids_e:
            afectados_e[e].append(evento)
        evento["aristas"] = sorted(ids_e)

    def publico(ev):
        return {k: ev[k] for k in ("id", "fuente", "motivo", "probabilidad", "numero_0_100",
                                    "umbral_pct", "ocurre", "determinista", "desde", "hasta")}

    def feature(i, datos, evs):
        return {"type": "Feature", "geometry": datos["geom"],
                "properties": {"id": i, "nombre": datos.get("name"),
                               "estado": "confirmada" if any(e["determinista"] for e in evs) else "sorteada",
                               "motivos": sorted({e["motivo"] for e in evs}),
                               "eventos": [publico(e) for e in evs]}}

    t0, t1 = _instante(escenario["t0"]).timestamp(), _instante(escenario["t1"]).timestamp()
    tecnicas = _evaluar_rutas(registro["rutas"], eventos, aristas, t0, t1)
    advertencias = list(modelo["advertencias"])
    if any(t["fuera_ventana"] for t in tecnicas.values()):
        advertencias.append("Hay salidas total o parcialmente fuera de la jornada simulada; "
                            "los cruces informados corresponden a los eventos de esa ventana.")
    return {"plan_id": plan_id, "semilla": escenario["semilla"],
            "t0": escenario["t0"], "t1": escenario["t1"], "regla": "U < 100 × p",
            "advertencias": advertencias,
            "resumen": {"eventos_sorteados": sum(not e["determinista"] for e in eventos),
                        "eventos_ocurridos": sum(e["ocurre"] and not e["determinista"] for e in eventos),
                        "bloqueos_confirmados": sum(e["determinista"] for e in eventos),
                        "aristas_afectadas": len(afectados_e), "nodos_afectados": len(afectados_n),
                        "rutas_afectadas": sum(t["rutas_afectadas"] for t in tecnicas.values())},
            "eventos": [{**publico(e), "aristas_afectadas": len(e["aristas"]) if e["ocurre"] else 0,
                         "nodos_afectados": len(e["nodos"]) if e["ocurre"] else 0} for e in eventos],
            "capas": {"aristas": {"type": "FeatureCollection", "features": [
                feature(i, aristas[i], evs) for i, evs in sorted(afectados_e.items())]},
                      "nodos": {"type": "FeatureCollection", "features": [
                feature(i, nodos[i], evs) for i, evs in sorted(afectados_n.items())]}},
            "tecnicas": tecnicas}
