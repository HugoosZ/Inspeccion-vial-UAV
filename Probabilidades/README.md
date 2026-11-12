# Probabilidad de falla por enlace y nodo

Punto 5 del enunciado: cada amenaza se modela como una **probabilidad de falla** de los enlaces (calles, `network_edges`) y nodos (intersecciones, `network_nodes`) de la red. Un enlace o nodo *falla* si queda inutilizable para el dron durante la jornada de inspección $[t_0,\ t_0+H]$.

## Archivos

| Archivo | Rol |
|---|---|
| `generar.py` | Lee la red y las amenazas desde PostGIS, calcula las probabilidades y escribe las tablas |
| `data/probabilidades_maipu.json` | Respaldo de la última ejecución: parámetros, resumen y probabilidad de cada enlace y nodo |

`main.py` lo ejecuta como etapa **3/3** del pipeline, después de cargar las amenazas. También se puede correr solo: `python Probabilidades/generar.py`.

## Modelo

Para cada enlace o nodo $x$ y cada amenaza $k \in \{A1, A2, A3, A4\}$ se calcula $p_k(x)$. Suponiendo amenazas independientes:

$$p(x) = 1 - \prod_{k}\bigl(1 - p_k(x)\bigr)$$

Dentro de una misma amenaza, los eventos que alcanzan a $x$ también se combinan como independientes, salvo el clima (ver A2). Un evento *alcanza* a un enlace si algún punto del enlace (muestreado cada 20 m) queda dentro de su geometría o a menos de su `radio_afectacion_m`, con la misma regla que usa el ruteo.

Dos componentes usan tasas históricas, con un proceso de Poisson: si en $D$ días ocurrieron $n$ eventos que afectan a $x$, la probabilidad de al menos uno durante una jornada de $H$ horas es

$$p = 1 - e^{-\lambda H/24},\qquad \lambda = n/D$$

### A1 · Incendios (CONAF)

| Componente | Fórmula | Origen de los valores |
|---|---|---|
| Incendio activo que alcanza $x$ | $p = P_{\text{estado}}$: En combate 0,9 · Foco o Bajo observación 0,6 · Controlado 0,3 | Supuesto de diseño, ordenado según la gravedad del estado de CONAF |
| Tasa histórica | $\lambda = n/D$, con $n$ incendios a menos de 2 km de $x$ en las dos temporadas de CONAF ($D \ge 365$ días), modulada por la probabilidad de ignición del día: $p = 1 - e^{-\lambda \frac{H}{24}\frac{PI}{0{,}5}}$ | Registros de CONAF; $PI$ = pronóstico de CONAF para la celda. Un $PI$ de 50 % deja la tasa sin cambio |

### A2 · Clima (Open-Meteo)

Para cada celda y hora con amenaza, una rampa lineal entre el umbral en que empieza el riesgo y el umbral en que el vuelo es imposible:

$$r(v) = \min\!\left(1,\ \max\!\left(0,\ \frac{v - v_{\text{riesgo}}}{v_{\text{imposible}} - v_{\text{riesgo}}}\right)\right)$$

| Variable | Riesgo | Imposible |
|---|---|---|
| Ráfaga | 30 km/h | 43 km/h |
| Precipitación | 0,1 mm/h | 0,5 mm/h |
| Visibilidad | 3.000 m | 1.000 m (escala invertida) |

Los umbrales son los de la amenaza A2 y siguen a Gao et al. (2021): los drones comunes no vuelan con viento sobre unos 10 m/s (36 km/h) ni con lluvia sobre 0–1 mm/h. Por hora, $p_h = 1 - \prod (1 - r_i)$. El enlace se cruza una sola vez en la jornada, así que se toma la **peor hora**, $p_{A2} = \max_h p_h$, en vez de unir todas las horas.

### A3 · SOSAFE

$p = P_{\text{nivel}}$ dentro de la geocerca: BAJO 0,05 · MEDIO 0,2 · ALTO 0,5 · CRÍTICO 0,8. Es el mismo supuesto de diseño que usa la métrica de resiliencia del ruteo.

### A4 · Accidentes, cierres y obras (TomTom + CONASET)

| Componente | Fórmula | Origen de los valores |
|---|---|---|
| Incidente TomTom vigente | $p = P_{\text{ocurrencia}} \cdot P_{\text{acción}}$ | `probabilityOfOccurrence` de TomTom: certain 1 · probable 0,75 · risk_of 0,4 · improbable 0,1. Acción: bloquea 1 · penaliza 0,5 (supuesto) |
| Tasa histórica de siniestros | $\lambda = n/2.192$, con $n$ siniestros CONASET 2020-2025 asociados a $x$ (el enlace y el nodo más cercanos, a menos de 30 m) | Base de siniestros de CONASET (M4) |

## Resultado

### Tablas en PostGIS

`prob_falla_aristas` (clave `edge_id`) y `prob_falla_nodos` (clave `node_id`), con una fila por elemento con $p > 0$:

| Columna | Descripción |
|---|---|
| `p_total` | Probabilidad combinada $p(x)$ |
| `p_a1` … `p_a4` | Probabilidad por amenaza |
| `n_amenazas` | Eventos que alcanzan al elemento |
| `detalle` | Hasta 6 aportes mayores: fuente, amenaza, tipo y $p$ |
| `t0`, `t1` | Jornada evaluada |

La API las entrega en `GET /api/probabilidades?elemento=aristas|nodos&minimo=0.01&amenaza=A1..A4` y el sitio las muestra en el grupo **Probabilidad de falla**, con colores por rango.

### Variables de entorno

| Variable | Defecto | Descripción |
|---|---|---|
| `PROB_HORIZONTE_H` | `8` | Duración de la jornada $H$ en horas |
| `PROB_T0` | ahora | Inicio de la jornada (ISO 8601) |

### Ejemplo (datos del repositorio, jornada 12-11-2026 15:00 a 23:00)

| | Enlaces | Nodos |
|---|---|---|
| Con $p > 0$ | 15.491 de 17.568 | 11.287 de 12.768 |
| $p$ media / máxima | 4,9 % / 15,7 % | 5,0 % / 15,5 % |
| Afectados por A1 (tasa histórica) | 10.794 (máx. 1,1 %) | 7.652 |
| Afectados por A2 (ráfagas de 32–34 km/h) | 7.307 (máx. 15,4 %) | 5.415 |
| Afectados por A4 (historial CONASET) | 2.252 (máx. 3,0 %) | 1.871 |

Ese día no había incendios activos, reportes SOSAFE vigentes ni incidentes TomTom cargados; con ellos, los enlaces alcanzados suben a 50–100 %. El cálculo completo toma unos 3 s.

## Supuestos y limitaciones

- Las amenazas se tratan como independientes entre sí.
- $P_{\text{estado}}$ (A1), $P_{\text{nivel}}$ (A3) y $P_{\text{acción}}$ (A4) son supuestos de diseño configurables en `generar.py`; los demás valores salen de los datos (CONAF, Open-Meteo, TomTom, CONASET).
- La tasa de siniestros solo considera los siniestros a menos de 30 m de la red.

## Referencias

- Gao, M., Hugenholtz, C. H., Fox, T. A., Kucharczyk, M., Barchyn, T. E. y Nesbit, P. R. (2021). Weather constraints on global drone flyability. *Scientific Reports*, 11, 12092. https://doi.org/10.1038/s41598-021-91325-w
- TomTom. Traffic Incidents API, Incident Details. https://docs.tomtom.com/traffic-api/documentation/traffic-incidents/incident-details
- CONAF. Situación actual y pronóstico de incendios. https://www.conaf.cl/incendios/situacion-actual-y-pronostico-de-incendios/
- CONASET. Portal de datos abiertos. https://mapas-conaset.opendata.arcgis.com/
