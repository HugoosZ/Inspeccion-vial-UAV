"""
Restricciones del usuario a partir de la metadata y sus valores
por defecto.

Cada preferencia transforma una capa de metadata en una restricción sobre el
grafo:

  Preferencia               Metadata   Efecto en el grafo
  evitar_aerodromos         M6         aristas a menos de radio_aerodromo_m -> eliminadas (w = ∞)
  evitar_paraderos          M1         aristas a menos de 25 m de un paradero -> w × factor_paradero
                                       (los buses tapan la señalética al detenerse)
  priorizar_vulnerables     M5         aristas junto a manzanas con V_m > 0 suben de prioridad
                                       al elegir las aristas a inspeccionar
  tipos_senal               M3         qué controles viales definen las aristas requeridas E_R
  considerar_clima          T3         si se aplica la penalización horaria del clima

Los parámetros operacionales del dron (velocidades y autonomía) también se
pueden ajustar, pero tienen valores por defecto razonables.
"""

from datetime import datetime, timezone

TIPOS_SENAL_VALIDOS = ("senal_pare", "senal_ceda_el_paso", "semaforo", "cruce_peatonal")

PREFERENCIAS_DEFECTO = {
    "k": 2,                                   # rutas por algoritmo (mínimo k >= 2)
    "hora_salida": None,                      # None = ahora
    "evitar_aerodromos": True,
    "radio_aerodromo_m": 2000,                # supuesto de diseño; verificar DAN 151 (DGAC)
    "evitar_paraderos": True,
    "factor_paradero": 1.3,
    "priorizar_vulnerables": True,
    "tipos_senal": ["senal_pare", "senal_ceda_el_paso", "semaforo"],
    "considerar_clima": True,
    "radio_corredor_m": 300,                  # ancho del corredor donde se buscan aristas requeridas
    "max_requeridas": 30,                     # tope de |E_R| para que el genético responda rápido
    "velocidad_crucero_ms": 10.0,             # tramos de transición
    "velocidad_inspeccion_ms": 5.0,           # tramos requeridos (captura de imágenes)
    "autonomia_km": 20.0,                     # batería de cada dron
    "horizonte_h": 2.0,                       # ventana para evaluar amenazas en la métrica
}

LIMITES = {
    "k": (2, 4),
    "radio_aerodromo_m": (0, 10000),
    "factor_paradero": (1.0, 5.0),
    "radio_corredor_m": (50, 1500),
    "max_requeridas": (2, 60),
    "velocidad_crucero_ms": (1.0, 25.0),
    "velocidad_inspeccion_ms": (1.0, 25.0),
    "autonomia_km": (1.0, 100.0),
    "horizonte_h": (0.5, 12.0),
}


def _a_bool(valor):
    if isinstance(valor, bool):
        return valor
    return str(valor).strip().lower() in ("1", "true", "si", "sí", "on", "yes")


def normalizar(entrada):
    """Combina lo que ingresó el usuario con los valores por defecto.

    Devuelve (preferencias, lista_de_campos_que_quedaron_por_defecto).
    """
    entrada = {k: v for k, v in (entrada or {}).items() if v not in (None, "")}
    prefs, por_defecto = {}, []
    for clave, defecto in PREFERENCIAS_DEFECTO.items():
        if clave not in entrada:
            prefs[clave] = list(defecto) if isinstance(defecto, list) else defecto
            por_defecto.append(clave)
            continue
        valor = entrada[clave]
        if isinstance(defecto, bool):
            valor = _a_bool(valor)
        elif clave == "tipos_senal":
            lista = valor.split(",") if isinstance(valor, str) else list(valor)
            valor = [t.strip() for t in lista if t.strip() in TIPOS_SENAL_VALIDOS] or list(defecto)
        elif clave == "hora_salida":
            valor = str(valor)
        elif isinstance(defecto, int):
            valor = int(float(valor))
        elif isinstance(defecto, float):
            valor = float(valor)
        if clave in LIMITES:
            minimo, maximo = LIMITES[clave]
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
