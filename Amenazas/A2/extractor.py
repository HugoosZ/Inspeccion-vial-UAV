"""
==============================================================================
AMENAZA A2: Lluvia, ráfagas, baja visibilidad y tormentas (Open-Meteo)
==============================================================================
Extract:
  - Pronóstico horario de Open-Meteo (sin autenticación) para el centro de cada
    celda de una grilla de 3 x 3 sobre Maipú, en una sola llamada multi-punto.
Transform:
  - Cada hora y celda se evalúa contra umbrales operacionales del dron. Si algún
    umbral se supera, se genera una amenaza con la celda como polígono, la
    ventana horaria de validez y la acción sobre el grafo.
Load (archivo):
  - data/clima_maipu.geojson  (celdas con amenaza por hora, EPSG:4326)

Umbrales (supuestos de diseño, ajustables por variables de entorno):
  Variable                    MEDIO (penalizar)     ALTO/CRITICO (bloquear)
  precipitación [mm/h]        >= 0,1                >= 0,5  (ALTO)
  ráfaga a 10 m [km/h]        >= 30                 >= 43   (CRITICO, ~12 m/s)
  visibilidad [m]             < 3000                < 1000  (ALTO)
  weather_code WMO            -                     95, 96, 99 tormenta (CRITICO)

Uso:
  python extractor.py
Variables opcionales: CLIMA_HORAS (defecto 24), CLIMA_FILAS, CLIMA_COLUMNAS (defecto 3).
"""

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import comun  # noqa: E402

DATA_DIR = Path(__file__).resolve().parent / "data"
SALIDA = DATA_DIR / "clima_maipu.geojson"
API = "https://api.open-meteo.com/v1/forecast"
VARIABLES = ["precipitation", "wind_speed_10m", "wind_gusts_10m", "visibility", "weather_code"]

ORDEN_NIVEL = {None: 0, "MEDIO": 1, "ALTO": 2, "CRITICO": 3}


def umbrales():
    f = lambda k, d: float(os.getenv(k, d))  # noqa: E731
    return {
        "lluvia_medio": f("UMBRAL_LLUVIA_MEDIO_MM", "0.1"),
        "lluvia_alto": f("UMBRAL_LLUVIA_ALTO_MM", "0.5"),
        "rafaga_medio": f("UMBRAL_RAFAGA_MEDIO_KMH", "30"),
        "rafaga_critico": f("UMBRAL_RAFAGA_CRITICO_KMH", "43"),
        "visib_medio": f("UMBRAL_VISIBILIDAD_MEDIO_M", "3000"),
        "visib_alto": f("UMBRAL_VISIBILIDAD_ALTO_M", "1000"),
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
            if nivel is None:
                continue
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
                    "nivel_riesgo": nivel,
                    "accion_grafo": "PENALIZAR_ARISTAS" if nivel == "MEDIO" else "BLOQUEAR_ARISTAS",
                    "factor_penalizacion": 1.5 if nivel == "MEDIO" else "INF",
                    "valido_desde": inicio.isoformat(),
                    "valido_hasta": (inicio + timedelta(hours=1)).isoformat(),
                    "motivos": motivos,
                    "precipitacion_mm_h": v["precipitation"],
                    "viento_kmh": v["wind_speed_10m"],
                    "rafaga_kmh": v["wind_gusts_10m"],
                    "visibilidad_m": v["visibility"],
                    "weather_code": v["weather_code"],
                },
            })
        resumen.append({"id_celda": celda["id_celda"], "centro_lat": celda["centro_lat"],
                        "centro_lon": celda["centro_lon"], **maximos})

    print(f"    [+] {len(features)} pares celda-hora con amenaza en las próximas {horas} h")
    comun.guardar_geojson(SALIDA, "amenaza_t3_clima_maipu", features, {
        "amenaza": "A2 - Lluvia, ráfagas, baja visibilidad y tormentas",
        "fuente": "Open-Meteo Forecast API",
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
