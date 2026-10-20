# Infraestructura · Red vial de Maipú (nodos y aristas)

Punto 1 de la rúbrica: `extractor.py` automatiza la extracción y transformación de la red vial a GeoJSON. Punto 6: `cargar.py` lleva esos archivos a las tablas `network_nodes` y `network_edges`, que usa `pgr_dijkstra`.

## Archivos

| Archivo | Rol |
|---|---|
| `extractor.py` | Extract + Transform: descarga la red con OSMnx y genera los dos GeoJSON |
| `cargar.py` | Load: vacía e inserta `network_nodes` y `network_edges` (`--dry-run` para probar sin BD) |
| `data/nodos_maipu.geojson` | Nodos (intersecciones y extremos de calle) |
| `data/aristas_maipu.geojson` | Aristas (tramos de calle entre dos nodos) |

## Fuentes

| Dato | Fuente | Acceso |
|---|---|---|
| Límite comunal | OpenStreetMap, Nominatim (`ox.geocode_to_gdf`) | Libre |
| Red vial | OpenStreetMap, Overpass API (`ox.graph_from_polygon`, `network_type="drive"`) | Libre |
| Elevación de nodos | Open-Meteo Elevation API (Copernicus DEM 90 m), lotes de 100 puntos | Libre |

Si Nominatim no devuelve el polígono de la comuna, se usa el rectángulo de respaldo `(-70.92, -33.62, -70.70, -33.46)`; `metadata.origen_limite` indica cuál se usó.

## Decisiones de modelado

- **Red `drive`:** se descargan las calles vehiculares, que es donde están las señales, semáforos y demarcaciones que el dron inspecciona.
- **Grafo no dirigido:** el dron vuela sobre la calle y no respeta el sentido del tránsito, así que cada tramo es una sola arista con `reverse_cost = cost`. El dato `oneway` se conserva. Con `INFRA_RESPETAR_SENTIDO=1` las calles de sentido único reciben `reverse_cost = -1`.
- **Costo de la fase 2:** `cost = length_m` (ruta de peor caso, punto 8). La elevación queda lista para el costo energético de la fase 3:
$$E(e)=k_1\,d(e)+k_2\,\Delta h^{+}(e),\qquad \Delta h^{+}(e)=\max\{0,\;h(\text{target})-h(\text{source})\}$$

## Variables de entorno

| Variable | Defecto | Descripción |
|---|---|---|
| `INFRA_LUGAR` | `Maipú, Provincia de Santiago, Chile` | Consulta a Nominatim |
| `INFRA_NETWORK_TYPE` | `drive` | Tipo de red OSMnx |
| `INFRA_ELEVACION` | `1` | `0` omite la consulta de elevación (más rápido) |
| `INFRA_RESPETAR_SENTIDO` | `0` | `1` aplica el sentido del tránsito |

## Estructura de `nodos_maipu.geojson`

FeatureCollection de `Point` en EPSG:4326.

### `metadata`

| Campo | Descripción |
|---|---|
| `generado_utc` | Momento de la extracción |
| `comuna`, `total_features` | Comuna y cantidad de nodos |
| `fuente`, `lugar`, `origen_limite` | Fuente OSM, consulta usada y si el límite vino de Nominatim o del rectángulo |
| `network_type`, `dirigido`, `respetar_sentido` | Parámetros del grafo |
| `elevacion` | Fuente de la elevación, o `null` si se omitió |

### `properties`

| Campo | Tipo | Descripción | Columna en `network_nodes` |
|---|---|---|---|
| `id` | int | ID consecutivo del nodo (1..N) | `id` |
| `osm_id` | int | ID del nodo en OpenStreetMap | `osm_id` |
| `commune` | string | `Maipú` | `commune` |
| `elevation_m` | float | Altitud sobre el nivel del mar (0 si no se consultó) | `elevation_m` |
| `street_count` | int | Calles que confluyen en el nodo (1 = calle sin salida) | — |
| `geometry` | Point | Posición del nodo | `geom` |

## Estructura de `aristas_maipu.geojson`

FeatureCollection de `LineString` en EPSG:4326. `metadata` igual a la de nodos, más `longitud_total_km`.

### `properties`

| Campo | Tipo | Descripción | Columna en `network_edges` |
|---|---|---|---|
| `id` | int | ID consecutivo de la arista (1..M) | `id` |
| `osm_id` | int | ID OSM de la vía (la primera si el tramo une varias) | `osm_id` |
| `name` | string o null | Nombre de la calle; varios nombres separados por ` / ` | `name` |
| `highway` | string | Categoría OSM: `primary`, `secondary`, `residential`, etc. | `highway` |
| `source`, `target` | int | `id` de los nodos extremos | `source`, `target` |
| `length_m` | float | Largo del tramo en metros | `length_m` |
| `cost` | float | Costo en sentido source→target (= `length_m`) | `cost` |
| `reverse_cost` | float | Costo en sentido target→source (`length_m` o `-1`) | `reverse_cost` |
| `maxspeed_kmh` | int | Velocidad máxima OSM o valor por defecto según categoría | `maxspeed_kmh` |
| `oneway` | bool | `true` si la calle es de sentido único para vehículos | `oneway` |
| `lanes` | string o null | Número de pistas según OSM | — |
| `commune` | string | `Maipú` | `commune` |
| `geometry` | LineString | Trazado de la calle | `geom` |

## Carga y verificación

`cargar.py` ejecuta `TRUNCATE inspection_routes, network_edges, network_nodes RESTART IDENTITY CASCADE`, inserta en lotes de 1000 y ajusta las secuencias de los IDs. Para comprobar que el grafo funciona con pgRouting:

```sql
SELECT * FROM fn_calculate_baseline_dijkstra_route(1, 100);
```
