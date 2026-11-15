"""Calendario de operación por ventanas diarias fijas, sin vuelos nocturnos.

La ventana la configura el operador: no calcula amanecer ni atardecer.
Las funciones devuelven instantes UTC para que vuelos y pausas representen
tiempo transcurrido incluso al cambiar el horario de verano. No consulta
amenazas, modifica rutas ni añade pausas de batería.
"""

from datetime import date, datetime, time, timedelta, timezone
import math
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def politica(prefs):
    """Valida y devuelve solamente los parámetros del calendario diario."""
    hora = prefs.get("hora_inicio_jornada", "08:00")
    if not isinstance(hora, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", hora):
        raise ValueError("hora_inicio_jornada debe tener formato HH:MM (00:00 a 23:59).")
    zona = prefs.get("zona_horaria", "America/Santiago")
    try:
        ZoneInfo(zona)
    except (ZoneInfoNotFoundError, TypeError, ValueError) as exc:
        raise ValueError("zona_horaria debe ser una zona IANA válida; comprueba tzdata.") from exc
    valor = prefs.get("jornada_h", 8.0)
    try:
        horas = float(valor)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("jornada_h debe ser un número positivo y finito.") from exc
    if isinstance(valor, bool) or not math.isfinite(horas) or horas <= 0:
        raise ValueError("jornada_h debe ser un número positivo y finito.")
    h, m = map(int, hora.split(":"))
    if h * 60 + m + horas * 60 > 1440:
        raise ValueError("La ventana diaria no puede extenderse después de medianoche.")
    return {"hora_inicio_jornada": hora, "zona_horaria": zona, "jornada_h": horas}


def _utc(instante):
    if not isinstance(instante, datetime) or instante.tzinfo is None:
        raise ValueError("El instante debe ser datetime con zona horaria.")
    return instante.astimezone(timezone.utc)


def ventana(fecha, prefs):
    """Inicio inclusivo y cierre de una fecha operativa, ambos en UTC.

    Si una frontera local no existe por un adelanto horario, se normaliza al
    instante real posterior. En una frontera ambigua se elige la primera
    ocurrencia al abrir y la última al cerrar.
    """
    p = politica(prefs)
    zona = ZoneInfo(p["zona_horaria"])
    if isinstance(fecha, datetime):
        fecha = _utc(fecha).astimezone(zona).date()
    if not isinstance(fecha, date):
        raise ValueError("La fecha operativa debe ser date o datetime con zona horaria.")
    inicio_local = datetime.combine(fecha, time.fromisoformat(p["hora_inicio_jornada"]))
    fin_local = inicio_local + timedelta(hours=p["jornada_h"])
    inicio = inicio_local.replace(tzinfo=zona, fold=0).astimezone(timezone.utc)
    fin = fin_local.replace(tzinfo=zona, fold=1).astimezone(timezone.utc)
    if fin <= inicio:
        # Una jornada muy corta podría quedar íntegramente dentro de una hora
        # inexistente. Ese día no ofrece una ventana operativa utilizable.
        fin = inicio
    return inicio, fin


def programar(disponible, duracion_min, prefs):
    """Ubica un vuelo entero en la primera ventana donde cabe.

    ``disponible`` ya debe incluir cualquier pausa de batería pendiente.
    No se parte un vuelo entre días ni se añade otra pausa al reabrir.
    """
    p = politica(prefs)
    disponible = _utc(disponible)
    try:
        minutos = float(duracion_min)
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError("La duración del vuelo debe ser finita y no negativa.") from exc
    if isinstance(duracion_min, bool) or not math.isfinite(minutos) or minutos < 0:
        raise ValueError("La duración del vuelo debe ser finita y no negativa.")
    if minutos > p["jornada_h"] * 60 + 1e-9:
        raise ValueError("Una salida completa supera la duración de la jornada diaria.")
    duracion = timedelta(minutes=minutos)
    fecha = disponible.astimezone(ZoneInfo(p["zona_horaria"])).date()
    # Normalmente basta hoy o mañana. La cota evita ciclos sin término ante
    # cambios excepcionales de calendario o una configuración no utilizable.
    for _ in range(370):
        apertura, cierre = ventana(fecha, p)
        inicio = max(disponible, apertura)
        fin = inicio + duracion
        if apertura < cierre and inicio < cierre and fin <= cierre:
            return inicio, fin
        fecha += timedelta(days=1)
    raise ValueError("No se encontró una ventana diaria que permita completar la salida.")


def contiene(inicio, fin, prefs):
    """Indica si el vuelo cabe íntegramente en una única ventana diaria."""
    p = politica(prefs)
    inicio, fin = _utc(inicio), _utc(fin)
    if fin < inicio:
        return False
    apertura, cierre = ventana(inicio, p)
    return apertura < cierre and apertura <= inicio < cierre and fin <= cierre


def resumen_campana(t0, fin, prefs):
    """Fechas y tiempo calendario transcurrido; no estima tiempo de vuelo."""
    p = politica(prefs)
    t0, fin = _utc(t0), _utc(fin)
    if fin < t0:
        raise ValueError("El fin de campaña no puede ser anterior al inicio.")
    zona = ZoneInfo(p["zona_horaria"])
    return {"inicio": t0.isoformat(), "fin": fin.isoformat(),
            "duracion_calendario_min": (fin - t0).total_seconds() / 60,
            "dias_calendario": (fin.astimezone(zona).date() - t0.astimezone(zona).date()).days + 1,
            **p}
