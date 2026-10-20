# T5 · Emergencias municipales por lluvia (Municipalidad de Maipú)

Amenaza **dinámica por puntos**: los reportes municipales de anegamientos, desbordes de canal y colapsos de alcantarillado marcan calles con cuadrillas, maquinaria y cortes de tránsito. El dron no debe sobrevolar la operación de emergencia, y la señalética de ese tramo no se puede evaluar bien mientras dure. El efecto es **gatillo + desvío**: la arista se bloquea o se posterga (se inspecciona después).

$$
w(e,t)=\begin{cases}
\infty & \text{si } \operatorname{dist}(e,r)\le R \text{ y } r \text{ activo con nivel ALTO o CRITICO}\\
2\cdot d(e) & \text{si } \operatorname{dist}(e,r)\le R \text{ y } r \text{ activo con nivel MEDIO}\\
d(e) & \text{en otro caso}
\end{cases}
\qquad R = 100\ \text{m}
$$

## Archivos

| Archivo | Rol |
|---|---|
| `extractor.py` | Extract + Transform: descarga paginada y clasificación de los reportes |
| `cargar.py` | Load: inserta el GeoJSON en `temp_amenazas` (`--dry-run` para probar sin BD) |
| `data/lluvias_maipu.geojson` | Salida del ETL |

## Fuente y acceso

Capa pública ArcGIS `Reporte_Lluvias` de la Municipalidad de Maipú, sin autenticación:

```
GET https://services6.arcgis.com/dPivrbIaflr2oHb4/arcgis/rest/services/service_76553a6268c0473cad084325616e002c/FeatureServer/0/query
    ?where=1=1&outFields=objectid,BARRIO,CALLE,ESTADOS,PUNTO_CRITICO,CreationDate,EditDate,REF_INSP
    &returnGeometry=true&outSR=4326&orderByFields=objectid
    &resultOffset=0&resultRecordCount=1000&f=geojson
```

El servicio entrega como máximo 1000 registros por consulta; el extractor pagina con `resultOffset` mientras la respuesta traiga `exceededTransferLimit`.

**Privacidad:** la capa también tiene contacto de afectados, número de domicilio y nombres de funcionarios. El extractor **no solicita** esos campos y redondea las coordenadas a 4 decimales (unos 11 m).

## Clasificación

| `ESTADOS` | Nivel base |
|---|---|
| DESBORDE DE CANAL | ALTO |
| RIESGO DE DESBORDE, COLAPSO DE ALCANTARILLADO, CASAS AFECTADAS, TRABAJOS EN DESARROLLO, ZONA AFECTACION FERIA | MEDIO |
| SITUACION CONTROLADA | BAJO |

- Si `PUNTO_CRITICO = SI`, el nivel sube un escalón (MEDIO → ALTO → CRITICO).
- Un reporte es **activo** si no está en SITUACION CONTROLADA y se editó en los últimos `LLUVIAS_VENTANA_DIAS` días (7 por defecto).
- Los reportes inactivos se guardan como historial con nivel BAJO y acción `NINGUNA`. Sirven para identificar puntos de anegamiento recurrentes.

Variables: `LLUVIAS_VENTANA_DIAS` (7), `LLUVIAS_RADIO_M` (100).

## Estructura de `lluvias_maipu.geojson`

### Bloque `metadata`

| Campo | Tipo | Descripción |
|---|---|---|
| `generado_utc` | string ISO 8601 | Momento de la extracción |
| `comuna` | string | `Maipú` |
| `total_features` | int | Reportes descargados |
| `amenaza`, `fuente`, `url_servicio` | string | Identificación de la fuente |
| `ventana_activa_dias` | int | Ventana para considerar un reporte activo |
| `radio_afectacion_m` | int | Radio $R$ alrededor del punto |
| `total_activas` | int | Reportes activos |
| `campos_excluidos_por_privacidad` | array | Campos de la capa que no se descargan |

### `geometry`

Point en EPSG:4326 (`[longitud, latitud]`), redondeado a 4 decimales.

### `properties`

| Campo | Tipo | Descripción |
|---|---|---|
| `id_amenaza` | string | `LLUVIA-{objectid}` |
| `fuente` | string | Municipalidad de Maipú - Reporte_Lluvias |
| `tipo_amenaza` | string | `emergencia_lluvia` |
| `objectid` | int | ID del registro en ArcGIS |
| `barrio` | string | Barrio municipal (21 valores, por ejemplo `LA FARFANA`) |
| `calle` | string | Calle del reporte (sin número) |
| `estado` | string | Valor de `ESTADOS` en mayúsculas |
| `punto_critico` | bool | `true` si la municipalidad lo marcó como punto crítico |
| `nivel_riesgo` | string | Nivel efectivo: `BAJO` si está inactivo |
| `nivel_si_se_activa` | string | Nivel que tendría si se reactiva |
| `activa` | bool | Ver criterio de clasificación |
| `accion_grafo` | string | `BLOQUEAR_ARISTAS`, `POSTERGAR_ARISTAS` o `NINGUNA` |
| `factor_penalizacion` | number o string | `INF`, `2.0` o `1.0` |
| `radio_afectacion_m` | int | Radio $R$ |
| `fecha_creacion` | string ISO 8601 (UTC) | Creación del reporte |
| `fecha_edicion` | string ISO 8601 (UTC) | Última edición |
| `fecha_ultima_inspeccion` | string ISO 8601 o null | Última inspección municipal |

## Carga en la base de datos

| Columna de `temp_amenazas` | Valor |
|---|---|
| `nombre` | `Emergencia lluvia {barrio} - {calle}` |
| `tipo` | `emergencia_lluvia` |
| `nivel_riesgo` | `properties.nivel_riesgo` |
| `comuna` | `Maipú` |
| `descripcion` | Estado, vigencia, punto crítico y fecha |
| `propiedades` | Todas las `properties` + `fuente_etl = "T5_lluvias_maipu"` |
| `geom` | Punto |

Carga idempotente: borra antes las filas con `fuente_etl = "T5_lluvias_maipu"`. Consulta de aristas afectadas:

```sql
SELECT DISTINCT e.id
FROM network_edges e
JOIN temp_amenazas a
  ON a.propiedades->>'fuente_etl' = 'T5_lluvias_maipu'
 AND (a.propiedades->>'activa')::boolean
 AND ST_DWithin(e.geom::geography, a.geom::geography,
                (a.propiedades->>'radio_afectacion_m')::float);
```

## Limitaciones

- La capa se actualiza en eventos de lluvia; fuera de temporada casi todos los reportes estarán controlados (inactivos).
- El radio de 100 m y la ventana de 7 días son supuestos de diseño.
