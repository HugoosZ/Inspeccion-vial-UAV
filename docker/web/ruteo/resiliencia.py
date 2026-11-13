"""Servicio de inspección conservado por un plan de flota (punto 8).

La unidad de servicio es la señal requerida original, incluso cuando su calle
queda postergada o fuera de alcance. Cada tarea aporta su conteo de señales,
sin suponer una densidad uniforme por metro de calle. Para cada salida
factible, la probabilidad de completarla es el producto de las supervivencias
de sus eventos únicos y de los eventos de sus dependencias. Un bloqueo
confirmado anula esa probabilidad. Una salida comprende acceso, inspección y
retorno.

Este módulo no consulta datos ni sortea fallas: recibe las exposiciones que
el punto 7 ya proyectó sobre los recorridos y horarios del mismo plan. Se mide
la absorción de la perturbación sobre la misión completa, sin recuperación ni
respaldos implícitos entre drones y sin modificar el plan. Esta es la referencia
SIN recuperación; recuperacion.py evalúa las rutas recalculadas y
simulacion_servicio.py publica ambas métricas para el mismo escenario.
"""

from collections.abc import Mapping
from graphlib import CycleError, TopologicalSorter
from numbers import Integral
import math


TECNICAS = ("T1", "T2", "T3", "T4")

SUPUESTOS = (
    "El denominador conserva todas las señales requeridas originales, incluidas "
    "las de tareas postergadas y fuera de alcance.",
    "Cada tarea se pondera por su conteo de señales, sin suponer una densidad "
    "uniforme por metro de calle.",
    "Una salida aporta servicio solo si es factible, cabe completa en la jornada "
    "y sus dependencias también son factibles.",
    "La exposición comprende acceso, inspección y retorno, con los mismos "
    "eventos e intervalos de recorrido del punto 7.",
    "Cada evento se cuenta una sola vez por salida y sus dependencias; los "
    "eventos probabilísticos distintos se consideran independientes.",
    "Un bloqueo confirmado impide completar la salida; una salida afectada "
    "pierde todas sus tareas bajo el supuesto conservador de salida completa.",
    "En la referencia esperada y sin recuperación, cada tarea tiene una única "
    "salida factible asignada. La R recuperada se calcula aparte sobre las rutas nuevas.",
)


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


def _claves(valores, campo):
    if isinstance(valores, (str, bytes, Mapping)):
        raise ValueError(f"{campo} debe ser una colección de identificadores.")
    try:
        claves = tuple(valores)
    except TypeError as exc:
        raise ValueError(f"{campo} debe ser una colección de identificadores.") from exc
    if any(not isinstance(clave, str) or not clave for clave in claves):
        raise ValueError(f"{campo} debe contener identificadores de texto no vacíos.")
    return claves


def evaluar(requeridas, salidas, exposiciones, eventos_ocurridos=None):
    """Evalúa un plan sin alterar ninguna entrada.

    ``requeridas`` es ``{tarea_id: senales_originales}``, con conteos enteros
    no negativos. Cada tarea se pondera por su conteo de señales y no por el
    largo de su calle. Cada elemento de ``salidas`` contiene ``id`` (texto
    único), ``dron``, ``tipo``
    (``inspeccion`` o ``traslado``), ``tareas`` (IDs requeridos), ``factible``
    (booleano que ya incluye batería, materialización, retorno y jornada),
    ``dependencias`` (IDs de salidas) y ``eventos`` (claves de exposición).

    ``exposiciones`` es ``{evento_id: p}``, con p en [0, 1]; ``None`` indica
    un bloqueo confirmado. Las exposiciones deben venir recortadas por el
    horario de cada arista/nodo, según el punto 7. Las dependencias propagan
    tanto inviabilidad como exposición, sin repetir eventos compartidos.

    ``eventos_ocurridos=None`` selecciona la esperanza analítica. Una colección
    (también vacía) selecciona el servicio observado del mismo sorteo P7;
    puede incluir eventos del catálogo que no cruzan ninguna salida. Los
    bloqueos confirmados siempre se aplican, aun si no figuran en ella.

    Las coberturas y R están en [0, 1] y usan el denominador original. Los
    deltas son cobertura posterior menos cobertura previa, en esa escala.
    Se rechaza una tarea asignada a varias salidas factibles porque este plan
    no define una política de respaldo. Sin señales requeridas, R y las
    coberturas quedan en None y el estado es ``no_disponible``.
    """
    if not isinstance(requeridas, Mapping) or not isinstance(exposiciones, Mapping):
        raise ValueError("Las requeridas y las exposiciones deben ser diccionarios.")
    senales = {}
    for tarea, valor in requeridas.items():
        if isinstance(valor, bool) or not isinstance(valor, Integral) or valor < 0:
            raise ValueError("La cantidad de señales requeridas debe ser un entero no negativo.")
        senales[tarea] = int(valor)
    probabilidades = {}
    for clave, valor in exposiciones.items():
        if not isinstance(clave, str) or not clave:
            raise ValueError("Los eventos deben tener identificadores de texto no vacíos.")
        if valor is None:
            probabilidades[clave] = None
        else:
            p = _numero(valor, "La probabilidad de indisponibilidad")
            if not 0 <= p <= 1:
                raise ValueError("La probabilidad de indisponibilidad debe estar entre 0 y 1.")
            probabilidades[clave] = p
    ocurridos = (None if eventos_ocurridos is None else
                 frozenset(_claves(eventos_ocurridos, "Los eventos ocurridos")))

    if isinstance(salidas, (str, bytes, Mapping)):
        raise ValueError("Las salidas deben ser una colección de diccionarios.")
    try:
        lista = tuple(salidas)
    except TypeError as exc:
        raise ValueError("Las salidas deben ser una colección de diccionarios.") from exc
    por_id = {}
    for salida in lista:
        if not isinstance(salida, Mapping):
            raise ValueError("Cada salida debe ser un diccionario.")
        clave = salida.get("id")
        if not isinstance(clave, str) or not clave:
            raise ValueError("Cada salida debe tener un identificador de texto no vacío.")
        if clave in por_id:
            raise ValueError(f"El identificador de salida {clave} está repetido.")
        if not isinstance(salida.get("factible"), bool):
            raise ValueError(f"La factibilidad de la salida {clave} debe ser booleana.")
        tipo = salida.get("tipo")
        if tipo not in ("inspeccion", "traslado"):
            raise ValueError(f"El tipo de la salida {clave} debe ser inspeccion o traslado.")
        tareas = salida.get("tareas", ())
        if isinstance(tareas, (str, bytes, Mapping)):
            raise ValueError(f"Las tareas de la salida {clave} deben ser una colección de IDs.")
        try:
            tareas = tuple(tareas)
            tareas_unicas = set(tareas)
        except TypeError as exc:
            raise ValueError(f"Las tareas de la salida {clave} deben contener IDs válidos.") from exc
        if len(tareas_unicas) != len(tareas):
            raise ValueError(f"La salida {clave} tiene tareas repetidas.")
        if tareas_unicas - set(senales):
            raise ValueError(f"La salida {clave} contiene una tarea requerida desconocida.")
        if tipo == "traslado" and tareas:
            raise ValueError(f"El traslado {clave} no puede aportar tareas de inspección.")
        dependencias = _claves(salida.get("dependencias", ()), f"Las dependencias de {clave}")
        eventos = frozenset(_claves(salida.get("eventos", ()), f"Los eventos de {clave}"))
        if eventos - probabilidades.keys():
            raise ValueError(f"La salida {clave} contiene un evento sin probabilidad o bloqueo definido.")
        por_id[clave] = {
            "id": clave, "dron": salida.get("dron"), "tipo": tipo,
            "tareas": tareas, "factible": salida["factible"],
            "dependencias": tuple(dict.fromkeys(dependencias)), "eventos": eventos,
        }
    for clave, salida in por_id.items():
        if set(salida["dependencias"]) - por_id.keys():
            raise ValueError(f"La salida {clave} depende de una salida desconocida.")
    try:
        orden = tuple(TopologicalSorter({i: s["dependencias"] for i, s in por_id.items()}).static_order())
    except CycleError as exc:
        raise ValueError("Las dependencias de las salidas contienen un ciclo.") from exc

    evaluadas, asignadas = {}, set()
    for clave in orden:
        salida = por_id[clave]
        deps = [evaluadas[i] for i in salida["dependencias"]]
        factible = salida["factible"] and all(d["factible_pre"] for d in deps)
        eventos = set(salida["eventos"])
        for dependencia in deps:
            eventos.update(dependencia["eventos"])
        eventos = sorted(eventos)
        confirmados = [e for e in eventos if probabilidades[e] is None]
        supervivencia = (math.prod(1 - probabilidades[e] for e in eventos)
                         if factible and not confirmados else 0.0)
        completa = (None if ocurridos is None else
                    factible and not confirmados and not (set(eventos) & ocurridos))
        tareas = set(salida["tareas"])
        if factible:
            if tareas & asignadas:
                raise ValueError("Una tarea requerida está asignada a varias salidas factibles; "
                                 "el plan no define rutas de respaldo.")
            asignadas.update(tareas)
        cantidad = sum(senales[t] for t in salida["tareas"])
        evaluadas[clave] = {
            "id": clave, "dron": salida["dron"], "tipo": salida["tipo"],
            "tareas": list(salida["tareas"]), "factible": salida["factible"],
            "factible_pre": factible, "dependencias": list(salida["dependencias"]),
            "eventos": eventos, "bloqueos_confirmados": confirmados,
            "probabilidad_completar": supervivencia, "completada_escenario": completa,
            "senales_requeridas": cantidad, "senales_planificadas": cantidad if factible else 0,
            "senales_esperadas": cantidad * supervivencia,
            "senales_escenario": None if completa is None else cantidad if completa else 0,
        }

    detalle = [evaluadas[i] for i in por_id]
    total = sum(senales.values())
    planificadas = sum(s["senales_planificadas"] for s in detalle)
    esperadas = math.fsum(s["senales_esperadas"] for s in detalle)
    observadas = (None if ocurridos is None else
                  sum(s["senales_escenario"] for s in detalle))
    cobertura_pre = min(1.0, planificadas / total) if total else None
    cobertura_esperada = min(cobertura_pre, max(0.0, esperadas / total)) if total else None
    cobertura_escenario = (min(cobertura_pre, max(0.0, observadas / total))
                           if total and observadas is not None else None)
    return {
        "estado": "ok" if total else "no_disponible",
        "motivo": None if total else "El plan no contiene señales requeridas para evaluar.",
        "R": cobertura_esperada if ocurridos is None else cobertura_escenario,
        "R_esperada": cobertura_esperada, "R_escenario": cobertura_escenario,
        "tipo": "esperada" if ocurridos is None else "escenario",
        "cobertura_pre": cobertura_pre, "cobertura_esperada": cobertura_esperada,
        "cobertura_escenario": cobertura_escenario,
        "delta_esperada": cobertura_esperada - cobertura_pre if total else None,
        "delta_escenario": cobertura_escenario - cobertura_pre if cobertura_escenario is not None else None,
        "senales_requeridas": total, "senales_planificadas": planificadas,
        "senales_esperadas": esperadas, "senales_escenario": observadas,
        "por_salida": detalle, "supuestos": list(SUPUESTOS),
    }


def comparar(metricas, campo="R", tolerancia=1e-9):
    """Compara scores sin redondear; una única técnica no produce ganadora.

    Con dos o tres técnicas válidas se informa una comparación parcial. La
    comparación es completa cuando están evaluadas T1, T2, T3 y T4. Todas las
    técnicas a distancia absoluta <= tolerancia del máximo empatan.
    """
    if not isinstance(metricas, Mapping):
        raise ValueError("Las métricas por técnica deben ser un diccionario.")
    if not isinstance(campo, str) or not campo:
        raise ValueError("El campo a comparar debe ser un texto no vacío.")
    tolerancia = _numero(tolerancia, "La tolerancia de comparación")
    if tolerancia < 0:
        raise ValueError("La tolerancia de comparación no puede ser negativa.")
    valores = {}
    for tecnica in TECNICAS:
        metrica = metricas.get(tecnica)
        if not isinstance(metrica, Mapping) or metrica.get("estado") != "ok":
            continue
        valor = metrica.get(campo)
        if valor is None:
            continue
        valor = _numero(valor, f"El score {campo} de {tecnica}")
        if not 0 <= valor <= 1:
            raise ValueError(f"El score {campo} de {tecnica} debe estar entre 0 y 1.")
        valores[tecnica] = valor
    evaluadas = list(valores)
    maximo = max(valores.values()) if valores else None
    if len(evaluadas) < 2:
        estado, ganadoras = "parcial" if evaluadas else "no_disponible", []
        motivo = ("Una única técnica evaluada no permite establecer superioridad."
                  if evaluadas else "No hay técnicas con el score solicitado disponible.")
    else:
        estado = "completa" if len(evaluadas) == len(TECNICAS) else "parcial"
        ganadoras = [t for t, valor in valores.items() if maximo - valor <= tolerancia]
        motivo = ("Comparación de T1, T2, T3 y T4."
                  if estado == "completa" else "Comparación limitada a las técnicas evaluadas.")
        if maximo == 0:
            motivo += " Todas las técnicas evaluadas conservan cero servicio; el empate no implica cumplimiento."
    return {
        "estado": estado, "campo": campo, "tecnicas_evaluadas": evaluadas,
        "tecnicas_requeridas": len(TECNICAS), "ganadoras": ganadoras,
        "R_max": maximo, "motivo": motivo,
    }
