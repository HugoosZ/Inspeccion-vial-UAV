# M4 · Zonas de siniestros de tránsito (CONASET)

Los choques derriban señales y semáforos, y donde se desobedecen las señales una señal faltante es más grave. Este módulo identifica las zonas de Maipú con más siniestros para priorizar la inspección de su señalética.

- El manual de mantención de señales de la FHWA indica que los vehículos que chocan una señal la doblan o derriban, y que una señal regulatoria o de advertencia dañada debe reponerse de inmediato.
- Identificar los puntos con más siniestros es el método estándar para priorizar intervenciones de seguridad vial (Cheng y Washington, 2005).
- En Maipú, 1.005 siniestros entre 2020 y 2025 se atribuyen a desobedecer una señal (PARE, ceda el paso o luz roja) y 27 a una señal o semáforo en mal estado, mal instalado o no visible.

## Archivos

| Archivo | Rol |
|---|---|
| `extractor.py` | Extract + Transform: consulta CONASET, clasifica causas y agrupa en celdas |
| `cargar.py` | Load: inserta las zonas en `temp_metadata` (`--dry-run` para probar sin BD) |
| `data/siniestros_maipu.geojson` | Un punto por siniestro (respaldo de los conteos, no se carga) |
| `data/zonas_siniestros_maipu.geojson` | Celdas agregadas, que se cargan en la BD |

## Fuente y acceso

Base de siniestros 2020-2025 de la Comisión Nacional de Seguridad de Tránsito (CONASET), publicada en su portal de datos abiertos (https://mapas-conaset.opendata.arcgis.com/), cuenta `conaset_mtt`, sin autenticación:
`https://services3.arcgis.com/vaJl1B5HEzZj7154/arcgis/rest/services/Base_SINIESTROS_2020_2025/FeatureServer/0`

Se consulta con `COMUNA = 'Maipú'` y se pagina de a 2.000 registros (límite del servicio).

## Transformación

1. **Clasificación de la causa** (`CAUSA_SINI`). La base escribe la misma causa como `SENALIZACION` y `SEÑALIZACION`; se unifican antes de comparar.
   - `desobediencia_senal`: causas que empiezan con `SEÑALIZACION DESOBEDECER` (PARE, ceda el paso, luz roja, luz intermitente, otra), salvo la indicación de un carabinero.
   - `senal_defectuosa`: causas de señalización con `MAL ESTADO`, `DEFICIENTE`, `MAL INSTALADA` o `NO VISIBLE`.
2. **Agregación** en celdas de 100 × 100 m (`SINIESTROS_CELDA_M`), del orden de una intersección. Se conservan las celdas con 2 o más siniestros o con al menos uno ligado a la señalización.
3. **Nivel** por ranking de frecuencia: `CRITICA` = 5 % de las celdas con más siniestros, `ALTA` = siguiente 15 %, `MEDIA` = resto. Los cortes se calculan en cada ejecución y quedan en `metadata.cortes_n_siniestros`.

## Estructura de `zonas_siniestros_maipu.geojson`

### Bloque `metadata`

| Campo | Descripción |
|---|---|
| `generado_utc`, `comuna`, `total_features` | Momento, comuna y cantidad de celdas |
| `fuente`, `url`, `filtro` | Servicio y filtro aplicado |
| `celda_m` | Lado de la celda en metros |
| `criterio_celdas`, `criterio_nivel` | Reglas de los pasos 2 y 3 |
| `cortes_n_siniestros` | Siniestros mínimos para `CRITICA` y `ALTA` |
| `total_siniestros` | Siniestros con coordenadas en Maipú |

### `properties` de cada zona

| Campo | Descripción |
|---|---|
| `id_celda` | Índice de la celda (columna_fila) |
| `ubicacion_principal` | Intersección más repetida en la celda (`Calle_Uno con Calle_Dos`) |
| `n_siniestros` | Siniestros 2020-2025 en la celda |
| `n_desobediencia_senal` | De ellos, por desobedecer una señal |
| `n_senal_defectuosa` | De ellos, por señal defectuosa |
| `fallecidos`, `graves`, `menos_graves`, `leves` | Víctimas por gravedad |
| `anios` | Rango de años con siniestros |
| `nivel` | `CRITICA`, `ALTA` o `MEDIA` |

La geometría es un `Polygon` (la celda) en EPSG:4326.

### `properties` de cada siniestro (`siniestros_maipu.geojson`)

| Campo | Campo CONASET | Descripción |
|---|---|---|
| `id` | `ID` | Identificador del siniestro |
| `anio`, `fecha`, `hora` | `AÑO`, `FECHA`, `Hora` | Momento del siniestro |
| `tipo`, `subtipo` | `TIPO__CONA`, `TIPO_SINIE` | Colisión, choque, atropello, volcadura, … |
| `causa` | `CAUSA_SINI` | Causa detallada (normalizada) |
| `causa_agrupada` | `CAUSA_NUEV` | Causa agrupada por CONASET |
| `zona` | `ZONA` | Urbana o rural |
| `ubicacion` | `Calle_Uno`, `Calle_Dos` | Calles del siniestro |
| `fallecidos`, `graves`, `menos_graves`, `leves` | `FALLECIDOS`, `GRAVES`, `MENOS_GRAV`, `LEVES` | Víctimas |
| `desobediencia_senal`, `senal_defectuosa` | (calculados) | Clasificación del paso 1 |

## Carga en la base de datos

Cada zona va a `temp_metadata` con `tipo = zona_siniestros` y `fuente_etl = "M4_siniestros"`.

## Resultado de referencia (12-11-2026)

| Dato | Valor |
|---|---|
| Siniestros 2020-2025 en Maipú | 8.963 |
| Por desobedecer una señal | 1.005 |
| Por señal defectuosa | 27 |
| Celdas conservadas | 1.144 (59 críticas, 171 altas, 914 medias) |
| Corte `CRITICA` / `ALTA` | 23 / 9 siniestros |
| Celda con más siniestros | Los Pajaritos con Américo Vespucio (195) |

## Limitaciones

- Es información **histórica** (hasta el 31-12-2025): sirve para priorizar, no para reaccionar a un choque de hoy.
- El servicio no declara licencia ni condiciones de uso; se cita como datos abiertos de CONASET.

## Referencias

- CONASET. Portal de datos abiertos: georreferenciación de siniestros de tránsito. https://mapas-conaset.opendata.arcgis.com/
- Cheng, W. y Washington, S. P. (2005). Experimental evaluation of hotspot identification methods. *Accident Analysis & Prevention*, 37(5), 870–881.
- Federal Highway Administration. *Maintenance of Signs and Sign Supports*, cap. X, Repair and Replacement. https://highways.dot.gov/safety/local-rural/maintenance-signs-and-sign-supports/x-repair-and-replacement
