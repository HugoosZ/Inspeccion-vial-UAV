# Documentación de Estructura de Datos: `waze_threats_geofences.geojson`
### Diccionario y Especificación Geoespacial

Este documento detalla la especificación técnica, estructura y diccionario de datos del archivo [`waze_threats_geofences.geojson`](waze_threats_geofences.geojson), generado a partir de las alertas de calles cerradas y obras de construcción en la comuna de Maipú extraídas de Waze Live Map.

---

## 1. Resumen y Propósito del Archivo

* **Nombre del archivo**: `waze_threats_geofences.geojson`
* **Ubicación**: `Amenazas/A4/data/waze_threats_geofences.geojson`
* **Estándar**: GeoJSON ([RFC 7946](https://tools.ietf.org/html/rfc7946))
* **Sistema de Coordenadas (CRS)**: WGS 84 (`EPSG:4326`), coordenadas geográficas expresadas en grados decimales `[Longitud, Latitud]`.
* **Propósito**: 
  1. **Visualización en Plataforma Web (Leaflet)**: Representación de marcadores de alerta puntual y geocercas poligonales de exclusión aérea (No-Fly Zones).
  2. **Intersección Espacial en Base de Datos (PostGIS)**: Inserción en la tabla `temp_amenazas` con `fuente_etl='A4_waze'` para cruce espacial con aristas de la red vial (`network_edges`) mediante `ST_Intersects`.
  3. **Modelamiento en el Grafo Resiliente**: Eliminación o asignación de penalización infinita (`factor_penalizacion = INF`, `accion_grafo = BLOQUEAR_ARISTAS`) a las aristas viales que caigan dentro del radio de afectación.

---

## 2. Relación entre Archivos de Salida

| Archivo | Formato | Propósito Principal en la Arquitectura |
| :--- | :---: | :--- |
| **`waze_threats_geofences.geojson`** | GeoJSON (RFC 7946) | **Capa espacial y visual:** Contiene geometrías de Puntos (incidente) y Polígonos circulares (geocerca de exclusión) para Leaflet y PostGIS. |
| **`active_waze_threats_graph.json`** | JSON estructurado | **Algoritmo de Ruteo en Memoria:** Contiene la lista limpia de amenazas con su radio métrico y atributos para inyección directa en algoritmos de ruteo. |

---

## 3. Estructura General del GeoJSON

El archivo está estructurado como una colección de objetos espaciales (`FeatureCollection`). Por cada alerta confirmada se generan **dos entidades complementarias**:

1. Una entidad de tipo **`Point`** (`amenaza_punto`): Representa el punto exacto de la obra o corte vial.
2. Una entidad de tipo **`Polygon`** (`geocerca_exclusion`): Representa el área de exclusión aérea circular (No-Fly Zone) generada a partir del radio de seguridad establecido.

---

## 4. Diccionario de Datos: Propiedades de las Entidades (`properties`)

| Campo | Tipo | Ejemplo | Descripción |
| :--- | :---: | :--- | :--- |
| **`id_amenaza`** | `String` | `"WAZE_wz-cl-maipu-001"` | Identificador único con prefijo de la fuente para trazabilidad. |
| **`fuente`** | `String` | `"Waze Live Map"` | Fuente de datos original. |
| **`sector`** | `String` | `"Plaza de Maipú / Centro Cívico"` | Sector o cuadrante de los 10 definidos en Maipú. |
| **`tipo_amenaza`** | `String` | `"calle_cerrada"`, `"construccion_vial"` | Tipo estandarizado para la tabla `temp_amenazas`. |
| **`categoria`** | `String` | `"CALLE_CERRADA"`, `"CONSTRUCCION_VIAL"` | Categoría operacional asignada. |
| **`nivel_riesgo`** | `String` | `"CRITICO"`, `"ALTO"` | Nivel de severidad operacional. |
| **`accion_grafo`** | `String` | `"BLOQUEAR_ARISTAS"` | Acción requerida sobre el grafo de navegación. |
| **`factor_penalizacion`** | `String` | `"INF"` | Factor de costo asignado a las aristas afectadas. |
| **`calle`** | `String` | `"Avenida 5 de Abril"` | Nombre de la arteria vial afectada. |
| **`comuna`** | `String` | `"Maipú"` | Comuna de la entidad. |
| **`descripcion`** | `String` | `"Calle cerrada por obras viales..."` | Descripción del reporte reportado en Waze. |
| **`justificacion`** | `String` | `"Vía cerrada al tránsito..."` | Justificación técnica del riesgo para el UAV a 20m. |
| **`radio_buffer`** | `Integer` | `60` o `50` | Radio de seguridad en metros. |
| **`marker_color`** | `String` | `"#d90429"` o `"#f77f00"` | Código hexadecimal para simbología visual en Leaflet. |
| **`confianza`** | `Integer` | `5` | Nivel de confiabilidad asignado por Waze (1 a 5). |
| **`tipo_geometria`** | `String` | `"amenaza_punto"` o `"geocerca_exclusion"` | Distingue entre el marcador puntual y el polígono. |
| **`valido_desde`** | `String (ISO 8601)` | `"2026-10-22T21:00:00Z"` | Inicio de validez temporal. |
| **`valido_hasta`** | `String (ISO 8601)` | `"2026-10-23T21:00:00Z"` | Fin de validez temporal estimada. |
| **`activa`** | `Boolean` | `true` | Indicador de evento dinámico vigente. |

---

## 5. Justificación Operacional para Vuelo a 20 Metros AGL

* **Calle Cerrada (CRÍTICO, 60 m):**
  Un cierre vial por obras mayores implica el corte total del flujo vehicular y peatonal. En estas zonas, la señalética reglamentaria o preventiva se encuentra cubierta, retirada o no vigente; además, suelen instalarse barreras físicas de hormigón (New Jersey) o rejas que entorpecen la maniobra del dron a baja cota.
* **Construcción en Calzada (ALTO, 50 m):**
  Las obras activas conllevan la presencia de maquinaria pesada con partes móviles en elevación (plumas, tolvas, brazos articulados) y la generación de nubes de polvo y calor térmico. El radio de 50 metros asegura que el dron no entre en la zona de influencia de la maquinaria ni degrade sus cámaras fotográficas.
