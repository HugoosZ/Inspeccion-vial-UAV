# Delimitación de Alcance y Criterios Operacionales
## Módulo SOSAFE & Clasificación Inteligente de Amenazas UAV

Este documento define con precisión técnica el **alcance actual** y el **alcance futuro (trabajo pendiente/fuera de alcance)** del módulo de ingesta y clasificación semántica de reportes ciudadanos de SOSAFE para la flota de drones (UAVs) en la comuna de Maipú.

---

## 1. Contexto y Objetivos Operacionales

El objetivo primordial del proyecto es la **inspección visual automatizada de la red vial urbana mediante drones**. Durante la navegación autónoma (a una altitud de crucero de 20 metros para captura detallada de señales de tránsito y elementos viales), la aeronave debe convivir con el dinamismo del entorno urbano.

Los reportes ciudadanos de SOSAFE aportan información en tiempo real que ningún mapa estático posee. No obstante, la gran mayoría de estos reportes corresponden a quejas comunitarias o incidentes a nivel de suelo que no suponen ningún riesgo para una aeronave en vuelo.

Por ello, el sistema discrimina semánticamente tres tipos de eventos:
1. **Amenazas Críticas al Vuelo (Alcance Actual)**: Incidentes que ponen en riesgo físico o normativo a la aeronave.
2. **Ruido Comunitario Irrelevante (Alcance Actual - Filtrado y Descarte)**: Incidentes a nivel de calle sin impacto aéreo.
3. **Infraestructura Vial Dañada (Fuera de Alcance Actual - Futura Ganancia)**: Incidentes viales que no amenazan al dron, sino que representan objetivos de inspección de alto valor.

---

## 2. Alcance Actual (Enfoque en Seguridad de Vuelo)

### 2.1. Filtro Territorial
* **Zona delimitada**: Exclusivamente la comuna de **Maipú** (Región Metropolitana de Santiago).
* **Parámetros**: Coordenadas centradas en Plaza de Maipú (`-33.5108, -70.7573`) con un radio geográfico de consulta de 6.500 m y filtrado estricto por campo `location == "Maipú"`.

### 2.2. Amenazas Aeronáuticas Reconocidas y Buffer de Exclusión (No-Fly Zones)
El clasificador (mediante Gemini AI o heurística de respaldo) identifica únicamente las siguientes amenazas directas:

| Categoría | Descripción y Riesgo Aeronáutico | Nivel de Riesgo | Radio Buffer (NFZ) |
| :--- | :--- | :---: | :---: |
| **`PIROTECNIA_FUEGOS_ARTIFICIALES`** | Fuegos artificiales, bengalas o petardos. Detonan entre 15 y 50 m con detonación destructiva e impacto directo sobre la aeronave a 20 m de altura. *(Nota: en SOSAFE se reportan bajo tipo 14 o 17)*. | **CRÍTICO** | **400 metros** |
| **`DISPAROS_BALACERA`** | Balaceras activas, tiroteos o disparos al aire. Peligro de proyectiles balísticos en ascenso/descenso. | **CRÍTICO** | **400 metros** |
| **`OBSTACULO_CABLES_POSTES`** | Cables de tendido eléctrico arrancados o colgando, postes derribados o camiones enganchados. Peligro fatal de colisión con rotores (a 20 m el dron vuela a la altura directa del tendido aéreo urbano). | **CRÍTICO** | **300 metros** |
| **`AERONAVE_EMERGENCIA`** | Helicópteros policiales o de rescate operando a baja cota / aterrizajes de emergencia. Peligro por turbulencia de rotor (*downwash*) y obligación normativa de despeje inmediato. | **CRÍTICO** | **500 metros** |
| **`INCENDIO_COLUMNA_TERMICA`** | Incendios estructurales, vehiculares o forestales activos con humo denso. Provocan ceguera de sensores y corrientes térmicas violentas. | **ALTO** | **500 metros** |

### 2.3. Descarte Sistemático de Ruido Comunitario (`RUIDO_IRRELEVANTE`)
El sistema prompt de IA descarta expresamente y clasifica como `es_amenaza: false` (buffer: 0 m) los siguientes eventos:
* **Falsos disturbios y quejas vecinales**: "Niños tirando limones / piedras a un portón", disputas verbales, música a alto volumen.
* **Avisos comunitarios**: Venta de comida, avisos de mascotas perdidas, personas sospechosas o en situación de calle.
* **Incidencias cotidianas a nivel de suelo**: Baches menores, acumulación de basura doméstica, colapsos de alcantarillado sin fuego ni cables.

### 2.4. Salidas Generadas
* `data/filtered_reports.json`: Reportes crudos de SOSAFE pertenecientes a Maipú.
* `data/classified_threats.json`: Estructura JSON completa de auditoría (amenazas confirmadas vs. ruido descartado, con justificación técnica).
* `data/active_threats_graph.json`: Estructura JSON limpia con **únicamente las amenazas activas**, sus coordenadas, radio de afectación y directiva de penalización para el grafo de navegación.
* `data/threats_geofences.geojson`: Archivo GeoJSON con puntos de amenaza y polígonos circulares de exclusión para Leaflet y consultas PostGIS espaciales.

---

## 3. Fuera del Alcance Actual / Trabajo Futuro (Infraestructura Vial Dañada como Ganancia)

### 3.1. Justificación de la Exclusión en la Fase Actual
Un reporte comunitario que indique:
> *"Se cayó la señalética del disco Pare en la esquina"*  
> *"Semáforo roto por choque"*  
> *"Letrero vial arrancado por el viento"*

**NO constituye una amenaza cinemática ni térmica para un dron que vuela a 20 metros de altitud.**  
Si el sistema actual clasificara estos reportes como "amenaza", generaría una geocerca de exclusión (No-Fly Zone) que prohibiría el paso del dron por dicha intersección, impidiendo precisamente lo que el proyecto busca: **inspeccionar la vialidad.**

### 3.2. Visión para la Siguiente Fase: Nodos de Recompensa (Gain / Reward Nodes)
En fases posteriores del proyecto, estos reportes se integrarán en el modelo de ruteo como **nodos de ganancia** (Problema de Orienteering o Inspección con Recompensa):
1. **Detección**: Identificar reportes donde se denuncie deterioro de infraestructura vial.
2. **Asignación de Ganancia**: Atribuirle un puntaje de utilidad o prioridad a la arista o nodo correspondiente en la red vial de OSM.
3. **Desvío de Misión**: El algoritmo de planificación de rutas desviará dinámicamente al dron para que vuele sobre el punto, tome fotografías aéreas de alta resolución y alimente el módulo de detección de anomalías visuales.
4. **Verificación Automática**: Confirmar o descartar el daño vial reportado por el ciudadano mediante visión por computadora a bordo.

### 3.3. Preparación Actual en el Código
Para no entorpecer el desarrollo futuro, el clasificador ya contempla la categoría especial:
* **`categoria: "INFRAESTRUCTURA_VIAL_DANADA"`**
* **`es_amenaza: false`**
* **`radio_buffer_metros: 0`**
* **`justificacion`: *"Elemento vial dañado. Fuera de alcance de amenaza; futura oportunidad de inspección."***

De este modo, estos eventos quedan catalogados en `classified_threats.json` pero no generan No-Fly Zones en `threats_geofences.geojson`, listos para ser consumidos por el futuro módulo de ruteo con ganancia.
