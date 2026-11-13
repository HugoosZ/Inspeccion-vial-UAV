"""Escenarios reproducibles de indisponibilidad para la flota (punto 7).

El punto 5 calcula marginales para una jornada. Aquí se conservan los eventos
que las originan: un incendio alcanza conjuntamente enlaces y nodos, y una
celda meteorológica comparte su realización durante la hora correspondiente.
A2 comparte una realización regional por intervalo; no se interpreta el
máximo horario del punto 5 como probabilidad de toda la campaña.
"""

import hashlib
import importlib.util
import json
import math
import random
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def _probabilidades():
    """Usa la política del punto 5, tanto en el repositorio como en Docker."""
    archivo = Path(__file__).resolve()
    candidatos = [raiz / "Probabilidades" / "generar.py" for raiz in archivo.parents]
    ruta = next((p for p in candidatos if p.is_file()), None)
    if ruta is None:
        raise RuntimeError("No se encuentra Probabilidades/generar.py para cargar el modelo de fallas.")
    spec = importlib.util.spec_from_file_location("ruteo_modelo_probabilidades", ruta)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def _numero(valor, campo):
    if isinstance(valor, bool):
        raise ValueError(f"{campo} debe ser un número finito.")
    try:
        numero = float(valor)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{campo} debe ser un número finito.") from exc
    if not math.isfinite(numero):
        raise ValueError(f"{campo} debe ser un número finito.")
    return numero


def _probabilidad(valor):
    p = _numero(valor, "La probabilidad")
    if not 0 <= p <= 1:
        raise ValueError("La probabilidad debe estar entre 0 y 1.")
    return p


def _geometria(geom):
    """Impide publicar NaN/Infinity en la geometría del escenario."""
    def coordenadas(valor):
        if isinstance(valor, (list, tuple)):
            return [coordenadas(v) for v in valor]
        return _numero(valor, "La coordenada")

    return {"type": geom["type"], "coordinates": coordenadas(geom["coordinates"])}


def _eventos_unicos(eventos):
    """Une las geometrías de un mismo reporte o de una misma celda-hora.

    SOSAFE puede cargar el punto y su geocerca como filas separadas. Ambos
    representan el mismo bloqueo, no dos sucesos independientes.
    """
    unicos = {}
    for original in eventos:
        evento = dict(original)
        evento.setdefault("efecto", {"A1": "desvio", "A2": "suspension", "A3": "desvio", "A4": "inspeccion"}[evento["fuente"]])
        identificador = str(evento["id"])
        evento["id"] = identificador
        if not isinstance(evento["determinista"], bool):
            raise ValueError("El carácter determinista de un evento debe ser booleano.")
        evento["desde"] = _numero(evento["desde"], "El inicio del evento")
        evento["hasta"] = _numero(evento["hasta"], "El fin del evento")
        if evento["desde"] >= evento["hasta"]:
            raise ValueError("El intervalo de un evento debe tener duración positiva.")
        if not evento["determinista"]:
            evento["probabilidad"] = _probabilidad(evento["probabilidad"])
        elif evento["probabilidad"] is not None:
            raise ValueError("Un bloqueo determinista no lleva probabilidad.")
        evento["aristas"] = set(map(int, evento["aristas"]))
        evento["nodos"] = set(map(int, evento["nodos"]))
        anterior = unicos.get(identificador)
        if anterior is None:
            unicos[identificador] = evento
            continue
        if any(anterior[k] != evento[k] for k in ("fuente", "efecto", "desde", "hasta", "determinista", "motivo")):
            raise ValueError(f"El identificador {identificador} representa eventos incompatibles.")
        if not evento["determinista"]:
            anterior["probabilidad"] = max(anterior["probabilidad"], evento["probabilidad"])
        anterior["aristas"].update(evento["aristas"])
        anterior["nodos"].update(evento["nodos"])
    resultado = []
    for identificador in sorted(unicos):
        evento = unicos[identificador]
        resultado.append({**evento, "aristas": sorted(evento["aristas"]), "nodos": sorted(evento["nodos"])})
    return resultado


def firma_red(conn):
    """Detecta recargas del grafo antes de materializar rutas recuperadas."""
    with conn.cursor() as cur:
        cur.execute("""SELECT md5(COALESCE(string_agg(concat_ws(':', id, source, target,
                       cost, reverse_cost, length_m, encode(ST_AsEWKB(geom), 'hex')),
                       '|' ORDER BY id), '')) FROM network_edges""")
        return cur.fetchone()[0]


def cargar(conn, t0, horas):
    """Construye los eventos y la red con los datos actuales de PostGIS.

    Usa el cálculo del punto 5 sobre toda la red para conservar sus radios y
    el periodo histórico global de CONAF. Los siniestros de tránsito son nueva
    demanda y no se utilizan como fallas de vuelo.
    """
    horas = _numero(horas, "El horizonte")
    if horas <= 0:
        raise ValueError("El horizonte debe ser positivo.")
    if not isinstance(t0, datetime):
        raise ValueError("El inicio debe ser una fecha y hora.")
    if t0.tzinfo is None:
        t0 = t0.replace(tzinfo=timezone.utc)
    modelo = _probabilidades()
    aristas_geom, nodos_coords, amenazas = modelo.cargar_desde_bd(conn)
    eventos = []
    configuracion = modelo.cargar_configuracion()
    marginales, t1, advertencias = modelo.calcular(
        aristas_geom, nodos_coords, amenazas, modelo.cargar_siniestros(), t0, horas,
        eventos=eventos, configuracion=configuracion)
    with conn.cursor() as cur:
        cur.execute("SELECT id, source, target, name, length_m, ST_AsGeoJSON(geom, 6) "
                    "FROM network_edges ORDER BY id")
        filas = cur.fetchall()
    aristas = {}
    for identificador, source, target, nombre, longitud, geometria_plan in filas:
        identificador = int(identificador)
        length_m = _numero(longitud, "La longitud del enlace")
        if length_m < 0:
            raise ValueError("La longitud del enlace no puede ser negativa.")
        aristas[identificador] = {
            "source": int(source), "target": int(target), "name": nombre,
            # La huella del plan utiliza esta misma precisión de PostGIS.
            # Redondear después en Python difiere en empates a medio decimal.
            "length_m": length_m, "geom": _geometria(json.loads(geometria_plan)),
        }
    nodos = {int(i): {"geom": _geometria({"type": "Point", "coordinates": coords})}
             for i, coords in nodos_coords.items()}
    eventos = _eventos_unicos(eventos)
    huella_red = firma_red(conn)
    firma = hashlib.sha256(json.dumps({
        "t0": t0.isoformat(), "t1": t1.isoformat(), "configuracion": configuracion,
        "marginales": marginales, "eventos": eventos, "red": huella_red,
    }, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return {
        "t0": t0.isoformat(), "t1": t1.isoformat(),
        "eventos": eventos, "aristas": aristas, "nodos": nodos,
        "probabilidades": {"aristas": {r["id"]: r for r in marginales["e"]},
                           "nodos": {r["id"]: r for r in marginales["v"]}},
        "configuracion_escenarios": configuracion, "id_modelo": firma, "firma_red": huella_red,
        "advertencias": advertencias,
    }


def clima_para_plan(modelo):
    """Avisos iniciales con los mismos intervalos regionales que sorteará P7."""
    por_arista, horas = {}, []
    for evento in modelo["eventos"]:
        if evento["fuente"] != "A2":
            continue
        p = evento["probabilidad"]
        horas.append((evento["desde"], evento["hasta"], p,
                      "ALTO" if evento["determinista"] else None))
        por_arista.update((e, "regional") for e in evento["aristas"])
    return por_arista, {"regional": horas}


def seleccionar_fuentes(modelo, fuentes):
    """Proyecta P5 y su catálogo sobre la misma selección de la consulta."""
    fuentes = sorted(set(fuentes))
    modelo["eventos"] = [e for e in modelo["eventos"] if e["fuente"] in fuentes]
    bloqueos = {"aristas": {}, "nodos": {}}
    for evento in modelo["eventos"]:
        if not evento["determinista"] or evento["fuente"] == "A4":
            continue
        for tipo in bloqueos:
            for identificador in evento[tipo]:
                bloqueos[tipo].setdefault(identificador, set()).add(evento["motivo"])
    for tipo, filas in modelo["probabilidades"].items():
        for identificador, fila in filas.items():
            for fuente in ("A1", "A2", "A3", "A4"):
                if fuente not in fuentes:
                    fila["p_" + fuente.lower()] = 0.0
            fila["p_total"] = round(1 - math.prod(1 - (fila["p_" + f.lower()] or 0)
                                                  for f in fuentes), 6)
            fila["motivos_bloqueo"] = sorted(bloqueos[tipo].get(identificador, ()))
            fila["bloqueado"] = bool(fila["motivos_bloqueo"])
    modelo["fuentes_seleccionadas"] = fuentes
    modelo["id_modelo"] = hashlib.sha256(json.dumps(
        [modelo["id_modelo"], fuentes], separators=(",", ":")).encode()).hexdigest()
    return modelo


def sortear(modelo, semilla):
    """Devuelve una realización sin alterar el modelo ni el generador global.

    Una semilla local derivada de (semilla, id del evento) mantiene el mismo
    número si cambia el orden o se incorporan otros eventos. El número vive
    en [0, 100): p=0 nunca ocurre y p=1 siempre ocurre. Un bloqueo confirmado
    ocurre siempre y no consume un sorteo.
    """
    if isinstance(semilla, bool) or not isinstance(semilla, int) or not 0 <= semilla <= 2_147_483_647:
        raise ValueError("La semilla debe ser un entero entre 0 y 2147483647.")
    eventos = []
    for evento in _eventos_unicos(modelo["eventos"]):
        numero, umbral = None, None
        ocurre = True
        if not evento["determinista"]:
            entrada = json.dumps([semilla, evento["id"]], ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            semilla_evento = int.from_bytes(hashlib.sha256(entrada).digest(), "big")
            numero = random.Random(semilla_evento).random() * 100.0
            umbral = 100.0 * evento["probabilidad"]
            ocurre = numero < umbral
        eventos.append({**evento, "numero_0_100": numero, "umbral_pct": umbral, "ocurre": ocurre})
    return {
        "semilla": semilla, "t0": modelo["t0"], "t1": modelo["t1"], "eventos": eventos,
        "advertencias": list(modelo.get("advertencias", [])),
    }
