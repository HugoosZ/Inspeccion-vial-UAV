# Restricciones Aéreas

Este módulo contiene la información geoespacial oficial de la Dirección General de Aeronáutica Civil (DGAC) sobre aeródromos y zonas de exclusión aérea, junto con su validación espacial sobre los paraderos de Maipú, con el objetivo de garantizar que los drones operen fuera de áreas restringidas.
# Documentación: restricciones aéreas y aeródromos

## 1. Descripción

El módulo está compuesto por el archivo `aerodromos.geojson`, que reúne el catastro georreferenciado de aeródromos y pistas de aterrizaje de la Dirección General de Aeronáutica Civil (DGAC), y por el archivo `paraderos_seguros.csv`, que contiene el cruce de seguridad aérea aplicado sobre los 942 paraderos de la comuna de Maipú. Su propósito es impedir la planificación de rutas de drones dentro de zonas de exclusión o protección aeroportuaria.

## 2. Fuente y extracción

- **Fuente:** Dirección General de Aeronáutica Civil (DGAC) — Visor de zonas de vuelo (`AERODROMOS.kmz` / `doc.kml`), cruzado con el catastro de paraderos de Maipú.
- **Formato de salida:** GeoJSON (`aerodromos.geojson`) y CSV (`paraderos_seguros.csv`).
- **Área de interés:** Catastro nacional de aeródromos (774 registros) evaluado sobre el área comunal de Maipú, Chile.
- **Elementos consultados:** Coordenadas puntuales (`Point`) de aeródromos y pistas chicas, comparadas contra las coordenadas de los 942 paraderos comunales.
- **Criterio de exclusión:** Radio de seguridad mínimo de 2 km alrededor de cada aeródromo, calculado mediante distancia ortodrómica (fórmula de Haversine).

## 3. Estructura general

### Archivo `aerodromos.geojson`
Utiliza una estructura `FeatureCollection` acompañada de un bloque `metadata` con el resumen de generación (`layer`, `category`, `source`, `generatedAt`, `featureCount`: 774). Cada elemento de `features` es una `Feature` compuesta por:

- `properties`: metadatos del aeródromo e información de la capa KML/KMZ de origen.
- `geometry`: geometría tipo `Point` con coordenadas en formato `[longitud, latitud, altitud]`.

### Archivo `paraderos_seguros.csv`
Presenta una estructura tabular de 942 filas y 8 columnas que conserva los campos base de los paraderos de Maipú (`ID`, `Codigo`, `Latitud`, `Longitud`, `Tipo`, `Estado`, `Comuna`) y añade la columna de verificación aérea `Alerta_Aerodromo`.

## 4. Atributos principales

### Archivo GeoJSON (`aerodromos.geojson`)

| Campo | Descripción |
|---|---|
| `name` | Nombre oficial o identificador del aeródromo/pista (ej. `AERODROMO PELDEHUE`, `Ad. Eulogio Sánchez`) |
| `description` | Descripción adicional del recinto en el registro KML |
| `layer` | Nombre de la capa temática (`Aeródromos`) |
| `category` | Categoría de restricción aérea (`Aeródromos`) |
| `folder` | Ruta jerárquica dentro del archivo KMZ de origen (`AERODROMOS.kmz`, `Pistas chicas`, etc.) |
| `sourceFile` | Archivo fuente extraído del paquete comprimido (`doc.kml`) |
| `geometryType` | Tipo de geometría espacial (`Point`) |
| `geometry.coordinates` | Arreglo de coordenadas geográficas `[longitud, latitud, altitud]` |

### Archivo CSV (`paraderos_seguros.csv`)

| Campo | Descripción |
|---|---|
| `ID` | Identificador correlativo de ruteo del paradero (1 a 942) |
| `Codigo` | Código técnico del paradero (ej. `L-13-44-15-SN`) |
| `Latitud` | Coordenada geográfica de latitud del paradero |
| `Longitud` | Coordenada geográfica de longitud del paradero |
| `Tipo` | Tipología de parada (`PS`, `ET`, `PD`) |
| `Estado` | Condición física del paradero (`CON REFUGIO`, `SIN REFUGIO`, etc.) |
| `Comuna` | Comuna registrada (`MAIPé`) |
| `Alerta_Aerodromo` | Estado de validación respecto al radio de exclusión de 2 km (ej. `Seguro`) |

## 5. Consideraciones

- **Geometría puntual y radio de resguardo:** Dado que `aerodromos.geojson` representa cada recinto como un punto (`Point`) y no como un polígono de pista, el script `procesar_aerodromos.py` proyecta un buffer radial de 2 km mediante la fórmula de Haversine para modelar el área de exclusión.
- **Duplicidad en capas de origen:** El archivo KML fuente de la DGAC contiene registros que aparecen tanto en la carpeta general de aeródromos como en subcarpetas de pistas chicas; esto no afecta el filtrado espacial, pero debe considerarse si se realizan conteos estadísticos sobre el GeoJSON.
- **Resultado en Maipú:** La totalidad de los 942 paraderos catastrados en la comuna de Maipú se encuentra a una distancia superior a 2 km respecto a los aeródromos registrados, quedando clasificados con `Alerta_Aerodromo = "Seguro"`.
