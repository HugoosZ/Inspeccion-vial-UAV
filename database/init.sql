-- ==============================================================================
-- SISTEMA DE DETECCIÓN DE SEÑALÉTICAS Y ELEMENTOS VIALES MEDIANTE FLOTA UAV
-- Base de Datos Geoespacial: PostgreSQL + PostGIS + pgRouting
-- ==============================================================================

-- 1. Habilitar extensiones geoespaciales y de ruteo
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pgrouting;

-- ------------------------------------------------------------------------------
-- 2. INFRAESTRUCTURA (Red Vial - Grafo para pgRouting)
-- ------------------------------------------------------------------------------

-- Tabla de vértices/nodos de la red vial
CREATE TABLE IF NOT EXISTS network_nodes (
    id BIGSERIAL PRIMARY KEY,
    osm_id BIGINT UNIQUE,
    commune VARCHAR(50) NOT NULL, -- 'Santiago', 'Estación Central', 'Maipú'
    elevation_m NUMERIC(6, 2) DEFAULT 0.0,
    geom geometry(Point, 4326) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Tabla de aristas/calles para algoritmos de ruteo (pgr_dijkstra)
CREATE TABLE IF NOT EXISTS network_edges (
    id BIGSERIAL PRIMARY KEY,
    osm_id BIGINT,
    name VARCHAR(150),
    highway VARCHAR(50) NOT NULL, -- motorway, primary, secondary, tertiary, residential, etc.
    source BIGINT NOT NULL REFERENCES network_nodes(id) ON DELETE CASCADE,
    target BIGINT NOT NULL REFERENCES network_nodes(id) ON DELETE CASCADE,
    cost DOUBLE PRECISION NOT NULL, -- Longitud métrica (Fase 2: peor caso de Dijkstra)
    reverse_cost DOUBLE PRECISION NOT NULL, -- -1 si es sentido único, longitud si es bidireccional
    length_m DOUBLE PRECISION NOT NULL,
    maxspeed_kmh INTEGER DEFAULT 50,
    oneway BOOLEAN DEFAULT FALSE,
    commune VARCHAR(50),
    geom geometry(LineString, 4326) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Índices espaciales y de conectividad topológica
CREATE INDEX IF NOT EXISTS idx_network_nodes_geom ON network_nodes USING GIST (geom);
CREATE INDEX IF NOT EXISTS idx_network_nodes_commune ON network_nodes (commune);

CREATE INDEX IF NOT EXISTS idx_network_edges_geom ON network_edges USING GIST (geom);
CREATE INDEX IF NOT EXISTS idx_network_edges_source ON network_edges (source);
CREATE INDEX IF NOT EXISTS idx_network_edges_target ON network_edges (target);
CREATE INDEX IF NOT EXISTS idx_network_edges_commune ON network_edges (commune);

-- ------------------------------------------------------------------------------
-- 3. METADATA (Tabla Temporal y Flexible con JSONB y Geometría Universal)
-- ------------------------------------------------------------------------------
-- Permite cargar cualquier dato de metadata (inventario MOP, OSM, Waze, clima)
-- sin amarrar al equipo a un esquema rígido antes de tiempo.
CREATE TABLE IF NOT EXISTS temp_metadata (
    id BIGSERIAL PRIMARY KEY,
    nombre VARCHAR(150),                     -- Nombre o identificador del elemento
    tipo VARCHAR(50) NOT NULL,               -- 'senaletica', 'semaforo', 'trafico', 'clima', etc.
    fuente VARCHAR(100),                     -- 'MOP', 'OSM', 'Waze', 'OpenMeteo', etc.
    comuna VARCHAR(50),                      -- 'Maipú', 'Estación Central', 'Santiago'
    descripcion TEXT,
    propiedades JSONB,                       -- Atributos libres del JSON original
    geom geometry(Geometry, 4326),           -- Acepta Point, LineString o Polygon
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_temp_metadata_geom ON temp_metadata USING GIST (geom);
CREATE INDEX IF NOT EXISTS idx_temp_metadata_tipo ON temp_metadata (tipo);
CREATE INDEX IF NOT EXISTS idx_temp_metadata_comuna ON temp_metadata (comuna);
CREATE INDEX IF NOT EXISTS idx_temp_metadata_json ON temp_metadata USING GIN (propiedades);

-- ------------------------------------------------------------------------------
-- 4. AMENAZAS (Tabla Temporal y Flexible con JSONB y Geometría Universal)
-- ------------------------------------------------------------------------------
-- Permite cargar cualquier amenaza (zonas hostiles, jammers, lluvias, etc.)
-- de manera libre y compatible con cualquier GeoJSON/JSON.
CREATE TABLE IF NOT EXISTS temp_amenazas (
    id BIGSERIAL PRIMARY KEY,
    nombre VARCHAR(150),                     -- Nombre del sector o amenaza
    tipo VARCHAR(50) NOT NULL,               -- 'zona_hostil', 'jammer', 'clima_severo', etc.
    nivel_riesgo VARCHAR(20),                -- 'BAJO', 'MEDIO', 'ALTO', 'CRITICO'
    comuna VARCHAR(50),                      -- 'Maipú', 'Estación Central', 'Santiago'
    descripcion TEXT,
    propiedades JSONB,                       -- Atributos libres del JSON original
    geom geometry(Geometry, 4326),           -- Acepta Point, LineString o Polygon
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_temp_amenazas_geom ON temp_amenazas USING GIST (geom);
CREATE INDEX IF NOT EXISTS idx_temp_amenazas_tipo ON temp_amenazas (tipo);
CREATE INDEX IF NOT EXISTS idx_temp_amenazas_riesgo ON temp_amenazas (nivel_riesgo);
CREATE INDEX IF NOT EXISTS idx_temp_amenazas_json ON temp_amenazas USING GIN (propiedades);

-- ------------------------------------------------------------------------------
-- 5. RUTAS Y RESULTADOS BASELINE (Dijkstra - Rúbrica Puntos 7 y 8)
-- ------------------------------------------------------------------------------

-- Almacena las rutas calculadas para ser visualizadas en Leaflet
CREATE TABLE IF NOT EXISTS inspection_routes (
    id BIGSERIAL PRIMARY KEY,
    nombre VARCHAR(150) DEFAULT 'Ruta Peor Caso (Dijkstra)',
    algoritmo VARCHAR(50) NOT NULL DEFAULT 'pgr_dijkstra_baseline',
    start_node_id BIGINT REFERENCES network_nodes(id),
    end_node_id BIGINT REFERENCES network_nodes(id),
    costo_total DOUBLE PRECISION NOT NULL,
    distancia_m DOUBLE PRECISION NOT NULL,
    geom geometry(LineString, 4326),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_inspection_routes_geom ON inspection_routes USING GIST (geom);

-- Función que resuelve la ruta más corta con pgr_dijkstra por longitud (peor caso)
CREATE OR REPLACE FUNCTION fn_calculate_baseline_dijkstra_route(
    start_node BIGINT,
    end_node BIGINT
)
RETURNS TABLE (
    seq INTEGER,
    node_id BIGINT,
    edge_id BIGINT,
    cost DOUBLE PRECISION,
    agg_cost DOUBLE PRECISION,
    geom geometry
) AS $$
BEGIN
    RETURN QUERY
    SELECT 
        d.seq,
        d.node,
        d.edge,
        d.cost,
        d.agg_cost,
        e.geom
    FROM pgr_dijkstra(
        'SELECT id, source, target, cost, reverse_cost FROM network_edges',
        start_node,
        end_node,
        directed := true
    ) AS d
    LEFT JOIN network_edges e ON d.edge = e.id
    ORDER BY d.seq;
END;
$$ LANGUAGE plpgsql;
