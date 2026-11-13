# Indisponibilidad de enlaces y nodos (punto 5)

Punto 5 del enunciado: el efecto de cada amenaza sobre los enlaces (calles, `network_edges`) y los nodos (intersecciones, `network_nodes`) durante la jornada de inspección $[t_0,\ t_0+H]$, para el dron del proyecto, el **DJI Mavic 3E**.

El modelo distingue tres cosas que antes se mezclaban en una sola probabilidad:

| Concepto | Columna | Qué significa |
|---|---|---|
| **Prohibición** | `bloqueado`, `motivos_bloqueo` | Un evento confirmado que alcanza el elemento. No es un sorteo con $p<1$: el dron no puede pasar |
| **Incertidumbre** | `p_total`, `p_a1`, `p_a2` | Probabilidad de que el elemento quede no disponible por un evento que aún no ocurre (incendio cercano, mal tiempo) |
| **Nueva demanda** | `p_accidente` | Probabilidad de un siniestro de tránsito, que puede derribar señales. No es una falla del vuelo, sino una calle que habría que volver a inspeccionar |

No se convierten escalas subjetivas (BAJO, MEDIO, ALTO, CRÍTICO) en probabilidades.

## Archivos

| Archivo | Rol |
|---|---|
| `generar.py` | Lee la red y las amenazas desde PostGIS, calcula la indisponibilidad y escribe las tablas `prob_falla_aristas` y `prob_falla_nodos` |
| `data/probabilidades_maipu.json` | Respaldo de la última ejecución: parámetros, resumen y una fila por elemento con registro (listas en el orden de `metadata.columnas`) |

`main.py` lo ejecuta como etapa **3/3** del pipeline, después de cargar las amenazas. También se puede correr solo: `python Probabilidades/generar.py`.

## Dron y política de vuelo (DJI Mavic 3E)

| Parámetro | Valor | Origen |
|---|---|---|
| Ráfaga preventiva | 28,8 km/h (8 m/s) | **Política conservadora del proyecto**: deja 4 m/s de margen bajo el máximo del fabricante. No es un umbral certificado |
| Resistencia máxima al viento | 12 m/s (43,2 km/h) | Ficha técnica DJI (despegue y aterrizaje) |
| Lluvia | cualquier precipitación pronosticada (> 0, resolución 0,1 mm/h) | El Mavic 3E no declara resistencia al agua |
| Visibilidad mínima de operación | 100 m | Documentación DJI; la niebla se bloquea aparte |
| Cámara gran angular | 3.840 px de ancho (video 4K), FOV 84° | Ficha técnica DJI |
| Altura de vuelo | 20 m | Supuesto de la misión |
| Señal de referencia | 60 cm de ancho, centro a 2 m de altura, a 30 m en horizontal | Hipótesis de diseño |
| Tamaño mínimo de la señal en la imagen | 32 px | Referencia experimental, no garantía de detección |
| Contraste mínimo | 80 % del contraste intrínseco | Hipótesis de calidad |

**Visibilidad objetivo derivada de la cámara.** La distancia cámara–señal es $d=\sqrt{(20-2)^2+30^2}\approx 35{,}0$ m, y la señal mide

$$\text{px}=\frac{3840\cdot 0{,}60}{2\,d\,\tan(84°/2)}\approx 36{,}6\ \text{px}\ \ge 32$$

así que la geometría alcanza para la calidad objetivo con aire limpio. Con la ley de Koschmieder, el contraste aparente a distancia $d$ con visibilidad $V$ es $C(d)/C_0=0{,}05^{\,d/V}$. Para conservar el 80 % del contraste:

$$V_{\min}=d\,\frac{\ln 0{,}05}{\ln 0{,}80}\approx 470\ \text{m}$$

Este valor reemplaza el umbral anterior de 3.000 m, que no tenía relación con la cámara.

## Modelo por amenaza

Un evento **alcanza** a un enlace si algún punto del enlace (muestreado cada 20 m) queda dentro de su geometría o a menos de su `radio_afectacion_m`.

| Amenaza | Tratamiento | Columna |
|---|---|---|
| **A1** incendio activo | Bloqueo (`incendio_activo`): zona de exclusión | `bloqueado` |
| **A1** historial de incendios | $p=1-e^{-\lambda H/24}$, con $\lambda=n/D$ incendios a menos de 2 km en los $D\ge 365$ días del historial de CONAF (proceso de Poisson). No se multiplica por la probabilidad de ignición (PI) porque eso requeriría una regresión calibrada; PI se muestra en el mapa | `p_a1` |
| **A2** clima, con ensamble | Fracción de escenarios del pronóstico por conjuntos que incumplen la política, en la **peor hora** de la jornada | `p_a2` |
| **A2** clima, sin ensamble | El pronóstico determinista solo dice apto o no apto (0/1): es el estado de un escenario, no una probabilidad. Si no es apto se trata como bloqueo (`meteorologia_no_apta`) | `bloqueado` |
| **A3** SOSAFE | Bloqueo (`seguridad_activa`) si la alerta es ALTA o CRÍTICA o trae `bloqueo_vuelo`; si no, nota informativa | `bloqueado` / `observaciones` |
| **A4** accidente activo (TomTom) | Bloqueo (`accidente_activo`) a 100 m: concentra personas y equipos de emergencia bajo la ruta (Primatesta et al., 2020). Queda además la nota de reinspección | `bloqueado` |
| **A4** cierre, obra u otro incidente | Nota: una calle cerrada a los automóviles no es espacio aéreo prohibido | `observaciones` |
| Siniestros CONASET 2020-2025 | $p=1-e^{-nH/(24\cdot 2192)}$, con $n$ siniestros asociados al enlace o nodo más cercano a menos de 30 m | `p_accidente` |

Se combinan solo las dos probabilidades de indisponibilidad, como independientes (aproximación):

$$p_{\text{total}}=1-(1-p_{a1})(1-p_{a2})$$

Es un **proxy no calibrado** de indisponibilidad futura, no una probabilidad observada de accidente del dron (`calidad = proxy_no_calibrado`; `proxy_historico` si solo hay A1).

Además se guardan dos índices que no son probabilidades:
- `riesgo_viento` $=\min\!\big(1,(\text{ráfaga}/28{,}8)^2\big)$, un índice de costo.
- `contraste_visual` $=0{,}05^{\,d/V}$, la calidad de imagen esperada.

### Pronóstico por conjuntos (A2)

`Amenazas/A2/extractor.py` descarga, para el centro de cada celda de la grilla 3 × 3, los escenarios horarios de la Ensemble API de Open-Meteo:

| Modelo | Escenarios | Variables que se usan |
|---|---|---|
| ECMWF IFS 0,25° | 51 (control + 50) | Ráfaga a 10 m y precipitación |
| NOAA GEFS | 31 (control + 30) | Visibilidad (ECMWF no la entrega en esta API) |

Para cada celda y hora:

$$p_{a2}=1-\big(1-f_{\text{ECMWF}}\big)\big(1-f_{\text{GEFS}}\big)$$

donde $f$ es la fracción de escenarios de cada modelo que incumplen la política. Como cada modelo aporta variables distintas, se combinan como independientes. Las ráfagas de GEFS no se usan porque resultaron sistemáticamente muy inferiores a las de ECMWF y del pronóstico determinista (6 a 8 km/h frente a unos 20 km/h el 13-11-2026 a mediodía).

La fracción de escenarios de un ensamble es la forma estándar de expresar un pronóstico como probabilidad. No está calibrada contra observaciones de Maipú; es un supuesto declarado.

**Peor hora.** La tabla cubre toda la jornada y la calle se cruza una sola vez, a una hora que todavía no se conoce, así que se toma $\max_h p_{a2}(h)$. El planificador (punto 6) usa en cambio la probabilidad **de la hora de cada salida**.

## Uso en el ruteo (punto 6)

- **Bloqueos.** Las calles con incendio activo, alerta SOSAFE peligrosa o accidente activo salen del grafo.
- **Costo resiliente.** $c_e=\ell_e/(1-p_e)$ usa $p_{a1}$ (y las demás amenazas con probabilidad, si se agregan).
- **Clima.** No entra en el costo de las calles: con celdas de 7 × 6 km y modelos de 25 km es prácticamente uniforme en un área. Multiplicaría todas las calles por el mismo factor, lo que no cambia ninguna ruta pero sí agotaría la batería de forma ficticia. Se evalúa por la hora de cada salida, y el planificador sugiere reprogramar las salidas con la mitad o más de los escenarios no aptos.
- **`p_accidente`.** Es la base para priorizar reinspecciones; no se usa como falla.

## Resultado

### Tablas en PostGIS

`prob_falla_aristas` (clave `edge_id`) y `prob_falla_nodos` (clave `node_id`), con una fila por elemento con algún registro:

| Columna | Descripción |
|---|---|
| `bloqueado`, `motivos_bloqueo` | Prohibición y sus causas |
| `p_total`, `p_a1`, `p_a2` | Indisponibilidad (proxy); `p_a2` es NULL sin ensamble |
| `p_a3`, `p_a4` | NULL: SOSAFE y TomTom se tratan como bloqueo o nota |
| `p_accidente` | Nueva demanda de inspección (CONASET) |
| `riesgo_viento`, `contraste_visual` | Índices de costo y de calidad de imagen |
| `calidad`, `observaciones` | Tipo de estimación y notas |
| `t0`, `t1` | Jornada evaluada |

La API las entrega en `GET /api/probabilidades?elemento=aristas|nodos&minimo=0.001&amenaza=A1|A2|accidente` (los bloqueados siempre se incluyen). El sitio las muestra en el grupo **Indisponibilidad (punto 5)**: los bloqueados en negro y el resto con colores por `p_total`.

### Variables de entorno

| Variable | Defecto | Descripción |
|---|---|---|
| `PROB_HORIZONTE_H` | `8` | Duración de la jornada $H$ en horas |
| `PROB_T0` | ahora | Inicio de la jornada (ISO 8601) |

### Ejemplo: 13-11-2026, red de 17.568 enlaces

Datos del repositorio de ese día y escenarios reales de ECMWF para la tarde:

| Jornada (hora de Chile) | Enlaces bloqueados | $p_{a2}$ máxima | Enlaces con $p_{a1}>0$ (máx.) | Enlaces con $p_{\text{accidente}}>0$ (máx.) |
|---|---|---|---|---|
| 07:00–10:00 | 44 (alerta SOSAFE crítica) | sin escenarios no aptos | 10.794 (0,4 %) | 2.252 (1,1 %) |
| 12:00–20:00 | 44 | 1,00 | 10.794 (1,1 %) | 2.252 (3,0 %) |

- De 14:00 a 18:00, entre el 96 y el 100 % de los 51 escenarios de ECMWF pronostican ráfagas sobre 28,8 km/h en Maipú. A las 12:00 lo hace el 22 %.
- Con la política preventiva del Mavic 3E, las tardes de viento de Santiago no se pueden volar: la probabilidad indica **cuándo** inspeccionar.
- Ninguno de los 9 incidentes de TomTom de ese día bloquea: 8 cierres y 1 obra.

Con el modelo anterior (solo el pronóstico determinista), las mismas ráfagas habrían bloqueado los 17.568 enlaces durante toda la jornada.

## Supuestos y limitaciones

- La política (8 m/s, lluvia nula, 80 % de contraste, 32 px) es conservadora y de diseño; debe validarse con vuelos y detecciones reales.
- $p_{a1}$ y $p_{a2}$ se combinan como independientes. El ensamble no está calibrado para Maipú, y las ráfagas a 10 m no representan exactamente el viento a 20 m entre edificios.
- Los radios de afectación (2 km para el historial de incendios, 100 m para accidentes, 30 m para siniestros) son supuestos de diseño.
- Sin ensamble, el clima queda como bloqueo determinista de toda la jornada, y el extractor lo advierte.

## Referencias

- DJI. *Mavic 3 Enterprise Specs*: 45 min de vuelo (32,4 km/h sin viento), resistencia al viento 12 m/s, FOV 84°, video 3.840 × 2.160. https://enterprise.dji.com/mavic-3-enterprise/specs
- Koschmieder, H. (1924). Theorie der horizontalen Sichtweite. *Beiträge zur Physik der freien Atmosphäre*, 12, 33–53.
- Open-Meteo. *Ensemble API*. https://open-meteo.com/en/docs/ensemble-api
- Primatesta, S., Rizzo, A. y la Cour-Harbo, A. (2020). Ground risk map for unmanned aircraft in urban environments. *Journal of Intelligent & Robotic Systems*, 97, 489–509.
- CONAF. Situación actual y pronóstico de incendios. https://www.conaf.cl/incendios/situacion-actual-y-pronostico-de-incendios/
- CONASET. Portal de datos abiertos. https://mapas-conaset.opendata.arcgis.com/
