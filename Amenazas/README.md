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

---

## Horizontes Temporales, Tiempos de Respuesta y Latencia de Datos

Para el análisis operacional del sistema UAV es fundamental distinguir dos dimensiones temporales:
1. **Tiempo de respuesta de la API (latencia HTTP):** El tiempo que tarda el servidor remoto en devolver el payload JSON tras una solicitud `GET`.
2. **Frescura y latencia del dato (*Data Latency*):** El retraso real entre el momento en que el evento ocurre físicamente en el territorio y el momento en que queda publicado y disponible en la API.

| ID | Amenaza / Fuente | Tiempo de Respuesta API (HTTP) | Frecuencia de Actualización | Latencia del Dato (Mundo Real) | Clasificación Temporal | Rol Operacional en la Misión UAV |
|---|---|:---:|:---:|:---:|:---:|---|
| **A1** | **Incendios**<br>*(NASA FIRMS + GDACS)* | 1,0 -- 2,5 s | 2 a 4 veces al día (pasos satelitales) | **3 a 6 horas** *(Near Real Time - NRT)* | **Cuasi Tiempo Real / NRT** | Planificación previa al despegue; evita zonas con columnas de humo persistentes o combate aéreo de Conaf. |
| **A2** | **Clima Severo**<br>*(Open-Meteo)* | 0,3 -- 0,8 s | Cada 1 a 6 horas (corridas de modelos numéricos) | **1 hora** *(resolución de pronóstico horario)* | **Pronóstico Numérico Horario** | Planificación de ventanas de vuelo seguras; penaliza aristas por hora según viento, lluvia y visibilidad. |
| **A3** | **Incidentes Ciudadanos**<br>*(SOSAFE)* | 0,8 -- 1,5 s | Tiempo real continuo | **1 a 5 minutos** | **Tiempo Real Dinámico (Crowdsourcing)** | Alerta táctica inmediata durante el vuelo; aborta o desvía rutas ante balaceras, fuegos artificiales o cables caídos. |
| **A4** | **Cortes y Obras**<br>*(Waze Live Map)* | 1,0 -- 3,0 s por sector | Tiempo real continuo | **2 a 5 minutos** | **Tiempo Real Dinámico (Tránsito)** | Bloqueo preventivo de vías cerradas o con maquinaria pesada en calzada antes y durante el recorrido. |

### Justificación de la Arquitectura Temporal en Tres Capas

Esta diferenciación temporal sustenta la resiliencia del sistema de inspección:
* **Capa 1: Planificación Estática (Metadata M1 a M4):** Datos de infraestructura fija, cartografía DGAC y censo que definen el marco basal del vuelo.
* **Capa 2: Planificación Táctica Previa (A1 Incendios, A2 Clima):** Datos NRT y modelos numéricos consultados antes de armar la ruta del día para determinar si las condiciones macro permiten volar y qué macrozonas evitar.
* **Capa 3: Control Táctico en Vivo (A3 SOSAFE, A4 Waze):** Telemetría ciudadana y vial de alta frecuencia capaz de re-enrutar al dron en vuelo ante eventos repentinos que invaliden el paso por una calle.
