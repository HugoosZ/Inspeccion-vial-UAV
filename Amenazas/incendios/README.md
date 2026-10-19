# T2 · Incendios forestales y de pastizales (GDACS + NASA FIRMS)

Amenaza **dinámica con polígonos**: un incendio activo genera humo (que reduce la visibilidad de la señalética) y operación aérea de combate (aviones y helicópteros), con la que el dron no debe interferir. Toda arista que cruce el polígono queda bloqueada:

$$b(e,t)=0 \quad \text{si } e \cap P_{\text{fuego}}(t)\neq\varnothing$$

## Archivos

| Archivo | Rol |
|---|---|
| `extractor.py` | Extract + Transform: consulta GDACS y FIRMS y genera el GeoJSON |
| `cargar.py` | Load: inserta el GeoJSON en `temp_amenazas` (`--dry-run` para probar sin BD) |
| `data/incendios_maipu.geojson` | Salida del ETL |

## Fuentes y acceso

| Fuente | Endpoint | Autenticación |
|---|---|---|
| GDACS, lista de eventos | `https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH?eventlist=WF&country=Chile&fromDate=…&toDate=…` | Ninguna |
| GDACS, polígono del episodio | `https://www.gdacs.org/gdacsapi/api/polygons/getgeometry?eventtype=WF&eventid=…&episodeid=…` | Ninguna |
| NASA FIRMS, focos por área | `https://firms.modaps.eosdis.nasa.gov/api/area/csv/{MAP_KEY}/VIIRS_SNPP_NRT/-70.92,-33.62,-70.70,-33.46/{1-5}` | `FIRMS_MAP_KEY` gratuita, se pide con un correo, sin tarjeta |

Si `FIRMS_MAP_KEY` no está definida, el extractor usa solo GDACS y lo avisa en consola.

## Variables de entorno

| Variable | Defecto | Descripción |
|---|---|---|
| `FIRMS_MAP_KEY` | vacío | Clave FIRMS |
| `FIRMS_SOURCE` | `VIIRS_SNPP_NRT` | Satélite/sensor FIRMS |
| `FIRMS_DIAS` | `5` | Días hacia atrás (1 a 5) |
| `GDACS_DESDE` | hoy − 365 días | Fecha inicial de búsqueda GDACS |
| `GDACS_ALCANCE` | `region` | `region`: solo polígonos que tocan la RM. `pais`: todo Chile (útil para demostrar en el mapa) |
| `INCENDIOS_RADIO_M` | `2000` | Radio de exclusión alrededor de cada foco FIRMS (supuesto de diseño) |

## Estructura de `incendios_maipu.geojson`

```json
{
  "type": "FeatureCollection",
  "name": "amenaza_t2_incendios_maipu",
  "metadata": { ... },
  "features": [ { "type": "Feature", "geometry": { "type": "Polygon", ... }, "properties": { ... } } ]
}
```

### Bloque `metadata`

| Campo | Tipo | Descripción |
|---|---|---|
| `generado_utc` | string ISO 8601 | Momento de la extracción |
| `comuna` | string | Comuna de estudio (`Maipú`) |
| `total_features` | int | Cantidad de polígonos |
| `amenaza` | string | Identificador de la amenaza |
| `fuentes_consultadas` | array | Fuentes que respondieron (`GDACS`, `FIRMS`) |
| `gdacs_desde` | string fecha | Inicio de la búsqueda GDACS |
| `gdacs_alcance` | string | `region` o `pais` |
| `radio_exclusion_m` | int | Radio usado para FIRMS |
| `aoi_maipu_bbox` | array [oeste, sur, este, norte] | Rectángulo de Maipú usado en FIRMS |

### `geometry`

Polygon o MultiPolygon en EPSG:4326 (`[longitud, latitud]`).
- **GDACS:** polígono oficial del área quemada o afectada.
- **FIRMS:** cuadrado centrado en el foco, de lado igual al píxel del satélite (`scan` × `track`, unos 375 m en VIIRS), expandido `radio_exclusion_m` por lado.

### `properties` (comunes)

| Campo | Tipo | Descripción |
|---|---|---|
| `id_amenaza` | string | `GDACS-{eventid}-{episodeid}` o `FIRMS-{fecha}-{hora}-{lat}-{lon}` |
| `fuente` | string | `GDACS` o `NASA FIRMS` |
| `tipo_amenaza` | string | `incendio_forestal` (GDACS) o `foco_de_calor` (FIRMS) |
| `nivel_riesgo` | string | `MEDIO`, `ALTO` o `CRITICO` |
| `accion_grafo` | string | Siempre `BLOQUEAR_ARISTAS` |
| `factor_penalizacion` | string | `INF`: peso infinito, la arista no se usa |
| `valido_desde` | string ISO 8601 | Inicio del evento o hora de detección |
| `valido_hasta` | string o null | Fin del evento (GDACS); `null` en FIRMS |
| `radio_exclusion_m` | int | 0 en GDACS (ya es el área), `INCENDIOS_RADIO_M` en FIRMS |

### `properties` exclusivas de GDACS

| Campo | Descripción |
|---|---|
| `es_actual` | `true` si GDACS marca el evento como vigente |
| `en_region_metropolitana` | `true` si el polígono toca el rectángulo de la RM |
| `alerta_gdacs` | `Green`, `Orange` o `Red` |
| `area_afectada_ha` | Hectáreas afectadas según GDACS |
| `clase_poligono` | Clase de polígono informada por GDACS |
| `glide` | Código internacional del desastre |
| `url_reporte` | Reporte oficial GDACS |

Nivel de riesgo: `Red` → CRITICO, `Orange` → ALTO, `Green` → MEDIO.

### `properties` exclusivas de FIRMS

| Campo | Descripción |
|---|---|
| `latitud_foco`, `longitud_foco` | Centro del píxel detectado |
| `lado_pixel_m` | Lado del píxel en metros |
| `confianza` | `l` (baja), `n` (nominal), `h` (alta) |
| `frp_mw` | Potencia radiativa del fuego en MW |
| `satelite`, `instrumento` | Origen de la detección |
| `dia_noche` | `D` o `N` |

Nivel de riesgo: confianza `h` o FRP ≥ 20 MW → CRITICO; confianza `n` → ALTO; resto → MEDIO.

## Carga en la base de datos

`cargar.py` inserta cada feature en `temp_amenazas`:

| Columna | Valor |
|---|---|
| `nombre` | `Incendio forestal GDACS {glide}` o `Foco de calor FIRMS {fecha}` |
| `tipo` | `properties.tipo_amenaza` |
| `nivel_riesgo` | `properties.nivel_riesgo` |
| `comuna` | `Maipú` |
| `descripcion` | Resumen legible del evento |
| `propiedades` | Todas las `properties` + `fuente_etl = "T2_incendios"` |
| `geom` | `ST_SetSRID(ST_GeomFromGeoJSON(geometry), 4326)` |

La carga es idempotente: antes de insertar borra las filas con `fuente_etl = "T2_incendios"`.

## Limitaciones

- En invierno y primavera es esperable que FIRMS devuelva 0 focos en Maipú. Es una amenaza estacional (verano).
- GDACS solo registra incendios grandes (2 en Chile desde 2024). Con `GDACS_ALCANCE=pais` se puede mostrar el polígono real de enero de 2026 para demostrar el flujo.
- El radio de 2 km es un supuesto de diseño; la restricción normativa precisa debe verificarse en la DAN 151 de la DGAC.
