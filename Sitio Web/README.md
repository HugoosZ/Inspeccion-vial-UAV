# Sitio Web · Visualizador Leaflet

`index.html` crea un sitio con **Leaflet 1.9.4** que muestra la infraestructura, la metadata y las amenazas cargadas en PostGIS. El contenedor `web` (FastAPI) lo sirve en `http://localhost:8080` y le entrega los datos en GeoJSON a través de la API de `docker/web/app.py`.

```
Navegador ── GET /            ──> FastAPI sirve Sitio Web/index.html
          ── GET /api/capas   ──> catálogo y conteos desde la BD
          ── GET /api/...     ──> GeoJSON generado con ST_AsGeoJSON en PostGIS
```

## Archivos

| Archivo | Rol |
|---|---|
| `index.html` | Sitio completo (HTML, CSS y JavaScript en un solo archivo). Carga Leaflet desde unpkg |
| `../docker/web/app.py` | API que consulta PostGIS y sirve esta carpeta |

## Capas

Cada capa corresponde a uno de los JSON cargados en la BD y se descarga solo al activarla.

| Grupo | Capa | Endpoint | Tabla | Visualización |
|---|---|---|---|---|
| Infraestructura | Red vial | `/api/infraestructura` | `network_edges` | Líneas; vías principales más gruesas |
| Infraestructura | Nodos | `/api/nodos` | `network_nodes` | Puntos (apagada por defecto) |
| Metadata | M1 · Paraderos | `/api/metadata?fuente=M1_paraderos` | `temp_metadata` | Puntos azules |
| Metadata | M3 · Controles viales | `/api/metadata?fuente=M3_controles_viales` | `temp_metadata` | PARE, Ceda el paso, semáforos y cruces por color |
| Metadata | M5 · Vulnerabilidad | `/api/metadata?fuente=M5_censo_manzanas&simplificar=0.00003` | `temp_metadata` | Coropleta del índice $V_m$ por manzana |
| Metadata | M6 · Aeródromos | `/api/metadata?fuente=M6_aerodromos` | `temp_metadata` | Puntos |
| Amenazas | T2 · Incendios | `/api/amenazas?fuente=T2_incendios` | `temp_amenazas` | Polígonos de exclusión |
| Amenazas | T3 · Clima severo | `/api/amenazas?fuente=T3_clima` | `temp_amenazas` | Celdas por nivel de riesgo, con selector de hora |
| Amenazas | T5 · Lluvias | `/api/amenazas?fuente=T5_lluvias_maipu` | `temp_amenazas` | Círculo con el radio de afectación (activas); historial opcional |
| Amenazas | T6 · SOSAFE | `/api/amenazas?fuente=T6_sosafe` | `temp_amenazas` | Geocerca y punto del reporte |

Colores de riesgo, comunes a todas las amenazas: BAJO gris, MEDIO amarillo, ALTO naranjo, CRÍTICO rojo.

## API

| Endpoint | Parámetros | Devuelve |
|---|---|---|
| `GET /health` | — | Estado de la conexión a la BD |
| `GET /api/capas` | — | Conteos de la red vial y de cada `fuente_etl` en metadata y amenazas |
| `GET /api/infraestructura` | `highway`, `simplificar` | Aristas de la red vial |
| `GET /api/nodos` | — | Nodos de la red vial |
| `GET /api/metadata` | `fuente`, `tipo`, `comuna`, `simplificar` | Filas de `temp_metadata` |
| `GET /api/amenazas` | `fuente`, `tipo`, `riesgo` | Filas de `temp_amenazas` |
| `GET /api/route/baseline` | `start_node`, `end_node` | Ruta `pgr_dijkstra` por longitud (una ruta) |
| `GET /api/rutas` | `inicio_lon`, `inicio_lat`, `objetivo_lon`, `objetivo_lat` + restricciones opcionales | k rutas `pgr_dijkstra` y k rutas TDRPP-GA, métrica de resiliencia y diferencial |
| `GET /api/rutas/preferencias` | — | Restricciones por defecto |

`simplificar` es la tolerancia en grados de `ST_SimplifyPreserveTopology`; se usa en las manzanas del censo para que el GeoJSON pese menos. La documentación interactiva de la API queda en `http://localhost:8080/docs`.

## Uso

```bash
docker compose up -d --build
docker compose exec etl python main.py     # si la BD aún no tiene datos
# abrir http://localhost:8080
```

El sitio requiere conexión a internet para descargar Leaflet y el mapa base.

## Mapa base

Se usa OpenStreetMap (`https://tile.openstreetmap.org/{z}/{x}/{y}.png`), que no requiere API key. La atribución "© OpenStreetMap contributors" debe quedar visible en el mapa.

## Planificación de rutas

El panel **Planificación de rutas** permite:

1. Fijar el **inicio** y el **objetivo** con dos clics en el mapa (los marcadores se pueden arrastrar).
2. Ajustar las **restricciones del usuario** (k, hora de salida, aeródromos, paraderos, vulnerabilidad, tipos de señal, clima, corredor, autonomía). Lo que no se modifica se envía como valor por defecto.
3. Presionar **Calcular rutas**. El mapa muestra las aristas a inspeccionar (amarillo), las k rutas de `pgr_dijkstra` (azules, punteadas) y las k rutas del TDRPP-GA (verde, magenta…), con una tabla que compara $R$, $R_{sup}$, cobertura, solapamiento y longitud, junto al diferencial $\Delta R$.

El detalle del modelo de fallas, los pesos, las restricciones, el algoritmo y la métrica está en [`docker/web/ruteo/README.md`](../docker/web/ruteo/README.md).
