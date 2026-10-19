# Inspeccion-vial-UAV

Sistema de planificación, ruteo y visualización geoespacial para una flota de vehículos aéreos no tripulados (UAVs), orientado a la **detección e inspección de señaléticas, semáforos y elementos viales** en las comunas de **Maipú, Estación Central y Santiago** (Región Metropolitana, Chile).

---

## Estructura del Repositorio

Siguiendo la rúbrica de la Fase 2 del curso de Algoritmos de Ruteo (UDP):

```text
├── .gitignore               # Configuración de exclusión para extra/, SO, cachés e IDEs
├── docker-compose.yml       # Orquestador de contenedores (db, etl, web)
├── main.py                  # Script principal para automatizar el flujo ETL (Punto 9)
├── README.md                # Documentación general del repositorio
├── database/                # Punto 0: DDL inicial (.sql) y diagrama ER
│   ├── init.sql
│   └── README.md
├── docker/                  # Definición de imágenes Docker
│   ├── etl/                 # Contenedor con herramientas geoespaciales (GeoPandas, OSMnx)
│   └── web/                 # Contenedor con FastAPI y servidor estático Leaflet
├── Infraestructura/         # Punto 1: Extracción y transformación de red vial (nodos y aristas)
├── Metadata/                # Punto 2: Extracción y transformación de metadatos (MOP, Waze, clima)
├── Amenazas/                # Punto 3: Extracción y transformación de amenazas (jammers, zonas hostiles)
└── Sitio Web/               # Puntos 7 y 8: Visualizador Leaflet y ruta baseline (pgr_dijkstra)
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
- Docker y Docker Compose instalados.

### 2. Levantar los Servicios
```bash
docker compose up -d --build
```

### 3. Ejecutar el Pipeline ETL
```bash
docker compose exec etl python main.py
```

### 4. Acceder al Visualizador
Abrir en el navegador:
```text
http://localhost:8080
```
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
