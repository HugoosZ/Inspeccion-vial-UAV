# Módulos de Metadata

Este directorio contiene los módulos de extracción, transformación y carga (ETL) de la metadata de la comuna de **Maipú**.

A diferencia de las amenazas dinámicas, la metadata describe el territorio de forma **estática**: qué se inspecciona, dónde pueden ubicarse las bases de los drones, qué estructuras condicionan el vuelo y qué zonas conviene priorizar.

---

## 1. Catálogo de fuentes

| # | Metadata | Carpeta | Fuente | Geometría | Rol en el ruteo |
|---|---|---|---|---|---|
| **M1** | Señaléticas | `M1/` | OpenStreetMap vía ohsome | Puntos | **Demanda:** cada calle con una señal elegida es una tarea a inspeccionar |
| **M2** | Lugares públicos | `M2/` | MINVU · Equipamientos (ArcGIS) | Puntos | **Candidatos a droneport:** las bases se eligen entre recintos públicos |
| **M3** | Puentes | `M3/` | MOP · Vialidad y Concesiones (ArcGIS) | Puntos | **Estructuras elevadas:** obstáculo y zona de señal GNSS degradada |
| **M4** | Zonas de siniestros | `M4/` | CONASET · Base de siniestros 2020-2025 (ArcGIS) | Polígonos (celdas de 100 m) | **Priorización:** las calles en zonas críticas se inspeccionan primero |

`comun_bd.py` hace la carga idempotente a `temp_metadata`. `comun_arcgis.py` reúne las consultas a servicios ArcGIS (paginación, conversión a GeoJSON y el límite comunal de MINVU), que usan M2, M3, M4 y la amenaza A1. `limite_maipu.geojson` guarda el último límite comunal descargado como respaldo.

## 2. Dinámica temporal

| ID | Fuente | Tiempo de respuesta | Actualización de la fuente | Clasificación |
|---|---|:---:|:---:|:---:|
| **M1** | ohsome (OSM) | 2–5 s | Continua (edición colaborativa) | Estático / infraestructura |
| **M2** | MINVU | 1–3 s por capa | Según cada catastro (educación: abril 2021) | Estático / equipamiento |
| **M3** | MOP | 1–3 s por capa | Catastros del MOP | Estático / infraestructura |
| **M4** | CONASET | 5–15 s (5 páginas) | Anual (base 2020-2025) | Estático / histórico |

## 3. Integración en la base de datos

Cada subcarpeta tiene un `cargar.py` que inserta sus registros en la tabla `temp_metadata` de PostGIS (`database/init.sql`) con su `fuente_etl`:

| Carpeta | `fuente_etl` | `tipo` |
|---|---|---|
| `M1/` | `M1_senaleticas` | `senal_pare`, `senal_ceda_el_paso`, `semaforo`, `cruce_peatonal` |
| `M2/` | `M2_lugares_publicos` | `salud_publica`, `educacion_publica`, `carabineros`, `bomberos` |
| `M3/` | `M3_puentes` | `puente`, `pasarela`, `enlace` |
| `M4/` | `M4_siniestros` | `zona_siniestros` |

## 4. Cambios respecto de la entrega 2

Se retiraron los paraderos (DTPM), el Censo 2024 y los aeródromos (DGAC). Las señales de OSM pasaron de `M2/` a `M1/`. Al cargar, `main.py` borra de la base de datos las filas de las fuentes retiradas (`M1_paraderos`, `M2_controles_viales`, `M3_censo_manzanas`, `M4_aerodromos`).
