# Ruteo · planificación por área con una flota de drones y técnicas de ruteo

El módulo tiene dos modos:

| Modo | Endpoint | Uso |
|---|---|---|
| **Planificación por área con n drones** (`flota.py`) | `POST /api/flota` | Lo usa el sitio. El operador elige un área (o toda la comuna) y la cantidad de drones; cada dron recibe una parte del área con su base y recorre todas las calles con señalética de esa parte. |
| k rutas entre dos puntos (`dijkstra_k.py`) | `GET /api/rutas` | Modo anterior, se mantiene por compatibilidad: k rutas alternativas `pgr_dijkstra` entre un inicio y un objetivo. |

| Archivo | Contenido |
|---|---|
| `flota.py` | Planificación por área: tareas, partición entre drones, bases (instancia común) y ejecución de las técnicas T1–T4 |
| `tecnicas.py` | Técnicas de ruteo (punto 6): costo resiliente, regla constructiva + 2-opt (T1/T3), split óptimo, objetivo común |
| `milp.py` | T2: modelo MILP de ruteo multi-salida, resuelto con Gurobi (licencia WLS) o HiGHS de respaldo |
| `dpso.py` | T4: metaheurística DA-DPSO con búsqueda local y reinicios |
| `verificar.py` | Comprueba T2 y T4 contra fuerza bruta y compara las formulaciones del MILP |
| `preferencias.py` | Restricciones del usuario y sus valores por defecto (de ambos modos) |
| `datos.py` | Consultas PostGIS: nodos, amenazas por arista, restricciones de metadata, puntos a inspeccionar |
| `dijkstra_k.py` | k rutas con `pgr_dijkstra` sobre $w(e)$, por penalización iterativa |
| `metrica.py` | Resiliencia de k rutas |
| `simulacion.py` | Eventos del modelo del punto 5 y sorteo reproducible por semilla (punto 7) |
| `simulacion_servicio.py` | Conserva planes en memoria, aplica el escenario común y genera capas y cruces por técnica |
| `resiliencia.py` | Cobertura esperada y realizada del servicio original, con comparación de técnicas (punto 8) |
| `__init__.py` | `planificar()`: orquesta el modo de k rutas |

---

## A. Planificación por área con una flota de drones (`flota.py`)

Responde al planteamiento de que el dron sale de una base, revisa una zona completa y vuelve a la base (no une dos puntos arbitrarios), y sigue a **Zhong et al. (2023)**, *A Route Planning Method for UAV Swarm Inspection of Roads Fusing Distributed Droneport Site Selection*, Sensors 23(20), 8479:

- La flota tiene un número fijo de drones y cada uno tiene su propia base (*droneport*).
- La ruta de cada dron se construye con la regla del paper: desde su posición, el dron inspecciona **el tramo pendiente más cercano**, y así sucesivamente.
- Se busca **minimizar el kilometraje total** $Dis=\sum_i dis_i$ y **equilibrar el uso** de los drones (varianza mínima de los $dis_i$); el tiempo de inspección global es el del dron que más tarda, $T=\max_i T_i$.

### A.1 Pasos

1. **Tareas.** Cada señalética M1 de los tipos elegidos (PARE, Ceda el paso, semáforos) dentro del área se asocia a la calle más cercana, a menos de 30 m. Cada calle con al menos una señal es una tarea. Las tareas sobre calles bloqueadas por las amenazas elegidas que estén vigentes en la jornada (incendio activo, alerta SOSAFE alta o crítica, accidente de TomTom) se **postergan** y se informan aparte. Los cierres y obras de TomTom no bloquean el vuelo (ver `Amenazas/A4/README.md`).
2. **Partición entre drones.** k-medias con capacidad: la carga de cada tarea es su largo más 1,3 veces la distancia a la tarea vecina (estimación del traslado). Así cada dron recibe un área compacta con una carga parecida.
3. **Bases en recintos públicos.** El sistema lo operaría la municipalidad, así que las bases se eligen entre los lugares públicos de M2 (salud, educación, Carabineros y Bomberos, filtrables con `categorias_base`). Para cada área se elige el recinto $j$ que minimiza la suma de distancias a sus calles, ponderadas por su largo $\ell_i$ (1-mediana discreta sobre sitios candidatos, como la selección de droneports de Zhong et al.):
$$j^{*}=\arg\min_{j\in C}\sum_{i\in \text{área}} \ell_i\,\lVert p_i-c_j\rVert$$
   Dos drones no comparten recinto. El dron despega desde la calle más cercana al recinto (en el mapa, una línea punteada une el recinto con ese punto). Si un área es grande, se divide en sectores que quepan en el alcance de la batería; cada sector tiene su propio recinto (droneport distribuido) y el dron vuela de uno a otro al terminar un sector. Con `bases = centralizada` todos los drones salen de un solo recinto, para comparar. Si M2 no está cargado, la base queda en la calle más cercana a la mediana geométrica del área y el plan lo avisa.
4. **Ruta de cada dron** (técnica T3; T1, T2 y T4 resuelven este mismo paso de otra forma, sección C):
   - costos entre la base y los extremos de sus tareas con **`pgr_dijkstraCostMatrix`**, usando el costo resiliente $c_e=\ell_e/(1-p_e)$ de la sección C.2 (calles bloqueadas fuera del grafo);
   - salidas con la regla del tramo pendiente más cercano, entrando por el extremo más cercano de cada calle; antes de tomar una tarea se verifica que alcance la batería para inspeccionarla y volver a la base, y si no alcanza el dron vuelve, cambia batería y empieza otra salida;
   - mejora **2-opt** dentro de cada salida (invierte tramos de la secuencia si acortan los traslados);
   - trazado de cada traslado sobre las calles con **`pgr_dijkstra`** (consulta con pares origen–destino).
5. **Equilibrio.** El ciclo partición–ruteo se repite hasta dos veces más, aumentando la carga de las áreas que resultaron más lentas, y se conserva el plan con menor $T$.
6. **Tiempos y clima.** Cada salida dura $\text{km}/v$; entre salidas se suma el cambio de batería. Cada salida tiene hora de inicio y término, y se avisa si coincide con una celda de clima A2 vigente en esa franja (el clima decide **cuándo** volar cada salida, no se usa como bloqueo de calles).

El alcance por salida es

$$C = v \cdot t_{\text{autonomía}} \cdot \left(1-\frac{\text{reserva}}{100}\right) = 10\ \tfrac{\text{m}}{\text{s}} \cdot 36\ \text{min} \cdot 0{,}8 \approx 17{,}3\ \text{km}$$

con los valores por defecto para el **DJI Mavic 3E**, el mismo dron del punto 5:
- **Autonomía:** DJI declara 45 min de vuelo, medidos a 32,4 km/h sin viento hasta agotar la batería, y 38 min en vuelo estacionario. Se usan 36 min, el valor de la propuesta del grupo, más conservador que ambos.
- **Reserva y velocidad:** 20 % de reserva para retorno seguro, y 10 m/s de velocidad de crucero.
- **Entre salidas:** se cambia la batería, en 15 min.

Ver las [especificaciones oficiales del Mavic 3E](https://enterprise.dji.com/mavic-3-enterprise/specs).

El alcance es una referencia de planificación, no la autonomía verificada de una misión. Los 10 m/s, la reserva y el cambio de batería son supuestos del proyecto, no valores garantizados por DJI. [Especificaciones oficiales del Mavic 3E](https://enterprise.dji.com/mavic-3-enterprise/specs).

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
| `autonomia_min` | 36 | Autonomía del DJI Mavic 3E usada en la planificación (min; DJI declara 45 a 32,4 km/h sin viento) |
| `reserva_bateria_pct` | 30 | Batería que no se usa |
| `tiempo_recarga_min` | 15 | Cambio de batería entre salidas |
| `tecnicas` | T1, T2, T3, T4 | Técnicas de ruteo a ejecutar y comparar (sección C) |
| `amenazas` | A1, A2, A3, A4 | Amenazas que bloquean calles o entran en el costo resiliente de T2–T4, y A2 en la evaluación por hora; vacía = sin amenazas |
| `limite_milp_s` | 60 | Tiempo máximo de Gurobi (T2) para toda el área, repartido entre sectores según su número de calles |
| `limite_dpso_s` | 20 | Tiempo máximo de DA-DPSO (T4) para toda el área, repartido igual |

### A.4 Uso de la API

```
POST /api/flota
{
  "area": {"type": "Polygon", "coordinates": [[[-70.77,-33.523],[-70.745,-33.523],[-70.745,-33.498],[-70.77,-33.498],[-70.77,-33.523]]]},
  "preferencias": {"n_drones": 4, "bases": "distribuidas"}
}
```

`area: null` planifica toda la comuna. La respuesta trae `resumen` (la instancia común), `capas` (GeoJSON de tareas, calles postergadas, áreas y bases, iguales para todas las técnicas), `tecnicas` (por técnica: nombre, tiempo de cálculo, indicadores, detalle del solver, drones con sus salidas y GeoJSON de rutas) y `avisos`.

### A.5 Resultados de referencia (red de Maipú, 13/11/2026, técnica T3)

Toda la comuna con los valores por defecto (Mavic 3E: 36 min, 20 % de reserva, 17,3 km por salida) y salida a las 08:00:
- 1.824 calles y 173 km a inspeccionar (señaléticas M1 PARE, Ceda el paso y semáforo).
- 4 calles postergadas, dentro de la alerta SOSAFE crítica de ese día. Los cierres de TomTom ya no bloquean.

| Drones | Bases | $T$ | $Dis$ | $\beta$ | Salidas |
|---|---|---|---|---|---|
| 1 | distribuidas | 23,4 h | 520 km | — | 33 |
| 3 | distribuidas | 8,3 h | 535 km | 0,88 | 34 |
| 4 | distribuidas | 6,1 h | 492 km | 0,66 | 32 |
| 6 | distribuidas | 3,9 h | 514 km | 0,82 | 34 |
| 6 | centralizada | 6,2 h | 795 km | 0,76 | 49 |

- Con 4 drones la inspección completa cabe en una jornada de 8 h.
- Con una sola base para toda la flota, el kilometraje sube un 55 % y el tiempo crece 1,6 veces respecto de bases distribuidas, porque cada salida gasta buena parte de la batería en ir y volver.
- Con 4 drones las bases quedan en la Séptima Compañía de Bomberos de Maipú, la Escuela Básica Los Alerces, el Colegio San Sebastián de Rinconada, el Centro de Referencia de Salud de Maipú y el CETP CODEDUC. Uno de los drones tiene dos sectores.
- Las cifras se obtuvieron con el mismo código, ejecutado sobre los GeoJSON del repositorio y reemplazando las consultas de PostGIS/pgRouting por equivalentes en Python.

---

## C. Técnicas de ruteo comparadas (punto 6: `tecnicas.py`, `milp.py`, `dpso.py`)

El sitio permite elegir una o más de cuatro técnicas; cada una queda como una capa que se puede encender o apagar, y la leyenda del mapa muestra su nombre y su tiempo de cálculo.

| Técnica | Costo de cada calle | Amenazas | Cómo arma las salidas |
|---|---|---|---|
| **T1** · `pgr_dijkstra` por distancia | largo $\ell_e$ | ninguna (línea base) | Regla del tramo pendiente más cercano con batería + 2-opt (Zhong et al., 2023) |
| **T2** · Modelo MILP con Gurobi | resiliente $c_e$ | bloqueos + $p(e)$ | Óptimo del modelo de la sección C.3, con brecha certificada |
| **T3** · `pgr_dijkstra` resiliente | resiliente $c_e$ | bloqueos + $p(e)$ | Misma regla constructiva que T1 |
| **T4** · Metaheurística DA-DPSO | resiliente $c_e$ | bloqueos + $p(e)$ | PSO discreta + split óptimo + búsqueda local (sección C.4) |

### C.1 Instancia común (diseño del experimento)

Las cuatro técnicas resuelven **el mismo subproblema**: las mismas calles a inspeccionar, la misma partición del área entre drones, las mismas bases en recintos públicos (M2) y los mismos sectores (pasos 1 a 3 de la sección A.1). Solo cambia el paso 4, cómo se rutea cada sector. Así, cualquier diferencia en los indicadores se debe a la técnica de ruteo y no a otra decisión (control de variables). La partición se calcula una vez, con T3, y se reutiliza.

En cada sector, el subproblema es: desde la base, recorrer todas las calles requeridas en salidas que empiezan y terminan en la base, sin que ninguna salida consuma más que la batería $B$, y con un cambio de batería de $\tau$ minutos entre salidas consecutivas. Es un problema de ruteo de arcos capacitado con múltiples viajes. Las cuatro técnicas se comparan con la misma función objetivo, el tiempo del dron en el sector:

$$Z=\frac{1}{60\,v}\sum_{\text{salidas}}\text{costo}+\tau\,(K-1)$$

donde $K$ es el número de salidas. T2 y T4 la minimizan directamente; T1 y T3 la construyen con una regla.

### C.2 Costo resiliente $c_e=\ell_e/(1-p_e)$

$p_e$ es la probabilidad de falla de la calle durante la jornada (punto 5, tabla `prob_falla_aristas`), combinando solo las amenazas elegidas: $p_e=1-\prod_{k}(1-p_{k}(e))$.

**Justificación.** Si cada intento de cruzar la calle falla con probabilidad $p_e$ y el dron debe repetirlo, el número de intentos sigue una distribución geométrica de media $1/(1-p_e)$. El costo esperado de la calle es entonces $\ell_e/(1-p_e)$: con $p_e=0$ vale $\ell_e$, crece de forma monótona con el riesgo y tiende a infinito cuando la calle es intransitable. Tiene unidades de metros (metros esperados), por lo que se suma directamente con la distancia sin un peso arbitrario $\lambda$, a diferencia del costo $\ell_e(1+\lambda r_e)$ de la propuesta original.

- Las calles con una amenaza que **bloquea** (incendio activo, alerta SOSAFE alta o crítica, accidente de TomTom) se eliminan del grafo (costo $-1$ en `pgr_dijkstra`). Los cierres y obras de TomTom no bloquean: restringen a los automóviles, no el espacio aéreo.
- En el modelo revisado del punto 5, $p_e$ viene de A1 (tasa histórica de incendios). A2 (clima) **no entra en el costo**. Con celdas de 7 × 6 km y escenarios de 25 km, el clima es prácticamente igual en todas las calles de un área: multiplicarlas todas por el mismo factor no cambia ninguna ruta (Dijkstra es invariante a escalar los costos). En cambio, sí agotaría la batería de forma ficticia, porque la batería se controla con el costo resiliente: con $p=0{,}99$ cada calle costaría 100 veces su largo. El clima se evalúa por la **hora de cada salida** (C.5).
- $p_e$ se acota en $0{,}99$ (la calle cuesta como máximo 100 veces su largo) para que el costo sea finito.
- La batería también se controla con el costo resiliente, lo que es conservador: el costo resiliente nunca es menor que la distancia real. Sin A2, la diferencia es pequeña ($p_{a1}\le$ 1 % en Maipú).
- Sin la tabla del punto 5, el costo es el largo y solo se aplican los bloqueos. No se inventan probabilidades a partir de los niveles BAJO a CRÍTICO.

### C.3 T2 · Modelo MILP (`milp.py`)

Cada calle $q$ se representa por dos copias orientadas, $q^+$ ($U\to V$) y $q^-$ ($V\to U$), lo que transforma el ruteo de arcos en uno de nodos (transformación CARP→VRP, Longo, Poggi de Aragão y Uchoa, 2006). Los costos $c_{ij}$ entre copias son los caminos mínimos resilientes calculados con `pgr_dijkstraCostMatrix`.

$$
\begin{aligned}
\min\ & \kappa\Big(\sum_{(i,j)} c_{ij}\,x_{ij}+\sum_a L_a z_a\Big)+\tau\Big(\sum_a x_{0a}-1\Big)\\
\text{s.a. }\ & z_{q^+}+z_{q^-}=1 && \forall q\quad\text{(cada calle una vez, en un sentido)}\\
& \textstyle\sum_j x_{aj}=z_a,\ \ \sum_j x_{ja}=z_a && \forall a\quad\text{(flujo)}\\
& \textstyle\sum_k g_{bk}=\sum_a g_{ab}+\sum_a c_{ab}x_{ab}+L_b z_b && \forall b\quad\text{(balance de energía)}\\
& (c_{0a}+L_a)\,x_{ab}\le g_{ab}\le (B-c_{ab}-L_b-c_{b0})\,x_{ab} && \forall a\to b\\
& g_{0b}=0,\quad g_{a0}\le (B-c_{a0})\,x_{a0}\\
& \textstyle\sum_a x_{0a}\ \ge\ \lceil \sum_q L_q / B\rceil && \text{(desigualdad válida)}\\
& x,z\in\{0,1\},\ g\ge 0
\end{aligned}
$$

$\kappa=1/(60v)$ convierte metros en minutos, y $g_{ab}$ es la energía ya gastada al dejar $a$ rumbo a $b$. El número de salidas $\sum_a x_{0a}$ es una variable, así que el modelo decide cuántas recargas hacer. El balance de energía impide ciclos que no pasen por la base, porque la energía crece estrictamente a lo largo de una salida.

| Decisión | Justificación |
|---|---|
| **Flujo de una mercancía** (Gavish y Graves, 1978) en vez de MTZ | Su relajación lineal es mucho más ajustada. Con HiGHS, en las mismas instancias, el modelo de flujo demuestra el óptimo de 6 calles en 0,8 s y de 7 calles en 5,7 y 24,5 s; MTZ tarda 27,7 s con 6 calles y no lo demuestra en 60 s con 7 (tabla C.6) |
| Reducción previa | Se eliminan las copias que no caben solas en una salida y los arcos $a\to b$ con $c_{0a}+L_a+c_{ab}+L_b>B-c_{b0}$. Es exacta (no cambia el óptimo) y reduce el modelo |
| Arranque en caliente con T3 | Gurobi parte de una solución factible, así que nunca entrega algo peor que T3 |
| Brecha (gap) de parada $10^{-4}$ | Es el valor por defecto de Gurobi (`MIPGap`) |
| Límite de tiempo (60 s por defecto, configurable) | El problema es NP-difícil, así que se informa el **gap certificado** $(\text{valor}-\text{cota})/\text{valor}$: si es 0, la solución es óptima. El límite se reparte entre sectores según su número de calles |
| Hasta 200 calles por sector | Con 200 calles el modelo tiene unas 160.000 binarias antes de la reducción. Sobre ese tamaño el sector conserva la solución de T3 y el resultado lo indica |
| Licencia | WLS académica (`WLSACCESSID`, `WLSSECRET`, `LICENSEID` en el `.env`). Sin ella se resuelve con HiGHS y el nombre de la técnica lo dice |

**Alcance.** T2 es exacto **para cada sector**; la partición del área y las bases son comunes a todas las técnicas (C.1). Un único MILP que decidiera también la asignación de calles a drones y la ubicación de las bases no es tratable para los cientos de calles de un área real.

### C.4 T4 · DA-DPSO (`dpso.py`)

Es una adaptación del prototipo del grupo (`Tarea3/da_dpso_entrega`).

| Componente | Qué hace | Fundamento |
|---|---|---|
| Representación | Permutación de las calles del sector | Toda solución es una concatenación de salidas, así que toda solución está representada |
| Decodificador | **Split óptimo**: corta la permutación en salidas (camino mínimo en un grafo acíclico) y elige el sentido de cada calle por programación dinámica | Prins (2004). Para una permutación fija, el corte y los sentidos son óptimos, y toda partícula es factible |
| Movimiento | $X\leftarrow c_2\otimes CR(c_1\otimes CR(w\otimes MUT(X),P_i),G)$: mutación con probabilidad $w$ (intercambio, inserción o inversión), cruce de orden con la mejor propia $P_i$ (prob. $c_1$) y con la del enjambre $G$ (prob. $c_2$) | PSO discreta de Pan, Tasgetiren y Liang (2008). El cruce de orden (Davis, 1985) conserva el orden relativo de las calles del guía |
| Adaptación dinámica (DA) | $w=\mathrm{clip}(0{,}15+0{,}45(1-\delta)+0{,}15\min(1,s/25),\,0{,}15,\,0{,}70)$, con $\delta$ la diversidad (fracción de posiciones distintas al líder) y $s$ las iteraciones sin mejora | Exploración guiada por la diversidad (Riget y Vesterstrøm, 2002). Fórmula del prototipo del grupo |
| Búsqueda local | Invertir sentido, reubicar, or-opt (cadenas de 2 y 3 calles), intercambio entre salidas y 2-opt. Se aplica al líder y a cada mejor personal a menos de 2 % del líder, y la mejora se reescribe en la permutación (lamarckiano) | Esquema memético con split de Prins (2004). El or-opt mueve juntas calles contiguas, típico de una red vial |
| Reinicios | Tras 50 iteraciones sin mejora, cada partícula pasa a ser el líder perturbado con $n/5$ mutaciones y luego mejorado con búsqueda local | Búsqueda local iterada (Lourenço, Martin y Stützle, 2003) |
| Inicialización | Una partícula es T3 y el resto, permutaciones al azar | Como Pan et al. (2008), que siembran con la heurística NEH. Garantiza que T4 nunca es peor que T3 |
| Parada | Límite de tiempo, o 3 reinicios seguidos sin mejora | — |
| Semilla aleatoria | 7 | Reproducibilidad |

**Cambios respecto del prototipo.** El prototipo tenía cinco problemas, que esta versión corrige:
- Corría Dijkstra en cada decodificación (0,16 s por partícula). Ahora usa matrices precalculadas con `pgr_dijkstraCostMatrix`.
- Su `DiGraph` perdía las aristas paralelas. Ahora cada arista se identifica por su id de la red.
- Cortaba las salidas con un prefijo voraz que, ante una tarea infactible, dejaba pendientes todas las siguientes. Ahora usa el split óptimo.
- Su modelo "exacto" enumeraba planes completos ($|Q|!\,|D|^{|Q|}$). T2 es ahora un MILP real.
- Usaba un objetivo lexicográfico con el riesgo primero. Ahora el riesgo entra en el costo resiliente y el objetivo es el mismo de T2.

### C.5 Indicadores de cada técnica

Además de los de la sección A.2:

| Indicador | Definición |
|---|---|
| Tiempo de cálculo | Lo que tarda la técnica, incluidas su matriz de costos y el trazado con `pgr_dijkstra`. T2 y T4 incluyen la matriz resiliente y la solución inicial de T3 |
| Km esperados | $\sum_{e\in\text{ruta}} \ell_e/(1-p_e)$, con todas las amenazas |
| $p$ media | Probabilidad de falla media por metro recorrido, con todas las amenazas |
| Calles bloqueadas cruzadas | Calles con una amenaza activa que bloquea y que la ruta recorre |
| Clima por salida (A2) | Mayor fracción de escenarios del ensamble no aptos entre las celdas que recorre la salida, en las horas que dura. Con 50 % o más (mayoría de escenarios) se sugiere reprogramar; desde 20 % solo se avisa |
| Brecha (T2) | $(\text{valor}-\text{cota inferior})/\text{valor}$ |

El riesgo se evalúa **siempre con todas las amenazas**, aunque la técnica haya ruteado con menos o con ninguna. Así T1 muestra el riesgo que corre al ignorarlas.

Un evento puede afectar muchas calles y nodos a la vez. La simulación del punto 7 conserva esa relación: sortea una vez por evento y comparte el resultado entre todos sus elementos y las técnicas del plan.

#### C.5.1 Simulación de fallas (punto 7)

Después de **Planificar inspección**, el panel **Simulación de fallas** permite elegir una semilla entera y presionar **Simular fallas**. El servidor genera un número uniforme $U\in[0,100)$ por evento incierto y aplica la regla:

$$\text{ocurre la falla si } U < 100p.$$

Con $p=0$ nunca ocurre; con $p=1$ siempre ocurre. La misma semilla y los mismos datos del plan reproducen los números, aunque cambie el orden de los eventos. Los bloqueos operacionales confirmados permanecen activos sin sorteo.

El escenario reutiliza las funciones del modelo de `Probabilidades/generar.py` y las fuentes cargadas en PostGIS:

- **A1:** los incendios activos son bloqueos confirmados. El riesgo futuro se representa con procesos por localización histórica de CONAF; su unión conserva la tasa Poisson $1-e^{-nH/(24D)}$ del punto 5. Un incendio sorteado afecta conjuntamente a sus calles y nodos durante la ventana simulada.
- **A2:** un sorteo por celda y hora, usando la fracción de escenarios meteorológicos no aptos calculada con la misma política del punto 5. Sin ensamble se conserva el estado determinista del pronóstico y se muestra un aviso.
- **A3 y A4:** las alertas peligrosas y accidentes activos mantienen sus bloqueos confirmados. Los cierres terrestres, las obras informativas, `p_accidente` y los índices de viento/contraste no se convierten en fallas aleatorias de vuelo.

Una falla de nodo también impide utilizar las calles incidentes. El mapa superpone aristas y nodos afectados, distingue bloqueos confirmados de fallas sorteadas y permite ocultar cada capa. Los popups y la tabla de eventos muestran la causa, probabilidad, número y umbral.

Las cuatro técnicas conservan sus recorridos originales y reciben **el mismo escenario**. La tabla adicional identifica salidas que cruzan elementos indisponibles. Para A2 se contrasta el intervalo del evento con el horario estimado de paso por cada arista, manteniendo la velocidad constante del planificador. El mapa resume los elementos afectados en algún momento de la jornada; por eso una calle coloreada no implica que todas las salidas la crucen durante la falla. Las salidas que exceden la ventana se identifican aparte.

**Restablecer simulación** retira el escenario del mapa y conserva el plan. Cambiar el área, limpiar o calcular otro plan descarta el escenario anterior. La simulación no modifica la red ni las probabilidades persistidas en la base de datos.

API:

```text
POST /api/flota                 -> respuesta habitual + plan_id
POST /api/flota/simulacion      {"plan_id": "...", "semilla": 7}
```

El servidor conserva hasta cuatro planes durante 30 minutos. El modelo de eventos se congela al evaluar el plan y se comparte con sus sorteos; se reutilizan las mismas entradas para comparar semillas. Si el plan expira, se reinicia el servidor o cambia la geometría de su red antes de cargar el modelo, hay que volver a planificar.

La respuesta incluye `semilla`, `t0`, `t1`, `eventos`, `resumen`, capas GeoJSON de nodos/aristas y el detalle de cruces por técnica. Al actualizar el servicio web, `docker compose up -d --build web` incorpora el montaje de solo lectura del modelo de probabilidades.

#### C.5.2 Resiliencia del servicio (punto 8)

La unidad de servicio son los metros de calles requeridas originalmente. Se conserva ese conjunto, incluidas las tareas postergadas y fuera de alcance, para evaluar todas las técnicas con el mismo denominador. Cada tarea aporta servicio cuando puede completarse su salida de inspección, incluido el acceso y el regreso a la base, dentro de la autonomía útil y de la jornada. Las inspecciones también dependen de los traslados necesarios para alcanzar sus bases.

Para cada salida $s$, se reúne el conjunto $A_s$ de eventos que alcanzan sus aristas o nodos durante el horario de paso, junto a los eventos de sus traslados dependientes. Se utiliza el mismo proyector temporal y catálogo del punto 7. Un evento compartido se cuenta una sola vez; los eventos probabilísticos distintos se modelan independientes. Con $L_s$ metros de tareas de esa salida:

$$S_s=\prod_{a\in A_s}(1-p_a),\qquad R_{esperada}=\frac{\sum_s L_sS_s}{L_{original}}.$$

Un bloqueo confirmado o una salida no factible hacen $S_s=0$. La métrica suma trabajo complementario de los drones: no calcula la probabilidad de que sobreviva al menos uno. R esperada representa la fracción esperada de servicio conservado bajo este modelo, no la probabilidad de terminar toda la campaña.

Al simular las fallas del punto 7 se obtiene también:

$$R_{escenario}=\frac{\text{metros de tareas cuyas salidas y dependencias se completan en ese escenario}}{L_{original}}.$$

El escenario utiliza exactamente los eventos sorteados para sus mapas y cruces; no se vuelve a sortear para evaluar R. Una salida afectada no aporta sus tareas bajo la política conservadora de salida completa. Los recorridos originales se conservan.

La cobertura previa $Q_{pre}$ es la proporción de servicio programado en salidas factibles antes de aplicar los eventos. Se informa el cambio Post−Pre como $R-Q_{pre}$, en puntos porcentuales. Los detalles muestran metros requeridos, planificados, esperados y realizados en el escenario.

La sección **Resiliencia del servicio** presenta las R esperada y del escenario para T1–T4, con su tiempo de cálculo. La leyenda del mapa también incluye nombre de técnica, cómputo y R. Se indica qué técnica tiene mayor R y los empates se conservan, comparando valores sin redondear con tolerancia $10^{-9}$. Si se ejecutan menos técnicas o falta alguna evaluación, se informa que la comparación es parcial. Las notas de T2 identifican los planes que conservaron sectores de T3 o utilizaron el solver de respaldo.

**Restablecer simulación** retira la R del escenario y mantiene la evaluación esperada del plan. Si los datos no permiten calcular una evaluación, se muestra **No disponible** con el motivo, manteniendo las rutas calculadas. El costo de ruteo y sus tiempos se conservan; la preparación del modelo y la evaluación del servicio se realizan aparte.

API: `/api/flota` añade `tecnicas[codigo].resiliencia` y `comparacion_resiliencia`; `/api/flota/simulacion` devuelve los mismos campos para la realización sorteada. Las métricas usan fracciones entre 0 y 1 y las longitudes se devuelven en metros.

### C.6 Verificación y calibración

**Exactitud.** `verificar.py` compara T2 y T4 con la fuerza bruta (todas las permutaciones decodificadas con split) en instancias de 5 a 8 calles. T2 y T4 entregan siempre el óptimo, y T2 lo certifica.

| Calles | Óptimo (fuerza bruta) | T3 | T2 (HiGHS) | Cota T2 | Tiempo T2 | T4 | Tiempo T4 |
|---|---|---|---|---|---|---|---|
| 5 | 37,318 | 38,878 | 37,318 | 37,318 | 0,2 s | 37,318 | 0,0 s |
| 6 | 44,531 | 48,902 | 44,531 | 44,531 | 0,8 s | 44,531 | 0,0 s |
| 6 | 65,533 | 65,909 | 65,533 | 65,533 | 0,4 s | 65,533 | 0,0 s |
| 7 | 48,712 | 72,232 | 48,712 | 48,712 | 6,1 s | 48,712 | 0,1 s |
| 7 | 51,778 | 55,493 | 51,778 | 51,778 | 25,4 s | 51,778 | 0,0 s |
| 8 | 50,676 | 69,922 | 50,676 | 50,676 | 3,8 s | 50,676 | 0,1 s |

Valores en minutos de la función objetivo $Z$. Las instancias son calles al azar con distancias euclidianas, que cumplen la desigualdad triangular como las de `pgr_dijkstraCostMatrix`.

**Formulación del MILP.** Tiempo hasta certificar el óptimo con HiGHS, con un límite de 60 s:

| Calles | Flujo (Gavish y Graves) | MTZ |
|---|---|---|
| 6 | 0,9 s | 27,0 s |
| 7 | 6,0 s | no lo certifica en 60 s |
| 8 | 3,7 s | no lo certifica en 60 s |
| 10 | no lo certifica en 60 s | no lo certifica en 60 s |

Gurobi es en general varias veces más rápido que HiGHS en modelos de este tipo; con la licencia WLS, `verificar.py` repite ambas tablas.

**Calibración de DA-DPSO.** Se usaron 7 sectores reales de Maipú (de 6 a 150 calles, áreas del banco de prueba). Se probaron 9 configuraciones con 3 semillas cada una (7, 11 y 13) y un límite de 5 s, o de 10 s sobre 100 calles. La tabla muestra el exceso medio sobre la referencia: el óptimo certificado por T2 (HiGHS, hasta 600 s) para $n\le 40$, y el mejor valor encontrado por cualquier configuración para 131 y 150 calles.

| Configuración | 6 | 8 | 21 | 22 | 40 | 131 | 150 |
|---|---|---|---|---|---|---|---|
| **Base**: población 30, $c_1=c_2=0{,}5$, $w$ adaptativa, búsqueda local y reinicios | 0 % | 0 % | 0 % | 0 % | 0 % | 1,87 % | 0,26 % |
| Población 15 | 0 % | 0 % | 0,20 % | 0 % | 0 % | 1,28 % | 0,26 % |
| Población 50 | 0 % | 0 % | 0 % | 0 % | 0 % | 1,92 % | 0,26 % |
| $c_1=0{,}3$, $c_2=0{,}7$ | 0 % | 0 % | 0,10 % | 0 % | 0 % | 1,92 % | 0,26 % |
| $c_1=0{,}7$, $c_2=0{,}3$ | 0 % | 0 % | 0 % | 0 % | 0 % | 1,92 % | 0,26 % |
| $w$ fija 0,15 | 0 % | 0 % | 0,20 % | 0 % | 0 % | 1,92 % | 0,26 % |
| $w$ fija 0,5 | 0 % | 0 % | 0,10 % | 0 % | 0 % | 1,92 % | 0,26 % |
| Sin búsqueda local | 0 % | 0 % | 0,30 % | 4,58 % | 4,81 % | 8,20 % | 5,93 % |
| Sin reinicios | 0 % | 0 % | 0,30 % | 3,99 % | 3,17 % | 1,73 % | 0,18 % |
| *T3 (referencia)* | *3,79 %* | *0,01 %* | *0,30 %* | *5,75 %* | *8,75 %* | *8,21 %* | *6,42 %* |

Lectura:
- Con óptimo conocido ($n\le 40$), la configuración base lo alcanza en las 15 corridas. Las variantes de población, $c_1/c_2$ o $w$ fija fallan por 0,1–0,2 % en alguna semilla de $n=21$.
- La búsqueda local y los reinicios son los componentes que más pesan: sin ellos el resultado empeora entre 3 y 8 %. La población y $c_1/c_2$ cambian poco el resultado (el método es robusto a ellos), y se dejan los valores de la configuración base.
- En 131 calles la base queda 1,9 % sobre el mejor valor encontrado, que lo halló una sola corrida con población 15.

Las cifras de esta sección y de C.7 se obtuvieron con el mismo código, ejecutado sobre los GeoJSON del repositorio y reemplazando las consultas de PostGIS/pgRouting por equivalentes en Python (sin Gurobi en ese entorno, por eso T2 usa HiGHS).

### C.7 Ejemplo (banco con datos del repositorio)

Área de 1,7 km² en el centro-oriente de Maipú (Av. 5 de Abril y Los Estandartes), con 2 drones, valores por defecto, salida a las 08:00, límite de 60 s para T2 y 20 s para T4. La instancia común tiene 75 calles a inspeccionar (8,5 km) y una base por dron: el CESFAM El Abrazo y el CETP CODEDUC.

| Técnica | $T$ | Km | $Z$ (resiliente) | Cálculo |
|---|---|---|---|---|
| T1 · distancia | 14,8 min | 17,6 | 29,4 min | 0,04 s |
| T2 · MILP (HiGHS) | 14,6 min | 17,1 | 28,5 min (gap 7,9 %) | 60 s |
| T3 · resiliente | 14,8 min | 17,6 | 29,4 min | 0,03 s |
| T4 · DA-DPSO | 13,6 min | 16,3 | **27,1 min** | 4,4 s |

Lectura:
- Ese día no había incendios activos, accidentes ni alertas SOSAFE en el área, y los cierres de TomTom ya no bloquean. Por eso T1 y T3 coinciden: sin amenazas que cambien el costo de las calles, el costo resiliente es igual al largo ($p_{a1}=0$ en esta zona).
- La diferencia entre técnicas se debe solo a la optimización. T4 mejora a T3 en 7,8 %, y T2 (HiGHS, 60 s) no alcanza a superar a T4.
- Con salida a las 14:00, todas las salidas quedan con el 100 % de los escenarios no aptos por viento, y el planificador sugiere reprogramarlas.
- El contraste entre T1 y las técnicas resilientes aparece cuando hay bloqueos en el área (incendio, alerta SOSAFE o accidente) o calles con mayor $p_{a1}$; depende de las amenazas activas el día de la ejecución.

### C.8 Referencias de las técnicas

- Davis, L. (1985). Applying adaptive algorithms to epistatic domains. *Proceedings of IJCAI 1985*, 162–164.
- Gavish, B. y Graves, S. C. (1978). *The travelling salesman problem and related problems*. Working Paper OR-078-78, Operations Research Center, MIT.
- Longo, H., Poggi de Aragão, M. y Uchoa, E. (2006). Solving capacitated arc routing problems using a transformation to the CVRP. *Computers & Operations Research*, 33(6), 1823–1837.
- Lourenço, H. R., Martin, O. C. y Stützle, T. (2003). Iterated local search. En *Handbook of Metaheuristics* (pp. 321–353). Springer.
- Pan, Q.-K., Tasgetiren, M. F. y Liang, Y.-C. (2008). A discrete particle swarm optimization algorithm for the no-wait flowshop scheduling problem. *Computers & Operations Research*, 35(9), 2807–2839.
- Prins, C. (2004). A simple and effective evolutionary algorithm for the vehicle routing problem. *Computers & Operations Research*, 31(12), 1985–2002.
- Riget, J. y Vesterstrøm, J. S. (2002). *A diversity-guided particle swarm optimizer – the ARPSO*. EVALife Technical Report 2002-02, Universidad de Aarhus.
- Zhong, X. et al. (2023). A Route Planning Method for UAV Swarm Inspection of Roads Fusing Distributed Droneport Site Selection. *Sensors*, 23(20), 8479.

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
| A3 SOSAFE | Geocerca circular | Mientras el reporte esté vigente | Dura: bloquea (ALTO o CRÍTICO) |
| A4 TomTom | Punto o tramo + radio | Mientras el incidente esté vigente | Accidente: bloquea 100 m; cierres y obras: informativos |

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
| Autonomía útil por dron | 17,3 km (10 m/s, 36 min, 20 % de reserva) |
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
