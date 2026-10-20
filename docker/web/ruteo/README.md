# Ruteo resiliente · pgr_dijkstra vs TDRPP-GA

Módulo que usa la API (`GET /api/rutas`) y el sitio Leaflet para generar, con el mismo par (inicio, objetivo), las mismas amenazas y el mismo conjunto de aristas a inspeccionar:

- **k rutas con `pgr_dijkstra`**, solo por distancia en km (rúbrica 8),
- **k rutas con el TDRPP-GA**, con pesos resilientes (rúbrica 9),
- la **métrica de resiliencia** de cada conjunto y su **diferencial** (rúbrica 10 y 11.2).

| Archivo | Contenido |
|---|---|
| `preferencias.py` | Restricciones del usuario y sus valores por defecto (6.1, 6.2) |
| `datos.py` | Consultas PostGIS: grafo, amenazas por arista, restricciones de metadata, aristas requeridas |
| `dijkstra_k.py` | k rutas con `pgr_dijkstra` por penalización iterativa (8) |
| `tdrpp_ga.py` | Algoritmo genético TDRPP-GA con 2-Opt (7, 9) |
| `metrica.py` | Resiliencia de k rutas y diferencial (10, 11.2) |
| `__init__.py` | `planificar()`: orquesta todo y arma la respuesta GeoJSON |

---

## 5.1 Modelamiento de las fallas

Cada amenaza cargada en `temp_amenazas` es un **evento de falla** con zona de afectación, vigencia y nivel de riesgo.

| Fuente | Zona de afectación | Vigencia | Tipo de falla |
|---|---|---|---|
| T2 Incendios (GDACS, FIRMS) | Polígono de exclusión | Del evento o de la detección | Dura: bloquea |
| T3 Clima (Open-Meteo) | Celda de la grilla 3 × 3 | Una hora (`valido_desde`, `valido_hasta`) | Dependiente del tiempo |
| T5 Lluvias (Municipalidad) | Punto + `radio_afectacion_m` | Solo reportes activos | Dura (ALTO/CRÍTICO) o blanda (POSTERGAR) |
| T6 SOSAFE | Geocerca circular | Mientras el reporte esté vigente | Dura: bloquea |

Una arista $e$ queda afectada por una amenaza $a$ si su geometría intersecta la zona de $a$ (o está a menos del radio, en T5) y $a$ está vigente en la ventana $[t_0,\,t_0+H]$, con $H$ = `horizonte_h` (2 h por defecto).

## 5.2 Asignación de pesos a las aristas

$$
w(e,t)=
\begin{cases}
\infty & \text{si } e \text{ toca una amenaza dura (T2, T6, T5 ALTO/CRÍTICO)}\\
d(e)\cdot\rho(e)\cdot\pi(e)\cdot\varphi(e,t) & \text{en otro caso}
\end{cases}
$$

| Factor | Significado | Valores |
|---|---|---|
| $d(e)$ | Largo de la arista en km | `length_m / 1000` |
| $\rho(e)$ | Amenaza estática | 1,5 si PENALIZAR · 2,0 si POSTERGAR (T5 MEDIO) · 1 si no hay |
| $\pi(e)$ | Restricción de metadata (6.1) | 1,3 junto a paraderos · ∞ cerca de aeródromos · 1 en otro caso |
| $\varphi(e,t)$ | Clima en la hora en que el dron cruza la arista | 1,5 si MEDIO · 10 si ALTO/CRÍTICO ("bloqueo blando") · 1 si no hay |

El clima no elimina aristas, porque una celda de 7 × 6 km con lluvia dejaría a Maipú sin red. Las encarece en esa hora, y el genético puede reordenar la ruta para cruzarlas en otro momento.

## 6.1 Restricciones desde la metadata (preferencias del usuario)

| Preferencia | Metadata | Conversión en restricción |
|---|---|---|
| `evitar_aerodromos` + `radio_aerodromo_m` | M6 Aeródromos DGAC | Se eliminan las aristas a menos del radio ($\pi=\infty$) |
| `evitar_paraderos` | M1 Paraderos | Aristas a menos de 25 m de un paradero: $\pi = 1{,}3$, porque los buses detenidos tapan la señalética |
| `priorizar_vulnerables` | M5 Censo, índice $V_m$ | Las aristas junto a manzanas con $V_m>0$ suman +2 al puntaje para entrar en $E_R$ |
| `tipos_senal` | M3 Controles viales OSM | Define qué aristas se deben inspeccionar: las que tienen una señal de esos tipos a menos de 15 m |
| `radio_corredor_m`, `max_requeridas` | — | Ancho del corredor inicio–objetivo donde se buscan aristas y tope de $\lvert E_R\rvert$ |
| `considerar_clima` | T3 | Activa o desactiva $\varphi(e,t)$ |
| `velocidad_*`, `autonomia_km` | — | Parámetros del dron: tiempo de llegada a cada arista y límite de batería |

## 6.2 Preferencias por defecto

Si el usuario no ingresa un valor, se usa este (`GET /api/rutas/preferencias`):

| Preferencia | Por defecto |
|---|---|
| k | 2 |
| Hora de salida | Ahora |
| Evitar aeródromos / radio | Sí / 2.000 m (supuesto; verificar en la DAN 151 de la DGAC) |
| Evitar oclusión en paraderos | Sí (factor 1,3) |
| Priorizar manzanas vulnerables | Sí |
| Señales a inspeccionar | PARE, Ceda el paso, Semáforo |
| Considerar clima por hora | Sí |
| Ancho del corredor | 300 m |
| Máximo de aristas requeridas | 30 |
| Velocidad de crucero / de inspección | 10 m/s / 5 m/s |
| Autonomía por dron | 20 km |
| Horizonte de amenazas | 2 h |

La respuesta de `/api/rutas` incluye `preferencias_por_defecto`, con la lista de campos que quedaron por defecto.

---

## 7.1 Algoritmo de ruteo y paper

**TDRPP-GA**: algoritmo genético para el *Time-Dependent Rural Postman Problem*.

- **Base:** Xin, J., Yu, B., D'Ariano, A., Wang, H. y Wang, M. (2022). *Time-dependent rural postman problem: time-space network formulation and genetic algorithm*. Operational Research, 22(3), 2943–2972. https://doi.org/10.1007/s12351-021-00639-0
- **Mejora local 2-Opt:** Croes, G. A. (1958). *A method for solving traveling-salesman problems*. Operations Research, 6(6), 791–812.
- Las referencias de energía multi-UAV (Wang y Qian) y de arquitectura offline/online (Li et al.) deben completarse con su DOI.

## 7.2 Funcionamiento

Inspeccionar señalética es un **Rural Postman Problem**: hay que recorrer un subconjunto de aristas obligatorias $E_R$ (las que tienen señales) usando cualquier otra arista para moverse entre ellas. Es *time-dependent* porque el costo de cada arista depende de la hora en que el dron la cruza (clima T3 por hora).

1. **Aristas requeridas.** Se toman las aristas del corredor inicio–objetivo con señales de los tipos elegidos, ordenadas por puntaje (señales + vulnerabilidad) y descartando las bloqueadas, hasta `max_requeridas`.
2. **Fase offline: caminos de transición.** Se calculan los caminos de costo mínimo $w$ entre todos los extremos de $E_R$, el inicio y el objetivo. Por la propiedad de optimalidad del paper (con tiempos FIFO, la transición óptima entre dos aristas requeridas es un camino mínimo), el genético solo decide el **orden** y el **sentido** de las aristas requeridas.
3. **Cromosoma** $=(\text{permutación de } E_R,\ \text{sentido de cada arista},\ k-1 \text{ cortes})$. Los cortes reparten la permutación entre los $k$ drones; cada dron va del inicio al objetivo pasando por sus aristas.
4. **Evaluación dependiente del tiempo.** Se simula cada dron desde $t_0$: transiciones a velocidad de crucero e inspección a velocidad de inspección. Cada arista se cobra con $w(e,t_e)$ según su hora de llegada $t_e$.
$$
F=\sum_{i=1}^{k}C(P_i)+\beta\max_i C(P_i)+\lambda_{bat}\sum_{i}\max\bigl(0,\,L(P_i)-L_{max}\bigr)+\lambda_{vac}\cdot\#\{\text{drones sin aristas}\}
$$
   con $\beta=0{,}5$ (balance entre drones), $\lambda_{bat}=5$ por km sobre la autonomía y $\lambda_{vac}=50$.
5. **Operadores.** Población de 80 (una semilla ordenada por proyección sobre la recta inicio→objetivo y el resto aleatoria), selección por torneo de 3, cruce de orden (OX) con probabilidad 0,9, mutaciones por intercambio, inversión, cambio de sentido y desplazamiento de corte (0,3 cada una), elitismo de 2 y hasta 400 generaciones o 8 s.
6. **2-Opt.** Sobre la mejor solución se invierten subsecuencias de cada dron (cambiando también el sentido) mientras haya mejora.
7. **Resultado.** $k$ rutas que cubren $E_R$ repartido entre drones, evitan las amenazas duras y esquivan las horas de mal tiempo. La semilla aleatoria es fija (42), así que los resultados son reproducibles.

**Por qué es resiliente:** las rutas no cruzan zonas bloqueadas, pagan más por las zonas de riesgo y, al repartir $E_R$, los drones recorren calles distintas. Si una amenaza corta una ruta, las demás siguen operativas.

## 8. k rutas con `pgr_dijkstra` (peor caso)

1. $P_1$ = `pgr_dijkstra` con `cost = length_m / 1000`, solo distancia en km.
2. Las aristas ya usadas multiplican su costo por $3^{n}$, donde $n$ es el número de rutas previas que las usaron, y se vuelve a ejecutar `pgr_dijkstra`.
3. Se repite hasta tener $k$ rutas distintas.

No usa metadata ni amenazas: es la línea base de comparación.

## 10. Métrica de resiliencia de k rutas

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

## 11.2 Diferencial de resiliencia

$$
\Delta R = R_{\text{TDRPP-GA}}-R_{\text{Dijkstra}}
$$

Se informan también $\Delta R_{sup}$, $\Delta C$ y $\Delta J$ (en puntos porcentuales en el sitio). Un $\Delta R>0$ indica que el TDRPP-GA entrega un conjunto de rutas más resiliente para la misión de inspección.

## Uso de la API

```
GET /api/rutas?inicio_lon=-70.757&inicio_lat=-33.510&objetivo_lon=-70.735&objetivo_lat=-33.490
GET /api/rutas?...&k=3&evitar_paraderos=false&tipos_senal=senal_pare,semaforo&hora_salida=2026-10-20T15:00:00Z
GET /api/rutas/preferencias
```

Responde con `dijkstra.rutas` y `tdrpp_ga.rutas` (GeoJSON MultiLineString por ruta), `aristas_requeridas`, `metricas` de cada algoritmo, `diferencial`, `contexto` y `avisos`.
