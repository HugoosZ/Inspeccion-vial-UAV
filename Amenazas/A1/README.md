# A1 · Incendios forestales y de pastizales (CONAF)

Amenaza **dinámica**: un incendio activo genera una columna de humo con corrientes ascendentes y operación aérea de combate (aviones y helicópteros), con la que el dron no debe interferir. Shawon et al. (2025) midieron corrientes ascendentes de 6 a 10 m/s dentro de la pluma de un incendio de pastizal, con cambios significativos de actitud, altitud y velocidad del UAS. Toda arista que cruce el área de exclusión de un incendio activo queda bloqueada:

$$b(e,t)=0 \quad \text{si } e \cap P_{\text{fuego}}(t)\neq\varnothing$$

## Archivos

| Archivo | Rol |
|---|---|
| `extractor.py` | Extract + Transform: consulta los servicios de CONAF y genera el GeoJSON |
| `cargar.py` | Load: inserta el GeoJSON en `temp_amenazas` (`--dry-run` para probar sin BD) |
| `data/incendios_maipu.geojson` | Salida del ETL |

## Fuente y acceso

CONAF publica en ArcGIS los mismos datos que muestra en su página *Situación actual y pronóstico de incendios* (cuenta `deigeprif` / `sapif_geprif`). No requieren autenticación.

| Servicio | URL de la capa | Contenido | Actualización |
|---|---|---|---|
| `incendios_base` | `https://services5.arcgis.com/A1ELWse9bRAi2JiV/arcgis/rest/services/incendios_base/FeatureServer/0` | Incendios de la temporada en curso | Diaria |
| `incendios_totales` | `…/incendios_totales/FeatureServer/0` | Incendios de la temporada anterior (julio 2025 a mayo 2026) | Al cierre de temporada |
| `PI` | `…/PI/FeatureServer/0` | Probabilidad de ignición del día, en clases de 10 % y celdas de 2 km | Diaria |

Se consulta un rectángulo de búsqueda y se conservan únicamente los incendios cuyo punto de origen está dentro del límite comunal oficial de Maipú (MINVU, copia en `Metadata/limite_maipu.geojson`). El mismo filtro se aplica al cargar archivos ya guardados en la base de datos. No se incluyen incendios de comunas vecinas ni se utiliza un margen de búsqueda adicional. Si no está disponible el límite oficial, se aborta la operación en lugar de aceptar el rectángulo completo.

Las celdas de probabilidad de ignición se seleccionan por su centro dentro de Maipú. El radio de exclusión de un incendio originado en Maipú puede extenderse fuera de la comuna: representa su área de precaución, no otro incendio.

Reemplaza a GDACS + NASA FIRMS de la entrega anterior: GDACS solo registra incendios de gran magnitud (la consulta devolvía 0 en Maipú) y FIRMS detecta puntos de calor por satélite, con falsas alarmas y una latencia de 3 a 6 horas. CONAF registra cada incendio confirmado en terreno.

## Transformación

| Estado CONAF | `tipo_amenaza` | Geometría | `nivel_riesgo` | `accion_grafo` |
|---|---|---|---|---|
| En combate | `incendio_activo` | Círculo de exclusión | CRITICO | `BLOQUEAR_ARISTAS` |
| Foco, Bajo observación | `incendio_activo` | Círculo de exclusión | ALTO | `BLOQUEAR_ARISTAS` |
| Controlado | `incendio_activo` | Círculo de exclusión | MEDIO | `PENALIZAR_ARISTAS` |
| Extinguido | `incendio_historico` | Punto | BAJO | `NINGUNA` |
| (pronóstico) | `probabilidad_ignicion` | Celda de 1 km | según la clase | `NINGUNA` |

Radio del círculo de exclusión:

$$r = \sqrt{\frac{A_{\text{ha}} \cdot 10\,000}{\pi}} + r_{\text{margen}}$$

donde $A_{\text{ha}}$ es la superficie afectada informada por CONAF y $r_{\text{margen}}$ = `INCENDIOS_RADIO_M` (2000 m por defecto). El margen es un **supuesto de diseño configurable**, no una norma.

Los incendios extinguidos y la probabilidad de ignición no modifican el grafo: quedan en la base de datos para calcular la probabilidad de falla de cada arista (punto 5 del enunciado) y para evidenciar que la extracción funciona aunque hoy no haya incendios activos en Maipú.

**Horas:** CONAF guarda la hora local de Chile como si fuera UTC (en la temporada anterior, `f_inicio` = `"2026-04-28 10:57"` y `f_inicio_date_time` = 10:57 Z). El extractor la reinterpreta como hora de Chile (`America/Santiago`).

## Variables de entorno

| Variable | Defecto | Descripción |
|---|---|---|
| `INCENDIOS_RADIO_M` | `2000` | Margen de exclusión alrededor del área quemada (m) |

## Estructura de `incendios_maipu.geojson`

### Bloque `metadata`

| Campo | Descripción |
|---|---|
| `generado_utc` | Momento de la extracción |
| `comuna` | `Maipú` |
| `total_features` | Cantidad de elementos |
| `amenaza` | Identificador de la amenaza |
| `fuente` | Servicios consultados |
| `consultas_exitosas` | `incendios` y/o `probabilidad_ignicion` |
| `por_tipo` | Conteo por `tipo_amenaza` |
| `radio_exclusion_m` | Margen usado en los círculos |
| `ambito_geografico` | Selección por origen dentro del límite comunal oficial |
| `aoi_maipu_bbox` | Rectángulo de Maipú [oeste, sur, este, norte] |

### `properties` de los incendios (`incendio_activo` e `incendio_historico`)

| Campo | Descripción |
|---|---|
| `id_amenaza` | `CONAF-{id}` |
| `fuente` | `CONAF` |
| `id_conaf` | Identificador del incendio en CONAF |
| `nombre_incendio` | Nombre asignado por CONAF (habitualmente el sector) |
| `estado` | En combate, Foco, Bajo observación, Controlado o Extinguido |
| `temporada` | `en curso` o `anterior` |
| `comuna_incendio` | Comuna donde se inició |
| `superficie_ha` | Superficie afectada en hectáreas |
| `combustible_inicial` | Vegetación donde se inició (pastizal, matorral, …) |
| `inicio`, `control`, `extincion` | Fechas del ciclo del incendio (hora de Chile) |
| `lon`, `lat` | Punto de inicio |
| `tipo_amenaza`, `nivel_riesgo`, `accion_grafo` | Según la tabla de transformación |
| `radio_exclusion_m` | Solo activos: radio del círculo |
| `valido_desde`, `valido_hasta` | Solo activos: inicio del incendio y `null` (sigue vigente) |

### `properties` de la probabilidad de ignición

| Campo | Descripción |
|---|---|
| `id_amenaza` | `CONAF-PI-F{fila}C{columna}-{fecha}` |
| `id_celda` | Celda de la grilla de 1 km |
| `clase_pi` | Clase de CONAF, por ejemplo `51-60 %` |
| `probabilidad_ignicion` | Punto medio de la clase (0,55 para `51-60 %`) |
| `nivel_riesgo` | BAJO (< 40 %), MEDIO (40–69 %) o ALTO (≥ 70 %) |
| `valido_desde`, `valido_hasta` | El día del pronóstico (00:00 a 24:00, hora de Chile) |

## Carga en la base de datos

`cargar.py` inserta cada feature en `temp_amenazas` con `tipo` = `tipo_amenaza`, `nivel_riesgo`, un nombre y una descripción legibles, y todas las `properties` en `propiedades` más `fuente_etl = "A1_incendios"`. La carga es idempotente: antes de insertar borra las filas anteriores de esa fuente. El ruteo ignora las filas con `accion_grafo = NINGUNA`.

## Resultado de referencia (12-11-2026)

| Tipo | Cantidad |
|---|---|
| Incendios activos | 0 |
| Incendios extinguidos (ambas temporadas, Maipú y alrededores) | 71 (22 iniciados en Maipú) |
| Celdas de probabilidad de ignición | 87 (clases 41-50 % y 51-60 %) |

## Limitaciones

- CONAF registra incendios **forestales y de pastizales**. Los incendios estructurales urbanos (viviendas, bodegas) los atiende Bomberos y no aparecen en esta fuente.
- El servicio no declara licencia ni condiciones de uso; se cita como datos de CONAF publicados en su portal.
- El margen de 2 km es un supuesto de diseño configurable.

## Referencias

- CONAF. Situación actual y pronóstico de incendios. https://www.conaf.cl/incendios/situacion-actual-y-pronostico-de-incendios/
- Shawon, M. H. et al. (2025). Vertical Wind Velocity Estimation during UAS Fire Plume Encounters. AIAA SciTech Forum. https://doi.org/10.2514/6.2025-1624
