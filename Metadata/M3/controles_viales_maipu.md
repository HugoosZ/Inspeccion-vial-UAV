# Documentación: controles viales de Maipú

## 1. Descripción

El archivo `controles_viales_maipu.geojson` contiene elementos de OpenStreetMap obtenidos mediante la API de ohsome. Representa controles viales y cruces peatonales dentro del área geográfica consultada de Maipú, Chile.

## 2. Fuente y extracción

- **Fuente:** OpenStreetMap, consultado mediante ohsome API v2.
- **Formato de salida:** GeoJSON.
- **Área de interés:** bounding box definido mediante coordenadas geográficas.
- **Elementos consultados:** nodos y líneas que coinciden con el filtro de controles viales.
- **Clasificaciones principales:** `highway=stop`, `highway=give_way`, `highway=traffic_signals` y `highway=crossing`.

El área de consulta es un rectángulo geográfico aproximado y no necesariamente coincide con el límite administrativo de la comuna.

## 3. Estructura general

El archivo utiliza una estructura `FeatureCollection`. Cada elemento de `features` es una `Feature` compuesta por:

- `properties`: atributos e información de OpenStreetMap.
- `geometry`: tipo de geometría y coordenadas geográficas.

El sistema de referencia declarado es CRS84, con coordenadas en el orden longitud, latitud.

## 4. Atributos principales

| Campo | Descripción |
|---|---|
| `osm_type` | Tipo de elemento de OpenStreetMap |
| `osm_id` | Identificador original del elemento |
| `edit_timestamp` | Fecha y hora de edición registrada |
| `version` | Versión del elemento |
| `geom_type` | Tipo de geometría |
| `tag_highway` | Clasificación vial principal |
| `tag_crossing` | Tipo de cruce peatonal, si está registrado |
| `tag_kerb` | Información sobre el borde de la acera |
| `tag_tactile_paving` | Información sobre pavimento táctil |
| `tag_traffic_signals:sound` | Información sobre señales acústicas del semáforo |
| `tag_traffic_signals:vibration` | Información sobre señales vibratorias del semáforo |
| `geometry` | Geometría espacial del elemento |

El archivo también conserva otros atributos y etiquetas disponibles en la respuesta de la API.

## 5. Consideraciones

- Los atributos pueden contener valores nulos cuando una etiqueta no está registrada.
- La información depende de la cobertura y actualización de OpenStreetMap.
- La presencia de un elemento no garantiza por sí sola que su ubicación y clasificación hayan sido verificadas en terreno.
