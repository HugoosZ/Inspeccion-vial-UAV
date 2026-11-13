# A4 · Accidentes, calles cerradas y obras viales (TomTom)

Amenaza **dinámica**: un accidente aparece y desaparece durante la jornada y obliga a cambiar una ruta ya planificada; los cierres y las obras se informan. Además, un choque puede derribar señales y semáforos (FHWA, *Maintenance of Signs and Sign Supports*, cap. X), así que los accidentes quedan marcados para revisar la señalética del tramo.

## Archivos

| Archivo | Rol |
|---|---|
| `tomtom.py` | Extract + Transform: consulta TomTom y genera el GeoJSON (requiere `TOMTOM_API_KEY`) |
| `cargar_tomtom.py` | Load: inserta el GeoJSON en `temp_amenazas` (`--dry-run` para probar sin BD) |
| `data/tomtom_incidentes_maipu.geojson` | Salida del ETL |

## Fuente y acceso

TomTom Traffic API, **Incident Details v5**:
`https://api.tomtom.com/traffic/services/5/incidentDetails?key=…&bbox=-70.92,-33.62,-70.70,-33.46&fields=…&language=es-ES&timeValidityFilter=present`

- Clave gratuita de desarrollador en https://developer.tomtom.com: 2.500 solicitudes al mes, sin tarjeta.
- La clave va en `TOMTOM_API_KEY` del `.env` (no se sube al repositorio). Sin ella, `main.py` omite el extractor y carga el último GeoJSON guardado.
- Una sola consulta cubre Maipú (el límite del rectángulo es 10.000 km²).

## Transformación

| `iconCategory` | `tipo_amenaza` | `nivel_riesgo` | `accion_grafo` | `radio_afectacion_m` |
|---|---|---|---|---|
| 1 Accidente | `accidente` | ALTO | `INSPECCIONAR` | 100 |
| 8 Calle cerrada | `calle_cerrada` | MEDIO | `NINGUNA` (informativo) | 60 |
| 9 Obras | `construccion_vial` | MEDIO | `NINGUNA` (informativo) | 50 |
| 7 Carril cerrado | `carril_cerrado` | BAJO | `NINGUNA` (informativo) | 50 |
| 3 Condición peligrosa | `condicion_peligrosa` | BAJO | `NINGUNA` (informativo) | 50 |
| 14 Vehículo detenido | `vehiculo_detenido` | BAJO | `NINGUNA` (informativo) | 30 |

Se descartan las retenciones (6), porque la congestión no afecta al dron, y las categorías de clima (2, 4, 5, 10, 11), que cubre A2. Cada incidente conserva su geometría original (punto o tramo). Los radios son **supuestos de diseño configurables**.

**Efecto actual.** Un accidente solicita revisar las calles asociadas a menos de 100 m;
no bloquea el vuelo en el modelo del grupo. El punto 7 incorpora esa tarea y recalcula
para visitarla. El radio es de asociación de demanda, no una exclusión aérea.
Los escenarios A4 futuros y su evaluación se detallan en
[ESCENARIOS.md](../../Probabilidades/ESCENARIOS.md).

Un **cierre, una obra o un carril cerrado** restringen el tránsito de automóviles, **no el espacio aéreo**, así que quedan solo como información en el mapa y en la tabla de indisponibilidad del punto 5 (`observaciones`). Es el mismo criterio de `Probabilidades/generar.py`.

## `properties` de cada incidente

| Campo | Campo TomTom | Descripción |
|---|---|---|
| `id_amenaza`, `id_tomtom` | `id` | Identificador del incidente |
| `fuente` | — | `TomTom Traffic Incidents` |
| `tipo_amenaza`, `categoria` | `iconCategory` | Tipo según la tabla anterior |
| `descripcion` | `events[].description` | Descripción en español |
| `tramo` | `from`, `to` | Inicio y fin del tramo afectado |
| `vias` | `roadNumbers` | Rutas afectadas |
| `largo_m`, `demora_s`, `magnitud_demora` | `length`, `delay`, `magnitudeOfDelay` | Largo del tramo, demora y su magnitud |
| `probabilidad` | `probabilityOfOccurrence` | certain, probable, risk_of o improbable |
| `n_reportes`, `ultimo_reporte` | `numberOfReports`, `lastReportTime` | Reportes de usuarios |
| `nivel_riesgo`, `accion_grafo`, `radio_afectacion_m` | — | Efecto en el ruteo |
| `revisar_senaletica` | — | `true` en accidentes |
| `valido_desde`, `valido_hasta` | `startTime`, `endTime` | Vigencia (ISO 8601) |

## Carga en la base de datos

`cargar_tomtom.py` inserta cada incidente en `temp_amenazas` con `fuente_etl = "A4_tomtom"`. Al cargar, `main.py` borra las filas antiguas de Waze (`A4_waze`).

---

## Versión anterior: Waze Live Map

El módulo de Waze (`extractor.py`, `classifier.py`, `run.py`, `cargar.py`) queda en la carpeta como referencia, pero ya no lo ejecuta `main.py`. La justificación que sigue, que bloqueaba cierres y obras, corresponde al criterio anterior; el vigente es el de la sección **Transformación**.


Este módulo automatiza la extracción, filtrado geoespacial y modelamiento operacional de eventos reportados en tiempo real por la comunidad y operadores viales a través de **Waze Live Map** para la comuna de **Maipú**.

Se enfoca específicamente en dos tipos de eventos dinámicos críticos para la inspección vial mediante drones:
1. **Calles cerradas al tránsito** (`ROAD_CLOSED`, cortes totales, desvíos).
2. **Obras de construcción y faenas en calzada** (`HAZARD_ON_ROAD_CONSTRUCTION`, pavimentación, recarpeteo, bacheo y trabajos de infraestructura).

---

## 1. Justificación Operacional UAV (Cota de Vuelo 20 metros AGL)

El ruteo de la flota UAV opera a una altitud nominal de **20 metros sobre el nivel del suelo (AGL)** para capturar imágenes de alta resolución de señales de tránsito y elementos viales. A esta altura de vuelo:

* **Riesgo crítico de colisión con maquinaria pesada:** Las faenas viales emplean grúas pluma, camiones tolva con tolva alzada (hasta 8--12 m), retroexcavadoras y andamiajes temporales que invaden el espacio aéreo inferior del dron.
* **Degradación de sensores ópticos por material particulado:** La demolición de pavimento, corte de hormigón y aplicación de asfalto generan densas columnas de polvo y calor térmico local que ciegan las cámaras RGB y sensores de posicionamiento óptico del UAV.
* **Invalidez de la inspección:** Durante una reparación o corte de calle, la señalética vertical y demarcación horizontal se encuentran provisionalmente retiradas, tapadas con lonas plásticas, derribadas o desalineadas por los contratistas, por lo que destinar tiempo de batería a inspeccionar un segmento en obra carece de utilidad técnica.
* **Seguridad operacional de cuadrillas en tierra:** Normativas aeronáuticas (DAN 151 / DAN 91) prohíben el sobrevuelo de UAV sobre concentraciones de trabajadores en la vía pública.

Por consiguiente, estos eventos se modelan como **amenazas de bloqueo duro (`BLOQUEAR_ARISTAS`, costo `INF`)** en el grafo de ruteo resiliente.

---

## 2. Parámetros Geoespaciales y API Waze

* **Endpoint oficial Live Map:**
  `https://www.waze.com/live-map/api/georss`
* **Estrategia Multi-Sector (10 Zonas):**
  Para evitar que la API trunque los resultados al solicitar un área muy extensa, la comuna se divide en 10 sectores operativos de aproximadamente $3 \times 3$ km cada uno, con un retraso configurable de **3,0 segundos** entre peticiones (`WAZE_REQUEST_DELAY=3.0`) para proteger la cuota de la sesión y evitar bloqueos por tasa de refresco (*Rate Limiting*):
  1. *Pajaritos Norte / Del Sol / Monte Tabor*
  2. *Plaza de Maipú / Centro Cívico*
  3. *Esquina Blanca / Camino Melipilla Oriente / Ferrocarril*
  4. *Rinconada / Hospital El Carmen*
  5. *El Olimpo / Los Presidentes*
  6. *Las Naciones / Cuatro Poniente*
  7. *Pajaritos Sur / Nueva San Martín*
  8. *El Abrazo / Tres Poniente*
  9. *Ciudad Satélite / Camino Melipilla Poniente*
  10. *Loma Blanca / Lumen / Segunda Transversal*

* **Parámetros de consulta (por sector):**
  * `env`: `row` (Rest of World / Chile)
  * `types`: `alerts,traffic`

---

## 3. Matriz de Clasificación de Amenazas

| Categoría | Tipo Waze | Nivel de Riesgo | Radio de Exclusión | Efecto en Grafo | Justificación |
|---|---|:---:|:---:|:---:|---|
| **`CALLE_CERRADA`** | `ROAD_CLOSED` / cortes programados | **`CRITICO`** | 60 m | `BLOQUEAR_ARISTAS` (`INF`) | Vía intransitable; señalética deshabilitada o ausente. |
| **`CONSTRUCCION_VIAL`** | `HAZARD_ON_ROAD_CONSTRUCTION` | **`ALTO`** | 50 m | `BLOQUEAR_ARISTAS` (`INF`) | Maquinaria en altura, cuadrillas en calzada y polvo en suspensión. |

---

## 4. Estructura de Archivos

```text
Amenazas/A4/
├── .env.example                       # Plantilla de variables de configuración
├── .env                               # Configuración local (bounding box y cookies)
├── README.md                          # Documentación técnica
├── extractor.py                       # Extracción HTTP de API Waze y filtrado en Maipú
├── classifier.py                      # Asignación de radios y geocercas No-Fly Zones
├── cargar.py                          # Carga idempotente a la tabla temp_amenazas (PostGIS)
├── run.py                             # Orquestador del flujo completo
└── data/
    ├── threats_geofences.md           # Diccionario de datos de salida
    ├── waze_raw.json                  # Datos crudos de la API GeoRSS
    ├── waze_filtered_construction.json # Alertas filtradas dentro de Maipú
    ├── waze_threats_geofences.geojson  # Geocercas (Puntos y Polígonos circulares)
    └── active_waze_threats_graph.json # JSON de amenazas para Dijkstra/pgRouting
```

---

## 5. Ejecución

### Ejecución independiente

```bash
# Extracción y filtrado
python Amenazas/A4/extractor.py

# Generación de geocercas y grafo
python Amenazas/A4/classifier.py

# Carga en la base de datos (PostGIS)
python Amenazas/A4/cargar.py            # Carga real
python Amenazas/A4/cargar.py --dry-run  # Simulación sin escribir en BD
```

### Flujo integral

```bash
python Amenazas/A4/run.py
python Amenazas/A4/run.py --dry-run
python Amenazas/A4/run.py --load
```

---

## 6. Control Perimetral (HTTP 403 Forbidden) y Modo Respaldo

La API pública de Waze Live Map se encuentra detrás del control perimetral Cloud Armor / WAF de Google. Si el servidor detecta una conexión automatizada y responde con `403 Forbidden`:

1. El extractor informa el estado en consola.
2. Si el operador desea actualizar datos en vivo, puede copiar la cabecera `Cookie` desde las Herramientas de Desarrollador del navegador (F12 -> Pestaña *Red* en `https://www.waze.com/live-map`) y colocarla en `Amenazas/A4/.env` como `WAZE_COOKIE='...'`.
3. Si no se dispone de credenciales actualizadas, el pipeline continúa de manera transparente utilizando el conjunto de datos verificado local en `data/waze_raw.json`, garantizando la ejecución ininterrumpida del sistema.

---

## 7. Tiempos de Respuesta y Dinámica en Tiempo Real

* **Tiempo de respuesta de la API (HTTP):** Entre **1,0 y 2,0 segundos** por sector consultado. Con la estrategia multi-sector (10 sectores) y pausa de protección de 3 segundos (`WAZE_REQUEST_DELAY=3.0`), la extracción completa de Maipú toma aproximadamente **35 a 45 segundos**.
* **Frecuencia de actualización y frescura del dato en el mundo real:**
  * **Tiempo Real Vial (*Crowdsourcing*):** Los reportes de cierres de calle, trabajos de pavimentación y maquinaria pesada son alimentados continuamente por conductores y editores viales. La latencia entre la instalación del corte/obra en terreno y su reflejo en la API GeoRSS es de **2 a 5 minutos**.
* **Implicancia operacional UAV:** Permite identificar arterias intransitables o intervenidas con maquinaria en elevación, garantizando que el algoritmo de ruteo descarte esas calles antes y durante la ejecución del vuelo.
