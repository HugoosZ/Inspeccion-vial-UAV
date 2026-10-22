# Amenazas

Cada subcarpeta automatiza la extracción y transformación de una fuente de amenaza a un JSON/GeoJSON, documenta su estructura en su respectivo `.md` y contiene un `cargar.py` que la inserta en la tabla `temp_amenazas`.

| # | Amenaza | Carpeta | Fuente | Geometría | Efecto en el grafo | Acceso |
|---|---|---|---|---|---|---|
| A1 | Incendios forestales y de pastizales | `A1/` | GDACS + NASA FIRMS | Polígonos | Bloqueo | GDACS libre; FIRMS con key gratuita por correo |
| A2 | Lluvia, ráfagas, baja visibilidad, tormentas | `A2/` | Open-Meteo | Polígonos (celdas 3 × 3) | Penalización o bloqueo por hora | Libre |
| A3 | Zona conflictiva activa | `A3/` | SOSAFE + clasificador IA | Puntos + geocercas | Bloqueo temporal | Credenciales de la app |
| A4 | Calles cerradas y obras de construcción | `A4/` | Waze Live Map API | Puntos + geocercas | Bloqueo | Sesión / datos locales |

`comun.py` reúne lo compartido por A1 y A2 (A3 y A4 lo usan para la carga): área de estudio, HTTP con reintentos, geometría simple sin dependencias extra, escritura de GeoJSON y la carga idempotente a la BD.

## Ejecución

Dentro del contenedor `etl` (`docker compose exec etl bash`):

```bash
python Amenazas/A1/extractor.py  && python Amenazas/A1/cargar.py
python Amenazas/A2/extractor.py  && python Amenazas/A2/cargar.py
python Amenazas/A3/run.py        && python Amenazas/A3/cargar.py
python Amenazas/A4/run.py        && python Amenazas/A4/cargar.py
```

`main.py` ejecuta todo esto automáticamente. Cada `cargar.py` acepta `--dry-run` para revisar las filas sin escribir en la BD.

## Convención común de `properties`

Para que el módulo de ruteo pueda leer todas las amenazas igual, cada feature de A1, A2 y A4 incluye:

| Campo | Valores |
|---|---|
| `id_amenaza` | Identificador único con prefijo de la fuente |
| `fuente` | Nombre de la fuente original |
| `tipo_amenaza` | `incendio_forestal`, `foco_de_calor`, `clima_severo`, `calle_cerrada`, `construccion_vial` |
| `nivel_riesgo` | `BAJO`, `MEDIO`, `ALTO`, `CRITICO` |
| `accion_grafo` | `BLOQUEAR_ARISTAS`, `PENALIZAR_ARISTAS`, `POSTERGAR_ARISTAS`, `NINGUNA` |
| `factor_penalizacion` | `INF` o multiplicador del costo |
| `fuente_etl` | Lo agrega `cargar.py`: `A1_incendios`, `A2_clima`, `A3_sosafe`, `A4_waze` |
