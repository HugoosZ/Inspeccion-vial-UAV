# A2 · Lluvia, ráfagas, baja visibilidad y tormentas (Open-Meteo)

Amenaza **dinámica por celdas**: la lluvia moja el lente y el dron, las ráfagas superan la política de vuelo del **DJI Mavic 3E**, la baja visibilidad degrada la detección y las tormentas eléctricas obligan a suspender el vuelo. Maipú se divide en una grilla de 3 × 3 celdas, y cada celda recibe el pronóstico horario de su centro de dos formas:

1. **Determinista** (un solo escenario): define el nivel de la hora para el mapa.
2. **Por conjuntos (ensamble)**: 51 escenarios de ECMWF IFS y 31 de NOAA GEFS. Con ellos `Probabilidades/generar.py` calcula la probabilidad de clima no apto como la fracción de escenarios que incumplen la política (punto 5).

El clima decide **cuándo** volar: el planificador evalúa la probabilidad en la hora de cada salida y sugiere reprogramar las que caen en horas no aptas. No encarece las calles, porque a esta escala es casi uniforme en un área (ver `Probabilidades/README.md`).

## Archivos

| Archivo | Rol |
|---|---|
| `extractor.py` | Extract + Transform: pronóstico multi-punto y evaluación de umbrales |
| `cargar.py` | Load: inserta el GeoJSON en `temp_amenazas` (`--dry-run` para probar sin BD) |
| `data/clima_maipu.geojson` | Salida del ETL |

## Fuente y acceso

Ambas sin autenticación, con los 9 centros de celda en una sola llamada cada una.

`GET https://api.open-meteo.com/v1/forecast` (determinista):

```
?latitude=lat1,…,lat9&longitude=lon1,…,lon9
&hourly=precipitation,wind_speed_10m,wind_gusts_10m,visibility,weather_code
&wind_speed_unit=kmh&timezone=America/Santiago&forecast_days=2
```

`GET https://ensemble-api.open-meteo.com/v1/ensemble` (por conjuntos):

```
?latitude=…&longitude=…&hourly=wind_gusts_10m,precipitation,visibility
&models=ecmwf_ifs025,gfs_seamless&wind_speed_unit=kmh&timezone=America/Santiago&forecast_days=2
```

La respuesta trae una serie por variable, escenario y modelo: `wind_gusts_10m_ecmwf_ifs025_ensemble` (control) y `wind_gusts_10m_member01_ecmwf_ifs025_ensemble` … `member50`. De ECMWF se usan la ráfaga y la precipitación; de GEFS, solo la visibilidad, que ECMWF no entrega. Las ráfagas de GEFS no se usan porque resultaron muy inferiores a las de ECMWF y del pronóstico determinista. ICON-EPS no entrega ráfagas ni visibilidad para Maipú. Si la Ensemble API no responde, el extractor sigue solo con el pronóstico determinista y lo advierte.

## Tiempos de Respuesta y Naturaleza del Dato

* **Tiempo de respuesta de la API (HTTP):** Muy rápido, típicamente **0,3 a 0,8 segundos** para los 9 puntos simultáneos.
* **Frecuencia de actualización y frescura del dato:**
  * Open-Meteo no transmite telemetría de sensores meteorológicos en tiempo real segundo a segundo; ejecuta **modelos numéricos de predicción meteorológica** (NWP, como ECMWF IFS, GFS y DWD ICON).
  * Los modelos globales asimilan datos y se recalculan cada **1 a 6 horas**.
  * La resolución temporal de los datos entregados es **horaria** (un valor proyectado por celda para cada hora del día).
* **Implicancia operacional UAV:** El clima se usa por hora. Si los escenarios pronostican ráfagas a las 14:00, el planificador marca las salidas de esa hora para reprogramarlas, y la inspección puede hacerse en horas previas o posteriores sin cancelar la misión completa.

## Umbrales (política del DJI Mavic 3E)

| Variable | MEDIO: sobre la política | ALTO / CRITICO | Variable de entorno | Origen |
|---|---|---|---|---|
| Precipitación [mm/h] | ≥ 0,1 | ≥ 0,5 (ALTO) | `UMBRAL_LLUVIA_MEDIO_MM`, `UMBRAL_LLUVIA_ALTO_MM` | Sin resistencia al agua declarada: cualquier lluvia pronosticada (0,1 es la resolución) |
| Ráfaga a 10 m [km/h] | ≥ 28,8 (8 m/s) | ≥ 43,2, es decir 12 m/s (CRITICO) | `UMBRAL_RAFAGA_MEDIO_KMH`, `UMBRAL_RAFAGA_CRITICO_KMH` | 8 m/s es la ráfaga preventiva del proyecto; 12 m/s, el máximo de DJI |
| Visibilidad [m] | < 470 | < 100 (ALTO) | `UMBRAL_VISIBILIDAD_MEDIO_M`, `UMBRAL_VISIBILIDAD_ALTO_M` | 470 m conserva el 80 % del contraste de la señal a 35 m (Koschmieder, ver `Probabilidades/README.md`); 100 m es el mínimo de DJI |
| Código WMO | — | 95, 96, 99: tormenta (CRITICO) | — | Suspensión del vuelo |

Un escenario del ensamble es **no apto** si supera cualquiera de los umbrales MEDIO. El nivel de la hora es el máximo entre las variables del pronóstico determinista. Otros parámetros: `CLIMA_HORAS` (horizonte, por defecto 24), `CLIMA_FILAS` y `CLIMA_COLUMNAS` (por defecto 3).

## Estructura de `clima_maipu.geojson`

Contiene los pares celda-hora en que el pronóstico determinista supera un umbral **o** algún escenario del ensamble es no apto. Si no hay ninguno, `features` queda vacío y el resumen por celda sigue presente en `metadata`.

### Bloque `metadata`

| Campo | Tipo | Descripción |
|---|---|---|
| `generado_utc` | string ISO 8601 | Momento de la extracción |
| `comuna` | string | `Maipú` |
| `total_features` | int | Pares celda-hora con amenaza |
| `amenaza`, `fuente` | string | Identificación |
| `horizonte_horas` | int | Horas evaluadas desde ahora |
| `grilla` | string | Filas × columnas |
| `umbrales` | object | Umbrales usados (ver tabla anterior) |
| `resumen_por_celda` | array | Por celda: `id_celda`, `centro_lat`, `centro_lon`, `precipitacion_mm_h` (máx.), `rafaga_kmh` (máx.), `visibilidad_min_m`, `horas_con_amenaza` |

### `geometry`

Polygon rectangular de la celda en EPSG:4326 (unos 7 × 6 km cada una con la grilla 3 × 3).

### `properties`

| Campo | Tipo | Descripción |
|---|---|---|
| `id_amenaza` | string | `OM-{id_celda}-{AAAAMMDDTHHMM}` |
| `fuente` | string | `Open-Meteo` |
| `tipo_amenaza` | string | `clima_severo` |
| `id_celda` | string | `F{fila}C{columna}`; F0 es el sur y C0 el oeste |
| `centro_lat`, `centro_lon` | float | Punto consultado a Open-Meteo |
| `nivel_riesgo` | string | `BAJO` (solo escenarios del ensamble no aptos), `MEDIO`, `ALTO` o `CRITICO` |
| `accion_grafo` | string | `NINGUNA` (BAJO), `PENALIZAR_ARISTAS` (MEDIO) o `BLOQUEAR_ARISTAS` |
| `factor_penalizacion` | number o string | `1.0`, `1.5` o `INF` |
| `valido_desde`, `valido_hasta` | string ISO 8601 con zona horaria | Hora pronosticada (1 h) |
| `motivos` | array de string | Umbrales superados, por ejemplo `["ráfaga 50 km/h"]` |
| `precipitacion_mm_h` | float | Precipitación horaria |
| `viento_kmh` | float | Viento medio a 10 m |
| `rafaga_kmh` | float | Ráfaga máxima a 10 m |
| `visibilidad_m` | float | Visibilidad horizontal |
| `weather_code` | int | Código de tiempo WMO |
| `p_no_apto_ensamble` | float o null | Fracción combinada de escenarios no aptos, $1-(1-f_{\text{ECMWF}})(1-f_{\text{GEFS}})$; la recalcula `Probabilidades/generar.py` |
| `ensambles` | object o null | Escenarios por modelo: `{"ecmwf_ifs025_ensemble": [{"rafaga_kmh", "precipitacion_mm_h"}, …], "ncep_gefs_seamless": [{"visibilidad_m"}, …]}` |

## Carga en la base de datos

| Columna de `temp_amenazas` | Valor |
|---|---|
| `nombre` | `Clima severo celda {id_celda} {hora}` |
| `tipo` | `clima_severo` |
| `nivel_riesgo` | `properties.nivel_riesgo` |
| `comuna` | `Maipú` |
| `descripcion` | Motivos y ventana de validez |
| `propiedades` | Todas las `properties` + `fuente_etl = "A2_clima"` |
| `geom` | Polígono de la celda |

Carga idempotente: borra antes las filas con `fuente_etl = "A2_clima"`. La consulta de amenazas vigentes en el instante `t` es:

```sql
SELECT * FROM temp_amenazas
WHERE propiedades->>'fuente_etl' = 'A2_clima'
  AND (propiedades->>'valido_desde')::timestamptz <= :t
  AND (propiedades->>'valido_hasta')::timestamptz >  :t;
```

## Limitaciones

- La resolución del modelo de Open-Meteo puede ser comparable o más gruesa que una celda, así que varias celdas pueden recibir valores iguales.
- Los escenarios de ECMWF y GEFS tienen unos 25 km de resolución, así que varias celdas reciben los mismos valores.
- La fracción de escenarios no aptos no está calibrada contra observaciones de Maipú, y la ráfaga a 10 m no representa exactamente el viento a 20 m entre edificios.
