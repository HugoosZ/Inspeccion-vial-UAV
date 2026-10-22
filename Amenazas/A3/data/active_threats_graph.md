# Documentación de Estructura de Datos: `active_threats_graph.json`
### Documentación de Archivos GeoJSON / JSON

Este documento detalla la especificación técnica, estructura y diccionario de datos del archivo [`active_threats_graph.json`](data/active_threats_graph.json), generado por el clasificador IA.

---

## 1. Resumen y Propósito del Archivo

* **Nombre del archivo**: `active_threats_graph.json`
* **Ubicación**: `Amenazas/A3/data/active_threats_graph.json`
* **Formato**: JSON estándar (Array de objetos)
* **Propósito**: Proporcionar una estructura tabular limpia, liviana y sin ruido con las **amenazas activas confirmadas** para modificar dinámicamente los pesos o cortar aristas en el grafo de navegación vial.

---

## 2. Ejemplo JSON

```json
[
  {
    "id": 12034739,
    "uuid": "c572768c-a42e-5dec-a2e0-4ab3bbeb3d61",
    "categoria": "INCENDIO_COLUMNA_TERMICA",
    "nivel_riesgo": "ALTO",
    "latitud": -33.480072,
    "longitud": -70.744834,
    "coordenadas": [
      -33.480072,
      -70.744834
    ],
    "radio_buffer_metros": 500,
    "accion_grafo": "BLOQUEAR_ARISTAS",
    "factor_penalizacion": "INF",
    "descripcion": "Quema ilegal , cochinos y demás con peligro de propagación",
    "justificacion": "Humo denso y columnas térmicas que desestabilizan el control de actitud del dron."
  }
]
```

---

## 3. Diccionario de Parámetros

| Parámetro | Tipo de Dato | Ejemplo | Explicación Técnica y Uso en el Ruteo |
| :--- | :---: | :--- | :--- |
| **`id`** | `Integer` | `12034739` | Identificador numérico único del reporte en SOSAFE. |
| **`uuid`** | `String` | `"c572768c-..."` | Identificador UUID global del reporte para trazabilidad unívoca. |
| **`categoria`** | `String` | `"INCENDIO_COLUMNA_TERMICA"` | Tipo de amenaza clasificada (`PIROTECNIA_FUEGOS_ARTIFICIALES`, `DISPAROS_BALACERA`, `OBSTACULO_CABLES_POSTES`, `AERONAVE_EMERGENCIA`, `INCENDIO_COLUMNA_TERMICA`). |
| **`nivel_riesgo`** | `String` | `"ALTO"` | Severidad asignada por la IA: `CRITICO`, `ALTO`, `MEDIO`. |
| **`latitud`** | `Float` | `-33.480072` | Latitud geográfica del centro de la amenaza (WGS 84). |
| **`longitud`** | `Float` | `-70.744834` | Longitud geográfica del centro de la amenaza (WGS 84). |
| **`coordenadas`** | `Array[Float]` | `[-33.480072, -70.744834]` | Tupla `[latitud, longitud]` lista para funciones de distancia (KD-Tree, Haversine, scipy). |
| **`radio_buffer_metros`** | `Integer` | `500` | Radio métrico de la zona de peligro alrededor del punto central. |
| **`accion_grafo`** | `String` | `"BLOQUEAR_ARISTAS"` | Directiva para el algoritmo de ruteo: `"BLOQUEAR_ARISTAS"` (arista eliminada o costo infinito) o `"PENALIZAR_COSTO"` (aumento de costo multiplicativo). |
| **`factor_penalizacion`** | `String / Float` | `"INF"` o `10.0` | Multiplicador de impedancia para la función de costo en el grafo de navegación. |
| **`descripcion`** | `String` | `"Quema ilegal..."` | Texto original de la alerta comunitaria. |
| **`justificacion`** | `String` | `"Humo denso..."` | Argumentación técnica de seguridad aeronáutica emitida por la IA. |

---

*Nota: Este archivo de documentación fue elaborado con el apoyo de Inteligencia Artificial (IA).*

