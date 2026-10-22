# A2 · Lluvia, ráfagas, baja visibilidad y tormentas (Open-Meteo)

Amenaza **dinámica por celdas**: la lluvia moja el lente y el dron, las ráfagas superan su resistencia al viento, la baja visibilidad degrada la detección y las tormentas eléctricas obligan a suspender el vuelo. Maipú se divide en una grilla de 3 × 3 celdas; cada celda recibe el pronóstico horario de su centro, y las horas que superan un umbral se convierten en un polígono con una ventana de validez.

$$
w(e,t)=\begin{cases}
\infty & \text{si } e \cap C_k \neq \varnothing \text{ y nivel}(C_k,t)\in\{\text{ALTO},\text{CRITICO}\}\\
1{,}5\cdot d(e) & \text{si } e \cap C_k \neq \varnothing \text{ y nivel}(C_k,t)=\text{MEDIO}\\
d(e) & \text{en otro caso}
\end{cases}
$$

## Archivos

| Archivo | Rol |
|---|---|
| `extractor.py` | Extract + Transform: pronóstico multi-punto y evaluación de umbrales |
| `cargar.py` | Load: inserta el GeoJSON en `temp_amenazas` (`--dry-run` para probar sin BD) |
| `data/clima_maipu.geojson` | Salida del ETL |

## Fuente y acceso

`GET https://api.open-meteo.com/v1/forecast` sin autenticación, con los 9 centros de celda en una sola llamada:

```
?latitude=lat1,…,lat9&longitude=lon1,…,lon9
&hourly=precipitation,wind_speed_10m,wind_gusts_10m,visibility,weather_code
&wind_speed_unit=kmh&timezone=America/Santiago&forecast_days=2
```

## Tiempos de Respuesta y Naturaleza del Dato

* **Tiempo de respuesta de la API (HTTP):** Muy rápido, típicamente **0,3 a 0,8 segundos** para los 9 puntos simultáneos.
* **Frecuencia de actualización y frescura del dato:**
  * Open-Meteo no transmite telemetría de sensores meteorológicos en tiempo real segundo a segundo; ejecuta **modelos numéricos de predicción meteorológica** (NWP, como ECMWF IFS, GFS y DWD ICON).
  * Los modelos globales asimilan datos y se recalculan cada **1 a 6 horas**.
  * La resolución temporal de los datos entregados es **horaria** (un valor proyectado por celda para cada hora del día).
* **Implicancia operacional UAV:** El sistema utiliza estos datos como un **bloqueo blando dependiente del tiempo**. Si se pronostican ráfagas o lluvia a las 14:00 en la celda norte, el algoritmo penaliza el paso a esa hora pero permite programar el sobrevuelo en horas previas o posteriores sin cancelar la misión completa.

## Umbrales (supuestos de diseño)

| Variable | MEDIO → penalizar (×1,5) | ALTO / CRITICO → bloquear | Variable de entorno |
|---|---|---|---|
| Precipitación [mm/h] | ≥ 0,1 | ≥ 0,5 (ALTO) | `UMBRAL_LLUVIA_MEDIO_MM`, `UMBRAL_LLUVIA_ALTO_MM` |
| Ráfaga a 10 m [km/h] | ≥ 30 | ≥ 43, unos 12 m/s (CRITICO) | `UMBRAL_RAFAGA_MEDIO_KMH`, `UMBRAL_RAFAGA_CRITICO_KMH` |
| Visibilidad [m] | < 3000 | < 1000 (ALTO) | `UMBRAL_VISIBILIDAD_MEDIO_M`, `UMBRAL_VISIBILIDAD_ALTO_M` |
| Código WMO | — | 95, 96, 99: tormenta (CRITICO) | — |

El nivel de la hora es el máximo entre las variables. Otros parámetros: `CLIMA_HORAS` (horizonte, por defecto 24), `CLIMA_FILAS` y `CLIMA_COLUMNAS` (por defecto 3).

## Estructura de `clima_maipu.geojson`

Contiene **solo los pares celda-hora con amenaza**. Si no hay mal tiempo pronosticado, `features` queda vacío y el resumen por celda sigue presente en `metadata`.

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
| `nivel_riesgo` | string | `MEDIO`, `ALTO` o `CRITICO` |
| `accion_grafo` | string | `PENALIZAR_ARISTAS` (MEDIO) o `BLOQUEAR_ARISTAS` |
| `factor_penalizacion` | number o string | `1.5` o `INF` |
| `valido_desde`, `valido_hasta` | string ISO 8601 con zona horaria | Hora pronosticada (1 h) |
| `motivos` | array de string | Umbrales superados, por ejemplo `["ráfaga 50 km/h"]` |
| `precipitacion_mm_h` | float | Precipitación horaria |
| `viento_kmh` | float | Viento medio a 10 m |
| `rafaga_kmh` | float | Ráfaga máxima a 10 m |
| `visibilidad_m` | float | Visibilidad horizontal |
| `weather_code` | int | Código de tiempo WMO |

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
- Los umbrales de viento y lluvia dependen del dron que usen finalmente; ajústenlos a la ficha técnica.
