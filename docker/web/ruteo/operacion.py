"""Operación simulada paso a paso: revisar, completar o postergar una salida.

Es independiente del sorteo P7/P8: descarga fuentes al solicitar cada revisión,
consulta la BD actual y conserva un registro de tareas confirmadas. No controla
un dron real ni conoce su posición o carga en vuelo. Las confirmaciones son del
operador, no inferencias de una ruta calculada.
"""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import math
import os
import threading
import urllib.request
from uuid import uuid4
from zoneinfo import ZoneInfo

from . import datos, flota, jornadas, simulacion


RADIO_A4_M = 1000.0
MAX_TAREAS_REINTENTO = 100


def _instante(valor):
    t = valor if isinstance(valor, datetime) else datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
    if t.tzinfo is None:
        raise ValueError("El horario de operación debe incluir zona horaria.")
    return t.astimezone(timezone.utc)


def _base(modelo, valor):
    if isinstance(valor, dict):
        return {"id": int(valor["id"]), "lon": float(valor["lon"]), "lat": float(valor["lat"])}
    if valor is None:
        raise ValueError("La salida no conserva su base. Vuelve a planificar la campaña.")
    nodo = modelo["nodos"].get(int(valor))
    if nodo is None:
        raise ValueError("La base ya no existe en la red. Vuelve a planificar.")
    lon, lat = nodo["geom"]["coordinates"][:2]
    return {"id": int(valor), "lon": lon, "lat": lat}


def _estado(registro, codigo):
    if codigo not in registro["tecnicas_originales"]:
        raise ValueError("La técnica no forma parte del plan guardado.")
    estados = registro.setdefault("operacion", {})
    if codigo in estados:
        return estados[codigo]
    colas, bases, cursores = {}, {}, {}
    modelo = registro["modelo"]
    for feature in registro["tecnicas_originales"][codigo]["rutas"]["features"]:
        f = deepcopy(feature)
        p = f["properties"]
        d = int(p["dron"])
        colas.setdefault(d, []).append({"feature": f, "disponible": p["inicio"]})
        bases.setdefault(d, _base(modelo, p.get("base_inicio")))
        cursores.setdefault(d, registro["t0"].isoformat())
    for cola in colas.values():
        cola.sort(key=lambda j: int(j["feature"]["properties"]["salida"]))
    asignaciones = {}
    for d, (_, _, _, pasos) in enumerate(registro["instancia"]["plan"], start=1):
        for tipo, sector in pasos:
            if tipo != "sector":
                continue
            base = _base(modelo, sector["base"])
            for tarea in sector["grupo"]:
                asignaciones[tarea["id"]] = {"dron": d, "base": base}
    opciones = []
    for d, (_, _, bs, _) in enumerate(registro["instancia"]["plan"], start=1):
        opciones.extend((d, _base(modelo, b)) for b in bs)
    pendientes = {}
    for tarea in registro["instancia"]["tareas"]:
        e = int(tarea["id"])
        asignacion = asignaciones.get(e)
        if asignacion is None and opciones:
            d, base = min(opciones, key=lambda db: (db[1]["lon"]-tarea["lon"])**2
                         + (db[1]["lat"]-tarea["lat"])**2)
            asignacion = {"dron": d, "base": base}
        if asignacion:
            pendientes[e] = {**asignacion, "disponible": registro["t0"].isoformat()}
    estado = {"colas": colas, "bases": bases, "cursores": cursores,
              "completadas": set(), "a4_atendidas": set(), "pendientes": pendientes,
              "tareas": {int(t["id"]): dict(t) for t in registro["instancia"]["tareas"]},
              "revision": None, "confirmaciones": {}, "historial": [],
              "secuencia": max((int(j["feature"]["properties"]["salida"])
                                 for cola in colas.values() for j in cola), default=0)}
    estados[codigo] = estado
    return estado


def _avance(registro, estado):
    cumplidas = estado["completadas"] & registro["requeridas"].keys()
    return {"senales_completadas": sum(registro["requeridas"][e] for e in cumplidas),
            "completadas": len(cumplidas),
            "pendientes": sorted(registro["requeridas"].keys()-estado["completadas"])}


def _nuevo_job(estado, d, base, tareas, disponible, tipo="inspeccion", destino=None):
    estado["secuencia"] += 1
    destino = destino or base
    bbox = flota.expandir_bbox((min(base["lon"], destino["lon"]), min(base["lat"], destino["lat"]),
                               max(base["lon"], destino["lon"]), max(base["lat"], destino["lat"])),
                              flota.MARGEN_MAX_M)
    p = {"dron": d, "salida": estado["secuencia"], "tipo": tipo,
         "inicio": disponible, "fin": disponible, "vuelo_min": 0,
         "tarea_ids": sorted(tareas), "aristas": [], "base_inicio": base["id"],
         "base_fin": destino["id"], "bbox": bbox, "camino_completo": False,
         "retorno_base": tipo != "inspeccion" or base["id"] == destino["id"]}
    return {"feature": {"type": "Feature", "geometry": {"type": "MultiLineString", "coordinates": []},
                        "properties": p}, "disponible": disponible}


def _encolar_reintentos(estado):
    """Retoma pendientes al acabar la cola del dron, con traslado si cambia de base."""
    en_cola = {e for cola in estado["colas"].values() for j in cola
               for e in j["feature"]["properties"].get("tarea_ids", [])}
    for d, cola in estado["colas"].items():
        if cola:
            continue
        candidatas = [(e, v) for e, v in estado["pendientes"].items()
                      if v["dron"] == d and e not in estado["completadas"] and e not in en_cola]
        if not candidatas:
            continue
        _, primera = min(candidatas, key=lambda ev: (ev[1]["disponible"], ev[0]))
        base = primera["base"]
        grupo = [(e, v) for e, v in candidatas if v["base"]["id"] == base["id"]][:MAX_TAREAS_REINTENTO]
        disponible = max(_instante(estado["cursores"][d]),
                         max(_instante(v["disponible"]) for _, v in grupo)).isoformat()
        if estado["bases"][d]["id"] != base["id"]:
            cola.append(_nuevo_job(estado, d, estado["bases"][d], [], disponible,
                                  tipo="traslado", destino=base))
        cola.append(_nuevo_job(estado, d, base, [e for e, _ in grupo], disponible))


def _actualizar(fuentes):
    if not fuentes:
        return {"fuentes": {}, "detalle": "No hay fuentes seleccionadas."}
    url = os.getenv("ACTUALIZADOR_URL", "http://etl:8090/actualizar")
    solicitud = urllib.request.Request(url, json.dumps({"fuentes": sorted(fuentes)}).encode(),
                                      {"Content-Type": "application/json"})
    try:
        # El ETL permite hasta 600 s por extractor y por cargador. El límite
        # cliente por defecto acompaña sus 120 s; se puede configurar ambos.
        limite = float(os.getenv("REVISION_TIMEOUT_S", str(240 * len(fuentes) + 20)))
        with urllib.request.urlopen(solicitud, timeout=limite) as respuesta:
            datos = json.load(respuesta)
        if not isinstance(datos, dict) or not isinstance(datos.get("fuentes"), dict):
            raise ValueError("Respuesta inválida")
        return datos
    except Exception:
        # La actualización externa es oportunista; no sustituir datos válidos
        # por un vacío ni mostrar la excepción/URL (pueden contener secretos).
        return {"fuentes": {f: {"estado": "local", "detalle": "No se pudo contactar al actualizador; "
                               "se revisarán los datos ya cargados en la base."} for f in sorted(fuentes)}}


def _cierres(modelo):
    eventos = [e for e in modelo["eventos"] if e["determinista"] and e["fuente"] in ("A1", "A3")]
    cierres = {e for ev in eventos for e in ev["aristas"]}
    nodos = {n for ev in eventos for n in ev["nodos"]}
    cierres.update(e for e, g in modelo["aristas"].items() if g["source"] in nodos or g["target"] in nodos)
    return cierres


def _en_area(conn, registro, ids):
    if not ids:
        return set()
    with conn.cursor() as cur:
        area = registro["instancia"].get("area")
        if area:
            cur.execute("SELECT id FROM network_edges WHERE id=ANY(%s) AND "
                        "ST_Intersects(geom,ST_SetSRID(ST_GeomFromGeoJSON(%s),4326))",
                        (list(ids), json.dumps(area)))
        else:
            cur.execute("SELECT id FROM network_edges WHERE id=ANY(%s) AND "
                        "ST_Intersects(geom,ST_MakeEnvelope(%s,%s,%s,%s,4326))",
                        (list(ids), *registro["instancia"]["bbox"]))
        return {int(r[0]) for r in cur.fetchall()}


def _demanda_cercana(conn, registro, estado, modelo, job):
    ids = {e for ev in modelo["eventos"] if ev["fuente"] == "A4" and ev["determinista"]
           for e in ev["aristas"]} - estado["completadas"] - estado["a4_atendidas"]
    ids = _en_area(conn, registro, ids)
    p = job["feature"]["properties"]
    trayecto = p.get("aristas") or p.get("tarea_ids", [])
    puntos = [c for e in trayecto for c in modelo["aristas"][e]["geom"]["coordinates"]]
    if not puntos:
        base = _base(modelo, p["base_inicio"])
        puntos = [[base["lon"], base["lat"]]]
    mx, my = 111320 * math.cos(math.radians(-33.51)), 110574
    cercanas = set()
    for e in ids:
        coords = modelo["aristas"][e]["geom"]["coordinates"]
        if any((mx*(a[0]-b[0]))**2 + (my*(a[1]-b[1]))**2 <= RADIO_A4_M**2
               for a in coords for b in puntos):
            cercanas.add(e)
    return cercanas


def _tarea(modelo, e):
    g = modelo["aristas"][e]
    lon, lat = g["geom"]["coordinates"][len(g["geom"]["coordinates"])//2][:2]
    return {"id": e, "source": g["source"], "target": g["target"], "name": g["name"],
            "length_m": max(g["length_m"], 0.1), "lon": lon, "lat": lat,
            "n_puntos": 0, "tipos": {"inspeccion_A4": 1}}


def _rutear(conn, registro, estado, codigo, job, modelo, cierres, demanda):
    p = job["feature"]["properties"]
    base, destino = _base(modelo, p["base_inicio"]), _base(modelo, p["base_fin"])
    columnas = ["p_"+f.lower() for f in modelo["fuentes_seleccionadas"] if f in ("A1", "A3")]
    prob = {e: 1-math.prod(1-(r.get(k) or 0) for k in columnas)
            for e, r in modelo["probabilidades"]["aristas"].items()}
    red = flota.Red(cierres, {e: flota.factor_resiliente(v) for e, v in prob.items() if v > 0})
    red_dist = flota.Red(solo_distancia=True, cierres_obligatorios=cierres)
    flota.preparar_pesos(conn, red)
    if p["tipo"] == "traslado":
        salida = {"tipo": "traslado", "pasos": [("tramo", base["id"], destino["id"])],
                  "bbox": tuple(p["bbox"]), "tareas": [], "metros": 0}
        salidas = flota.materializar(conn, red_dist if codigo == "T1" else red, [salida])
    else:
        ids = (set(p.get("tarea_ids", [])) | demanda)-estado["completadas"]-cierres
        salidas, fuera, pasos = [], [], []
        for conjunto in (ids & demanda, ids-demanda):
            if not conjunto:
                continue
            grupo = [estado["tareas"].get(e) or _tarea(modelo, e) for e in sorted(conjunto)]
            sal, no, ctxs = flota._rutear_sector(conn, red, base, grupo, registro["capacidad_m"])
            salidas.extend(sal)
            fuera.extend(no)
            pasos.extend(("sector", c) for c in ctxs)
        plan = [(salidas, fuera, [base], pasos)]
        soluciones = flota._resolver_tecnicas(conn, plan, [codigo], red_dist,
                                             registro["preferencias"], registro["capacidad_m"])
        salidas = flota._plan_tecnica(plan, soluciones[codigo]["ordenes"])[0][0]
        flota.materializar(conn, red_dist if codigo == "T1" else red, salidas)
    geoms = datos.geometrias(conn, [e for s in salidas for e in s["aristas"]])
    features = []
    for s in salidas:
        metros = sum(geoms[e]["length_m"] for e in s["aristas"])
        vuelo = metros/registro["preferencias"]["velocidad_crucero_ms"]/60
        props = {**p, "aristas": list(s["aristas"]), "tarea_ids": list(s["tareas"]),
                 "tareas": len(s["tareas"]), "km": metros/1000, "metros_recorrido": metros,
                 "vuelo_min": vuelo, "bbox": list(s["bbox"]),
                 "camino_completo": s.get("camino_completo", False), "aristas_bloqueadas": 0,
                 "retorno_base": s["tipo"] == "traslado" or base["id"] == destino["id"]}
        features.append({"type": "Feature", "geometry": {"type": "MultiLineString",
                         "coordinates": [geoms[e]["coords"] for e in s["aristas"]]}, "properties": props})
    return features


def revisar(conn, registro, codigo):
    """Actualiza amenazas y propone una salida; no confirma tareas automáticamente."""
    with registro.setdefault("cerrojo_operacion", threading.Lock()):
        estado = _estado(registro, codigo)
        if estado["revision"] is not None:
            return deepcopy(estado["revision"]["publico"])
        # Una nueva demanda A4 puede haber adelantado la inspección de una
        # tarea que también figuraba en otra salida original. No repetirla.
        for cola in estado['colas'].values():
            while cola and cola[0]['feature']['properties']['tipo'] == 'inspeccion' and not (
                    set(cola[0]['feature']['properties'].get('tarea_ids', [])) - estado['completadas']):
                cola.pop(0)
        _encolar_reintentos(estado)
        prefs = registro["preferencias"]
        candidatos = []
        for d, cola in estado["colas"].items():
            if not cola:
                continue
            job = cola[0]
            disponible = max(_instante(job["disponible"]), _instante(estado["cursores"][d]))
            ini, _ = jornadas.programar(disponible, job["feature"]["properties"].get("vuelo_min", 0), prefs)
            candidatos.append((ini, d, job))
        if not candidatos:
            pendientes = _avance(registro, estado)
            return {"estado": "finalizada" if not pendientes['pendientes'] else 'sin_ruta',
                    "tecnica": codigo, "autorizada": False,
                    "revision_id": None, "advertencias": ["No quedan salidas programables."],
                    "rutas": {"type": "FeatureCollection", "features": []}, **pendientes}
        ini, d, job = min(candidatos, key=lambda c: (c[0], c[1]))
        fuentes = set(prefs["amenazas"])
        if not prefs["considerar_clima"]:
            fuentes.discard("A2")
        conn.rollback()
        actualizacion = _actualizar(fuentes)
        modelo = simulacion.seleccionar_fuentes(simulacion.cargar(conn, ini, prefs["jornada_h"]), fuentes)
        if modelo.get("firma_red") != registro["modelo"].get("firma_red"):
            raise ValueError("La red cambió desde la planificación. Vuelve a planificar antes de revisar salidas.")
        cierres = _cierres(modelo)
        demanda = (_demanda_cercana(conn, registro, estado, modelo, job)
                   if job["feature"]["properties"]["tipo"] == "inspeccion" else set())
        base = _base(modelo, job["feature"]["properties"]["base_inicio"])
        for e in demanda:
            estado["tareas"].setdefault(e, _tarea(modelo, e))
            estado["pendientes"].setdefault(e, {"dron": d, "base": base, "disponible": ini.isoformat()})
        try:
            features = _rutear(conn, registro, estado, codigo, job, modelo, cierres, demanda)
        finally:
            conn.rollback()
        tipo, avisos = "autorizada", list(modelo["advertencias"])
        extras = []
        feature = features[0] if features else None
        fin = ini
        if feature is None:
            tipo = "bloqueada"
            avisos.append("No hay una salida factible para estas tareas con las amenazas actuales.")
        else:
            p = feature["properties"]
            ajustado, fin = jornadas.programar(ini, p["vuelo_min"], prefs)
            if ajustado != ini:
                tipo = "requiere_revision_otra_jornada"
                avisos.append("El desvío no cabe en la ventana actual. Postergue y revise en la siguiente jornada.")
            p.update({"inicio": ajustado.isoformat(), "fin": fin.isoformat(),
                      "jornada_fecha": ajustado.astimezone(ZoneInfo(prefs["zona_horaria"])).date().isoformat()})
            if (not p["camino_completo"] or not p["retorno_base"]
                    or p["metros_recorrido"] > registro["capacidad_m"]+1e-6
                    or set(p["aristas"]) & cierres):
                tipo = "inviable"
            elif tipo == "autorizada" and "A2" in fuentes:
                if not simulacion.clima_cubierto(modelo, ajustado, fin):
                    tipo = "sin_pronostico"
                    avisos.append("El pronóstico disponible no cubre toda la salida; no se autoriza por ausencia de datos.")
                elif any(ev["fuente"] == "A2" and ev["desde"] < fin.timestamp()
                         and ev["hasta"] > ajustado.timestamp()
                         and (ev["determinista"] or (ev["probabilidad"] or 0) >= flota.UMBRAL_CLIMA_REPROGRAMAR)
                         for ev in modelo["eventos"]):
                    tipo = "suspendida_A2"
                    avisos.append("La política meteorológica requiere suspender esta salida.")
            extras = features[1:]
        revision_id = uuid4().hex
        publico = {"estado": tipo, "autorizada": tipo == "autorizada", "revision_id": revision_id,
                   "tecnica": codigo, "dron": d, "salida": job["feature"]["properties"]["salida"],
                   "inicio": feature["properties"]["inicio"] if feature else ini.isoformat(),
                   "fin": fin.isoformat(), "id_modelo": modelo["id_modelo"],
                   "actualizacion": actualizacion,
                   "rutas": {"type": "FeatureCollection", "features": [feature] if feature else []},
                   "salidas_adicionales": len(extras), "nuevas_tareas_A4": sorted(demanda),
                   "advertencias": avisos+["Operación simulada con confirmación manual; no controla un dron real.",
                       "A4 prioriza nuevas tareas a menos de 1 km del recorrido actual; no repite aristas ya completadas."],
                   **_avance(registro, estado)}
        estado["revision"] = {"publico": publico, "job": job, "feature": feature,
                              "extras": extras, "demanda": demanda, "base": base}
        return deepcopy(publico)


def confirmar(registro, codigo, revision_id, completada):
    """Confirma el vuelo simulado o conserva sus tareas para una jornada posterior."""
    if not isinstance(completada, bool):
        raise ValueError("completada debe ser true o false")
    with registro.setdefault("cerrojo_operacion", threading.Lock()):
        estado = _estado(registro, codigo)
        if revision_id in estado["confirmaciones"]:
            previo = estado["confirmaciones"][revision_id]
            if previo["completada"] != completada:
                raise ValueError("La revisión ya fue confirmada con una decisión distinta.")
            return deepcopy(previo["respuesta"])
        revision = estado["revision"]
        if revision is None or revision["publico"]["revision_id"] != revision_id:
            raise ValueError("La revisión no existe o ya no es la actual.")
        publico = revision["publico"]
        if completada and not publico["autorizada"]:
            raise ValueError("La salida no está aprobada; solo puede postergarse.")
        d = publico["dron"]
        cola = estado["colas"][d]
        if not cola or cola[0] is not revision["job"]:
            raise ValueError("La cola cambió; revisa nuevamente la salida.")
        prefs = registro["preferencias"]
        if completada:
            p = revision["feature"]["properties"]
            estado["completadas"].update(p["tarea_ids"])
            estado["a4_atendidas"].update(set(p["tarea_ids"]) & revision["demanda"])
            estado["bases"][d] = {**revision["base"], "id": int(p["base_fin"])}
            # Traslado conserva la coordenada del destino (no de la base inicial).
            if p["base_fin"] != p["base_inicio"]:
                estado["bases"][d] = _base(registro["modelo"], p["base_fin"])
            estado["cursores"][d] = (_instante(p["fin"])+timedelta(minutes=prefs["tiempo_recarga_min"])).isoformat()
            cola.pop(0)
            nuevas = []
            for f in revision["extras"]:
                estado["secuencia"] += 1
                f["properties"]["salida"] = estado["secuencia"]
                nuevas.append({"feature": f, "disponible": estado["cursores"][d]})
            cola[:0] = nuevas
        else:
            fecha = _instante(publico["inicio"]).astimezone(ZoneInfo(prefs["zona_horaria"])).date()+timedelta(days=1)
            siguiente, _ = jornadas.ventana(fecha, prefs)
            p = revision["job"]["feature"]["properties"]
            for e in p.get("tarea_ids", []):
                if e in estado["pendientes"]:
                    estado["pendientes"][e]["disponible"] = siguiente.isoformat()
            if p["tipo"] == "traslado":
                # No fingir que se alcanzó la base siguiente.
                revision["job"]["disponible"] = siguiente.isoformat()
                estado["cursores"][d] = siguiente.isoformat()
            else:
                cola.pop(0)
        estado["revision"] = None
        respuesta = {"estado": "confirmada" if completada else "postergada", "tecnica": codigo,
                     "revision_id": revision_id, **_avance(registro, estado)}
        estado["confirmaciones"][revision_id] = {"completada": completada, "respuesta": respuesta}
        estado["historial"].append({"revision_id": revision_id, "dron": d, "salida": publico["salida"],
                                    "inicio": publico["inicio"], "fin": publico["fin"],
                                    "estado_revision": publico["estado"], "completada": completada,
                                    "id_modelo": publico["id_modelo"], "actualizacion": publico["actualizacion"]})
        return deepcopy(respuesta)
