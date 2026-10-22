# Módulo de Amenazas SOSAFE y Clasificación Semántica con IA

Este módulo ingesta reportes ciudadanos en tiempo real desde la API V3 de **SOSAFE**, filtra los eventos territoriales de la comuna de **Maipú** y utiliza **Google Gemini AI** (o un clasificador de respaldo heurístico) con un *System Prompt* de seguridad operacional aeronáutica para identificar amenazas reales a la navegación de drones (UAVs) y descartar el ruido comunitario cotidiano.

---

## 1. Estructura de Archivos

```text
Amenazas/A3/
├── .env                          # Credenciales SOSAFE y Gemini API Key (ignorado en git)
├── .env.example                  # Plantilla de configuración con variables
├── ALCANCE.md                    # Delimitación formal de alcance actual vs. trabajo futuro
├── README.md                     # Documentación de uso y arquitectura
├── extractor.py                  # Extracción HTTP GET a SOSAFE API V3 y filtro por comuna
├── classifier.py                 # Clasificador semántico (Gemini AI + fallback heurístico + GeoJSON)
├── run.py                        # Orquestador del flujo completo
└── data/
    ├── raw_reports.json          # Respuesta cruda devuelta por la API SOSAFE
    ├── filtered_reports.json     # Reportes pertenecientes a la comuna de Maipú
    ├── classified_threats.json   # Auditoría: amenazas y ruido catalogados con justificación
    ├── active_threats_graph.json # JSON limpio de amenazas activas para el algoritmo de ruteo
    ├── active_threats_graph.md   # Documentación y diccionario del JSON
    ├── threats_geofences.geojson # Geocercas circulares (No-Fly Zones) para Leaflet y PostGIS
    └── threats_geofences.md      # Documentación y diccionario del GeoJSON
```

---

## 2. Configuración (`.env`)

Copia la plantilla `.env.example` a `.env` y configura los valores:

```bash
cp .env.example .env
```

Campos principales en `.env`:
* **`SOSAFE_ACCESS_TOKEN`**, **`SOSAFE_USER_UUID`**, **`SOSAFE_SESSION`**, **`SOSAFE_DEVICE_ID`**: Credenciales capturadas desde la app móvil SOSAFE.
* **`TARGET_COMMUNE`**: Comuna a filtrar (por defecto `Maipú`).
* **`GEMINI_MODEL`**: Modelo de IA a utilizar (por defecto `gemini-3.8-flash`).
* **`GEMINI_API_KEY`**: Clave de API de Google Gemini (opcional; si se omite o falla, el sistema aplica automáticamente el clasificador de respaldo sin interrumpir el proceso).

---

## 3. Criterio del Prompt de Sistema (Seguridad de Vuelo UAV)

El clasificador analiza semánticamente el título y la descripción de cada reporte bajo un criterio técnico estricto para drones volando a muy baja cota urbana (20 metros de altitud para inspección cercana):

### Amenazas Reales Confirmadas (`es_amenaza: true`)
1. **`PIROTECNIA_FUEGOS_ARTIFICIALES`**: Fuegos artificiales o pirotecnia (detonan entre 15 y 50 m de altitud con impacto destructivo directo sobre el dron). Buffer: 400 m.
2. **`DISPAROS_BALACERA`**: Balaceras activas y disparos de arma de fuego con proyectiles balísticos. Buffer: 400 m.
3. **`OBSTACULO_CABLES_POSTES`**: Cables eléctricos cortados o colgando, postes caídos o vehículos enganchados (riesgo crítico: a 20 m el dron vuela a la altura directa del tendido aéreo urbano). Buffer: 300 m.
4. **`AERONAVE_EMERGENCIA`**: Helicópteros policiales o de rescate operando a baja cota o aterrizando en calzada (turbulencia severa de rotor downwash y obligación normativa). Buffer: 500 m.
5. **`INCENDIO_COLUMNA_TERMICA`**: Incendios activos con columnas térmicas violentas y humo negro denso a nivel de calle. Buffer: 500 m.

### Ruido Comunitario Descartado (`es_amenaza: false`)
* Disputas vecinales, música fuerte o niños jugando ("niños tirando limones").
* Comercio informal, mascotas extraviadas o personas en situación de calle.
* Microbasurales, baches o alcantarillas sin tapa a nivel de suelo.

> Para mayor detalle sobre el alcance actual y el tratamiento de señales de tránsito caídas como futura recompensa de inspección, consulta `ALCANCE.md`.

---

## 4. Ejecución

Para ejecutar el flujo completo (extracción + clasificación + geocercas):

```bash
cd Inspeccion-vial-UAV/Amenazas/A3
python3 run.py
```

Si deseas ejecutar solo la extracción o solo la clasificación por separado:

```bash
# Solo extracción HTTP
python3 extractor.py

# Solo clasificación sobre los datos ya descargados
python3 classifier.py
```
