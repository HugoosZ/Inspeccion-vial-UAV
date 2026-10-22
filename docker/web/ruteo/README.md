# Ruteo resiliente · pgr_dijkstra

Módulo que usa la API (`GET /api/rutas`) y el sitio Leaflet para generar, dado un par (inicio, objetivo):

- **k rutas alternativas con `pgr_dijkstra`** sobre **pesos resilientes**, que combinan las amenazas (A1–A4) y las restricciones de metadata (M1, M4),
- las **aristas a inspeccionar** $E_R$ del corredor (señales M2, priorizadas con M3),
- la **métrica de resiliencia** del conjunto de rutas.

| Archivo | Contenido |
|---|---|
| `preferencias.py` | Restricciones del usuario y sus valores por defecto |
| `datos.py` | Consultas PostGIS: nodos, amenazas por arista, restricciones de metadata, aristas requeridas |
| `dijkstra_k.py` | k rutas con `pgr_dijkstra` sobre $w(e)$, por penalización iterativa |
| `metrica.py` | Resiliencia de k rutas |
| `__init__.py` | `planificar()`: orquesta todo y arma la respuesta GeoJSON |

---

## 1. Modelamiento de las fallas

Cada amenaza cargada en `temp_amenazas` es un **evento de falla** con zona de afectación, vigencia y nivel de riesgo.

| Fuente | Zona de afectación | Vigencia | Tipo de falla |
|---|---|---|---|
| A1 Incendios (GDACS, FIRMS) | Polígono de exclusión | Del evento o de la detección | Dura: bloquea |
| A2 Clima (Open-Meteo) | Celda de la grilla 3 × 3 | Una hora (`valido_desde`, `valido_hasta`) | Dependiente del tiempo |
| A3 SOSAFE | Geocerca circular | Mientras el reporte esté vigente | Dura: bloquea |
| A4 Waze (Cierres y obras) | Geocerca circular (50--60 m) | Mientras la alerta esté activa | Dura: bloquea |

Una arista $e$ queda afectada por una amenaza $a$ si su geometría intersecta la zona de $a$ (o está a menos de `radio_afectacion_m`, si la amenaza lo define) y $a$ está vigente en la ventana $[t_0,\,t_0+H]$, con $H$ = `horizonte_h` (2 h por defecto).

## 2. Asignación de pesos a las aristas

$$
w(e,t)=
\begin{cases}
\infty & \text{si } e \text{ toca una amenaza dura (A1, A3, A4) o está cerca de un aeródromo (M4)}\\
d(e)\cdot\rho(e)\cdot\pi(e)\cdot\varphi(e,t_0) & \text{en otro caso}
\end{cases}
$$

| Factor | Significado | Valores |
|---|---|---|
| $d(e)$ | Largo de la arista en km | `length_m / 1000` |
| $\rho(e)$ | Amenaza estática | 1,5 si PENALIZAR · 2,0 si POSTERGAR · 1 si no hay |
| $\pi(e)$ | Restricción de metadata | 1,3 junto a paraderos · ∞ cerca de aeródromos · 1 en otro caso |
| $\varphi(e,t_0)$ | Clima a la hora de salida $t_0$ | 1,5 si MEDIO · 10 si ALTO/CRÍTICO ("bloqueo blando") · 1 si no hay |

En `pgr_dijkstra` las aristas con $w=\infty$ se entregan con `cost = -1`, que pgRouting interpreta como arista inexistente. El clima no elimina aristas, porque una celda de 7 × 6 km con lluvia dejaría a Maipú sin red: las encarece a la hora de salida, y `pgr_dijkstra` las esquiva si hay alternativa. Como `pgr_dijkstra` usa pesos estáticos, el clima se evalúa en $t_0$ (un vuelo dura minutos).

## 3. Restricciones desde la metadata (preferencias del usuario)

| Preferencia | Metadata | Conversión en restricción |
|---|---|---|
| `evitar_aerodromos` + `radio_aerodromo_m` | M4 Aeródromos DGAC | Se eliminan las aristas a menos del radio ($\pi=\infty$) |
| `evitar_paraderos` | M1 Paraderos | Aristas a menos de 25 m de un paradero: $\pi = 1{,}3$, porque los buses detenidos tapan la señalética |
| `priorizar_vulnerables` | M3 Censo, índice $V_m$ | Las aristas junto a manzanas con $V_m>0$ suman +2 al puntaje para entrar en $E_R$ |
| `tipos_senal` | M2 Controles viales OSM | Define qué aristas se deben inspeccionar: las que tienen una señal de esos tipos a menos de 15 m |
| `radio_corredor_m`, `max_requeridas` | — | Ancho del corredor inicio–objetivo donde se buscan las aristas a inspeccionar y tope de $\lvert E_R\rvert$ |
| `considerar_clima` | A2 | Activa o desactiva $\varphi(e,t)$ |
| `velocidad_crucero_ms`, `autonomia_km` | — | Parámetros del dron: duración estimada de cada ruta y aviso si supera la batería |

## 4. Preferencias por defecto

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
| Velocidad de crucero | 10 m/s |
| Autonomía por dron | 20 km |
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

Se toman las aristas del corredor inicio–objetivo con señales de los tipos elegidos (M2), ordenadas por puntaje (señales + 2 si están junto a una manzana vulnerable, M3) y descartando las bloqueadas, hasta `max_requeridas`. `pgr_dijkstra` no las fuerza en la ruta: sirven para medir cuánta señalética inspecciona el conjunto de rutas (cobertura $C$).

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
GET /api/rutas?...&k=3&evitar_paraderos=false&tipos_senal=senal_pare,semaforo&hora_salida=2026-10-20T15:00:00Z
GET /api/rutas/preferencias
```

Responde con `dijkstra.rutas` (GeoJSON MultiLineString por ruta), `dijkstra.metricas`, `dijkstra.metodo`, `aristas_requeridas`, `contexto` y `avisos`.
