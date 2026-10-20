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
│       └── ruteo/           # Dijkstra k rutas, TDRPP-GA y métrica de resiliencia
├── Infraestructura/         # Extracción y transformación de red vial (nodos y aristas)
├── Metadata/                # Extracción y transformación de metadatos (MOP, Waze, clima)
├── Amenazas/                # Extracción y transformación de amenazas (SOSAFE, clima, zonas hostiles)
└── Sitio Web/               # Visualizador Leaflet y comparación de rutas (pgr_dijkstra vs TDRPP-GA)
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
- Opcional: copiar `.env.example` a `.env` y completar las claves (`OHSOME_API_KEY`, `FIRMS_MAP_KEY`, SOSAFE, Gemini). Sin ellas, esos pasos se omiten y se cargan los JSON que ya están en el repositorio.

### 2. Ejecutar todo con un comando
Desde la raíz del repositorio:
```bash
python3 main.py
```
`main.py` automatiza el proceso completo:

| Paso | Qué hace | Dónde corre |
|---|---|---|
| 1. Contenedores | `docker compose up -d --build db etl` | Computador |
| 2. Descarga | Extractores de Infraestructura, Metadata (M1, M3, M5) y Amenazas (T2, T3, T5, T6) que escriben los JSON/GeoJSON | Contenedor `etl` |
| 3. Importación | Un `cargar.py` por cada JSON/GeoJSON hacia PostGIS, y conteo de filas por tabla y fuente | Contenedor `etl` |
| 4. Web | Levanta `web`, espera a que `/health` responda, verifica las capas y la API de ruteo, y abre el navegador | Computador |

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

### 4. Fuentes estáticas
M5 (Censo 2024) se carga desde `Metadata/M5/manzanas_maipu_completo.zip`; para regenerarlo hace falta la cartografía del INE (`Cartografia_censo2024_Pais.gpkg`, no versionada por su tamaño). M6 (aeródromos DGAC) se carga desde `Metadata/M6/aerodromos.geojson`.

### 5. Comparar rutas
En el panel **Planificación de rutas** del sitio, haz clic en el mapa para fijar el inicio y el objetivo, ajusta las restricciones (u ocupa las por defecto) y presiona **Calcular rutas**. Se muestran k rutas de `pgr_dijkstra` (solo distancia) y k rutas del **TDRPP-GA** (algoritmo genético para el Time-Dependent Rural Postman Problem, Xin et al., 2022), con su métrica de resiliencia y el diferencial $\Delta R$. El modelo completo está documentado en [`docker/web/ruteo/README.md`](docker/web/ruteo/README.md).

---

## Nota sobre los datos del Censo 2024 (Metadata 5)

En la carpeta `Metadata/M5/` encontrarás el archivo `manzanas_maipu_completo.zip`. 
Este archivo contiene el **GeoJSON final** con la geometría y los datos demográficos de las **5.875 manzanas de Maipú**, cruzados con el Censo 2024.

**¿Cómo usarlo?**
1. Descarga el archivo `.zip` y descomprímelo en tu computador.
2. Tendrás un archivo `.geojson` con todos los datos listos para tu algoritmo de ruteo.

**¿Cómo regenerarlo?**
Si necesitas volver a generarlo (porque actualizaste los datos o quieres ver el proceso), sigue estos pasos:
1. Abre una terminal y entra a la carpeta:
   cd Metadata/M5
2. Ejecuta el script:
    python procesar_censo.py
(Asegúrate de tener el GeoPackage original Cartografia_censo2024_Pais.gpkg en la misma carpeta para que el script funcione)
