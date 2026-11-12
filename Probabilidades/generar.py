"""
==============================================================================
PROBABILIDAD DE FALLA DE CADA ENLACE Y NODO (punto 5 del enunciado)
==============================================================================
Lee de PostGIS la red vial (network_edges, network_nodes) y las amenazas
cargadas (temp_amenazas), y calcula para cada enlace e y cada nodo v la
probabilidad de que falle (quede inutilizable para el dron) durante la jornada
[t0, t0 + H], separada por amenaza:

    p_k(e)  probabilidad de falla de e por la amenaza k (A1, A2, A3, A4)
    p(e)  = 1 - prod_k (1 - p_k(e))          (amenazas independientes)

Modelo por amenaza (detalle y supuestos en Probabilidades/README.md):
  A1 Incendios (CONAF)
     - incendio activo que alcanza e:  p = P_ESTADO[estado]
     - tasa histórica: n incendios a menos de R_HIST de e en D días de registro,
       modulada por la probabilidad de ignición del día (PI de CONAF):
           p = 1 - exp(-(n / D) * (H / 24) * PI / PI_REF)
  A2 Clima (Open-Meteo): para cada hora de la jornada, rampa lineal entre el
     umbral de riesgo y el umbral de vuelo imposible de ráfaga, lluvia y
     visibilidad; p = máximo de las horas de la jornada (el enlace se cruza una vez).
  A3 SOSAFE: p = PROB_NIVEL[nivel_riesgo] dentro de la geocerca.
  A4 Accidentes, cierres y obras
     - incidente TomTom vigente: p = P_OCURRENCIA[probabilidad] * P_ACCION[accion]
     - tasa histórica de siniestros (CONASET 2020-2025) asociados a e:
           p = 1 - exp(-(n / D_CONASET) * (H / 24))

Escribe las tablas prob_falla_aristas y prob_falla_nodos (solo p > 0) y el
archivo Probabilidades/data/probabilidades_maipu.json como respaldo.

Uso:
  python Probabilidades/generar.py
Variables de entorno opcionales:
  PROB_HORIZONTE_H   duración de la jornada evaluada en horas (defecto 8)
  PROB_T0            inicio de la jornada en ISO 8601 (defecto: ahora)
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
P_ESTADO = {"En combate": 0.9, "Foco": 0.6, "Bajo observacion": 0.6, "Controlado": 0.3}
R_HIST_INCENDIOS_M = 2000      # mismo margen de exclusión que A1 (INCENDIOS_RADIO_M)
PI_REF = 0.5                   # PI = 50 % deja la tasa histórica sin cambio
DIAS_MIN_HISTORIA = 365

# Umbrales de A2 (Amenazas/A2/extractor.py): (inicio de riesgo, vuelo imposible)
UMBRAL_RAFAGA_KMH = (30.0, 43.0)
UMBRAL_LLUVIA_MMH = (0.1, 0.5)
UMBRAL_VISIBILIDAD_M = (3000.0, 1000.0)

PROB_NIVEL = {"BAJO": 0.05, "MEDIO": 0.2, "ALTO": 0.5, "CRITICO": 0.8}   # igual que el ruteo
P_OCURRENCIA = {"certain": 1.0, "probable": 0.75, "risk_of": 0.4, "improbable": 0.1}
P_ACCION = {"BLOQUEAR_ARISTAS": 1.0, "PENALIZAR_ARISTAS": 0.5}
DIAS_CONASET = (datetime(2025, 12, 31) - datetime(2020, 1, 1)).days + 1    # 2.192 días
RADIO_SINIESTRO_M = 30         # un siniestro se asocia al enlace o nodo a menos de 30 m

FUENTES = ("A1", "A2", "A3", "A4")
LAT_REF, LON_REF = -33.51, -70.78
MX = 111_320 * math.cos(math.radians(LAT_REF))
MY = 110_574
PASO_MUESTREO_M = 20
CELDA_INDICE_M = 250


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
            self.lineas = [[xy(*c[:2]) for c in l] for l in lineas(geom)]
        elif t == "Polygon":
            self.poligonos = [[[xy(*c[:2]) for c in a] for a in geom["coordinates"]]]
        elif t == "MultiPolygon":
            self.poligonos = [[[xy(*c[:2]) for c in a] for a in p] for p in geom["coordinates"]]
        coords = self.puntos + [c for l in self.lineas for c in l] + \
            [c for p in self.poligonos for a in p for c in a]
        xs, ys = [c[0] for c in coords], [c[1] for c in coords]
        self.caja = (min(xs), min(ys), max(xs), max(ys))

    def distancia(self, px, py):
        d = math.inf
        for x, y in self.puntos:
            d = min(d, math.hypot(px - x, py - y))
        for l in self.lineas:
            for (x1, y1), (x2, y2) in zip(l, l[1:]):
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
        print(f"    [!] No existe {ARCHIVO_SINIESTROS.name}: A4 queda sin tasa histórica de siniestros")
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


def rampa(valor, inicio, fin):
    """0 antes del umbral de riesgo, 1 desde el umbral de vuelo imposible, lineal entre ambos."""
    if valor is None:
        return 0.0
    return max(0.0, min(1.0, (float(valor) - inicio) / (fin - inicio)))


def p_clima(props):
    partes = (rampa(props.get("rafaga_kmh"), *UMBRAL_RAFAGA_KMH),
              rampa(props.get("precipitacion_mm_h"), *UMBRAL_LLUVIA_MMH),
              rampa(props.get("visibilidad_m"), *UMBRAL_VISIBILIDAD_M))
    return 1 - math.prod(1 - p for p in partes)


# ------------------------------------------------------------------------------
# Cálculo
# ------------------------------------------------------------------------------
def calcular(aristas, nodos, amenazas, siniestros, t0, horas):
    t1 = t0 + timedelta(hours=horas)
    muestras = {("e", i): muestrear(g) for i, g in aristas.items()}
    muestras.update({("v", i): [xy(*c)] for i, c in nodos.items()})
    muestras = {k: v for k, v in muestras.items() if v}
    indice = Indice(muestras)
    # contribuciones[(elemento)][fuente] -> lista de (p, id_amenaza, tipo)
    contrib = defaultdict(lambda: defaultdict(list))

    def afectados(forma, radio):
        return [k for k in indice.candidatos(forma.caja, radio)
                if any(forma.distancia(x, y) <= radio for x, y in muestras[k])]

    activos = [a for a in amenazas if a["fuente"] in ("A1_incendios", "A2_clima", "A3_sosafe", "A4_tomtom")]
    historicos, celdas_pi = [], []
    for a in activos:
        p, tipo = a["props"], a["tipo"]
        if a["fuente"] == "A1_incendios" and tipo == "incendio_historico":
            historicos.append(a)
            continue
        if a["fuente"] == "A1_incendios" and tipo == "probabilidad_ignicion":
            if vigente(p, t0, t1):
                celdas_pi.append((Forma(a["geom"]), float(p.get("probabilidad_ignicion") or PI_REF)))
            continue
        if not vigente(p, t0, t1):
            continue
        radio = float(p.get("radio_afectacion_m") or 0)
        if a["fuente"] == "A1_incendios":
            fuente, prob = "A1", P_ESTADO.get(p.get("estado"), 0.6)
        elif a["fuente"] == "A2_clima":
            fuente, prob = "A2", p_clima(p)
        elif a["fuente"] == "A3_sosafe":
            fuente, prob = "A3", PROB_NIVEL.get(a["nivel"] or "MEDIO", 0.2)
            radio = radio or (float(p.get("radio_buffer") or 0) if a["geom"]["type"] == "Point" else 0)
        else:
            fuente = "A4"
            prob = P_OCURRENCIA.get(p.get("probabilidad"), 0.75) * P_ACCION.get(p.get("accion_grafo"), 1.0)
        if prob <= 0:
            continue
        for k in afectados(Forma(a["geom"]), radio):
            contrib[k][fuente].append((prob, a["id"], tipo))

    # A1: tasa histórica de incendios modulada por la probabilidad de ignición del día
    if historicos:
        fechas = [instante(h["props"].get("inicio")) for h in historicos]
        fechas = [f for f in fechas if f]
        dias = max(DIAS_MIN_HISTORIA, (max(fechas) - min(fechas)).days if fechas else 0)
        conteo = defaultdict(int)
        for h in historicos:
            for k in afectados(Forma(h["geom"]), R_HIST_INCENDIOS_M):
                conteo[k] += 1
        for k, n in conteo.items():
            x, y = muestras[k][len(muestras[k]) // 2]
            pi = next((v for forma, v in celdas_pi if forma.distancia(x, y) == 0), PI_REF)
            prob = 1 - math.exp(-(n / dias) * (horas / 24) * (pi / PI_REF))
            contrib[k]["A1"].append((prob, f"historial:{n} incendios en {dias} días, PI {pi:.2f}",
                                     "tasa_historica_incendios"))

    # A4: tasa histórica de siniestros CONASET asociados al enlace o nodo más cercano
    conteo_e, conteo_v = defaultdict(int), defaultdict(int)
    for lon, lat in siniestros:
        px, py = xy(lon, lat)
        caja = (px, py, px, py)
        mejor = {"e": (RADIO_SINIESTRO_M, None), "v": (RADIO_SINIESTRO_M, None)}
        for k in indice.candidatos(caja, RADIO_SINIESTRO_M):
            pts = muestras[k]
            if k[0] == "e":
                d = min(_dist_segmento(px, py, *a, *b) for a, b in zip(pts, pts[1:])) if len(pts) > 1 \
                    else math.hypot(px - pts[0][0], py - pts[0][1])
            else:
                d = math.hypot(px - pts[0][0], py - pts[0][1])
            if d <= mejor[k[0]][0]:
                mejor[k[0]] = (d, k)
        if mejor["e"][1]:
            conteo_e[mejor["e"][1]] += 1
        if mejor["v"][1]:
            conteo_v[mejor["v"][1]] += 1
    for conteo in (conteo_e, conteo_v):
        for k, n in conteo.items():
            prob = 1 - math.exp(-(n / DIAS_CONASET) * (horas / 24))
            contrib[k]["A4"].append((prob, f"historial:{n} siniestros 2020-2025", "tasa_historica_siniestros"))

    # Combinación
    filas = {"e": [], "v": []}
    for k, por_fuente in contrib.items():
        # A2: el enlace se cruza en un momento de la jornada, así que se toma la peor hora
        # (máximo) y no la unión de todas las horas; el resto de las amenazas se combina
        # como eventos independientes.
        p_f = {f: (max((c[0] for c in por_fuente.get(f, [])), default=0.0) if f == "A2"
                   else 1 - math.prod(1 - c[0] for c in por_fuente.get(f, []))) for f in FUENTES}
        total = 1 - math.prod(1 - v for v in p_f.values())
        if total <= 0:
            continue
        detalle = sorted(((c[0], c[1], c[2], f) for f, lista in por_fuente.items() for c in lista), reverse=True)[:6]
        filas[k[0]].append({
            "id": k[1], "p_total": round(total, 6), **{f"p_{f.lower()}": round(p_f[f], 6) for f in FUENTES},
            "n_amenazas": sum(len(v) for v in por_fuente.values()),
            "detalle": [{"fuente": f, "amenaza": str(i), "tipo": t, "p": round(p, 6)} for p, i, t, f in detalle]})
    return filas, t1


def resumen(filas, total_elementos):
    out = {}
    for clave, nombre in (("e", "aristas"), ("v", "nodos")):
        lista = filas[clave]
        r = {"total": total_elementos[clave], "con_probabilidad": len(lista)}
        for campo in ("p_total",) + tuple(f"p_{f.lower()}" for f in FUENTES):
            valores = [x[campo] for x in lista if x[campo] > 0]
            r[campo] = {"afectados": len(valores),
                        "media": round(sum(valores) / len(valores), 4) if valores else 0,
                        "maxima": round(max(valores), 4) if valores else 0}
        out[nombre] = r
    return out


# ------------------------------------------------------------------------------
# Escritura
# ------------------------------------------------------------------------------
SQL_TABLAS = """
CREATE TABLE IF NOT EXISTS prob_falla_aristas (
    edge_id BIGINT PRIMARY KEY, p_total DOUBLE PRECISION NOT NULL,
    p_a1 DOUBLE PRECISION, p_a2 DOUBLE PRECISION, p_a3 DOUBLE PRECISION, p_a4 DOUBLE PRECISION,
    n_amenazas INTEGER, detalle JSONB, t0 TIMESTAMPTZ, t1 TIMESTAMPTZ);
CREATE TABLE IF NOT EXISTS prob_falla_nodos (
    node_id BIGINT PRIMARY KEY, p_total DOUBLE PRECISION NOT NULL,
    p_a1 DOUBLE PRECISION, p_a2 DOUBLE PRECISION, p_a3 DOUBLE PRECISION, p_a4 DOUBLE PRECISION,
    n_amenazas INTEGER, detalle JSONB, t0 TIMESTAMPTZ, t1 TIMESTAMPTZ);
"""


def escribir_bd(conn, filas, t0, t1):
    from psycopg2.extras import execute_values

    with conn, conn.cursor() as cur:
        cur.execute(SQL_TABLAS)
        for tabla, col, clave in (("prob_falla_aristas", "edge_id", "e"), ("prob_falla_nodos", "node_id", "v")):
            cur.execute(f"TRUNCATE {tabla}")
            execute_values(cur, f"""INSERT INTO {tabla} ({col}, p_total, p_a1, p_a2, p_a3, p_a4, n_amenazas,
                                    detalle, t0, t1) VALUES %s""",
                           [(r["id"], r["p_total"], r["p_a1"], r["p_a2"], r["p_a3"], r["p_a4"], r["n_amenazas"],
                             json.dumps(r["detalle"], ensure_ascii=False), t0, t1) for r in filas[clave]],
                           page_size=1000)


def escribir_json(filas, t0, t1, horas, res):
    SALIDA.parent.mkdir(parents=True, exist_ok=True)
    columnas = ["id", "p_total", "p_a1", "p_a2", "p_a3", "p_a4", "n_amenazas"]
    SALIDA.write_text(json.dumps({
        "metadata": {
            "generado_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "t0": t0.isoformat(), "t1": t1.isoformat(), "horizonte_h": horas,
            "modelo": "p(e) = 1 - prod_k (1 - p_k(e)); ver Probabilidades/README.md",
            "parametros": {"P_ESTADO": P_ESTADO, "R_HIST_INCENDIOS_M": R_HIST_INCENDIOS_M, "PI_REF": PI_REF,
                           "UMBRAL_RAFAGA_KMH": UMBRAL_RAFAGA_KMH, "UMBRAL_LLUVIA_MMH": UMBRAL_LLUVIA_MMH,
                           "UMBRAL_VISIBILIDAD_M": UMBRAL_VISIBILIDAD_M, "PROB_NIVEL": PROB_NIVEL,
                           "P_OCURRENCIA": P_OCURRENCIA, "P_ACCION": P_ACCION, "DIAS_CONASET": DIAS_CONASET,
                           "RADIO_SINIESTRO_M": RADIO_SINIESTRO_M},
            "resumen": res,
            "columnas": columnas,
        },
        "aristas": [[r[c] for c in columnas] for r in sorted(filas["e"], key=lambda r: -r["p_total"])],
        "nodos": [[r[c] for c in columnas] for r in sorted(filas["v"], key=lambda r: -r["p_total"])],
        "top_aristas": sorted(filas["e"], key=lambda r: -r["p_total"])[:15],
    }, ensure_ascii=False), encoding="utf-8")
    print(f"    [+] {SALIDA}")


def t0_jornada():
    valor = os.getenv("PROB_T0")
    if valor:
        return instante(valor)
    return datetime.now(timezone.utc).replace(microsecond=0)


def main():
    sys.path.insert(0, str(RAIZ / "Metadata"))
    import comun_bd  # noqa: E402

    comun_bd.cargar_env()
    horas = float(os.getenv("PROB_HORIZONTE_H", "8"))
    t0 = t0_jornada()
    conn = comun_bd.conectar_bd()
    try:
        print("[*] Leyendo red vial y amenazas desde PostGIS...")
        aristas, nodos, amenazas = cargar_desde_bd(conn)
        siniestros = cargar_siniestros()
        print(f"    [+] {len(aristas)} aristas, {len(nodos)} nodos, {len(amenazas)} amenazas, "
              f"{len(siniestros)} siniestros CONASET")
        filas, t1 = calcular(aristas, nodos, amenazas, siniestros, t0, horas)
        res = resumen(filas, {"e": len(aristas), "v": len(nodos)})
        escribir_bd(conn, filas, t0, t1)
    finally:
        conn.close()
    escribir_json(filas, t0, t1, horas, res)
    for nombre, r in res.items():
        print(f"    [+] {nombre}: {r['con_probabilidad']} de {r['total']} con p > 0, "
              f"p media {r['p_total']['media']}, máxima {r['p_total']['maxima']}")


if __name__ == "__main__":
    main()
