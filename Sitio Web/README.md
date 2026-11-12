# Sitio Web · Visualizador Leaflet

`index.html` crea un sitio con **Leaflet 1.9.4** que muestra la infraestructura, la metadata y las amenazas cargadas en PostGIS. El contenedor `web` (FastAPI) lo sirve en `http://localhost:8080` y le entrega los datos en GeoJSON a través de la API de `docker/web/app.py`.

```
Navegador ── GET /            ──> FastAPI sirve Sitio Web/index.html
          ── GET /api/capas   ──> catálogo y conteos desde la BD
          ── GET /api/...     ──> GeoJSON generado con ST_AsGeoJSON en PostGIS
```

## Archivos

| Archivo | Rol |
|---|---|
| `index.html` | Sitio completo (HTML, CSS y JavaScript en un solo archivo). Carga Leaflet desde unpkg |
| `../docker/web/app.py` | API que consulta PostGIS y sirve esta carpeta |

## Capas

Cada capa corresponde a uno de los JSON cargados en la BD y se descarga solo al activarla.

| Grupo | Capa | Endpoint | Tabla | Visualización |
|---|---|---|---|---|
| Infraestructura | Red vial | `/api/infraestructura` | `network_edges` | Líneas; vías principales más gruesas |
| Infraestructura | Nodos | `/api/nodos` | `network_nodes` | Puntos (apagada por defecto) |
| Metadata | M1 · Señaléticas | `/api/metadata?fuente=M1_senaleticas` | `temp_metadata` | PARE, Ceda el paso, semáforos y cruces por color |
| Metadata | M2 · Lugares públicos | `/api/metadata?fuente=M2_lugares_publicos` | `temp_metadata` | Puntos por categoría (salud, educación, Carabineros, Bomberos) |
| Metadata | M3 · Puentes | `/api/metadata?fuente=M3_puentes` | `temp_metadata` | Puntos por tipo (puente, pasarela, enlace) |
| Metadata | M4 · Zonas de siniestros | `/api/metadata?fuente=M4_siniestros` | `temp_metadata` | Celdas de 100 m por nivel (crítica, alta, media) |
| Amenazas | A1 · Incendios | `/api/amenazas?fuente=A1_incendios` | `temp_amenazas` | Áreas de exclusión de incendios activos, incendios extinguidos y probabilidad de ignición |
| Amenazas | A2 · Clima severo | `/api/amenazas?fuente=A2_clima` | `temp_amenazas` | Celdas por nivel de riesgo, con selector de hora |
| Amenazas | A3 · SOSAFE | `/api/amenazas?fuente=A3_sosafe` | `temp_amenazas` | Geocerca y punto del reporte |
| Amenazas | A4 · TomTom | `/api/amenazas?fuente=A4_tomtom` | `temp_amenazas` | Accidentes, calles cerradas y obras (puntos y tramos) |

Colores de riesgo, comunes a todas las amenazas: BAJO gris, MEDIO amarillo, ALTO naranjo, CRÍTICO rojo.

## API

| Endpoint | Parámetros | Devuelve |
|---|---|---|
| `GET /health` | — | Estado de la conexión a la BD |
| `GET /api/capas` | — | Conteos de la red vial y de cada `fuente_etl` en metadata y amenazas |
| `GET /api/infraestructura` | `highway`, `simplificar` | Aristas de la red vial |
| `GET /api/nodos` | — | Nodos de la red vial |
| `GET /api/metadata` | `fuente`, `tipo`, `comuna`, `simplificar` | Filas de `temp_metadata` |
| `GET /api/amenazas` | `fuente`, `tipo`, `riesgo` | Filas de `temp_amenazas` |
| `GET /api/route/baseline` | `start_node`, `end_node` | Ruta `pgr_dijkstra` por longitud (una ruta) |
| `GET /api/rutas` | `inicio_lon`, `inicio_lat`, `objetivo_lon`, `objetivo_lat` + restricciones opcionales | k rutas `pgr_dijkstra` sobre pesos resilientes y métrica de resiliencia |
| `GET /api/rutas/preferencias` | — | Restricciones por defecto |
| `POST /api/flota` | JSON con `area` (polígono o `null`) y `preferencias` (`n_drones`, `bases`, …) | Plan por área con n drones: áreas, bases, salidas trazadas con `pgr_dijkstra` e indicadores |
| `GET /api/flota/preferencias` | — | Preferencias por defecto de la flota |

`simplificar` es la tolerancia en grados de `ST_SimplifyPreserveTopology`, para que los GeoJSON con polígonos pesen menos. La documentación interactiva de la API queda en `http://localhost:8080/docs`.

## Uso

```bash
docker compose up -d --build
docker compose exec etl python main.py     # si la BD aún no tiene datos
# abrir http://localhost:8080
```

El sitio requiere conexión a internet para descargar Leaflet y el mapa base.

## Mapa base

Se usa OpenStreetMap (`https://tile.openstreetmap.org/{z}/{x}/{y}.png`), que no requiere API key. La atribución "© OpenStreetMap contributors" debe quedar visible en el mapa.

## Planificación por área con una flota de drones

El panel **Planificación por área · flota UAV** permite:

1. Definir el **área**: con **Dibujar área** se hace clic en cada vértice y se termina con doble clic o **Cerrar área**; con **Toda la comuna** se planifica la red completa.
2. Elegir la **cantidad de drones** (1 a 10). Cada dron recibe una parte del área, con su propia base.
3. Ajustar, si se quiere, las restricciones y los parámetros del dron (bases distribuidas o una sola base, recintos públicos permitidos para las bases, hora de inicio, jornada, tipos de señal, clima, velocidad, autonomía, reserva y cambio de batería). Lo que no se modifica se envía con su valor por defecto.
4. Presionar **Planificar inspección**. El mapa muestra, con un color por dron, su área, las calles que debe inspeccionar (trazo grueso), sus salidas (trazo continuo; los traslados entre bases van punteados) y sus bases: un ícono de edificio con el número del dron sobre el recinto público elegido (CESFAM, colegio público, Carabineros o Bomberos), unido con una línea punteada a la calle desde donde despega. Al hacer clic se ve el nombre, la dependencia y la dirección del recinto, y el resultado lista la base de cada dron. Las calles postergadas por amenazas se muestran en gris punteado.

El panel resume el tiempo de inspección global, los km a inspeccionar, el kilometraje total, la eficiencia, el equilibrio entre drones y la repetición de calles; una tabla por dron permite ocultar o mostrar su capa, y el detalle de cada dron lista sus salidas con hora de inicio, km y aviso de clima.

El método (partición entre drones, bases, orden de visita y trazado con pgRouting) y el modo anterior de k rutas están documentados en [`docker/web/ruteo/README.md`](../docker/web/ruteo/README.md).
