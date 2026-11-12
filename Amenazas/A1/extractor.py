"""
==============================================================================
AMENAZA A1: Incendios forestales y de pastizales (CONAF)
==============================================================================
Extract (servicios públicos de CONAF en ArcGIS, sin autenticación; son los que
alimentan su página "Situación actual y pronóstico de incendios"):
  - incendios_base      incendios de la temporada en curso (se actualiza a diario)
  - incendios_totales   incendios de la temporada anterior
  - PI                  probabilidad de ignición del día (pronóstico de CONAF)
Transform:
  1. Incendios no extinguidos (En combate, Foco, Bajo observación, Controlado)
     dentro de Maipú y sus alrededores -> polígono circular de exclusión:
        radio = radio equivalente del área quemada + INCENDIOS_RADIO_M
     En combate / foco / bajo observación bloquean las aristas; controlado las
     penaliza (queda humo, pero ya no hay operación aérea de combate).
  2. Incendios extinguidos de ambas temporadas -> punto histórico sin efecto en
     el grafo (accion_grafo = NINGUNA). Sirven para estimar la probabilidad de
     falla y para evidenciar que la extracción funciona aunque hoy no haya fuego.
  3. Probabilidad de ignición -> grilla de 1 km sobre Maipú con la clase de PI
     de CONAF en el centro de cada celda (accion_grafo = NINGUNA).
Horas: CONAF guarda la hora local de Chile como si fuera UTC; se reinterpreta
como hora de Chile (America/Santiago).
Load (archivo):
  - data/incendios_maipu.geojson

Uso:
  python extractor.py
Variables de entorno opcionales:
  INCENDIOS_RADIO_M   margen de exclusión alrededor del fuego en metros (defecto 2000)
  INCENDIOS_MARGEN_M  distancia a Maipú dentro de la que se consideran incendios (defecto 5000)
"""

import math
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(DIR.parent))
sys.path.insert(0, str(DIR.parent.parent / "Metadata"))
import comun  # noqa: E402
import comun_arcgis as ag  # noqa: E402

SALIDA = DIR / "data" / "incendios_maipu.geojson"
CONAF = "https://services5.arcgis.com/A1ELWse9bRAi2JiV/arcgis/rest/services/"
URL_TEMPORADA = CONAF + "incendios_base/FeatureServer/0"
URL_ANTERIOR = CONAF + "incendios_totales/FeatureServer/0"
URL_PI = CONAF + "PI/FeatureServer/0"

# estado CONAF -> (nivel de riesgo, acción sobre el grafo)
ESTADOS_ACTIVOS = {
    "En combate": ("CRITICO", "BLOQUEAR_ARISTAS"),
    "Foco": ("ALTO", "BLOQUEAR_ARISTAS"),
    "Bajo observacion": ("ALTO", "BLOQUEAR_ARISTAS"),
    "Controlado": ("MEDIO", "PENALIZAR_ARISTAS"),
}
CELDA_PI_M = 1000


def zona_horaria_chile():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo("America/Santiago")
    except Exception:  # noqa: BLE001  (sin tzdata en el contenedor)
        return timezone(timedelta(hours=-3))


TZ_CHILE = zona_horaria_chile()


def epoch_inicio(a):
    """Inicio del incendio en ms: incendios_base usa f_inicio (fecha), la temporada
    anterior guarda el texto en f_inicio y la fecha en f_inicio_date_time."""
    valor = a.get("f_inicio_date_time")
    if valor is None and isinstance(a.get("f_inicio"), (int, float)):
        valor = a["f_inicio"]
    return valor


def fecha_local(epoch_ms):
    """CONAF guarda la hora local de Chile como si fuera UTC (en la temporada
    anterior, f_inicio '2026-04-28 10:57' corresponde a 10:57 Z en
    f_inicio_date_time). Se reinterpreta como hora de Chile."""
    if epoch_ms in (None, ""):
        return None
    reloj = datetime.fromtimestamp(int(epoch_ms) / 1000, tz=timezone.utc).replace(tzinfo=None)
    return reloj.replace(tzinfo=TZ_CHILE).isoformat()


def fecha_texto_o_epoch(valor):
    if isinstance(valor, (int, float)):
        return fecha_local(valor)
    return ag.texto(valor)


def radio_quemado_m(sup_ha):
    try:
        return math.sqrt(max(float(sup_ha or 0), 0) * 10_000 / math.pi)
    except (TypeError, ValueError):
        return 0.0


def bbox_ampliado(margen_m):
    lon_min, lat_min, lon_max, lat_max = comun.AOI_MAIPU
    dlon, dlat = comun.metros_a_grados(margen_m, (lat_min + lat_max) / 2)
    return (lon_min - dlon, lat_min - dlat, lon_max + dlon, lat_max + dlat)


def incendios(radio_m, margen_m):
    zona = bbox_ampliado(margen_m)
    features, vistos = [], set()
    for url, temporada in ((URL_TEMPORADA, "en curso"), (URL_ANTERIOR, "anterior")):
        print(f"[*] CONAF: incendios de la temporada {temporada}...")
        filas = ag.consultar(url, bbox=zona)
        n_act = n_hist = 0
        for fila in filas:
            a = fila.get("attributes") or {}
            if a.get("id") in vistos:
                continue
            vistos.add(a.get("id"))
            lon, lat = a.get("lon"), a.get("lat")
            if lon is None or lat is None:
                g = fila.get("geometry") or {}
                lon, lat = g.get("x"), g.get("y")
            if lon is None or lat is None:
                continue
            estado = ag.texto(a.get("estado")) or "Sin estado"
            inicio = fecha_local(epoch_inicio(a))
            comunes = {
                "id_amenaza": f"CONAF-{a.get('id')}",
                "fuente": "CONAF",
                "id_conaf": a.get("id"),
                "nombre_incendio": ag.texto(a.get("nombre")),
                "estado": estado,
                "temporada": temporada,
                "comuna_incendio": ag.texto(a.get("comuna")),
                "superficie_ha": a.get("sup_total"),
                "combustible_inicial": ag.texto(a.get("comb_inicial")),
                "inicio": inicio,
                "control": fecha_texto_o_epoch(a.get("f_control")),
                "extincion": fecha_texto_o_epoch(a.get("f_extincion")),
                "lon": round(float(lon), 6), "lat": round(float(lat), 6),
            }
            if estado in ESTADOS_ACTIVOS:
                nivel, accion = ESTADOS_ACTIVOS[estado]
                radio_total = round(radio_quemado_m(a.get("sup_total")) + radio_m)
                features.append({"type": "Feature",
                                 "geometry": comun.circulo(float(lon), float(lat), radio_total),
                                 "properties": {**comunes, "tipo_amenaza": "incendio_activo",
                                                "nivel_riesgo": nivel, "accion_grafo": accion,
                                                "radio_exclusion_m": radio_total,
                                                "valido_desde": inicio, "valido_hasta": None}})
                n_act += 1
            else:
                features.append({"type": "Feature",
                                 "geometry": {"type": "Point", "coordinates": [comunes["lon"], comunes["lat"]]},
                                 "properties": {**comunes, "tipo_amenaza": "incendio_historico",
                                                "nivel_riesgo": "BAJO", "accion_grafo": "NINGUNA"}})
                n_hist += 1
        print(f"    [+] {len(filas)} registros: {n_act} activos, {n_hist} extinguidos")
    return features


def probabilidad_ignicion():
    """Clase de PI de CONAF en una grilla de 1 km sobre Maipú."""
    print("[*] CONAF: probabilidad de ignición del día...")
    filas = ag.consultar(URL_PI, bbox=comun.AOI_MAIPU, extra={"geometryPrecision": "5"})
    poligonos = [(f["attributes"], f["geometry"]["rings"]) for f in filas if (f.get("geometry") or {}).get("rings")]
    if not poligonos:
        print("    [!] CONAF no publicó PI para Maipú hoy")
        return []
    try:
        limite = ag.limite_maipu()
    except RuntimeError as error:
        print(f"    [!] Sin límite comunal ({error}); la grilla cubre todo el rectángulo")
        limite = None
    lon_min, lat_min, lon_max, lat_max = comun.AOI_MAIPU
    dlon, dlat = comun.metros_a_grados(CELDA_PI_M, (lat_min + lat_max) / 2)
    columnas = math.ceil((lon_max - lon_min) / dlon)
    filas_g = math.ceil((lat_max - lat_min) / dlat)
    features = []
    for fi in range(filas_g):
        for co in range(columnas):
            x0, y0 = lon_min + co * dlon, lat_min + fi * dlat
            cx, cy = x0 + dlon / 2, y0 + dlat / 2
            if limite and not ag.dentro_de(cx, cy, limite):
                continue
            atributos = next((attrs for attrs, anillos in poligonos
                              if sum(ag._punto_en_anillo(cx, cy, r) for r in anillos) % 2 == 1), None)
            if not atributos or atributos.get("label") is None:
                continue
            clase = int(atributos["label"])          # límite superior de la clase (decil)
            dia = datetime.fromisoformat(str(atributos.get("date"))).replace(tzinfo=TZ_CHILE)
            nivel = "ALTO" if clase >= 70 else "MEDIO" if clase >= 40 else "BAJO"
            features.append({"type": "Feature", "geometry": comun.rectangulo(x0, y0, x0 + dlon, y0 + dlat),
                             "properties": {
                                 "id_amenaza": f"CONAF-PI-F{fi}C{co}-{atributos.get('date')}",
                                 "fuente": "CONAF", "tipo_amenaza": "probabilidad_ignicion",
                                 "id_celda": f"PI-F{fi}C{co}",
                                 "clase_pi": f"{max(clase - 9, 1)}-{clase} %",
                                 "probabilidad_ignicion": round((clase - 5) / 100, 2),
                                 "nivel_riesgo": nivel, "accion_grafo": "NINGUNA",
                                 "valido_desde": dia.isoformat(),
                                 "valido_hasta": (dia + timedelta(days=1)).isoformat()}})
    print(f"    [+] {len(features)} celdas de 1 km con probabilidad de ignición")
    return features


def main():
    comun.cargar_env()
    radio_m = int(os.getenv("INCENDIOS_RADIO_M", "2000"))
    margen_m = int(os.getenv("INCENDIOS_MARGEN_M", "5000"))
    features, consultadas = [], []
    try:
        features += incendios(radio_m, margen_m)
        consultadas.append("incendios")
    except RuntimeError as error:
        print(f"[!] Incendios CONAF no disponibles: {error}")
    try:
        features += probabilidad_ignicion()
        consultadas.append("probabilidad_ignicion")
    except RuntimeError as error:
        print(f"[!] Probabilidad de ignición no disponible: {error}")

    if "incendios" not in consultadas:
        print("[-] CONAF no respondió; se conserva el archivo anterior si existe.")
        sys.exit(1)

    resumen = {}
    for f in features:
        resumen[f["properties"]["tipo_amenaza"]] = resumen.get(f["properties"]["tipo_amenaza"], 0) + 1
    comun.guardar_geojson(SALIDA, "amenaza_a1_incendios_maipu", features, {
        "amenaza": "A1 - Incendios forestales y de pastizales",
        "fuente": "CONAF · servicios ArcGIS (incendios_base, incendios_totales, PI)",
        "consultas_exitosas": consultadas,
        "por_tipo": resumen,
        "radio_exclusion_m": radio_m,
        "margen_busqueda_m": margen_m,
        "aoi_maipu_bbox": list(comun.AOI_MAIPU),
    })


if __name__ == "__main__":
    main()
