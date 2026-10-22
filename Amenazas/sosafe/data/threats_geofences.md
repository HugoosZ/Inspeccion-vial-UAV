# Documentación de Estructura de Datos: `threats_geofences.geojson`
### Documentación de Archivos GeoJSON / JSON

Este documento detalla la especificación técnica, estructura y diccionario de datos del archivo [`threats_geofences.geojson`](data/threats_geofences.geojson), generado por el clasificador IA a partir de los reportes de SOSAFE para la comuna de Maipú.

---

## 1. Resumen y Propósito del Archivo

* **Nombre del archivo**: `threats_geofences.geojson`
* **Ubicación**: `Amenazas/sosafe/data/threats_geofences.geojson`
* **Estándar**: GeoJSON ([RFC 7946](https://tools.ietf.org/html/rfc7946))
* **Sistema de Coordenadas (CRS)**: WGS 84 (`EPSG:4326`), coordenadas geográficas expresadas en grados decimales `[Longitud, Latitud]`.
* **Propósito**: 
  1. **Visualización en Plataforma Web (Leaflet)**: Permite renderizar las capas de calor, marcadores de eventos y geocercas poligonales de exclusión aérea.
  2. **Intersección Espacial en Base de Datos (PostGIS)**: Permite almacenar los polígonos en la tabla `temp_amenazas` y cruzar espacialmente con las calles (`network_edges`) mediante `ST_Intersects`.
  3. **Modelamiento de Fallas y Asignación de Pesos**: Las aristas de la red vial que intersecten estas geocercas reciben penalización infinita (`cost = -1` o `cost = INF`) para evitar el derribo o daño del UAV.

---

## 2. Relación entre Archivos Post-IA

Tras el procesamiento semántico por parte del modelo de Inteligencia Artificial, se generan dos archivos filtrados complementarios:

| Archivo | Formato | Propósito Principal en la Arquitectura |
| :--- | :---: | :--- |
| **`threats_geofences.geojson`** | GeoJSON (RFC 7946) | **Capa espacial y visual:** Contiene geometrías de Puntos y Polígonos para Leaflet y consultas espaciales en PostGIS. |
| **`active_threats_graph.json`** | JSON plano | **Algoritmo de Ruteo en Memoria:** Contiene la lista limpia de amenazas activas con su radio métrico y factores numéricos de penalización para inyección directa en algoritmos de ruteo. |

---

## 3. Estructura General del GeoJSON

El archivo está estructurado como una colección de objetos espaciales (`FeatureCollection`):

```json
{
  "type": "FeatureCollection",
  "name": "uav_threat_geofences_maipu",
  "features": [
    { ...entidad punto... },
    { ...entidad polígono circular... }
  ]
}
```

Cada amenaza confirmada por la IA genera **dos entidades complementarias** en el array `features`:
1. Una entidad de tipo **`Point`** (`amenaza_punto`): Representa el punto exacto reportado por el vecino.
2. Una entidad de tipo **`Polygon`** (`geocerca_exclusion`): Representa el área de exclusión aérea (No-Fly Zone) generada a partir del radio de seguridad establecido para esa categoría.

---

## 4. Diccionario de Datos: Nivel Raíz

| Campo | Tipo | Ejemplo | Descripción |
| :--- | :---: | :--- | :--- |
| **`type`** | `String` | `"FeatureCollection"` | Identificador obligatorio de GeoJSON que indica que el documento agrupa múltiples entidades geográficas. |
| **`name`** | `String` | `"uav_threat_geofences_maipu"` | Nombre semántico de la capa vectorial para clientes SIG (Leaflet, QGIS, Geoserver). |
| **`features`** | `Array` | `[...]` | Lista secuencial de entidades geográficas (`Feature`) que componen la capa. |

---

## 5. Diccionario de Datos: Entidad Tipo 1 (`amenaza_punto`)

Representa el epicentro geográfico del incidente reportado.

### Ejemplo JSON
```json
{
  "type": "Feature",
  "geometry": {
    "type": "Point",
    "coordinates": [
      -70.744834,
      -33.480072
    ]
  },
  "properties": {
    "id": 12034739,
    "tipo": "Emergencia de Bomberos",
    "categoria": "INCENDIO_COLUMNA_TERMICA",
    "nivel_riesgo": "ALTO",
    "descripcion": "Quema ilegal , cochinos y demás con peligro de propagación",
    "justificacion": "Humo denso y columnas térmicas que desestabilizan el control de actitud del dron.",
    "radio_buffer": 500,
    "marker_color": "#f77f00",
    "tipo_geometria": "amenaza_punto"
  }
}
```

### Detalle de Parámetros

| Parámetro | Tipo de Dato | Ejemplo | Explicación Técnica y Uso Operacional |
| :--- | :---: | :--- | :--- |
| **`geometry.type`** | `String` | `"Point"` | Tipo geométrico GeoJSON. Indica un punto adimensional en el espacio. |
| **`geometry.coordinates`** | `Array[Float]` | `[-70.744834, -33.480072]` | Par de coordenadas en orden estándar GeoJSON: **`[Longitud, Latitud]`**. |
| **`properties.id`** | `Integer` | `12034739` | Identificador único del reporte en la plataforma SOSAFE. Funciona como clave primaria para trazabilidad. |
| **`properties.tipo`** | `String` | `"Emergencia de Bomberos"` | Categoría original reportada por el usuario en la app SOSAFE. |
| **`properties.categoria`** | `String` | `"INCENDIO_COLUMNA_TERMICA"` | Categoría aeronáutica asignada por la IA tras análisis semántico. Valores posibles: `PIROTECNIA_FUEGOS_ARTIFICIALES`, `DISPAROS_BALACERA`, `OBSTACULO_CABLES_POSTES`, `AERONAVE_EMERGENCIA`, `INCENDIO_COLUMNA_TERMICA`. |
| **`properties.nivel_riesgo`** | `String` | `"ALTO"` | Severidad para la aeronave UAV: `CRITICO` (riesgo inminente de destrucción o colisión), `ALTO` (humo denso y térmicas), `MEDIO` (precaución). |
| **`properties.descripcion`** | `String` | `"Quema ilegal..."` | Texto original escrito por el ciudadano en la aplicación comunitaria. |
| **`properties.justificacion`** | `String` | `"Humo denso..."` | Explicación técnica redactada por la IA que justifica por qué el evento pone en riesgo el vuelo a 20 m de altura. |
| **`properties.radio_buffer`** | `Integer` | `500` | Radio de seguridad asignado a la amenaza expresado en **metros**. |
| **`properties.marker_color`** | `String (HEX)` | `"#f77f00"` | Código hexadecimal para pintar el icono en Leaflet: Rojo (`#d90429`), Naranja (`#f77f00`), Amarillo (`#fcbf49`). |
| **`properties.tipo_geometria`** | `String` | `"amenaza_punto"` | Discriminador de tipo. Permite al frontend de Leaflet filtrar y aplicar funciones de marcador (`L.circleMarker` o `L.marker`). |

---

## 6. Diccionario de Datos: Entidad Tipo 2 (`geocerca_exclusion`)

Representa la zona poligonal de exclusión aérea (No-Fly Zone) calculada geodésicamente alrededor de la amenaza.

### Ejemplo JSON
```json
{
  "type": "Feature",
  "geometry": {
    "type": "Polygon",
    "coordinates": [
      [
        [-70.739449, -33.480072],
        [-70.739552, -33.479196],
        ...
        [-70.739449, -33.480072]
      ]
    ]
  },
  "properties": {
    "id": 12034739,
    "geofence_id": "nfz_12034739",
    "nivel_riesgo": "ALTO",
    "categoria": "INCENDIO_COLUMNA_TERMICA",
    "fillColor": "#f77f00",
    "color": "#f77f00",
    "weight": 2,
    "fillOpacity": 0.35,
    "radio_metros": 500,
    "tipo_geometria": "geocerca_exclusion"
  }
}
```

### Detalle de Parámetros

| Parámetro | Tipo de Dato | Ejemplo | Explicación Técnica y Uso Operacional |
| :--- | :---: | :--- | :--- |
| **`geometry.type`** | `String` | `"Polygon"` | Tipo geométrico GeoJSON. Representa una superficie bidimensional cerrada. |
| **`geometry.coordinates`** | `Array[Array[Array[Float]]]` | `[[[lon, lat], ...]]` | Anillo lineal cerrado de **32 vértices** generados geodésicamente considerando la curvatura terrestre y corrección por latitud ($\cos(\text{lat})$). El primer punto es idéntico al último para cerrar el polígono. |
| **`properties.id`** | `Integer` | `12034739` | Clave foránea referencial al ID del reporte SOSAFE de origen. |
| **`properties.geofence_id`** | `String` | `"nfz_12034739"` | Identificador único de la geocerca (prefijo `nfz_` + ID numérico). Usado por Leaflet para manipulación por capa. |
| **`properties.nivel_riesgo`** | `String` | `"ALTO"` | Nivel de severidad operacional de la zona (`CRITICO`, `ALTO`, `MEDIO`). |
| **`properties.categoria`** | `String` | `"INCENDIO_COLUMNA_TERMICA"` | Tipo de peligro que origina la zona de exclusión. |
| **`properties.fillColor`** | `String (HEX)` | `"#f77f00"` | Color de relleno interior del polígono para Leaflet. |
| **`properties.color`** | `String (HEX)` | `"#f77f00"` | Color de la línea de borde del polígono. |
| **`properties.weight`** | `Integer` | `2` | Grosor del trazo de contorno en píxeles de pantalla. |
| **`properties.fillOpacity`** | `Float` | `0.35` | Nivel de opacidad (0.0 a 1.0). Permite visualizar las calles viales subyacentes sin bloquear el mapa base. |
| **`properties.radio_metros`** | `Integer` | `500` | Dimensión física del radio del círculo en metros reales sobre el terreno. |
| **`properties.tipo_geometria`** | `String` | `"geocerca_exclusion"` | Discriminador para aplicar directamente la función `L.geoJSON(..., { style: ... })` en el visualizador web. |

---

*Nota: Este archivo de documentación técnica fue elaborado con el apoyo de Inteligencia Artificial (IA).*

