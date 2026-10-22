# Amenazas

Cada subcarpeta automatiza la extracción y transformación de una fuente de amenaza a un JSON/GeoJSON, documenta su estructura en su respectivo `.md` y contiene un `cargar.py` que la inserta en la tabla `temp_amenazas`.

| # | Amenaza | Carpeta | Fuente | Geometría | Efecto en el grafo | Acceso |
|---|---|---|---|---|---|---|
| T2 | Incendios forestales y de pastizales | `incendios/` | GDACS + NASA FIRMS | Polígonos | Bloqueo | GDACS libre; FIRMS con key gratuita por correo |
| T3 | Lluvia, ráfagas, baja visibilidad, tormentas | `clima/` | Open-Meteo | Polígonos (celdas 3 × 3) | Penalización o bloqueo por hora | Libre |
| T5 | Emergencias municipales por lluvia | `lluvias_maipu/` | ArcGIS Municipalidad de Maipú | Puntos + radio | Postergación o bloqueo | Libre |
| T6 | Zona conflictiva activa | `sosafe/` | SOSAFE + clasificador IA | Puntos + geocercas | Bloqueo temporal | Credenciales de la app |
| T7 | Calles cerradas y obras de construcción | `waze/` | Waze Live Map API | Puntos + geocercas | Bloqueo | Sesión / datos locales |

`comun.py` reúne lo compartido por T2, T3 y T5: área de estudio, HTTP con reintentos, geometría simple sin dependencias extra, escritura de GeoJSON y la carga idempotente a la BD.

## Ejecución

Dentro del contenedor `etl` (`docker compose exec etl bash`):

```bash
python Amenazas/incendios/extractor.py      && python Amenazas/incendios/cargar.py
python Amenazas/clima/extractor.py          && python Amenazas/clima/cargar.py
python Amenazas/lluvias_maipu/extractor.py  && python Amenazas/lluvias_maipu/cargar.py
python Amenazas/sosafe/run.py               && python Amenazas/sosafe/cargar.py
python Amenazas/waze/run.py                 && python Amenazas/waze/cargar.py
```

`main.py` ejecuta todo esto automáticamente. Cada `cargar.py` acepta `--dry-run` para revisar las filas sin escribir en la BD.

## Convención común de `properties`

Para que el módulo de ruteo pueda leer todas las amenazas igual, cada feature de T2, T3 y T5 incluye:

| Campo | Valores |
|---|---|
| `id_amenaza` | Identificador único con prefijo de la fuente |
| `fuente` | Nombre de la fuente original |
| `tipo_amenaza` | `incendio_forestal`, `foco_de_calor`, `clima_severo`, `emergencia_lluvia`, `calle_cerrada`, `construccion_vial` |
| `nivel_riesgo` | `BAJO`, `MEDIO`, `ALTO`, `CRITICO` |
| `accion_grafo` | `BLOQUEAR_ARISTAS`, `PENALIZAR_ARISTAS`, `POSTERGAR_ARISTAS`, `NINGUNA` |
| `factor_penalizacion` | `INF` o multiplicador del costo |
| `fuente_etl` | Lo agrega `cargar.py`: `T2_incendios`, `T3_clima`, `T5_lluvias_maipu`, `T6_sosafe`, `T7_waze` |
