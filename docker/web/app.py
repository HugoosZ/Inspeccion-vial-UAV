import os
from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import psycopg2
from psycopg2.extras import RealDictCursor

app = FastAPI(
    title="Plataforma de Ruteo UAV e Inspección Vial",
    description="API y Servidor Web Leaflet para visualización de Infraestructura, Metadata, Amenazas y Ruteo pgRouting",
    version="2.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_db_connection():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "db"),
        port=os.getenv("DB_PORT", "5432"),
        dbname=os.getenv("DB_NAME", "ruteo_db"),
        user=os.getenv("DB_USER", "ruteo_user"),
        password=os.getenv("DB_PASSWORD", "ruteo_pass")
    )

@app.get("/health")
def health_check():
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT 1;")
        cur.close()
        conn.close()
        return {"status": "ok", "database": "connected"}
    except Exception as e:
        return {"status": "degraded", "database_error": str(e)}

@app.get("/api/infraestructura")
def get_infraestructura():
    """Retorna las aristas de la red vial en formato GeoJSON."""
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        query = """
            SELECT json_build_object(
                'type', 'FeatureCollection',
                'features', COALESCE(json_agg(ST_AsGeoJSON(t.*)::json), '[]'::json)
            ) AS geojson
            FROM (
                SELECT id, osm_id, name, highway, source, target, cost, length_m, oneway, commune, geom
                FROM network_edges
                LIMIT 5000
            ) t;
        """
        cur.execute(query)
        result = cur.fetchone()
        cur.close()
        conn.close()
        return JSONResponse(content=result['geojson'])
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/metadata")
def get_metadata(tipo: str = None, comuna: str = None):
    """Retorna los registros de la tabla temporal de metadata en formato GeoJSON."""
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        conditions = []
        params = []
        if tipo:
            conditions.append("tipo = %s")
            params.append(tipo)
        if comuna:
            conditions.append("comuna = %s")
            params.append(comuna)

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        query = f"""
            SELECT json_build_object(
                'type', 'FeatureCollection',
                'features', COALESCE(json_agg(ST_AsGeoJSON(t.*)::json), '[]'::json)
            ) AS geojson
            FROM (
                SELECT id, nombre, tipo, fuente, comuna, descripcion, propiedades, geom
                FROM temp_metadata
                {where_clause}
            ) t;
        """
        cur.execute(query, tuple(params))
        result = cur.fetchone()
        cur.close()
        conn.close()
        return JSONResponse(content=result['geojson'])
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/amenazas")
def get_amenazas(tipo: str = None, riesgo: str = None):
    """Retorna los registros de la tabla temporal de amenazas en formato GeoJSON."""
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        conditions = []
        params = []
        if tipo:
            conditions.append("tipo = %s")
            params.append(tipo)
        if riesgo:
            conditions.append("nivel_riesgo = %s")
            params.append(riesgo)

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        query = f"""
            SELECT json_build_object(
                'type', 'FeatureCollection',
                'features', COALESCE(json_agg(ST_AsGeoJSON(t.*)::json), '[]'::json)
            ) AS geojson
            FROM (
                SELECT id, nombre, tipo, nivel_riesgo, comuna, descripcion, propiedades, geom
                FROM temp_amenazas
                {where_clause}
            ) t;
        """
        cur.execute(query, tuple(params))
        result = cur.fetchone()
        cur.close()
        conn.close()
        return JSONResponse(content=result['geojson'])
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/route/baseline")
def get_baseline_dijkstra_route(
    start_node: int = Query(..., description="ID del nodo inicial en network_nodes"),
    end_node: int = Query(..., description="ID del nodo final en network_nodes")
):
    """
    Rúbrica Punto 8: Calcula una ruta con pgr_dijkstra usando longitud como costo.
    Representa el peor caso (baseline) ya que no considera metadata ni amenazas.
    """
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        query = """
            SELECT json_build_object(
                'type', 'FeatureCollection',
                'features', COALESCE(json_agg(ST_AsGeoJSON(t.*)::json), '[]'::json)
            ) AS geojson
            FROM (
                SELECT 
                    d.seq,
                    d.node AS node_id,
                    d.edge AS edge_id,
                    d.cost,
                    d.agg_cost,
                    e.geom
                FROM pgr_dijkstra(
                    'SELECT id, source, target, cost, reverse_cost FROM network_edges',
                    %s,
                    %s,
                    directed := true
                ) AS d
                LEFT JOIN network_edges e ON d.edge = e.id
                ORDER BY d.seq
            ) t;
        """
        cur.execute(query, (start_node, end_node))
        result = cur.fetchone()
        cur.close()
        conn.close()
        return JSONResponse(content=result['geojson'])
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Servir archivos estáticos del sitio web (Leaflet)
static_dir = "/app/sitio_web"
if os.path.exists(static_dir):
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
