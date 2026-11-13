"""Escenarios reproducibles de indisponibilidad para la flota (punto 7).

El punto 5 calcula marginales para una jornada. Aquí se conservan los eventos
que las originan: un incendio alcanza conjuntamente enlaces y nodos, y una
celda meteorológica comparte su realización durante la hora correspondiente.
A2 se sortea por celda-hora, con su fracción de ensamble; no se interpreta el
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
        if any(anterior[k] != evento[k] for k in ("fuente", "desde", "hasta", "determinista", "motivo")):
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
    _, t1, advertencias = modelo.calcular(aristas_geom, nodos_coords, amenazas, [], t0, horas, eventos=eventos)
    with conn.cursor() as cur:
        cur.execute("SELECT id, source, target, name, length_m FROM network_edges ORDER BY id")
        filas = cur.fetchall()
    aristas = {}
    for identificador, source, target, nombre, longitud in filas:
        identificador = int(identificador)
        length_m = _numero(longitud, "La longitud del enlace")
        if length_m < 0:
            raise ValueError("La longitud del enlace no puede ser negativa.")
        aristas[identificador] = {
            "source": int(source), "target": int(target), "name": nombre,
            "length_m": length_m, "geom": _geometria(aristas_geom[identificador]),
        }
    nodos = {int(i): {"geom": _geometria({"type": "Point", "coordinates": coords})}
             for i, coords in nodos_coords.items()}
    return {
        "t0": t0.isoformat(), "t1": t1.isoformat(),
        "eventos": _eventos_unicos(eventos), "aristas": aristas, "nodos": nodos,
        "advertencias": advertencias,
    }


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
