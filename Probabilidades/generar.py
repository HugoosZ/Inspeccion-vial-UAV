"""
==============================================================================
Punto 5 · Indisponibilidad de la red para la inspección con DJI Mavic 3E
==============================================================================
Modelo revisado (separa prohibición, incertidumbre y nueva demanda):

  - bloqueado: prohibición operacional durante la jornada [t0, t0 + H]. Un
    evento confirmado que alcanza el enlace o nodo NO es un sorteo con p < 1:
    incendio activo (A1), alerta de seguridad peligrosa (A3), accidente
    activo (A4) o clima no apto sin ensamble (A2, ver abajo).
  - p_total: proxy de indisponibilidad futura, combinando como independientes
        p_a1 = probabilidad de al menos un incendio cercano en la jornada
               (tasa histórica de CONAF, proceso de Poisson), y
        p_a2 = fracción de escenarios del pronóstico por conjuntos (ensamble
               ECMWF IFS / NOAA GEFS) que incumplen la política de vuelo,
               en la peor hora de la jornada.
    No es una probabilidad observada de accidente del dron.
  - p_accidente: probabilidad de al menos un siniestro de tránsito asociado al
    enlace o nodo en la jornada (CONASET 2020-2025). Es NUEVA DEMANDA de
    inspección (un choque puede derribar señales), no falla del vuelo.
  - riesgo_viento y contraste_visual: índices de costo y de calidad de imagen.

No se convierten escalas subjetivas (BAJO, MEDIO, ALTO...) en probabilidades.
Versión revisada por el grupo; ver Probabilidades/README.md.

Uso:
  python Probabilidades/generar.py
Variables de entorno: PROB_HORIZONTE_H (8), PROB_T0 (ahora, ISO 8601).
"""

import json
import math
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

DIR = Path(__file__).resolve().parent
RAIZ = DIR.parent
SALIDA = DIR / "data" / "probabilidades_maipu.json"
ARCHIVO_SINIESTROS = RAIZ / "Metadata" / "M4" / "data" / "siniestros_maipu.geojson"

# ------------------------------------------------------------------------------
# Parámetros del modelo
# ------------------------------------------------------------------------------
R_HIST_INCENDIOS_M = 2000      # mismo margen de exclusión que A1 (INCENDIOS_RADIO_M)
# No se escala la tasa histórica por PI: requeriría una regresión calibrada.
# El pronóstico PI se visualiza en el mapa, pero no se usa como multiplicador.
DIAS_MIN_HISTORIA = 365

# Política CONSERVADORA del proyecto, no umbral de seguridad certificado:
# ráfaga preventiva de 8 m/s = 28,8 km/h; la ficha DJI indica 12 m/s como máximo.
RAFAGA_PREVENTIVA_KMH = 28.8
# Modelo óptico de calidad de inspección a 20 m de altura. Los parámetros de
# geometría y contraste son HIPÓTESIS DE DISEÑO; validar con detecciones reales.
# La niebla sigue siendo una restricción de vuelo independiente.
ALTURA_VUELO_M = 20.0                 # altura de la cámara sobre el terreno
ALTURA_SENAL_M = 2.0                  # altura aproximada del centro de la señal
DISTANCIA_HORIZONTAL_SENAL_M = 30.0   # separación horizontal supuesta
ANCHO_SENAL_M = 0.60                  # señal de referencia de 60 cm
ANCHO_VIDEO_PX = 3840                 # video 4K del Mavic 3E
FOV_HORIZONTAL_APROX_GRADOS = 84.0    # FOV de la cámara gran angular declarado por DJI
PIXELS_MIN_SENAL = 32.0               # referencia experimental, NO garantía de detección
CONTRASTE_MIN_RELATIVO = 0.80         # hipótesis de calidad, NO umbral certificado
VISIBILIDAD_MIN_OPERACIONAL_M = 100.0 # mínimo de la documentación DJI; la niebla se bloquea aparte

DIAS_CONASET = (datetime(2025, 12, 31) - datetime(2020, 1, 1)).days + 1    # 2.192 días
RADIO_SINIESTRO_M = 30         # un siniestro se asocia al enlace o nodo a menos de 30 m

LAT_REF, LON_REF = -33.51, -70.78
MX = 111_320 * math.cos(math.radians(LAT_REF))
MY = 110_574
PASO_MUESTREO_M = 20
CELDA_INDICE_M = 250


def geometria_inspeccion():
    """Distancia cámara-señal y tamaño proyectado de la señal en el video 4K."""
    if not (0 < CONTRASTE_MIN_RELATIVO < 1):
        raise ValueError("CONTRASTE_MIN_RELATIVO debe estar entre 0 y 1")
    distancia = math.hypot(ALTURA_VUELO_M - ALTURA_SENAL_M, DISTANCIA_HORIZONTAL_SENAL_M)
    ancho_px = (ANCHO_VIDEO_PX * ANCHO_SENAL_M
                / (2 * distancia * math.tan(math.radians(FOV_HORIZONTAL_APROX_GRADOS / 2))))
    return distancia, ancho_px


def visibilidad_requerida_m():
    """Visibilidad que conserva el contraste objetivo a la distancia de inspección.

    Ley de Koschmieder: C(d)/C0 = 0,05 ** (d / V). No es probabilidad de detección."""
    distancia, _ = geometria_inspeccion()
    return distancia * math.log(0.05) / math.log(CONTRASTE_MIN_RELATIVO)


def contraste_relativo(visibilidad_m):
    if visibilidad_m is None:
        return None
    v = float(visibilidad_m)
    if v <= 0:
        return 0.0
    distancia, _ = geometria_inspeccion()
    return 0.05 ** (distancia / v)


# Umbral derivado de la geometría y el contraste (unos 470 m); reemplaza los 3.000 m anteriores.
VISIBILIDAD_OBJETIVO_M = visibilidad_requerida_m()


# ------------------------------------------------------------------------------
# Geometría en metros (proyección local, suficiente a escala comunal)
# ------------------------------------------------------------------------------
def xy(lon, lat):
    return ((lon - LON_REF) * MX, (lat - LAT_REF) * MY)


def lineas(geom):
    if geom["type"] == "LineString":
        return [geom["coordinates"]]
    if geom["type"] == "MultiLineString":
        return geom["coordinates"]
    return []


def muestrear(geom):
    """Puntos (x, y) cada PASO_MUESTREO_M metros a lo largo de una línea."""
    puntos = []
    for linea in lineas(geom):
        p = [xy(*c[:2]) for c in linea]
        puntos.append(p[0])
        for (x1, y1), (x2, y2) in zip(p, p[1:]):
            n = max(1, int(math.hypot(x2 - x1, y2 - y1) // PASO_MUESTREO_M))
            puntos += [(x1 + (x2 - x1) * i / n, y1 + (y2 - y1) * i / n) for i in range(1, n + 1)]
    return puntos


def _dist_segmento(px, py, x1, y1, x2, y2):
    dx, dy = x2 - x1, y2 - y1
    t = 0.0 if dx == dy == 0 else max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - x1 - t * dx, py - y1 - t * dy)


def _en_anillo(px, py, anillo):
    dentro = False
    for (x1, y1), (x2, y2) in zip(anillo, anillo[1:]):
        if (y1 > py) != (y2 > py) and px < (x2 - x1) * (py - y1) / (y2 - y1) + x1:
            dentro = not dentro
    return dentro


class Forma:
    """Geometría de una amenaza en metros, con distancia desde un punto."""

    def __init__(self, geom):
        t = geom["type"]
        self.puntos, self.lineas, self.poligonos = [], [], []
        if t == "Point":
            self.puntos = [xy(*geom["coordinates"][:2])]
        elif t in ("LineString", "MultiLineString"):
            self.lineas = [[xy(*c[:2]) for c in linea] for linea in lineas(geom)]
        elif t == "Polygon":
            self.poligonos = [[[xy(*c[:2]) for c in a] for a in geom["coordinates"]]]
        elif t == "MultiPolygon":
            self.poligonos = [[[xy(*c[:2]) for c in a] for a in p] for p in geom["coordinates"]]
        coords = (self.puntos + [c for linea in self.lineas for c in linea]
                  + [c for p in self.poligonos for a in p for c in a])
        xs, ys = [c[0] for c in coords], [c[1] for c in coords]
        self.caja = (min(xs), min(ys), max(xs), max(ys))

    def distancia(self, px, py):
        d = math.inf
        for x, y in self.puntos:
            d = min(d, math.hypot(px - x, py - y))
        for linea in self.lineas:
            for (x1, y1), (x2, y2) in zip(linea, linea[1:]):
                d = min(d, _dist_segmento(px, py, x1, y1, x2, y2))
        for p in self.poligonos:
            if sum(_en_anillo(px, py, a) for a in p) % 2 == 1:
                return 0.0
            for a in p:
                for (x1, y1), (x2, y2) in zip(a, a[1:]):
                    d = min(d, _dist_segmento(px, py, x1, y1, x2, y2))
        return d


class Indice:
    """Índice de grilla: celda -> elementos cuyos puntos caen en ella."""

    def __init__(self, elementos):
        self.celdas = defaultdict(set)
        for clave, puntos in elementos.items():
            for x, y in puntos:
                self.celdas[(int(x // CELDA_INDICE_M), int(y // CELDA_INDICE_M))].add(clave)

    def candidatos(self, caja, margen):
        x0, y0 = int((caja[0] - margen) // CELDA_INDICE_M), int((caja[1] - margen) // CELDA_INDICE_M)
        x1, y1 = int((caja[2] + margen) // CELDA_INDICE_M), int((caja[3] + margen) // CELDA_INDICE_M)
        vistos = set()
        for i in range(x0, x1 + 1):
            for j in range(y0, y1 + 1):
                vistos |= self.celdas.get((i, j), set())
        return vistos


# ------------------------------------------------------------------------------
# Lectura
# ------------------------------------------------------------------------------
def cargar_desde_bd(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT id, ST_AsGeoJSON(geom) FROM network_edges")
        aristas = {int(i): json.loads(g) for i, g in cur.fetchall()}
        cur.execute("SELECT id, ST_X(geom), ST_Y(geom) FROM network_nodes")
        nodos = {int(i): (float(x), float(y)) for i, x, y in cur.fetchall()}
        cur.execute("""SELECT id, propiedades->>'fuente_etl', tipo, nivel_riesgo, propiedades,
                              ST_AsGeoJSON(geom) FROM temp_amenazas""")
        amenazas = [{"id": i, "fuente": f, "tipo": t, "nivel": n,
                     "props": p if isinstance(p, dict) else json.loads(p or "{}"), "geom": json.loads(g)}
                    for i, f, t, n, p, g in cur.fetchall()]
    return aristas, nodos, amenazas


def cargar_siniestros():
    if not ARCHIVO_SINIESTROS.exists():
        print(f"    [!] No existe {ARCHIVO_SINIESTROS.name}: sin tasa histórica de siniestros")
        return []
    datos = json.loads(ARCHIVO_SINIESTROS.read_text(encoding="utf-8"))
    return [f["geometry"]["coordinates"] for f in datos.get("features", []) if f.get("geometry")]


def instante(valor):
    if not valor:
        return None
    try:
        t = datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
        return t if t.tzinfo else t.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def vigente(props, t0, t1):
    desde, hasta = instante(props.get("valido_desde")), instante(props.get("valido_hasta"))
    return (desde is None or desde < t1) and (hasta is None or hasta > t0)


# ------------------------------------------------------------------------------
# Clima (A2)
# ------------------------------------------------------------------------------
def clima_no_apto(props):
    """Política de indisponibilidad para un escenario dado (no tasa de daño).

    Una variable ausente en el escenario (p. ej., ECMWF no entrega visibilidad)
    no incumple la política."""
    rafaga = props.get("rafaga_kmh")
    lluvia = props.get("precipitacion_mm_h")
    visi = props.get("visibilidad_m")
    niebla = props.get("niebla") or props.get("fog")
    if isinstance(niebla, str):
        niebla = niebla.strip().lower() in ("true", "1", "si", "sí")
    _, px = geometria_inspeccion()
    # Si la geometría deja la señal bajo 32 px, no se logra la calidad objetivo
    # ni con atmósfera limpia: habría que ajustar la misión (altura o distancia).
    calidad_insuficiente = (px < PIXELS_MIN_SENAL or (
        visi is not None and (float(visi) < VISIBILIDAD_MIN_OPERACIONAL_M
                              or contraste_relativo(visi) < CONTRASTE_MIN_RELATIVO)))
    return any((
        rafaga is not None and float(rafaga) >= RAFAGA_PREVENTIVA_KMH,
        lluvia is not None and float(lluvia) > 0,
        calidad_insuficiente,
        bool(niebla),
    ))


def p_clima(props):
    """Fracción de escenarios meteorológicos que incumplen la política.

    - ensambles: {modelo: [escenario, ...]} (A2/extractor.py). Cada modelo
      aporta variables distintas (ECMWF: ráfaga y lluvia; GEFS: visibilidad),
      así que se combinan como independientes: p = 1 - prod (1 - fracción).
    - ensemble_miembros: lista única de escenarios.
    Devuelve None si no hay ensamble: el pronóstico determinista solo da 0/1,
    que es estado del escenario y NO una probabilidad calibrada."""
    ensambles = props.get("ensambles")
    if isinstance(ensambles, dict) and ensambles:
        q = 1.0
        for miembros in ensambles.values():
            if miembros:
                q *= 1 - sum(clima_no_apto(m) for m in miembros) / len(miembros)
        return 1 - q
    miembros = props.get("ensemble_miembros")
    if isinstance(miembros, list) and miembros:
        if not all(isinstance(m, dict) for m in miembros):
            raise ValueError("ensemble_miembros debe contener objetos con variables meteorológicas")
        return sum(clima_no_apto(m) for m in miembros) / len(miembros)
    return None


# ------------------------------------------------------------------------------
# Cálculo: separar prohibición, incertidumbre y nueva demanda
# ------------------------------------------------------------------------------
def calcular(aristas, nodos, amenazas, siniestros, t0, horas):
    if horas <= 0:
        raise ValueError("El horizonte debe ser positivo")
    t1 = t0 + timedelta(hours=horas)
    muestras = {("e", i): muestrear(g) for i, g in aristas.items()}
    muestras.update({("v", i): [xy(*c)] for i, c in nodos.items()})
    muestras = {k: v for k, v in muestras.items() if v}
    indice = Indice(muestras)
    bloqueos = defaultdict(set)
    probs = defaultdict(lambda: defaultdict(list))
    notas = defaultdict(list)
    advertencias = set()
    riesgo_viento = defaultdict(list)
    contraste_visual = defaultdict(list)

    def afectados(forma, radio):
        return [k for k in indice.candidatos(forma.caja, radio)
                if any(forma.distancia(x, y) <= radio for x, y in muestras[k])]

    cache = {}

    def afectados_geom(geom, radio):
        """Las celdas de A2 se repiten en cada hora: misma geometría, mismo resultado."""
        clave = (json.dumps(geom, sort_keys=True), radio)
        if clave not in cache:
            cache[clave] = afectados(Forma(geom), radio)
        return cache[clave]

    historicos = []
    for a in amenazas:
        fuente, tipo, props = a["fuente"], a["tipo"], a["props"]
        if fuente == "A1_incendios" and tipo == "incendio_historico":
            historicos.append(a)
            continue
        if fuente == "A1_incendios" and tipo == "probabilidad_ignicion":
            continue        # sin calibración, PI no multiplica la tasa de Poisson
        if fuente not in ("A1_incendios", "A2_clima", "A3_sosafe", "A4_tomtom"):
            continue
        if not vigente(props, t0, t1):
            continue
        radio = float(props.get("radio_afectacion_m") or 0)
        if fuente == "A3_sosafe" and a["geom"]["type"] == "Point":
            radio = radio or float(props.get("radio_buffer") or 0)
        keys = afectados_geom(a["geom"], radio)

        if fuente == "A1_incendios":
            # Evento activo localizado: zona prohibida; una p no sustituye al bloqueo.
            for k in keys:
                bloqueos[k].add("incendio_activo")

        elif fuente == "A3_sosafe":
            # Solo los eventos de seguridad peligrosos bloquean; el nivel no se
            # transforma en una probabilidad inventada.
            es_peligrosa = bool(props.get("bloqueo_vuelo")) or (
                a["nivel"] in ("ALTO", "CRITICO") and tipo not in ("informativo", "aviso"))
            for k in keys:
                if es_peligrosa:
                    bloqueos[k].add("seguridad_activa")
                else:
                    notas[k].append("alerta_seguridad_informativa")

        elif fuente == "A2_clima":
            if props.get("visibilidad_m") is not None:
                contraste = contraste_relativo(props["visibilidad_m"])
                for k in keys:
                    contraste_visual[k].append(contraste)
            rafaga = props.get("rafaga_kmh")
            if rafaga is not None:
                # Índice de COSTO DE RUTA, no probabilidad de falla.
                riesgo = min(1.0, (max(0.0, float(rafaga)) / RAFAGA_PREVENTIVA_KMH) ** 2)
                for k in keys:
                    riesgo_viento[k].append(riesgo)
            p = p_clima(props)
            if p is not None:
                for k in keys:
                    probs[k]["clima"].append(p)
            else:
                # Sin ensamble, el valor determinista es estado del escenario, no
                # una probabilidad: se trata como prohibición (no simular con él).
                if clima_no_apto(props):
                    for k in keys:
                        bloqueos[k].add("meteorologia_no_apta")
                else:
                    for k in keys:
                        notas[k].append("meteo_apta_en_pronostico")
                advertencias.add("Clima sin ensamble: solo estado determinista del pronóstico")

        elif fuente == "A4_tomtom":
            # Una calle cerrada a los automóviles NO es espacio aéreo prohibido.
            # Un accidente activo sí bloquea: concentra personas y equipos de
            # emergencia bajo la ruta (riesgo en tierra, Primatesta et al., 2020),
            # y además deja la calle marcada para reinspección.
            accion = props.get("accion_grafo")
            estado = props.get("probabilidad")
            for k in keys:
                if props.get("bloqueo_vuelo_confirmado") is True:
                    bloqueos[k].add("restriccion_vuelo_confirmada")
                elif "acciden" in (tipo or "").lower():
                    bloqueos[k].add("accidente_activo")
                    notas[k].append("accidente: revisar si requiere reinspeccion")
                elif accion == "BLOQUEAR_ARISTAS" and estado == "certain":
                    notas[k].append("cierre_vial_terrestre_sin_bloqueo_aereo_confirmado")
                else:
                    notas[k].append("incidente_tomtom")

    # Poisson: probabilidad de AL MENOS un incendio cercano en la jornada.
    # No es directamente probabilidad de falla del dron ni de cierre aéreo.
    if historicos:
        fechas = [instante(h["props"].get("inicio")) for h in historicos]
        fechas = [d for d in fechas if d is not None]
        if fechas:
            dias = max(DIAS_MIN_HISTORIA, (max(fechas) - min(fechas)).days + 1)
            conteo = defaultdict(int)
            for h in historicos:
                for k in afectados(Forma(h["geom"]), R_HIST_INCENDIOS_M):
                    conteo[k] += 1
            for k, n in conteo.items():
                probs[k]["incendio_historico"].append(1 - math.exp(-n / dias * horas / 24))
        else:
            advertencias.add("Historial de incendios sin fechas: no se estima tasa")

    # CONASET: la probabilidad de siniestro estima NUEVA DEMANDA de inspección,
    # nunca p_falla del enlace por la sola presencia de accidentes viales.
    conteo_e, conteo_v = defaultdict(int), defaultdict(int)
    for lon, lat in siniestros:
        px, py = xy(lon, lat)
        mejor = {"e": (RADIO_SINIESTRO_M, None), "v": (RADIO_SINIESTRO_M, None)}
        for k in indice.candidatos((px, py, px, py), RADIO_SINIESTRO_M):
            pts = muestras[k]
            if k[0] == "e":
                d = min((_dist_segmento(px, py, *aa, *bb) for aa, bb in zip(pts, pts[1:])), default=math.inf)
            else:
                d = math.hypot(px - pts[0][0], py - pts[0][1])
            if d <= mejor[k[0]][0]:
                mejor[k[0]] = (d, k)
        if mejor["e"][1] is not None:
            conteo_e[mejor["e"][1]] += 1
        if mejor["v"][1] is not None:
            conteo_v[mejor["v"][1]] += 1
    p_acc = {}
    for counts in (conteo_e, conteo_v):
        for k, n in counts.items():
            p_acc[k] = 1 - math.exp(-n / DIAS_CONASET * horas / 24)

    filas = {"e": [], "v": []}
    # Solo se combinan dos probabilidades, bajo independencia APROXIMADA. Es un
    # proxy conservador de indisponibilidad (incendio cercano futuro + clima),
    # NO una probabilidad observada de accidente del dron.
    claves = set(bloqueos) | set(probs) | set(notas) | set(p_acc) | set(riesgo_viento) | set(contraste_visual)
    for k in claves:
        p_hist = 1 - math.prod(1 - p for p in probs[k].get("incendio_historico", []))
        # Peor hora de la jornada: la calle se cruza una vez, a una hora que aún
        # no se conoce al calcular la tabla.
        p_meteo = max(probs[k].get("clima", []), default=None)
        if p_meteo is None:
            p_pred = p_hist
            calidad = "proxy_historico" if p_hist else "no_estimado"
        else:
            p_pred = 1 - (1 - p_hist) * (1 - p_meteo)
            calidad = "proxy_no_calibrado"
        filas[k[0]].append({
            "id": k[1], "bloqueado": bool(bloqueos[k]),
            "motivos_bloqueo": sorted(bloqueos[k]),
            "p_total": round(p_pred, 6),
            "p_a1": round(p_hist, 6),
            "p_a2": None if p_meteo is None else round(p_meteo, 6),
            "p_a3": None, "p_a4": None,
            "p_accidente": round(p_acc.get(k, 0.0), 6),
            "riesgo_viento": round(max(riesgo_viento[k], default=0.0), 6),
            "contraste_visual": round(min(contraste_visual[k]), 6) if contraste_visual[k] else None,
            "calidad": calidad,
            "observaciones": sorted(set(notas[k])),
        })
    return filas, t1, sorted(advertencias)


def resumen(filas, totales):
    return {nombre: {"total": totales[clave],
                     "elementos_con_registro": len(filas[clave]),
                     "bloqueados": sum(r["bloqueado"] for r in filas[clave]),
                     "con_p_positiva": sum(r["p_total"] > 0 for r in filas[clave]),
                     "p_total_max": max((r["p_total"] for r in filas[clave]), default=0.0),
                     "con_p_accidente": sum(r["p_accidente"] > 0 for r in filas[clave])}
            for clave, nombre in (("e", "aristas"), ("v", "nodos"))}


# ------------------------------------------------------------------------------
# Escritura
# ------------------------------------------------------------------------------
SQL_TABLAS = """
CREATE TABLE IF NOT EXISTS prob_falla_aristas (edge_id BIGINT PRIMARY KEY, p_total DOUBLE PRECISION NOT NULL,
    p_a1 DOUBLE PRECISION, p_a2 DOUBLE PRECISION, p_a3 DOUBLE PRECISION, p_a4 DOUBLE PRECISION,
    t0 TIMESTAMPTZ, t1 TIMESTAMPTZ);
CREATE TABLE IF NOT EXISTS prob_falla_nodos (node_id BIGINT PRIMARY KEY, p_total DOUBLE PRECISION NOT NULL,
    p_a1 DOUBLE PRECISION, p_a2 DOUBLE PRECISION, p_a3 DOUBLE PRECISION, p_a4 DOUBLE PRECISION,
    t0 TIMESTAMPTZ, t1 TIMESTAMPTZ);
"""
COLUMNAS = ("p_total", "p_a1", "p_a2", "p_a3", "p_a4", "p_accidente", "riesgo_viento",
            "contraste_visual", "bloqueado", "motivos_bloqueo", "calidad", "observaciones")


def escribir_bd(conn, filas, t0, t1):
    from psycopg2.extras import execute_values
    with conn, conn.cursor() as cur:
        cur.execute(SQL_TABLAS)
        for tabla in ("prob_falla_aristas", "prob_falla_nodos"):
            # Migración de tablas creadas con el esquema anterior
            for col, tipo in [("p_accidente", "DOUBLE PRECISION DEFAULT 0"),
                              ("riesgo_viento", "DOUBLE PRECISION DEFAULT 0"),
                              ("contraste_visual", "DOUBLE PRECISION"),
                              ("bloqueado", "BOOLEAN DEFAULT FALSE"),
                              ("motivos_bloqueo", "JSONB"), ("calidad", "TEXT"), ("observaciones", "JSONB")]:
                cur.execute(f"ALTER TABLE {tabla} ADD COLUMN IF NOT EXISTS {col} {tipo}")
        for tabla, col, clave in (("prob_falla_aristas", "edge_id", "e"), ("prob_falla_nodos", "node_id", "v")):
            cur.execute(f"TRUNCATE {tabla}")
            datos = [(r["id"], *[json.dumps(r[c]) if c in ("motivos_bloqueo", "observaciones") else r[c]
                                 for c in COLUMNAS], t0, t1) for r in filas[clave]]
            if datos:
                execute_values(cur, f"INSERT INTO {tabla} ({col}, {', '.join(COLUMNAS)}, t0, t1) VALUES %s",
                               datos, page_size=1000)


def _compactas(filas):
    return [[r["id"], *[r[c] for c in COLUMNAS]] for r in filas
            if r["bloqueado"] or r["p_total"] > 0 or r["p_accidente"] > 0 or r["observaciones"]]


def escribir_json(filas, t0, t1, horas, res, advertencias):
    SALIDA.parent.mkdir(parents=True, exist_ok=True)
    distancia, px = geometria_inspeccion()
    salida = {"metadata": {
        "generado_utc": datetime.now(timezone.utc).isoformat(),
        "t0": t0.isoformat(), "t1": t1.isoformat(), "horizonte_h": horas,
        "interpretacion": ("bloqueado: prohibición operacional; p_total: proxy NO calibrado de indisponibilidad "
                           "futura (incendio cercano y clima); p_accidente: nueva tarea potencial, NO falla de vuelo"),
        "dron": "DJI Mavic 3E",
        "parametros": {
            "RAFAGA_PREVENTIVA_KMH": RAFAGA_PREVENTIVA_KMH,
            "VISIBILIDAD_OBJETIVO_M": round(VISIBILIDAD_OBJETIVO_M, 2),
            "ALTURA_VUELO_M": ALTURA_VUELO_M, "ALTURA_SENAL_M": ALTURA_SENAL_M,
            "DISTANCIA_HORIZONTAL_SENAL_M": DISTANCIA_HORIZONTAL_SENAL_M, "ANCHO_SENAL_M": ANCHO_SENAL_M,
            "ANCHO_VIDEO_PX": ANCHO_VIDEO_PX, "FOV_HORIZONTAL_APROX_GRADOS": FOV_HORIZONTAL_APROX_GRADOS,
            "PIXELS_MIN_SENAL": PIXELS_MIN_SENAL, "CONTRASTE_MIN_RELATIVO": CONTRASTE_MIN_RELATIVO,
            "DISTANCIA_OBSERVACION_M": round(distancia, 2), "PIXELS_SENAL_ESTIMADOS": round(px, 2),
            "R_HIST_INCENDIOS_M": R_HIST_INCENDIOS_M, "RADIO_SINIESTRO_M": RADIO_SINIESTRO_M},
        "advertencias": advertencias, "resumen": res,
        "columnas": ["id", *COLUMNAS]},
        # Filas compactas (listas en el orden de "columnas"); se omiten los
        # elementos sin bloqueo, sin probabilidad y sin notas.
        "aristas": _compactas(filas["e"]), "nodos": _compactas(filas["v"])}
    SALIDA.write_text(json.dumps(salida, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"    [+] {SALIDA}")


def main():
    sys.path.insert(0, str(RAIZ / "Metadata"))
    import comun_bd
    comun_bd.cargar_env()
    horas = float(os.getenv("PROB_HORIZONTE_H", "8"))
    t0 = instante(os.getenv("PROB_T0")) or datetime.now(timezone.utc).replace(microsecond=0)
    conn = comun_bd.conectar_bd()
    try:
        aristas, nodos, amenazas = cargar_desde_bd(conn)
        siniestros = cargar_siniestros()
        print(f"[*] Indisponibilidad de {len(aristas)} enlaces y {len(nodos)} nodos, jornada {t0:%d-%m %H:%M} UTC "
              f"+ {horas:g} h, {len(amenazas)} amenazas")
        filas, t1, advertencias = calcular(aristas, nodos, amenazas, siniestros, t0, horas)
        res = resumen(filas, {"e": len(aristas), "v": len(nodos)})
        escribir_bd(conn, filas, t0, t1)
    finally:
        conn.close()
    escribir_json(filas, t0, t1, horas, res, advertencias)
    for a in advertencias:
        print(f"    [!] {a}")
    print(f"    [+] {json.dumps(res, ensure_ascii=False)}")


if __name__ == "__main__":
    main()
