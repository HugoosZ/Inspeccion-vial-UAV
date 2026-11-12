# M3 · Puentes, pasarelas y enlaces a desnivel (MOP)

Un puente o paso a desnivel es una estructura elevada sobre la calle. Para el dron implica dos riesgos: si la estructura alcanza la altura de vuelo es un obstáculo (Primatesta et al., 2020, consideran no volable toda celda con un obstáculo a la altura de vuelo), y bajo o junto a ella la señal GNSS se degrada (Dorafshan et al., 2017, identifican el control sin GPS como el desafío principal de volar cerca de puentes).

## Archivos

| Archivo | Rol |
|---|---|
| `extractor.py` | Extract + Transform: consulta el MOP y filtra por el límite comunal |
| `cargar.py` | Load: inserta el GeoJSON en `temp_metadata` (`--dry-run` para probar sin BD) |
| `data/puentes_maipu.geojson` | Salida del ETL |

## Fuente y acceso

Servidor SIT del Ministerio de Obras Públicas (`https://rest-sit.mop.gob.cl/arcgis/rest/services/`), público y sin autenticación:

| Servicio y capa | Contenido |
|---|---|
| `VIALIDAD/Puentes/MapServer/0` | Catastro de puentes de la Dirección de Vialidad |
| `CCOP/ELEMENTOS/MapServer/8` | Puentes de las autopistas concesionadas |
| `CCOP/ELEMENTOS/MapServer/6` | Pasarelas peatonales de las autopistas concesionadas |
| `CCOP/INFRA_DGC/MapServer/6` | Enlaces a desnivel de las autopistas concesionadas |

Es una fuente distinta de OpenStreetMap (la red vial del proyecto). El límite comunal es el de MINVU (`Metadata/limite_maipu.geojson`).

## Estructura de `puentes_maipu.geojson`

### Bloque `metadata`

| Campo | Descripción |
|---|---|
| `generado_utc`, `comuna`, `total_features` | Momento, comuna y cantidad de estructuras |
| `fuente` | Servidor consultado |
| `por_capa` | Estructuras de cada capa dentro de Maipú |
| `capas_sin_respuesta` | Capas que fallaron en esta ejecución |
| `cobertura` | Qué caminos cubre el catastro |
| `limite_comunal` | Capa usada para el filtro espacial |

### `properties`

| Campo | Capas | Descripción |
|---|---|---|
| `tipo` | todas | `puente`, `pasarela` o `enlace` |
| `nombre` | todas | Nombre de la estructura |
| `fuente`, `servicio` | todas | Capa MOP de origen y su URL |
| `codigo` | Vialidad, puentes y pasarelas concesionadas | Código de la estructura en el MOP |
| `camino`, `rol_camino` | Vialidad, pasarelas, enlaces | Camino o autopista y su rol |
| `cruza` | Vialidad | Cauce o quebrada que cruza (p. ej. Río Mapocho) |
| `largo_m`, `ancho_total_m`, `ancho_calzada_m` | Vialidad | Dimensiones en metros |
| `anio_construccion` | Vialidad | Año de construcción, si está registrado |
| `carpeta` | Vialidad | Material de la calzada (asfalto, hormigón) |
| `n_cepas`, `n_vigas` | Vialidad | Apoyos intermedios y vigas |
| `tuicion` | Vialidad | Organismo a cargo (`DV` = Dirección de Vialidad) |
| `km` | concesiones | Kilómetro de la autopista |
| `observacion`, `urbano`, `actualizado` | puentes concesionados | Observación, si es urbano y fecha de actualización |

La geometría es un `Point` en EPSG:4326.

## Carga en la base de datos

Cada estructura va a `temp_metadata` con `tipo` = `puente`, `pasarela` o `enlace` y `fuente_etl = "M3_puentes"`.

## Resultado de referencia (12-11-2026)

| Tipo | En Maipú |
|---|---|
| Puentes (Vialidad) | 2 (Puente Rinconada sobre el Mapocho y Puente Rinconada 1) |
| Puentes (concesiones) | 4 (Paso Superior y tres sobre el Zanjón de la Aguada) |
| Enlaces | 2 (PI Américo Vespucio, PS Rinconada) |
| Pasarelas | 0 |

## Limitaciones

- El MOP registra solo las estructuras de caminos a su cargo (Vialidad y autopistas concesionadas). Los pasos sobre nivel de calles municipales no aparecen, así que la cobertura del sector urbano es parcial.
- Las capas no informan la altura libre ni la altura del tablero, que definirían si la estructura alcanza los 20 m de vuelo.

## Referencias

- Dorafshan, S. et al. (2017). Challenges in Bridge Inspection Using Small Unmanned Aerial Systems: Results and Lessons Learned. ICUAS, IEEE. https://doi.org/10.1109/ICUAS.2017.7991459
- Primatesta, S., Rizzo, A. y la Cour-Harbo, A. (2020). Ground risk map for Unmanned Aircraft in Urban Environments. *Journal of Intelligent & Robotic Systems*, 97, 489–509. https://doi.org/10.1007/s10846-019-01015-z
