# Módulos de Metadata

Este directorio contiene los módulos de extracción, transformación y carga (ETL) de los metadatos urbanos, socioeconómicos, normativos y de transporte para la comuna de **Maipú**.

A diferencia de las amenazas dinámicas, la metadata define la **estructura basal del territorio**: dónde están los elementos viales a inspeccionar (demanda/recompensa) y qué restricciones físicas o normativas fijas condicionan el vuelo.

---

## 1. Catálogo de Fuentes de Metadata

| # | Fuente | Carpeta | Origen de Datos | Tipo Geométrico | Rol en el Ruteo UAV |
|---|---|---|---|:---:|---|
| **M1** | **Paraderos** | `M1/` | Catastro DTPM / Directorio de Transporte Público Metropolitano | Puntos (942) | Factor de penalización ($\pi = 1{,}3$) por riesgo de oclusión visual (buses detenidos) y concentración de personas. |
| **M2** | **Controles Viales** | `M2/` | OpenStreetMap vía API Ohsome | Puntos (PARE, semáforos, ceda el paso, cruces) | **Demanda de inspección:** Define las aristas requeridas ($E_R$) que el dron debe sobrevolar y fotografiar. |
| **M3** | **Censo 2024** | `M3/` | Instituto Nacional de Estadísticas (INE) | Polígonos (manzanas censales) | **Priorización social:** Coropleta de vulnerabilidad ($V_m$) que otorga mayor puntaje a señales en barrios vulnerables. |
| **M4** | **Aeródromos** | `M4/` | Dirección General de Aeronáutica Civil (DGAC) | Puntos + conos de aproximación | **Restricción dura ($\pi = \infty$):** Zona de exclusión aérea por seguridad aeronáutica (DAN 151). |

---

## 2. Dinámica Temporal y Tiempos de Respuesta

**Ninguna de las fuentes de metadata es en tiempo real continuo**, y responde a una decisión técnica deliberada de diseño: la infraestructura física, los catastros censales y la zonificación aeronáutica no cambian minuto a minuto.

| ID | Fuente | Tiempo de Respuesta API / Script | Frecuencia de Actualización | Latencia del Dato | Clasificación Temporal |
|---|---|:---:|:---:|:---:|:---:|
| **M1** | Paraderos (DTPM) | 1,5 -- 3,0 s *(ArcGIS / CSV)* | Anual o semestral | Meses *(catastro consolidado)* | **Estático / Catastral** |
| **M2** | Controles viales (Ohsome OSM) | 2,0 -- 5,0 s *(HTTP API)* | Semanal o mensual *(comunidad OSM)* | Semanas *(edición colaborativa)* | **Estático / Infraestructura** |
| **M3** | Censo por manzana (INE) | Procesamiento local (< 5 s) | Decenal *(Censo 2024)* | Años *(censo oficial nacional)* | **Estático / Demográfico** |
| **M4** | Aeródromos (DGAC) | Archivo local estático | Periódica *(enmiendas AIRAC / anual)* | Meses *(publicación oficial)* | **Estático / Normativo** |

---

## 3. Integración en la Base de Datos

Cada subcarpeta cuenta con un script `cargar.py` que almacena los registros de forma idempotente en la tabla `temp_metadata` de PostGIS (`database/init.sql`), registrando su `fuente_etl` (`M1_paraderos`, `M2_controles_viales`, `M3_censo_manzanas`, `M4_aerodromos`).
