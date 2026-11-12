# M2 · Lugares públicos candidatos a droneport (MINVU)

El sistema lo operaría la municipalidad, así que las bases de los drones (droneports) deben quedar en recintos públicos y no en un punto cualquiera del mapa. Este módulo descarga los establecimientos públicos de Maipú, que el planificador usa como **sitios candidatos** para elegir las bases.

Es el mismo enfoque del paper base: Zhong et al. (2023) eligen los droneports entre un conjunto de sitios candidatos. Boutilier et al. (2017) usan como candidatos las 538 estaciones de bomberos, paramédicos y policía de Toronto para una red de drones con desfibriladores.

## Archivos

| Archivo | Rol |
|---|---|
| `extractor.py` | Extract + Transform: consulta MINVU y filtra por el límite comunal |
| `cargar.py` | Load: inserta el GeoJSON en `temp_metadata` (`--dry-run` para probar sin BD) |
| `data/lugares_publicos_maipu.geojson` | Salida del ETL |

## Fuente y acceso

Servicios de equipamiento del Ministerio de Vivienda y Urbanismo (MINVU), públicos y sin autenticación (`https://services3.arcgis.com/cTnMkBRk4HWkUCRo/arcgis/rest/services/`):

| Servicio y capa | Contenido | Criterio |
|---|---|---|
| `EQUIPAMIENTOS/FeatureServer/2` · Salud pública | Establecimientos del DEIS (CESFAM, CECOSF, SAPU, postas, hospitales) con nombre oficial y dependencia | Todos |
| `EQUIPAMIENTOS/FeatureServer/3` · Colegios | Directorio de MINEDUC al 30-04-2021, con nombre y RBD | Solo dependencia pública (ver abajo) |
| `Equipamientos_prod_vista_/FeatureServer/20` · Cuarteles de Carabineros | Comisarías y subcomisarías | Todos |
| `Equipamientos_prod_vista_/FeatureServer/22` · Compañías de Bomberos | Cuarteles de Bomberos | Todos |

El límite de la comuna se obtiene de la capa *División Político Administrativa Comunas* del mismo MINVU (`cod_com = 13119`) y se guarda en `Metadata/limite_maipu.geojson` como respaldo.

**Dependencia de los colegios** (`TIPO_DEPEN`, códigos de MINEDUC): 1 = municipal, 2 = particular subvencionado, 3 = particular pagado, 4 = corporación de administración delegada, 5 = Servicio Local de Educación. Se conservan 1, 4 y 5, que son recintos públicos.

## Estructura de `lugares_publicos_maipu.geojson`

### Bloque `metadata`

| Campo | Descripción |
|---|---|
| `generado_utc` | Momento de la extracción |
| `comuna`, `total_features` | Comuna y cantidad de lugares |
| `fuente`, `servicios` | Servicios consultados |
| `limite_comunal` | Capa usada para el filtro espacial |
| `por_capa` | Lugares de cada capa que quedaron dentro de Maipú |
| `criterio_educacion` | Dependencias conservadas |

### `properties`

| Campo | Descripción |
|---|---|
| `categoria` | `salud_publica`, `educacion_publica`, `carabineros` o `bomberos` |
| `dependencia` | `municipal` (colegios municipales y atención primaria municipal) o `estatal` (resto) |
| `nombre` | Nombre oficial del recinto |
| `direccion` | Dirección informada por la capa |
| `fuente` | `MINVU · Equipamientos` |
| `capa_origen` | Nombre de la capa MINVU |
| `id_origen` | `OBJECTID` en la capa de origen |
| `tipo_establecimiento` | Salud: Consultorio General Urbano, Centro Comunitario de Salud Familiar, Posta de Salud Rural, … |
| `dependencia_deis`, `nivel_atencion`, `codigo_deis` | Salud: dependencia (Municipal o Servicio de Salud), nivel de atención y código DEIS |
| `rbd`, `tipo_dependencia`, `matricula`, `anio_catastro` | Educación: Rol Base de Datos de MINEDUC, dependencia, matrícula total y año del catastro |
| `tipo_unidad`, `prefectura` | Carabineros: comisaría o subcomisaría y prefectura |
| `codigo_compania` | Bomberos: código de la compañía |

La geometría es un `Point` en EPSG:4326.

## Carga en la base de datos

Cada lugar va a `temp_metadata` con `tipo` = `categoria` y `fuente_etl = "M2_lugares_publicos"`.

## Resultado de referencia (12-11-2026)

| Categoría | Lugares en Maipú |
|---|---|
| Salud pública | 20 (10 municipales) |
| Educación pública | 27 (26 municipales) |
| Carabineros | 3 |
| Bomberos | 7 |
| **Total** | **57** |

## Limitaciones

- La capa de colegios corresponde al directorio de MINEDUC de abril de 2021.
- La capa no incluye edificios municipales (oficinas, centros comunitarios).
- Carabineros y Bomberos son recintos públicos pero no municipales: usarlos como base requeriría un convenio.

## Referencias

- Zhong, X. et al. (2023). A Route Planning Method for UAV Swarm Inspection of Roads Fusing Distributed Droneport Site Selection. *Sensors*, 23(20), 8479.
- Boutilier, J. J. et al. (2017). Optimizing a drone network to deliver automated external defibrillators. *Circulation*, 135(25), 2454–2465. https://doi.org/10.1161/CIRCULATIONAHA.116.026318
