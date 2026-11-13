"""
==============================================================================
AMENAZA A2: Lluvia, ráfagas, baja visibilidad y tormentas (Open-Meteo)
==============================================================================
Extract:
  - Pronóstico horario determinista de Open-Meteo (sin autenticación) para el
    centro de cada celda de una grilla de 3 x 3 sobre Maipú.
  - Pronóstico por conjuntos (ensamble) de Open-Meteo para los mismos puntos:
    ECMWF IFS 0,25° (51 miembros: ráfaga y precipitación) y NOAA GEFS
    (31 miembros: visibilidad, que ECMWF no entrega en esta API).
Transform:
  - Cada hora y celda se evalúa contra la política de vuelo del DJI Mavic 3E.
    Se genera una amenaza por celda-hora si el pronóstico determinista supera
    un umbral o si algún miembro del ensamble incumple la política. Cada
    amenaza guarda los miembros (ensambles), con los que
    Probabilidades/generar.py calcula p = fracción de escenarios no aptos.
Load (archivo):
  - data/clima_maipu.geojson  (celdas por hora, EPSG:4326)

Umbrales (DJI Mavic 3E; ajustables por variables de entorno):
  Variable                    MEDIO (penalizar)            ALTO/CRITICO (bloquear)
  precipitación [mm/h]        >= 0,1 (resolución)          >= 0,5  (ALTO)
  ráfaga a 10 m [km/h]        >= 28,8 (8 m/s preventivo)   >= 43,2 (CRITICO, 12 m/s máx. DJI)
  visibilidad [m]             < 470 (contraste 80 % a 35 m) < 100  (ALTO, mínimo DJI)
  weather_code WMO            -                            95, 96, 99 tormenta (CRITICO)
  El umbral de 470 m sale de la geometría de la cámara: ver Probabilidades/README.md.

Uso:
  python extractor.py
Variables opcionales: CLIMA_HORAS (defecto 24), CLIMA_FILAS, CLIMA_COLUMNAS (defecto 3).
"""

import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import comun  # noqa: E402

DATA_DIR = Path(__file__).resolve().parent / "data"
SALIDA = DATA_DIR / "clima_maipu.geojson"
API = "https://api.open-meteo.com/v1/forecast"
API_ENSAMBLE = "https://ensemble-api.open-meteo.com/v1/ensemble"
# Variables que se toman de cada modelo de ensamble (sufijo de la respuesta JSON)
ENSAMBLES = {
    "ecmwf_ifs025_ensemble": {"wind_gusts_10m": "rafaga_kmh", "precipitation": "precipitacion_mm_h"},
    "ncep_gefs_seamless": {"visibility": "visibilidad_m"},
}
MODELOS_ENSAMBLE = "ecmwf_ifs025,gfs_seamless"
VARIABLES = ["precipitation", "wind_speed_10m", "wind_gusts_10m", "visibility", "weather_code"]

ORDEN_NIVEL = {None: 0, "MEDIO": 1, "ALTO": 2, "CRITICO": 3}


def umbrales():
    f = lambda k, d: float(os.getenv(k, d))  # noqa: E731
    return {
        "lluvia_medio": f("UMBRAL_LLUVIA_MEDIO_MM", "0.1"),
        "lluvia_alto": f("UMBRAL_LLUVIA_ALTO_MM", "0.5"),
        "rafaga_medio": f("UMBRAL_RAFAGA_MEDIO_KMH", "28.8"),
        "rafaga_critico": f("UMBRAL_RAFAGA_CRITICO_KMH", "43.2"),
        "visib_medio": f("UMBRAL_VISIBILIDAD_MEDIO_M", "470"),
        "visib_alto": f("UMBRAL_VISIBILIDAD_ALTO_M", "100"),
    }


def evaluar_hora(v, u):
    """Devuelve (nivel, motivos) para los valores v de una hora."""
    motivos, nivel = [], None

    def subir(nuevo, motivo):
        nonlocal nivel
        motivos.append(motivo)
        if ORDEN_NIVEL[nuevo] > ORDEN_NIVEL[nivel]:
            nivel = nuevo

    lluvia, rafaga, visib, codigo = v["precipitation"], v["wind_gusts_10m"], v["visibility"], v["weather_code"]
    if lluvia is not None:
        if lluvia >= u["lluvia_alto"]:
            subir("ALTO", f"lluvia {lluvia} mm/h")
        elif lluvia >= u["lluvia_medio"]:
            subir("MEDIO", f"llovizna {lluvia} mm/h")
    if rafaga is not None:
        if rafaga >= u["rafaga_critico"]:
            subir("CRITICO", f"ráfaga {rafaga} km/h")
        elif rafaga >= u["rafaga_medio"]:
            subir("MEDIO", f"ráfaga {rafaga} km/h")
    if visib is not None:
        if visib < u["visib_alto"]:
            subir("ALTO", f"visibilidad {visib:.0f} m")
        elif visib < u["visib_medio"]:
            subir("MEDIO", f"visibilidad {visib:.0f} m")
    if codigo in (95, 96, 99):
        subir("CRITICO", f"tormenta eléctrica (WMO {codigo})")
    return nivel, motivos


def no_apto(m, u):
    """Un miembro del ensamble incumple la política (mismos umbrales MEDIO)."""
    return ((m.get("rafaga_kmh") is not None and m["rafaga_kmh"] >= u["rafaga_medio"])
            or (m.get("precipitacion_mm_h") is not None and m["precipitacion_mm_h"] >= u["lluvia_medio"])
            or (m.get("visibilidad_m") is not None and m["visibilidad_m"] < u["visib_medio"]))


def p_no_apto(ensambles, u):
    """p = 1 - prod_modelos (1 - fracción de miembros no aptos). Los modelos
    aportan variables distintas y se combinan como independientes."""
    q = 1.0
    for miembros in ensambles.values():
        if miembros:
            q *= 1 - sum(no_apto(m, u) for m in miembros) / len(miembros)
    return 1 - q


def ensamble(celdas):
    """Miembros del pronóstico por conjuntos por celda y hora local.

    Devuelve {(id_celda, 'AAAA-MM-DDTHH:MM'): {modelo: [miembro, ...]}}, donde
    cada miembro es un dict con las variables que aporta ese modelo. En la
    respuesta, el control se llama variable_modelo y los demás
    variable_memberNN_modelo."""
    respuesta = comun.http_get(API_ENSAMBLE, params={
        "latitude": ",".join(str(c["centro_lat"]) for c in celdas),
        "longitude": ",".join(str(c["centro_lon"]) for c in celdas),
        "hourly": "wind_gusts_10m,precipitation,visibility",
        "models": MODELOS_ENSAMBLE,
        "wind_speed_unit": "kmh",
        "timezone": "America/Santiago",
        "forecast_days": 2,
    }, timeout=120)
    puntos = respuesta if isinstance(respuesta, list) else [respuesta]
    if len(puntos) != len(celdas):
        raise RuntimeError(f"Ensamble: se esperaban {len(celdas)} puntos y llegaron {len(puntos)}")
    patron = re.compile(r"^(wind_gusts_10m|precipitation|visibility)(?:_member(\d+))?_(.+)$")
    salida = {}
    for celda, punto in zip(celdas, puntos):
        hourly = punto["hourly"]
        series = {}                     # (modelo, miembro) -> {campo: lista}
        for clave, valores in hourly.items():
            m = patron.match(clave)
            if not m:
                continue
            variable, miembro, modelo = m.group(1), int(m.group(2) or 0), m.group(3)
            campo = ENSAMBLES.get(modelo, {}).get(variable)
            if campo and any(v is not None for v in valores):
                series.setdefault((modelo, miembro), {})[campo] = valores
        for i, t in enumerate(hourly["time"]):
            por_modelo = {}
            for (modelo, _), campos in sorted(series.items()):
                miembro = {campo: valores[i] for campo, valores in campos.items() if valores[i] is not None}
                if miembro:
                    por_modelo.setdefault(modelo, []).append(miembro)
            if por_modelo:
                salida[(celda["id_celda"], t)] = por_modelo
    return salida


def main():
    comun.cargar_env()
    horas = int(os.getenv("CLIMA_HORAS", "24"))
    filas = int(os.getenv("CLIMA_FILAS", "3"))
    columnas = int(os.getenv("CLIMA_COLUMNAS", "3"))
    u = umbrales()
    celdas = comun.grilla(comun.AOI_MAIPU, filas, columnas)

    print(f"[*] Open-Meteo: pronóstico horario para {len(celdas)} celdas de Maipú...")
    respuesta = comun.http_get(API, params={
        "latitude": ",".join(str(c["centro_lat"]) for c in celdas),
        "longitude": ",".join(str(c["centro_lon"]) for c in celdas),
        "hourly": ",".join(VARIABLES),
        "wind_speed_unit": "kmh",
        "timezone": "America/Santiago",
        "forecast_days": 2,
    })
    # Con varias coordenadas Open-Meteo devuelve una lista (una entrada por punto)
    puntos = respuesta if isinstance(respuesta, list) else [respuesta]
    if len(puntos) != len(celdas):
        raise RuntimeError(f"Se esperaban {len(celdas)} pronósticos y llegaron {len(puntos)}")

    print("[*] Open-Meteo Ensemble: ECMWF IFS (51 miembros) y GEFS (31 miembros)...")
    try:
        conjuntos = ensamble(celdas)
        print(f"    [+] {len(conjuntos)} pares celda-hora con miembros del ensamble")
    except RuntimeError as error:
        conjuntos = {}
        print(f"    [!] Sin ensamble ({error}): la probabilidad de clima queda determinista (0 o 1)")

    ahora = datetime.now(timezone.utc)
    limite = ahora + timedelta(hours=horas)
    features, resumen = [], []

    for celda, punto in zip(celdas, puntos):
        tz = timezone(timedelta(seconds=punto.get("utc_offset_seconds", 0)))
        hourly = punto["hourly"]
        maximos = {"precipitacion_mm_h": 0, "rafaga_kmh": 0, "visibilidad_min_m": None,
                   "horas_con_amenaza": 0}

        for i, t in enumerate(hourly["time"]):
            inicio = datetime.fromisoformat(t).replace(tzinfo=tz)
            if inicio + timedelta(hours=1) <= ahora or inicio > limite:
                continue
            v = {k: hourly[k][i] for k in VARIABLES}
            maximos["precipitacion_mm_h"] = max(maximos["precipitacion_mm_h"], v["precipitation"] or 0)
            maximos["rafaga_kmh"] = max(maximos["rafaga_kmh"], v["wind_gusts_10m"] or 0)
            if v["visibility"] is not None:
                vm = maximos["visibilidad_min_m"]
                maximos["visibilidad_min_m"] = v["visibility"] if vm is None else min(vm, v["visibility"])

            nivel, motivos = evaluar_hora(v, u)
            conj = conjuntos.get((celda["id_celda"], t))
            p = round(p_no_apto(conj, u), 4) if conj else None
            if nivel is None and not p:
                continue
            if nivel is None:
                motivos = [f"{100 * p:.0f} % de los escenarios del ensamble no aptos"]
            maximos["horas_con_amenaza"] += 1
            features.append({
                "type": "Feature",
                "geometry": celda["geometry"],
                "properties": {
                    "id_amenaza": f"OM-{celda['id_celda']}-{inicio.strftime('%Y%m%dT%H%M')}",
                    "fuente": "Open-Meteo",
                    "tipo_amenaza": "clima_severo",
                    "id_celda": celda["id_celda"],
                    "centro_lat": celda["centro_lat"],
                    "centro_lon": celda["centro_lon"],
                    # sin amenaza determinista, la hora queda como BAJO (solo probabilidad)
                    "nivel_riesgo": nivel or "BAJO",
                    "accion_grafo": ("NINGUNA" if nivel is None else
                                     "PENALIZAR_ARISTAS" if nivel == "MEDIO" else "BLOQUEAR_ARISTAS"),
                    "factor_penalizacion": 1.0 if nivel is None else 1.5 if nivel == "MEDIO" else "INF",
                    "valido_desde": inicio.isoformat(),
                    "valido_hasta": (inicio + timedelta(hours=1)).isoformat(),
                    "motivos": motivos,
                    "precipitacion_mm_h": v["precipitation"],
                    "viento_kmh": v["wind_speed_10m"],
                    "rafaga_kmh": v["wind_gusts_10m"],
                    "visibilidad_m": v["visibility"],
                    "weather_code": v["weather_code"],
                    "p_no_apto_ensamble": p,
                    "ensambles": conj or None,
                },
            })
        resumen.append({"id_celda": celda["id_celda"], "centro_lat": celda["centro_lat"],
                        "centro_lon": celda["centro_lon"], **maximos})

    print(f"    [+] {len(features)} pares celda-hora con amenaza o escenarios no aptos en las próximas {horas} h")
    comun.guardar_geojson(SALIDA, "amenaza_t3_clima_maipu", features, {
        "amenaza": "A2 - Lluvia, ráfagas, baja visibilidad y tormentas",
        "fuente": "Open-Meteo Forecast API + Ensemble API (ECMWF IFS 0,25° y NOAA GEFS)",
        "ensamble": bool(conjuntos),
        "horizonte_horas": horas,
        "grilla": f"{filas}x{columnas}",
        "umbrales": u,
        "resumen_por_celda": resumen,
    })


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as error:
        print(f"[-] {error}")
        sys.exit(1)
