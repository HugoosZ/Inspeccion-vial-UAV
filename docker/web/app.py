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
  GET /api/rutas                  k rutas pgr_dijkstra resilientes + métrica de resiliencia
  GET /api/rutas/preferencias     preferencias por defecto de navegación
  POST /api/flota                 planificación por área con n drones y las técnicas T1-T4 (usada por el sitio)
  POST /api/flota/simulacion      sorteo reproducible de fallas sobre un plan calculado
  GET /api/flota/preferencias     preferencias por defecto de la flota
  GET /api/solver                 solver del modelo T2 (Gurobi con licencia WLS, o HiGHS de respaldo)
El sitio estático se sirve desde la carpeta "Sitio Web" montada en /app/sitio_web.
"""

import os
import sys

import psycopg2
from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from psycopg2.extras import RealDictCursor

# Módulo de ruteo (docker/web/ruteo): k rutas pgr_dijkstra resilientes y métrica
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ruteo  # noqa: E402
from ruteo import flota  # noqa: E402
from ruteo import milp  # noqa: E402
from ruteo import simulacion_servicio  # noqa: E402
from ruteo.preferencias import (AMENAZAS_VALIDAS, CATEGORIAS_BASE_VALIDAS, OPCIONES,  # noqa: E402
                                PREFERENCIAS_DEFECTO, PREFERENCIAS_FLOTA, TECNICAS_VALIDAS,
                                TIPOS_SENAL_VALIDOS)
from ruteo.tecnicas import NOMBRES as NOMBRES_TECNICAS  # noqa: E402

app = FastAPI(
    title="Plataforma de Ruteo UAV e Inspección Vial",
    description="API y servidor Leaflet para visualizar infraestructura, metadata, amenazas y ruteo pgRouting",
    version="2.2.0",
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
        try:
            probabilidades = consultar("""
                SELECT (SELECT count(*) FROM prob_falla_aristas WHERE p_total > 0) AS aristas,
                       (SELECT count(*) FROM prob_falla_nodos WHERE p_total > 0) AS nodos,
                       (SELECT count(*) FROM prob_falla_aristas WHERE bloqueado) AS aristas_bloqueadas,
                       (SELECT count(*) FROM prob_falla_nodos WHERE bloqueado) AS nodos_bloqueados,
                       (SELECT min(t0) FROM prob_falla_aristas) AS t0,
                       (SELECT min(t1) FROM prob_falla_aristas) AS t1
            """)
        except Exception:  # noqa: BLE001  (tablas aún no creadas)
            probabilidades = {"aristas": 0, "nodos": 0, "aristas_bloqueadas": 0, "nodos_bloqueados": 0,
                              "t0": None, "t1": None}
        return {"infraestructura": red, "metadata": metadata, "amenazas": amenazas,
                "probabilidades": probabilidades}
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
    fuente: str = Query(None, description="fuente_etl: M1_senaleticas, M2_lugares_publicos, M3_puentes, M4_siniestros"),
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
    fuente: str = Query(None, description="fuente_etl: A1_incendios, A2_clima, A3_sosafe, A4_tomtom"),
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


@app.get("/api/probabilidades")
def get_probabilidades(
    elemento: str = Query("aristas", description="aristas o nodos"),
    minimo: float = Query(0.0, description="Probabilidad mínima a devolver (0 a 1); los bloqueados siempre se devuelven"),
    amenaza: str = Query(None, description="A1, A2 o accidente: filtra por esa probabilidad en vez de p_total"),
):
    """Indisponibilidad por enlace o nodo (punto 5, tablas prob_falla_*) en GeoJSON."""
    columna = {None: "p_total", "": "p_total", "A1": "p_a1", "A2": "p_a2", "accidente": "p_accidente"}.get(amenaza)
    if columna is None:
        raise HTTPException(status_code=422, detail="amenaza debe ser A1, A2 o accidente")
    campos = ("p.p_total, p.p_a1, p.p_a2, p.p_accidente, p.riesgo_viento, p.contraste_visual, p.bloqueado, "
              "p.motivos_bloqueo, p.calidad, p.observaciones, p.t0, p.t1")
    if elemento == "nodos":
        sub = f"""SELECT p.node_id AS id, {campos}, n.geom
                  FROM prob_falla_nodos p JOIN network_nodes n ON n.id = p.node_id
                  WHERE COALESCE(p.{columna}, 0) > %s OR p.bloqueado"""
    elif elemento == "aristas":
        sub = f"""SELECT p.edge_id AS id, e.name, {campos}, e.geom
                  FROM prob_falla_aristas p JOIN network_edges e ON e.id = p.edge_id
                  WHERE COALESCE(p.{columna}, 0) > %s OR p.bloqueado"""
    else:
        raise HTTPException(status_code=422, detail="elemento debe ser aristas o nodos")
    return feature_collection(sub, (minimo,))


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
    priorizar_siniestros: bool = None,
    tipos_senal: str = Query(None, description="Lista separada por comas, p. ej. senal_pare,semaforo"),
    considerar_clima: bool = None, radio_corredor_m: float = None, max_requeridas: int = None,
    velocidad_crucero_ms: float = None, autonomia_km: float = None, horizonte_h: float = None,
):
    """
    Planificación de rutas: con el par (inicio, objetivo) y las restricciones del usuario
    (las omitidas toman su valor por defecto) devuelve k rutas de pgr_dijkstra sobre
    pesos resilientes (amenazas) y la métrica de resiliencia del conjunto.
    """
    entrada = {
        "k": k, "hora_salida": hora_salida,
        "priorizar_siniestros": priorizar_siniestros, "tipos_senal": tipos_senal,
        "considerar_clima": considerar_clima, "radio_corredor_m": radio_corredor_m,
        "max_requeridas": max_requeridas, "velocidad_crucero_ms": velocidad_crucero_ms,
        "autonomia_km": autonomia_km,
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


@app.get("/api/flota/preferencias")
def get_preferencias_flota():
    """Preferencias por defecto de la planificación por área y opciones válidas."""
    return {"defecto": PREFERENCIAS_FLOTA, "tipos_senal": list(TIPOS_SENAL_VALIDOS),
            "categorias_base": list(CATEGORIAS_BASE_VALIDAS), "opciones": OPCIONES,
            "tecnicas": list(TECNICAS_VALIDAS), "nombres_tecnicas": NOMBRES_TECNICAS,
            "amenazas": list(AMENAZAS_VALIDAS)}


@app.get("/api/solver")
def get_solver():
    """Solver que usará el modelo formal T2 (comprueba la licencia de Gurobi)."""
    return milp.estado_solver()


@app.post("/api/flota")
def post_flota(cuerpo: dict = Body(..., examples=[{
    "area": {"type": "Polygon", "coordinates": [[[-70.77, -33.523], [-70.745, -33.523],
                                                 [-70.745, -33.498], [-70.77, -33.498], [-70.77, -33.523]]]},
    "preferencias": {"n_drones": 3},
}])):
    """
    Planificación por área con una flota de drones (Zhong et al., 2023) y
    comparación de técnicas de ruteo (punto 6).

    Cuerpo JSON:
      - area: polígono GeoJSON (lon, lat) o null para toda la comuna.
      - preferencias: n_drones, bases, hora_salida, tipos_senal, categorias_base,
        tecnicas (T1..T4), amenazas (A1..A4), limite_milp_s, limite_dpso_s,
        considerar_clima, velocidad_crucero_ms, autonomia_min,
        reserva_bateria_pct, tiempo_recarga_min, jornada_h.
        Las omitidas toman su valor por defecto.

    Devuelve la instancia común (áreas, bases y calles de cada dron) y, por
    técnica, sus rutas, indicadores y tiempo de cómputo. plan_id permite
    simular fallas sobre ese mismo plan durante los próximos 30 minutos.
    Incluye resiliencia esperada por técnica y comparación sobre el mismo
    servicio original y modelo de eventos del punto 7.
    """
    conn = get_db_connection()
    try:
        resultado = flota.planificar_area(conn, cuerpo.get("area"), cuerpo.get("preferencias") or {})
        resultado["plan_id"] = simulacion_servicio.registrar_plan(resultado)
        metricas, comparacion = simulacion_servicio.evaluar_plan(conn, resultado["plan_id"])
        for codigo, tecnica in resultado["tecnicas"].items():
            tecnica["resiliencia"] = metricas[codigo]
        resultado["comparacion_resiliencia"] = comparacion
        return resultado
    except (flota.ErrorFlota, ValueError) as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")
    finally:
        conn.close()


@app.post("/api/flota/simulacion")
def post_simulacion(cuerpo: dict = Body(..., examples=[{"plan_id": "id devuelto por /api/flota", "semilla": 7}])):
    """Sortea eventos del punto 5 y señala cruces de las rutas con sus fallas.

    Usa el mismo escenario para todas las técnicas del plan. Un evento
    confirmado permanece bloqueado; los demás ocurren si U < 100*p.
    La semilla es un entero entre 0 y 2147483647. No modifica el plan ni la BD.
    Adjunta resiliencia realizada y su comparación para ese escenario común.
    """
    semilla = cuerpo.get("semilla", 7)
    if isinstance(semilla, bool) or not isinstance(semilla, int) or not 0 <= semilla <= 2147483647:
        raise HTTPException(status_code=422, detail="La semilla debe ser un entero entre 0 y 2147483647.")
    conn = get_db_connection()
    try:
        return simulacion_servicio.simular_plan(conn, cuerpo.get("plan_id"), semilla)
    except simulacion_servicio.PlanNoDisponible as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")
    finally:
        conn.close()


# Sitio estático (Leaflet). Se monta al final para no tapar las rutas /api.
static_dir = "/app/sitio_web"
if os.path.exists(static_dir):
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
