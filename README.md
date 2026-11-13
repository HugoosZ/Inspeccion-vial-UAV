# Inspeccion-vial-UAV

Sistema de planificación, ruteo y visualización geoespacial para una flota de vehículos aéreos no tripulados (UAVs), orientado a la **detección e inspección de señaléticas, semáforos y elementos viales** en las comunas de **Maipú, Estación Central y Santiago** (Región Metropolitana, Chile).

---

## Estructura del Repositorio

El proyecto se estructura de forma modular desacoplando extracción, base de datos y visualización:

```text
├── .gitignore               # Configuración de exclusión para extra/, SO, cachés e IDEs
├── docker-compose.yml       # Orquestador de contenedores (db, etl, web)
├── main.py                  # Script principal: Docker + descarga + carga a la BD + web
├── README.md                # Documentación general del repositorio
├── database/                # DDL inicial (.sql) y diagrama de base de datos
│   ├── init.sql
│   └── README.md
├── docker/                  # Definición de imágenes Docker
│   ├── etl/                 # Contenedor con herramientas geoespaciales (GeoPandas, OSMnx)
│   └── web/                 # Contenedor con FastAPI y servidor estático Leaflet
│       └── ruteo/           # Planificación por área con n drones (pgRouting) y k rutas pgr_dijkstra
├── Infraestructura/         # Extracción y transformación de red vial (nodos y aristas)
├── Metadata/                # Metadata: M1 señaléticas, M2 lugares públicos, M3 puentes, M4 siniestros
├── Amenazas/                # Extracción y transformación de amenazas (incendios, clima, SOSAFE, TomTom)
├── Probabilidades/          # Probabilidad de falla de cada enlace y nodo por amenaza (punto 5)
└── Sitio Web/               # Visualizador Leaflet y planificación por área con una flota de drones
```

---

## Arquitectura de Contenedores

El proyecto corre íntegramente sobre Docker mediante `docker-compose`:

1. **`db` (`pgrouting/pgrouting:16-3.4-3.6.1`)**: Base de datos PostgreSQL con soporte nativo para análisis espacial y algoritmos de ruteo (`pgr_dijkstra`).
2. **`etl` (Python 3.11)**: Entorno aislado con librerías geoespaciales (`geopandas`, `osmnx`, `shapely`, `networkx`, `psycopg2-binary`) para procesar las extracciones.
3. **`web` (FastAPI + Leaflet)**: API para consultas geoespaciales y servidor web para el mapa interactivo en el puerto `8080`.

---

## Inicio Rápido

### 1. Requisitos
- Docker Desktop (con Docker Compose) abierto.
- Python 3 en el computador (solo la biblioteca estándar; todo el procesamiento corre en Docker).
- Opcional: copiar `.env.example` a `.env` y completar las claves (`OHSOME_API_KEY`, SOSAFE, Gemini, `TOMTOM_API_KEY`). Sin ellas, esos pasos se omiten y se cargan los JSON que ya están en el repositorio.
- Opcional: licencia WLS académica de Gurobi (`WLSACCESSID`, `WLSSECRET`, `LICENSEID` en el `.env`) para el modelo formal T2. Sin ella, T2 se resuelve con HiGHS.

### 2. Ejecutar todo con un comando
Desde la raíz del repositorio:
```bash
python3 main.py
```
`main.py` automatiza el proceso completo:

| Paso | Qué hace | Dónde corre |
|---|---|---|
| 1. Contenedores | `docker compose up -d --build db etl` | Computador |
| 2. Descarga | Extractores de Infraestructura, Metadata (M1 a M4) y Amenazas (A1, A2, A3, A4) que escriben los JSON/GeoJSON | Contenedor `etl` |
| 3. Importación | Un `cargar.py` por cada JSON/GeoJSON hacia PostGIS, y conteo de filas por tabla y fuente | Contenedor `etl` |
| 4. Probabilidades | `Probabilidades/generar.py`: probabilidad de falla de cada enlace y nodo por amenaza (tablas `prob_falla_*`) | Contenedor `etl` |
| 5. Web | Levanta `web`, espera a que `/health` responda, verifica las capas y la API de ruteo, y abre el navegador | Computador |

Si un extractor falla (sin internet o sin clave), se usa el último JSON disponible y el resumen final lo marca. Solo la red vial es obligatoria: sin ella el ruteo no funciona y `main.py` termina con error.

Opciones:

| Comando | Uso |
|---|---|
| `python3 main.py --sin-descarga` | Carga los JSON ya versionados, sin descargar (rápido o sin internet) |
| `python3 main.py --solo-web` | Solo levanta los contenedores y verifica la web |
| `python3 main.py --no-abrir` | No abre el navegador al terminar |
| `docker compose down` | Detiene la plataforma |

En Windows se usa `python main.py`. Si ya tienes los contenedores arriba, el pipeline también se puede ejecutar a mano con `docker compose exec etl python main.py`.

### 3. Acceder al Visualizador
`main.py` abre el sitio automáticamente. También se puede abrir en:
```text
http://localhost:8080          # visualizador Leaflet
http://localhost:8080/docs     # documentación de la API
```

### 4. Fuentes de datos
Todas las fuentes se descargan con su extractor; ninguna depende de un archivo bajado a mano:

| Grupo | Fuente | Acceso |
|---|---|---|
| Infraestructura | OpenStreetMap (OSMnx) + elevación | Libre |
| M1 Señaléticas | OpenStreetMap vía ohsome | `OHSOME_API_KEY` gratuita |
| M2 Lugares públicos | MINVU · Equipamientos (ArcGIS) | Libre |
| M3 Puentes | MOP · Vialidad y Concesiones (ArcGIS) | Libre |
| M4 Zonas de siniestros | CONASET · Base de siniestros 2020-2025 (ArcGIS) | Libre |
| A1 Incendios | CONAF (ArcGIS) | Libre |
| A2 Clima | Open-Meteo | Libre |
| A3 SOSAFE | App SOSAFE + clasificador Gemini | Credenciales en `.env` |
| A4 Accidentes, cierres y obras | TomTom Traffic API | `TOMTOM_API_KEY` gratuita |

Si una fuente no responde, su extractor conserva el último JSON del repositorio y `main.py` lo carga igual.

### 5. Planificar la inspección
En el panel **Planificación por área · flota UAV** del sitio, dibuja el área a inspeccionar (o usa toda la comuna), elige la cantidad de drones y presiona **Planificar inspección**. Cada dron recibe una parte del área con su base y recorre las calles con señalética (PARE, Ceda el paso y semáforos), volviendo a la base cuando se le acaba la batería. Las distancias se calculan con `pgr_dijkstraCostMatrix` y cada traslado se traza con `pgr_dijkstra`; las calles bloqueadas por amenazas (A1, A3, A4) se postergan, y se avisa qué salidas coinciden con mal tiempo (A2). El método, basado en Zhong et al. (2023), está documentado en [`docker/web/ruteo/README.md`](docker/web/ruteo/README.md).

El plan se calcula con hasta cuatro técnicas de ruteo, cada una en su propia capa del mapa y con su tiempo de cálculo en la leyenda: **T1** `pgr_dijkstra` por distancia (sin amenazas), **T2** modelo MILP resuelto con Gurobi, **T3** `pgr_dijkstra` con costo resiliente $\ell/(1-p)$ y **T4** la metaheurística DA-DPSO. Todas resuelven la misma instancia (calles, drones y bases), así que la tabla del panel compara directamente tiempo, kilometraje, km esperados y calles bloqueadas cruzadas. Para comprobar T2 y T4 contra la fuerza bruta: `docker compose exec web python /app/server/ruteo/verificar.py`.
