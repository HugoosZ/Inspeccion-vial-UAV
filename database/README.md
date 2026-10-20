# Diseño y Estructura de la Base de Datos Geoespacial

Este directorio contiene la definición de la base de datos para el proyecto de **Detección de Señaléticas y Elementos Viales con Flota UAV** en las comunas de **Maipú, Estación Central y Santiago**.

---

## 1. Archivo SQL de Creación desde Cero
El archivo [`init.sql`](./init.sql) contiene las sentencias DDL para inicializar la base de datos PostgreSQL con las extensiones **PostGIS** y **pgRouting**. Al iniciar el contenedor de base de datos (`db`) mediante Docker Compose, este script se ejecuta automáticamente en `/docker-entrypoint-initdb.d/`.

---

## 2. Diagrama Entidad-Relación (Estructura Flexible)

Para el módulo de ETL y visualización, la base de datos se diseñó con un núcleo topológico estricto para **pgRouting** y tablas flexibles (`JSONB` + `geometry(Geometry, 4326)`) para **Metadata** y **Amenazas**. Esto permite extraer, transformar y cargar datos de diversas fuentes sin que el equipo quede atado a columnas fijas antes de consolidar el análisis.

```mermaid
erDiagram
    NETWORK_NODES ||--o{ NETWORK_EDGES : "source / target"
    NETWORK_NODES ||--o{ INSPECTION_ROUTES : "start / end node"

    NETWORK_NODES {
        bigserial id PK
        bigint osm_id
        varchar commune
        numeric elevation_m
        geometry geom "Point EPSG:4326"
    }

    NETWORK_EDGES {
        bigserial id PK
        bigint osm_id
        varchar name
        varchar highway
        bigint source FK
        bigint target FK
        double cost "Longitud (metros)"
        double reverse_cost
        double length_m
        int maxspeed_kmh
        boolean oneway
        varchar commune
        geometry geom "LineString EPSG:4326"
    }

    TEMP_METADATA {
        bigserial id PK
        varchar nombre
        varchar tipo "senaletica, semaforo, trafico, clima"
        varchar fuente "MOP, OSM, Waze, OpenMeteo"
        varchar comuna
        text descripcion
        jsonb propiedades "Atributos dinámicos del JSON"
        geometry geom "Geometry EPSG:4326 (Point/Line/Polygon)"
    }

    TEMP_AMENAZAS {
        bigserial id PK
        varchar nombre
        varchar tipo "zona_hostil, jammer, clima_severo"
        varchar nivel_riesgo "BAJO, MEDIO, ALTO, CRITICO"
        varchar comuna
        text descripcion
        jsonb propiedades "Atributos dinámicos del JSON"
        geometry geom "Geometry EPSG:4326 (Point/Line/Polygon)"
    }

    INSPECTION_ROUTES {
        bigserial id PK
        varchar nombre
        varchar algoritmo "pgr_dijkstra_baseline"
        bigint start_node_id FK
        bigint end_node_id FK
        double costo_total
        double distancia_m
        geometry geom "LineString EPSG:4326"
    }
```

---

## 3. Justificación Técnica del Diseño

1. **Infraestructura (`network_nodes`, `network_edges`)**:
   - Cumple con la topología estricta requerida por **pgRouting** para ejecutar `pgr_dijkstra`.
   - El atributo `cost` es la longitud en metros (`length_m`), cumpliendo con la ruta **peor caso** (baseline sin costos multivariables).

2. **Tablas Temporales/Genéricas (`temp_metadata` y `temp_amenazas`)**:
   - **`geom geometry(Geometry, 4326)`**: Permite almacenar puntos (señales, jammers), líneas (tráfico Waze) o polígonos (zonas hostiles, áreas de lluvia) en la misma tabla sin restricciones.
   - **`propiedades JSONB`**: Permite cargar cualquier estructura de atributos extraída de APIs o archivos JSON sin necesidad de alterar el esquema SQL por cada nueva fuente.
   - **Extensibilidad futura**: Una vez consolidadas las fuentes definitivas con el equipo, estas tablas se pueden normalizar a tablas especializadas.

3. **Ruteo Baseline (`inspection_routes` y `fn_calculate_baseline_dijkstra_route`)**:
   - Implementa la función que resuelve la ruta base con `pgr_dijkstra` para ser consumida y graficada por Leaflet en el sitio web.
