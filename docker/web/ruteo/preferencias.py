"""
Parámetros de planificación configurables desde la interfaz y valores por defecto.

En el código, "usuario" significa la persona que consulta la herramienta y
puede cambiar sus parámetros; los parámetros omitidos toman estos valores
predeterminados. Algunas opciones convierten metadata en criterios del grafo:

  Preferencia               Capa       Efecto
  tipos_senal               M1         qué señaléticas definen las aristas a inspeccionar
  priorizar_siniestros      M4         aristas en zonas de siniestros CRITICA o ALTA suben de
                                       prioridad al elegir las aristas a inspeccionar
  considerar_clima          A2         si se aplica la penalización horaria del clima
  amenazas (flota)          A1-A4      qué amenazas bloquean calles y entran en p(e) del costo
                                       resiliente (T2, T3 y T4); vacía = ruteo sin amenazas
  tecnicas (flota)          -          técnicas de ruteo a ejecutar (T1 a T4, punto 6)

Otras opciones configuran la misión y el dron (velocidad, autonomía, batería,
cantidad de drones y jornada); no son restricciones derivadas de metadata.

La planificación por área con una flota (ruteo/flota.py, POST /api/flota) usa
PREFERENCIAS_FLOTA: cantidad de drones, tipo de bases, jornada, tipos de señal,
clima y parámetros de la batería.
"""

from datetime import datetime, timezone

TIPOS_SENAL_VALIDOS = ("senal_pare", "senal_ceda_el_paso", "semaforo", "cruce_peatonal")
# Categorías de lugares públicos (M2) donde se pueden instalar las bases
CATEGORIAS_BASE_VALIDAS = ("salud_publica", "educacion_publica", "carabineros", "bomberos")
# Amenazas que el ruteo puede considerar (bloqueos y probabilidad de falla del punto 5)
AMENAZAS_VALIDAS = ("A1", "A2", "A3", "A4")
# Técnicas de ruteo comparadas (punto 6 del enunciado), ver ruteo/tecnicas.py
TECNICAS_VALIDAS = ("T1", "T2", "T3", "T4")

PREFERENCIAS_DEFECTO = {
    "k": 2,                                   # rutas alternativas (mínimo k >= 2)
    "hora_salida": None,                      # None = ahora
    "priorizar_siniestros": True,
    "tipos_senal": ["senal_pare", "senal_ceda_el_paso", "semaforo"],
    "considerar_clima": True,
    "radio_corredor_m": 300,                  # ancho del corredor donde se buscan aristas requeridas
    "max_requeridas": 30,                     # tope de |E_R| (aristas a inspeccionar)
    "velocidad_crucero_ms": 10.0,             # para estimar la duración de cada ruta
    "autonomia_km": 18.9,                     # alcance útil: 10 m/s, 45 min, 30 % reserva
    "horizonte_h": 2.0,                       # ventana para evaluar amenazas en la métrica
}

LIMITES = {
    "k": (2, 4),
    "radio_corredor_m": (50, 1500),
    "max_requeridas": (2, 60),
    "velocidad_crucero_ms": (1.0, 25.0),
    "autonomia_km": (1.0, 100.0),
    "horizonte_h": (0.5, 12.0),
}

# ------------------------------------------------------------------------------
# Planificación por área con una flota de drones (ruteo/flota.py)
# ------------------------------------------------------------------------------
# Equipo de referencia del proyecto: DJI Mavic 3E. Los 45 min son la autonomía
# máxima nominal del fabricante; la misión real dependerá de las condiciones.
# La velocidad de 10 m/s y la reserva de batería son supuestos del modelo.
PREFERENCIAS_FLOTA = {
    "n_drones": 3,                            # drones de la flota; cada uno cubre un área
    "categorias_base": list(CATEGORIAS_BASE_VALIDAS),  # recintos públicos (M2) donde instalar las bases
    "bases": "distribuidas",                  # distribuidas: una base por área; centralizada: una para todos
    "tecnicas": list(TECNICAS_VALIDAS),       # técnicas de ruteo a ejecutar y comparar
    "amenazas": list(AMENAZAS_VALIDAS),       # amenazas que bloquean o encarecen las calles
    "limite_milp_s": 60.0,                    # tiempo máximo de Gurobi (T2) para toda el área
    "limite_dpso_s": 20.0,                    # tiempo máximo de la metaheurística (T4) para toda el área
    "hora_salida": None,                      # None = ahora
    "tipos_senal": ["senal_pare", "senal_ceda_el_paso", "semaforo"],
    "considerar_clima": True,                 # avisa qué salidas caen en horas de mal tiempo (A2)
    "velocidad_crucero_ms": 10.0,
    "autonomia_min": 45.0,
    "reserva_bateria_pct": 30.0,
    "tiempo_recarga_min": 15.0,               # cambio de batería entre salidas
    "jornada_h": 8.0,                         # horas de vuelo diurno disponibles
}

LIMITES_FLOTA = {
    "n_drones": (1, 10),
    "velocidad_crucero_ms": (1.0, 25.0),
    "autonomia_min": (5.0, 120.0),
    "reserva_bateria_pct": (0.0, 60.0),
    "tiempo_recarga_min": (0.0, 180.0),
    "jornada_h": (1.0, 24.0),
    "limite_milp_s": (5.0, 600.0),
    "limite_dpso_s": (2.0, 300.0),
}

LISTAS = {"tipos_senal": TIPOS_SENAL_VALIDOS, "categorias_base": CATEGORIAS_BASE_VALIDAS,
          "amenazas": AMENAZAS_VALIDAS, "tecnicas": TECNICAS_VALIDAS}

OPCIONES = {"bases": ("distribuidas", "centralizada")}


def _a_bool(valor):
    if isinstance(valor, bool):
        return valor
    return str(valor).strip().lower() in ("1", "true", "si", "sí", "on", "yes")


def normalizar(entrada, defectos=None, limites=None):
    """Combina lo que ingresó el usuario con los valores por defecto.

    Por defecto usa las preferencias de /api/rutas; la flota pasa
    PREFERENCIAS_FLOTA y LIMITES_FLOTA.
    Devuelve (preferencias, lista_de_campos_que_quedaron_por_defecto).
    """
    defectos = PREFERENCIAS_DEFECTO if defectos is None else defectos
    limites = LIMITES if limites is None else limites
    entrada = {k: v for k, v in (entrada or {}).items() if v not in (None, "")}
    prefs, por_defecto = {}, []
    for clave, defecto in defectos.items():
        if clave not in entrada:
            prefs[clave] = list(defecto) if isinstance(defecto, list) else defecto
            por_defecto.append(clave)
            continue
        valor = entrada[clave]
        if isinstance(defecto, bool):
            valor = _a_bool(valor)
        elif clave in LISTAS:
            lista = valor.split(",") if isinstance(valor, str) else list(valor)
            validos = LISTAS[clave]
            valor = [t.strip() for t in lista if t.strip() in validos]
            # amenazas puede quedar vacía (ruteo sin amenazas); las demás vuelven al defecto
            if not valor and clave != "amenazas":
                valor = list(defecto)
        elif clave == "hora_salida":
            valor = str(valor)
        elif clave in OPCIONES:
            valor = str(valor) if str(valor) in OPCIONES[clave] else defecto
        elif isinstance(defecto, int):
            valor = int(float(valor))
        elif isinstance(defecto, float):
            valor = float(valor)
        if clave in limites:
            minimo, maximo = limites[clave]
            valor = max(minimo, min(maximo, valor))
        prefs[clave] = valor
    return prefs, por_defecto


def hora_salida(prefs):
    """Hora de salida como datetime con zona horaria (UTC si no trae)."""
    valor = prefs.get("hora_salida")
    if not valor:
        return datetime.now(timezone.utc)
    t = datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)
