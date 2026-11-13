# Ruteo · planificación por área con una flota de drones y rutas pgr_dijkstra

El módulo tiene dos modos:

| Modo | Endpoint | Uso |
|---|---|---|
| **Planificación por área con n drones** (`flota.py`) | `POST /api/flota` | Lo usa el sitio. El operador elige un área (o toda la comuna) y la cantidad de drones; cada dron recibe una parte del área con su base y recorre todas las calles con señalética de esa parte. |
| k rutas entre dos puntos (`dijkstra_k.py`) | `GET /api/rutas` | Modo anterior, se mantiene por compatibilidad: k rutas alternativas `pgr_dijkstra` entre un inicio y un objetivo. |

| Archivo | Contenido |
|---|---|
| `flota.py` | Planificación por área: tareas, partición entre drones, bases, orden de visita con batería y trazado con pgRouting |
| `preferencias.py` | Restricciones del usuario y sus valores por defecto (de ambos modos) |
| `datos.py` | Consultas PostGIS: nodos, amenazas por arista, restricciones de metadata, puntos a inspeccionar |
| `dijkstra_k.py` | k rutas con `pgr_dijkstra` sobre $w(e)$, por penalización iterativa |
| `metrica.py` | Resiliencia de k rutas |
| `__init__.py` | `planificar()`: orquesta el modo de k rutas |

---

## A. Planificación por área con una flota de drones (`flota.py`)

Responde al planteamiento de que el dron sale de una base, revisa una zona completa y vuelve a la base (no une dos puntos arbitrarios), y sigue a **Zhong et al. (2023)**, *A Route Planning Method for UAV Swarm Inspection of Roads Fusing Distributed Droneport Site Selection*, Sensors 23(20), 8479:

- La flota tiene un número fijo de drones y cada uno tiene su propia base (*droneport*).
- La ruta de cada dron se construye con la regla del paper: desde su posición, el dron inspecciona **el tramo pendiente más cercano**, y así sucesivamente.
- Se busca **minimizar el kilometraje total** $Dis=\sum_i dis_i$ y **equilibrar el uso** de los drones (varianza mínima de los $dis_i$); el tiempo de inspección global es el del dron que más tarda, $T=\max_i T_i$.

### A.1 Pasos

1. **Tareas.** Cada señalética M1 de los tipos elegidos (PARE, Ceda el paso, semáforos) dentro del área se asocia a la calle más cercana, a menos de 30 m. Cada calle con al menos una señal es una tarea. Las tareas sobre calles bloqueadas por amenazas vigentes en la jornada (A1, A3, A4) se **postergan** y se informan aparte.
2. **Partición entre drones.** k-medias con capacidad: la carga de cada tarea es su largo más 1,3 veces la distancia a la tarea vecina (estimación del traslado). Así cada dron recibe un área compacta con una carga parecida.
3. **Bases en recintos públicos.** El sistema lo operaría la municipalidad, así que las bases se eligen entre los lugares públicos de M2 (salud, educación, Carabineros y Bomberos, filtrables con `categorias_base`). Para cada área se elige el recinto $j$ que minimiza la suma de distancias a sus calles, ponderadas por su largo $\ell_i$ (1-mediana discreta sobre sitios candidatos, como la selección de droneports de Zhong et al.):
$$j^{*}=\arg\min_{j\in C}\sum_{i\in \text{área}} \ell_i\,\lVert p_i-c_j\rVert$$
   Dos drones no comparten recinto. El dron despega desde la calle más cercana al recinto (en el mapa, una línea punteada une el recinto con ese punto). Si un área es grande, se divide en sectores que quepan en el alcance de la batería; cada sector tiene su propio recinto (droneport distribuido) y el dron vuela de uno a otro al terminar un sector. Con `bases = centralizada` todos los drones salen de un solo recinto, para comparar. Si M2 no está cargado, la base queda en la calle más cercana a la mediana geométrica del área y el plan lo avisa.
4. **Ruta de cada dron:**
   - distancias entre la base y los extremos de sus tareas con **`pgr_dijkstraCostMatrix`**;
   - salidas con la regla del tramo pendiente más cercano, entrando por el extremo más cercano de cada calle; antes de tomar una tarea se verifica que alcance la batería para inspeccionarla y volver a la base, y si no alcanza el dron vuelve, cambia batería y empieza otra salida;
   - mejora **2-opt** dentro de cada salida (invierte tramos de la secuencia si acortan los traslados);
   - trazado de cada traslado sobre las calles con **`pgr_dijkstra`** (consulta con pares origen–destino).
5. **Equilibrio.** El ciclo partición–ruteo se repite hasta dos veces más, aumentando la carga de las áreas que resultaron más lentas, y se conserva el plan con menor $T$.
6. **Tiempos y clima.** Cada salida dura $\text{km}/v$; entre salidas se suma el cambio de batería. Cada salida tiene hora de inicio y término, y se avisa si coincide con una celda de clima A2 vigente en esa franja (el clima decide **cuándo** volar cada salida, no se usa como bloqueo de calles).

El alcance por salida es

$$C = v \cdot t_{\text{autonomía}} \cdot \left(1-\frac{\text{reserva}}{100}\right) = 10\ \tfrac{\text{m}}{\text{s}} \cdot 45\ \text{min} \cdot 0{,}7 \approx 18{,}9\ \text{km}$$

con los valores de referencia configurados para el **DJI Mavic 3E**: 45 min de autonomía máxima nominal, 10 m/s de velocidad de crucero y 30 % de reserva. DJI especifica hasta 45 min bajo condiciones de ensayo; por eso el alcance calculado es una referencia teórica, no la autonomía operativa verificada de una misión. Los 10 m/s y la reserva son supuestos del proyecto, no velocidades ni márgenes garantizados por DJI. [Especificaciones oficiales del Mavic 3E](https://enterprise.dji.com/mavic-3-enterprise/specs).

### A.2 Indicadores (Zhong et al., 2023)

| Indicador | Definición |
|---|---|
| Tiempo de inspección global $T$ | $\max_i T_i$: el dron que más tarda |
| Kilometraje total $Dis$ | $\sum_i dis_i$ |
| Eficiencia $\eta$ | km de calles a inspeccionar / $Dis$ |
| Uso equilibrado $\beta$ | $\min_i dis_i / \max_i dis_i$ |
| Repetición $\rho_i$ | km recorridos más de una vez / $dis_i$ (el paper la limita a 18 %) |

### A.3 Preferencias (`GET /api/flota/preferencias`)

| Preferencia | Por defecto | Descripción |
|---|---|---|
| `n_drones` | 3 | Drones de la flota (1 a 10) |
| `bases` | `distribuidas` | `distribuidas` o `centralizada` |
| `hora_salida` | ahora | Inicio de la jornada |
| `jornada_h` | 8 | Horas de vuelo disponibles; se avisa si $T$ las supera |
| `tipos_senal` | PARE, Ceda el paso, Semáforo | Señaléticas M1 a inspeccionar |
| `categorias_base` | Salud, educación, Carabineros, Bomberos | Recintos públicos (M2) donde se pueden instalar las bases |
| `considerar_clima` | Sí | Avisos de salidas con mal tiempo (A2) |
| `velocidad_crucero_ms` | 10 | Supuesto de velocidad de crucero (m/s) |
| `autonomia_min` | 45 | Autonomía máxima nominal de referencia del DJI Mavic 3E (min) |
| `reserva_bateria_pct` | 30 | Batería que no se usa |
| `tiempo_recarga_min` | 15 | Cambio de batería entre salidas |

### A.4 Uso de la API

```
POST /api/flota
{
  "area": {"type": "Polygon", "coordinates": [[[-70.77,-33.523],[-70.745,-33.523],[-70.745,-33.498],[-70.77,-33.498],[-70.77,-33.523]]]},
  "preferencias": {"n_drones": 4, "bases": "distribuidas"}
}
```

`area: null` planifica toda la comuna. La respuesta trae `resumen` (indicadores), `drones` (por dron: bases, calles, km, salidas con hora de inicio y aviso de clima, tiempo y $\rho$), `capas` (GeoJSON de rutas, tareas, calles postergadas, áreas y bases) y `avisos`.

### A.5 Resultados de referencia (red de Maipú, 12/11/2026)

Toda la comuna, con los valores por defecto (señaléticas M1 PARE, Ceda el paso y semáforo: 1.804 calles, 169 km a inspeccionar; 23 calles postergadas por amenazas):

| Drones | Bases | $T$ | $Dis$ | $\beta$ | Salidas |
|---|---|---|---|---|---|
| 1 | distribuidas | 23,6 h | 516 km | — | 33 |
| 3 | distribuidas | 8,2 h | 522 km | 0,89 | 34 |
| 4 | distribuidas | 5,9 h | 529 km | 0,86 | 34 |
| 6 | distribuidas | 4,4 h | 499 km | 0,61 | 32 |
| 6 | centralizada | 6,9 h | 772 km | 0,68 | 49 |

Con 4 drones la inspección completa cabe en una jornada de 8 h. Con una sola base para toda la flota, el kilometraje sube un 55 % y el tiempo crece 1,6 veces respecto de bases distribuidas, porque cada salida gasta buena parte de la batería en ir y volver. Estas cifras se obtuvieron con el mismo código ejecutado sobre los GeoJSON del repositorio, reemplazando las consultas de PostGIS/pgRouting por equivalentes en Python. Con 4 drones las bases quedan en el Liceo Reino de Dinamarca, la Escuela Básica Reina de Suecia, el Centro de Educación Técnico Profesional CODEDUC y la Escuela Básica Los Alerces de Maipú. Respecto de la entrega 2 las cifras bajan porque ya no se inspeccionan los paraderos.

---

## B. k rutas entre dos puntos (`dijkstra_k.py`, modo anterior)

Dado un par (inicio, objetivo), `GET /api/rutas` genera:

- **k rutas alternativas con `pgr_dijkstra`** sobre **pesos resilientes**, que combinan las amenazas (A1–A4) y las restricciones de metadata (M1, M4),
- las **aristas a inspeccionar** $E_R$ del corredor (señaléticas M1, priorizadas con las zonas de siniestros M4),
- la **métrica de resiliencia** del conjunto de rutas.


## 1. Modelamiento de las fallas

Cada amenaza cargada en `temp_amenazas` es un **evento de falla** con zona de afectación, vigencia y nivel de riesgo.

| Fuente | Zona de afectación | Vigencia | Tipo de falla |
|---|---|---|---|
| A1 Incendios (CONAF) | Círculo de exclusión del incendio activo | Desde el inicio hasta la extinción | Dura: bloquea (controlado: penaliza) |
| A2 Clima (Open-Meteo) | Celda de la grilla 3 × 3 | Una hora (`valido_desde`, `valido_hasta`) | Dependiente del tiempo |
| A3 SOSAFE | Geocerca circular | Mientras el reporte esté vigente | Dura: bloquea |
| A4 Waze (Cierres y obras) | Geocerca circular (50--60 m) | Mientras la alerta esté activa | Dura: bloquea |

Una arista $e$ queda afectada por una amenaza $a$ si su geometría intersecta la zona de $a$ (o está a menos de `radio_afectacion_m`, si la amenaza lo define) y $a$ está vigente en la ventana $[t_0,\,t_0+H]$, con $H$ = `horizonte_h` (2 h por defecto).

## 2. Asignación de pesos a las aristas

$$
w(e,t)=
\begin{cases}
\infty & \text{si } e \text{ toca una amenaza dura (A1, A3, A4)}\\
d(e)\cdot\rho(e)\cdot\varphi(e,t_0) & \text{en otro caso}
\end{cases}
$$

| Factor | Significado | Valores |
|---|---|---|
| $d(e)$ | Largo de la arista en km | `length_m / 1000` |
| $\rho(e)$ | Amenaza estática | 1,5 si PENALIZAR · 2,0 si POSTERGAR · 1 si no hay |
| $\varphi(e,t_0)$ | Clima a la hora de salida $t_0$ | 1,5 si MEDIO · 10 si ALTO/CRÍTICO ("bloqueo blando") · 1 si no hay |

En `pgr_dijkstra` las aristas con $w=\infty$ se entregan con `cost = -1`, que pgRouting interpreta como arista inexistente. El clima no elimina aristas, porque una celda de 7 × 6 km con lluvia dejaría a Maipú sin red: las encarece a la hora de salida, y `pgr_dijkstra` las esquiva si hay alternativa. Como `pgr_dijkstra` usa pesos estáticos, el clima se evalúa en $t_0$ (un vuelo dura minutos).

## 3. Restricciones desde la metadata (preferencias del usuario)

| Preferencia | Metadata | Conversión en restricción |
|---|---|---|
| `priorizar_siniestros` | M4 Zonas de siniestros (CONASET) | Las aristas que cruzan una zona CRITICA o ALTA suman +2 al puntaje para entrar en $E_R$ |
| `tipos_senal` | M1 Señaléticas OSM | Define qué aristas se deben inspeccionar: las que tienen una señal de esos tipos a menos de 15 m |
| `radio_corredor_m`, `max_requeridas` | — | Ancho del corredor inicio–objetivo donde se buscan las aristas a inspeccionar y tope de $\lvert E_R\rvert$ |
| `considerar_clima` | A2 | Activa o desactiva $\varphi(e,t)$ |
| `velocidad_crucero_ms`, `autonomia_km` | — | Parámetros del dron: duración estimada de cada ruta y aviso si supera la batería |

## 4. Preferencias por defecto

Si el usuario no ingresa un valor, se usa este (`GET /api/rutas/preferencias`):

| Preferencia | Por defecto |
|---|---|
| k | 2 |
| Hora de salida | Ahora |
| Priorizar zonas de siniestros | Sí |
| Señales a inspeccionar | PARE, Ceda el paso, Semáforo |
| Considerar clima por hora | Sí |
| Ancho del corredor | 300 m |
| Máximo de aristas requeridas | 30 |
| Velocidad de crucero | 10 m/s |
| Autonomía útil por dron | 18,9 km (10 m/s, 45 min nominales, 30 % de reserva) |
| Horizonte de amenazas | 2 h |

La respuesta de `/api/rutas` incluye `preferencias_por_defecto`, con la lista de campos que quedaron por defecto.

---

## 5. k rutas con `pgr_dijkstra`

1. $P_1$ = `pgr_dijkstra` (dirigido, respetando los sentidos únicos) con `cost` $= w(e)$, restringido a la región inicio–objetivo con margen.
2. Las aristas ya usadas multiplican su costo por $3^{n}$, donde $n$ es el número de rutas previas que las usaron, y se vuelve a ejecutar `pgr_dijkstra`.
3. Se repite hasta tener $k$ rutas distintas (o hasta que no aparezcan alternativas nuevas).

Los factores de cada arista se cargan en una tabla temporal de la sesión (`pesos_ruteo`), que la consulta interna de `pgr_dijkstra` lee con un `JOIN`. La penalización por uso solo sirve para que cada ejecución explore un camino distinto: todas las rutas salen de `pgr_dijkstra`.

**Por qué es resiliente:** las rutas no cruzan zonas bloqueadas, pagan más por las zonas de riesgo y la penalización por uso las obliga a recorrer calles distintas. Si una amenaza corta una ruta, las demás siguen operativas.

Para cada ruta se informa su longitud, su costo resiliente $\sum w(e)$, la duración estimada a `velocidad_crucero_ms` y si excede `autonomia_km`.

## 6. Aristas a inspeccionar $E_R$

Se toman las aristas del corredor inicio–objetivo con señales de los tipos elegidos (M1), ordenadas por puntaje (señales + 2 si cruzan una zona de siniestros crítica o alta, M4) y descartando las bloqueadas, hasta `max_requeridas`. `pgr_dijkstra` no las fuerza en la ruta: sirven para medir cuánta señalética inspecciona el conjunto de rutas (cobertura $C$).

## 7. Métrica de resiliencia de k rutas

Cada amenaza vigente en la ventana es un evento independiente que ocurre con probabilidad $p_a$: BAJO 0,05 · MEDIO 0,2 · ALTO 0,5 · CRÍTICO 0,8 (supuesto de diseño). Las celdas de clima cuentan como un evento por celda.

$$
S(P)=\prod_{a\in A(P)}(1-p_a)
\qquad
R_{sup}=\sum_{\varnothing\neq Q\subseteq\{1..k\}}(-1)^{|Q|+1}\prod_{a\in\bigcup_{i\in Q}A(P_i)}(1-p_a)
$$

$R_{sup}$ es la probabilidad **exacta** de que al menos una ruta sobreviva: por inclusión-exclusión, las amenazas compartidas cuentan una sola vez, así que rutas que pasan por la misma zona de riesgo no se cuentan como respaldo.

$$
C=\frac{\sum_{e\in E_R\cap\bigcup_i P_i} d(e)}{\sum_{e\in E_R} d(e)}
\qquad
J=\operatorname{prom}_{i<j}\frac{d(P_i\cap P_j)}{d(P_i\cup P_j)}
\qquad
R=R_{sup}\cdot C
$$

- $C$: cobertura de inspección.
- $J$: solapamiento entre rutas (0 = totalmente distintas).
- $R$: índice de resiliencia; una ruta que sobrevive pero no inspecciona no cumple la misión.

## Uso de la API

```
GET /api/rutas?inicio_lon=-70.757&inicio_lat=-33.510&objetivo_lon=-70.735&objetivo_lat=-33.490
GET /api/rutas?...&k=3&priorizar_siniestros=false&tipos_senal=senal_pare,semaforo&hora_salida=2026-10-20T15:00:00Z
GET /api/rutas/preferencias
```

Responde con `dijkstra.rutas` (GeoJSON MultiLineString por ruta), `dijkstra.metricas`, `dijkstra.metodo`, `aristas_requeridas`, `contexto` y `avisos`.
