# M1 · Señaléticas (OpenStreetMap vía ohsome)

Ubicación de las señales PARE, ceda el paso, semáforos y cruces peatonales de Maipú. Son los elementos que la flota debe inspeccionar: cada calle con una señal de los tipos elegidos se convierte en una tarea del planificador.

El uso de drones para inventariar señalética está respaldado por la revisión sistemática de Satama-Bermeo et al. (2025), que reporta avances en precisión, eficiencia y procesamiento en tiempo real al combinar drones con visión por computador.

## Archivos

| Archivo | Rol |
|---|---|
| `controles_viales.py` | Extract + Transform: consulta ohsome y guarda el GeoJSON (requiere `OHSOME_API_KEY`) |
| `cargar.py` | Load: inserta el GeoJSON en `temp_metadata` (`--dry-run` para probar sin BD) |
| `controles_viales_maipu.md` | Estructura del GeoJSON, campo por campo |
| `data/controles_viales_maipu.geojson` | Salida del ETL |

## Fuente

OpenStreetMap consultado con la API de ohsome (HeiGIT), filtro `highway=stop`, `give_way`, `traffic_signals` o `crossing`. Es la misma fuente que la red vial del proyecto: por eso este módulo se considera la **capa de demanda** (qué se inspecciona) y no una restricción del vuelo.

## Carga en la base de datos

Cada elemento va a `temp_metadata` con `fuente_etl = "M1_senaleticas"` y un tipo según `highway`:

| `highway` | `tipo` |
|---|---|
| `stop` | `senal_pare` |
| `give_way` | `senal_ceda_el_paso` |
| `traffic_signals` | `semaforo` |
| `crossing` | `cruce_peatonal` |

## Referencias

- Raifer, M. et al. (2019). OSHDB: a framework for spatio-temporal analysis of OpenStreetMap history data. *Open Geospatial Data, Software and Standards*, 4(3).
- Satama-Bermeo, G. et al. (2025). PRISMA Review: Drones and AI in Inventory Creation of Signage. *Drones*, 9(3), 221. https://doi.org/10.3390/drones9030221
