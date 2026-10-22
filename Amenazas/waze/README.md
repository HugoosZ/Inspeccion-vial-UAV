# Módulo de Amenazas: Waze Live Map (Calles Cerradas y Obras Viales)

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
* **Parámetros de consulta (centrados en Maipú):**
  * `top`: `-33.4600` (Límite norte Maipú)
  * `bottom`: `-33.5800` (Límite sur urbano Maipú)
  * `left`: `-70.8200` (Límite poniente urbano)
  * `right`: `-70.7000` (Límite oriente Maipú)
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
Amenazas/waze/
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
python Amenazas/waze/extractor.py

# Generación de geocercas y grafo
python Amenazas/waze/classifier.py

# Carga en la base de datos (PostGIS)
python Amenazas/waze/cargar.py            # Carga real
python Amenazas/waze/cargar.py --dry-run  # Simulación sin escribir en BD
```

### Flujo integral

```bash
python Amenazas/waze/run.py
python Amenazas/waze/run.py --dry-run
python Amenazas/waze/run.py --load
```

---

## 6. Control Perimetral (HTTP 403 Forbidden) y Modo Respaldo

La API pública de Waze Live Map se encuentra detrás del control perimetral Cloud Armor / WAF de Google. Si el servidor detecta una conexión automatizada y responde con `403 Forbidden`:

1. El extractor informa el estado en consola.
2. Si el operador desea actualizar datos en vivo, puede copiar la cabecera `Cookie` desde las Herramientas de Desarrollador del navegador (F12 -> Pestaña *Red* en `https://www.waze.com/live-map`) y colocarla en `Amenazas/waze/.env` como `WAZE_COOKIE='...'`.
3. Si no se dispone de credenciales actualizadas, el pipeline continúa de manera transparente utilizando el conjunto de datos verificado local en `data/waze_raw.json`, garantizando la ejecución ininterrumpida del sistema.
