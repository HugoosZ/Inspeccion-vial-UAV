# Amenazas

Cada subcarpeta automatiza la extracción y transformación de una fuente de amenaza a un JSON/GeoJSON, documenta su estructura en su respectivo `.md` y contiene un `cargar.py` que la inserta en la tabla `temp_amenazas`.

| # | Amenaza | Carpeta | Fuente | Geometría | Efecto en el grafo | Acceso |
|---|---|---|---|---|---|---|
| A1 | Incendios forestales y de pastizales | `A1/` | CONAF (ArcGIS) | Círculos de exclusión + puntos históricos + celdas de probabilidad de ignición | Bloqueo (activos) o penalización (controlados) | Libre |
| A2 | Lluvia, ráfagas, baja visibilidad, tormentas | `A2/` | Open-Meteo | Polígonos (celdas 3 × 3) | Penalización o bloqueo por hora | Libre |
| A3 | Zona conflictiva activa | `A3/` | SOSAFE + clasificador IA | Puntos + geocercas | Bloqueo temporal | Credenciales de la app |
| A4 | Accidentes, calles cerradas y obras | `A4/` | TomTom Traffic API (Incident Details v5) | Puntos y tramos + radio de afectación | Bloqueo (accidente, cierre) o penalización (obras) | `TOMTOM_API_KEY` gratuita |

`comun.py` reúne lo compartido por A1 y A2 (A3 y A4 lo usan para la carga): área de estudio, HTTP con reintentos, geometría simple sin dependencias extra, escritura de GeoJSON y la carga idempotente a la BD. A1 usa además `Metadata/comun_arcgis.py` para consultar los servicios ArcGIS de CONAF.

## Ejecución

Dentro del contenedor `etl` (`docker compose exec etl bash`):

```bash
python Amenazas/A1/extractor.py  && python Amenazas/A1/cargar.py
python Amenazas/A2/extractor.py  && python Amenazas/A2/cargar.py
python Amenazas/A3/run.py        && python Amenazas/A3/cargar.py
python Amenazas/A4/tomtom.py     && python Amenazas/A4/cargar_tomtom.py
```

`main.py` ejecuta todo esto automáticamente. Cada `cargar.py` acepta `--dry-run` para revisar las filas sin escribir en la BD.

## Convención común de `properties`

Para que el módulo de ruteo pueda leer todas las amenazas igual, cada feature de A1, A2 y A4 incluye:

| Campo | Valores |
|---|---|
| `id_amenaza` | Identificador único con prefijo de la fuente |
| `fuente` | Nombre de la fuente original |
| `tipo_amenaza` | `incendio_activo`, `incendio_historico`, `probabilidad_ignicion`, `clima_severo`, `calle_cerrada`, `construccion_vial` |
| `nivel_riesgo` | `BAJO`, `MEDIO`, `ALTO`, `CRITICO` |
| `accion_grafo` | `BLOQUEAR_ARISTAS`, `PENALIZAR_ARISTAS`, `POSTERGAR_ARISTAS`, `NINGUNA` |
| `factor_penalizacion` | `INF` o multiplicador del costo |
| `fuente_etl` | Lo agrega `cargar.py`: `A1_incendios`, `A2_clima`, `A3_sosafe`, `A4_tomtom` |

---

## Horizontes Temporales, Tiempos de Respuesta y Latencia de Datos

Para el análisis operacional del sistema UAV es fundamental distinguir dos dimensiones temporales:
1. **Tiempo de respuesta de la API (latencia HTTP):** El tiempo que tarda el servidor remoto en devolver el payload JSON tras una solicitud `GET`.
2. **Frescura y latencia del dato (*Data Latency*):** El retraso real entre el momento en que el evento ocurre físicamente en el territorio y el momento en que queda publicado y disponible en la API.

| ID | Amenaza / Fuente | Tiempo de Respuesta API (HTTP) | Frecuencia de Actualización | Latencia del Dato (Mundo Real) | Clasificación Temporal | Rol Operacional en la Misión UAV |
|---|---|:---:|:---:|:---:|:---:|---|
| **A1** | **Incendios**<br>*(CONAF)* | 1,0 -- 3,0 s por servicio | Diaria (registro de incendios y pronóstico de ignición) | **Horas** *(registro oficial de cada incendio confirmado)* | **Diario / Planificación previa** | Planificación previa al despegue; evita zonas con columnas de humo o combate aéreo de CONAF. |
| **A2** | **Clima Severo**<br>*(Open-Meteo)* | 0,3 -- 0,8 s | Cada 1 a 6 horas (corridas de modelos numéricos) | **1 hora** *(resolución de pronóstico horario)* | **Pronóstico Numérico Horario** | Planificación de ventanas de vuelo seguras; penaliza aristas por hora según viento, lluvia y visibilidad. |
| **A3** | **Incidentes Ciudadanos**<br>*(SOSAFE)* | 0,8 -- 1,5 s | Tiempo real continuo | **1 a 5 minutos** | **Tiempo Real Dinámico (Crowdsourcing)** | Alerta táctica inmediata durante el vuelo; aborta o desvía rutas ante balaceras, fuegos artificiales o cables caídos. |
| **A4** | **Accidentes, cortes y obras**<br>*(TomTom)* | 0,5 -- 2,0 s (una consulta) | Tiempo real continuo | **2 a 5 minutos** | **Tiempo Real Dinámico (Tránsito)** | Bloqueo preventivo de vías cerradas o con maquinaria pesada en calzada antes y durante el recorrido. |

### Justificación de la Arquitectura Temporal en Tres Capas

Esta diferenciación temporal sustenta la resiliencia del sistema de inspección:
* **Capa 1: Planificación Estática (Metadata M1 a M4):** Señaléticas a inspeccionar, lugares públicos para las bases, puentes y zonas de siniestros que definen el marco basal del vuelo.
* **Capa 2: Planificación Táctica Previa (A1 Incendios, A2 Clima):** Datos NRT y modelos numéricos consultados antes de armar la ruta del día para determinar si las condiciones macro permiten volar y qué macrozonas evitar.
* **Capa 3: Control Táctico en Vivo (A3 SOSAFE, A4 TomTom):** Telemetría ciudadana y vial de alta frecuencia capaz de re-enrutar al dron en vuelo ante eventos repentinos que invaliden el paso por una calle.
