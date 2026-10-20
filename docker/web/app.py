"""
==============================================================================
API geoespacial + servidor del sitio Leaflet
==============================================================================
Endpoints (todos devuelven GeoJSON salvo /health y /api/capas):
  GET /health                     estado de la conexión a la BD
  GET /api/capas                  catálogo de capas cargadas (fuente_etl, tipo, conteo)
  GET /api/infraestructura        aristas de la red vial (network_edges)
  GET /api/nodos                  nodos de la red vial (network_nodes)
  GET /api/metadata?fuente=...    filas de temp_metadata (filtrables)
  GET /api/amenazas?fuente=...    filas de temp_amenazas (filtrables)
  GET /api/route/baseline         ruta pgr_dijkstra por longitud (una ruta)
  GET /api/rutas                  k rutas pgr_dijkstra vs TDRPP-GA + métrica de resiliencia
  GET /api/rutas/preferencias     preferencias por defecto de navegación
El sitio estático se sirve desde la carpeta "Sitio Web" montada en /app/sitio_web.
"""

import os
import sys

import psycopg2
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from psycopg2.extras import RealDictCursor

# Módulo de ruteo (docker/web/ruteo): Dijkstra k rutas, TDRPP-GA y métrica
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ruteo  # noqa: E402
from ruteo.preferencias import PREFERENCIAS_DEFECTO, TIPOS_SENAL_VALIDOS  # noqa: E402

app = FastAPI(
    title="Plataforma de Ruteo UAV e Inspección Vial",
    description="API y servidor Leaflet para visualizar infraestructura, metadata, amenazas y ruteo pgRouting",
    version="2.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_db_connection():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "db"),
        port=os.getenv("DB_PORT", "5432"),
        dbname=os.getenv("DB_NAME", "ruteo_db"),
        user=os.getenv("DB_USER", "ruteo_user"),
        password=os.getenv("DB_PASSWORD", "ruteo_pass"),
    )


def consultar(sql, params=()):
    """Ejecuta una consulta que devuelve una sola fila y la entrega como dict."""
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params)
            return cur.fetchone()
    finally:
        conn.close()


def consultar_todas(sql, params=()):
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params)
            return cur.fetchall()
    finally:
        conn.close()


def feature_collection(subconsulta, params=()):
    """Envuelve una subconsulta (con columna geom) en un FeatureCollection GeoJSON."""
    sql = f"""
        SELECT json_build_object(
            'type', 'FeatureCollection',
            'features', COALESCE(json_agg(ST_AsGeoJSON(t.*, 'geom', 6)::json), '[]'::json)
        ) AS geojson
        FROM ({subconsulta}) t;
    """
    try:
        return JSONResponse(content=consultar(sql, params)["geojson"])
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(e))


def filtros(columnas_valores):
    """Arma un WHERE a partir de pares (expresion_sql, valor) ignorando los None."""
    condiciones, params = [], []
    for expresion, valor in columnas_valores:
        if valor is not None and valor != "":
            condiciones.append(expresion)
            params.append(valor)
    where = f"WHERE {' AND '.join(condiciones)}" if condiciones else ""
    return where, tuple(params)


def geom_simplificada(simplificar):
    """Expresión de geometría, simplificada si se pide (tolerancia en grados)."""
    if simplificar and simplificar > 0:
        return f"ST_SimplifyPreserveTopology(geom, {float(simplificar)}) AS geom"
    return "geom"


# ------------------------------------------------------------------------------
@app.get("/health")
def health_check():
    try:
        consultar("SELECT 1 AS ok;")
        return {"status": "ok", "database": "connected"}
    except Exception as e:  # noqa: BLE001
        return {"status": "degraded", "database_error": str(e)}


@app.get("/api/capas")
def get_capas():
    """Catálogo de lo cargado en la BD, para construir el panel de capas del sitio."""
    try:
        red = consultar("""
            SELECT (SELECT count(*) FROM network_nodes) AS nodos,
                   (SELECT count(*) FROM network_edges) AS aristas,
                   (SELECT round(COALESCE(sum(length_m), 0)::numeric / 1000, 1) FROM network_edges) AS km
        """)
        metadata = consultar_todas("""
            SELECT propiedades->>'fuente_etl' AS fuente_etl, fuente, tipo, count(*) AS total
            FROM temp_metadata GROUP BY 1, 2, 3 ORDER BY 1, 3
        """)
        amenazas = consultar_todas("""
            SELECT propiedades->>'fuente_etl' AS fuente_etl, tipo, nivel_riesgo, count(*) AS total
            FROM temp_amenazas GROUP BY 1, 2, 3 ORDER BY 1, 2, 3
        """)
        return {"infraestructura": red, "metadata": metadata, "amenazas": amenazas}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/infraestructura")
def get_infraestructura(
    highway: str = Query(None, description="Filtrar por categoría OSM (primary, residential, ...)"),
    simplificar: float = Query(0, description="Tolerancia de simplificación en grados (0 = sin simplificar)"),
):
    """Aristas de la red vial (network_edges) en GeoJSON."""
    where, params = filtros([("highway = %s", highway)])
    return feature_collection(f"""
        SELECT id, osm_id, name, highway, source, target, round(length_m::numeric, 1) AS length_m,
               maxspeed_kmh, oneway, commune, {geom_simplificada(simplificar)}
        FROM network_edges {where}
    """, params)


@app.get("/api/nodos")
def get_nodos():
    """Nodos de la red vial (network_nodes) en GeoJSON."""
    return feature_collection("""
        SELECT id, osm_id, commune, elevation_m, geom FROM network_nodes
    """)


@app.get("/api/metadata")
def get_metadata(
    fuente: str = Query(None, description="fuente_etl: M1_paraderos, M3_controles_viales, M5_censo_manzanas, M6_aerodromos"),
    tipo: str = None,
    comuna: str = None,
    simplificar: float = Query(0, description="Tolerancia de simplificación en grados"),
):
    """Registros de temp_metadata en GeoJSON."""
    where, params = filtros([
        ("propiedades->>'fuente_etl' = %s", fuente),
        ("tipo = %s", tipo),
        ("comuna = %s", comuna),
    ])
    return feature_collection(f"""
        SELECT id, nombre, tipo, fuente, comuna, descripcion, propiedades,
               {geom_simplificada(simplificar)}
        FROM temp_metadata {where}
    """, params)


@app.get("/api/amenazas")
def get_amenazas(
    fuente: str = Query(None, description="fuente_etl: T2_incendios, T3_clima, T5_lluvias_maipu, T6_sosafe"),
    tipo: str = None,
    riesgo: str = None,
):
    """Registros de temp_amenazas en GeoJSON."""
    where, params = filtros([
        ("propiedades->>'fuente_etl' = %s", fuente),
        ("tipo = %s", tipo),
        ("nivel_riesgo = %s", riesgo),
    ])
    return feature_collection(f"""
        SELECT id, nombre, tipo, nivel_riesgo, comuna, descripcion, propiedades, geom
        FROM temp_amenazas {where}
    """, params)


@app.get("/api/route/baseline")
def get_baseline_dijkstra_route(
    start_node: int = Query(..., description="ID del nodo inicial en network_nodes"),
    end_node: int = Query(..., description="ID del nodo final en network_nodes"),
):
    """
    Ruta con pgr_dijkstra usando la longitud como costo.
    Es el peor caso (baseline): no considera metadata ni amenazas.
    """
    return feature_collection("""
        SELECT d.seq, d.node AS node_id, d.edge AS edge_id, d.cost, d.agg_cost, e.geom
        FROM pgr_dijkstra(
            'SELECT id, source, target, cost, reverse_cost FROM network_edges',
            %s, %s, directed := true
        ) AS d
        LEFT JOIN network_edges e ON d.edge = e.id
        ORDER BY d.seq
    """, (start_node, end_node))


@app.get("/api/rutas/preferencias")
def get_preferencias_defecto():
    """Preferencias por defecto del usuario y tipos de señal válidos."""
    return {"defecto": PREFERENCIAS_DEFECTO, "tipos_senal": list(TIPOS_SENAL_VALIDOS)}


@app.get("/api/rutas")
def get_rutas(
    inicio_lon: float, inicio_lat: float, objetivo_lon: float, objetivo_lat: float,
    k: int = None, hora_salida: str = None,
    evitar_aerodromos: bool = None, radio_aerodromo_m: float = None,
    evitar_paraderos: bool = None, priorizar_vulnerables: bool = None,
    tipos_senal: str = Query(None, description="Lista separada por comas, p. ej. senal_pare,semaforo"),
    considerar_clima: bool = None, radio_corredor_m: float = None, max_requeridas: int = None,
    velocidad_crucero_ms: float = None, velocidad_inspeccion_ms: float = None,
    autonomia_km: float = None, horizonte_h: float = None,
):
    """
    Planificación de rutas: con el par (inicio, objetivo) y las restricciones del usuario
    (las omitidas toman su valor por defecto) devuelve k rutas de pgr_dijkstra,
    k rutas del TDRPP-GA, la métrica de resiliencia de cada conjunto y su diferencial.
    """
    entrada = {
        "k": k, "hora_salida": hora_salida, "evitar_aerodromos": evitar_aerodromos,
        "radio_aerodromo_m": radio_aerodromo_m, "evitar_paraderos": evitar_paraderos,
        "priorizar_vulnerables": priorizar_vulnerables, "tipos_senal": tipos_senal,
        "considerar_clima": considerar_clima, "radio_corredor_m": radio_corredor_m,
        "max_requeridas": max_requeridas, "velocidad_crucero_ms": velocidad_crucero_ms,
        "velocidad_inspeccion_ms": velocidad_inspeccion_ms, "autonomia_km": autonomia_km,
        "horizonte_h": horizonte_h,
    }
    conn = get_db_connection()
    try:
        return ruteo.planificar(conn, inicio_lon, inicio_lat, objetivo_lon, objetivo_lat, entrada)
    except ruteo.ErrorRuteo as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")
    finally:
        conn.close()


# Sitio estático (Leaflet). Se monta al final para no tapar las rutas /api.
static_dir = "/app/sitio_web"
if os.path.exists(static_dir):
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
