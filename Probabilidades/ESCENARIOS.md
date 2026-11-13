# Contrato de los puntos 5, 7 y 8

En este piloto, **falla del plan** significa que un evento obliga a revisar el
plan original. Se distingue el efecto: indisponibilidad local, suspensión
meteorológica o nueva demanda. Esto es una decisión del grupo; el enunciado
habla de falla de enlaces y nodos, por lo que A4 se presenta expresamente como
una extensión de demanda, no como un enlace averiado.

| Fuente | Punto 5 | Acción al ocurrir en el punto 7 |
|---|---|---|
| A1 incendios | Eventos activos y probabilidad histórica existente | Excluir zona y recalcular; tareas dentro de ella quedan pendientes |
| A2 clima | Máximo regional de las fracciones de escenarios horarios no aptos; estado determinista si falta ensamble | Un único sorteo regional por intervalo suspende las salidas coincidentes, sin buscar otra calle para evadirlo |
| A3 SOSAFE | Alertas peligrosas activas y escenarios futuros supuestos | Excluir zona y recalcular, igual que A1 |
| A4 TomTom | Accidentes activos y escenarios futuros supuestos | Añadir una inspección por arista asociada; se atiende primero al llegar a su base/sector |

Los cierres y obras terrestres siguen como información. No se modela presencia
de personas como bloqueo A4. Esto no es una autorización de vuelo real.

## Probabilidades que se están suponiendo

`escenario.json` es la fuente compartida por el generador del punto 5 y la
simulación. **No hay historial para calibrar A3/A4**. Los valores iniciales
sirven para experimentos, no son frecuencias observadas en Maipú:

| Parámetro | Valor inicial | Interpretación |
|---|---|---|
| A3 reportes/hora | 0,5 | Tasa supuesta sobre TODA la red cargada |
| A3 fracción crítica | 0,25 | Probabilidad condicional supuesta de alerta peligrosa |
| A4 reportes/hora | 0,5 | Tasa supuesta sobre TODA la red cargada |
| A4 fracción accidentes | 0,25 | Probabilidad condicional supuesta de nueva inspección |
| Celda espacial | 1.000 m | Distribución uniforme entre celdas con calles |
| Radio A3 sintético | 100 m | Zona supuesta alrededor del punto candidato |

Para cada fuente, con tasa `lambda`, fracción `q`, horizonte `H` y `C` celdas:

`p_celda = 1 - exp(-lambda * q * H / C)`.

Es la probabilidad de al menos un evento relevante bajo un proceso de Poisson
supuesto. Con los valores iniciales, en 8 horas hay una media de un evento
relevante por fuente en toda la red, no uno por calle. Cada celda elige un
punto vial representativo. A3 afecta su radio; A4 solicita revisar esa arista.
Las localizaciones sintéticas se identifican con `:supuesto:` y no se presentan
como reportes reales. Dos o más eventos de la misma fuente y celda se colapsan
en una ocurrencia. Las tasas no se extrapolan de una captura instantánea.

Las marginales de aristas/nodos combinan los eventos que los alcanzan.
`p_a3` y `p_a4` ya no son NULL. `p_total` combina A1–A4 como índice no calibrado
de alteración del plan, **no como probabilidad de cierre o caída del dron**.
Además A2 conserva el máximo horario en la tabla, por lo que `p_total` no es una
probabilidad temporal conjunta exacta de toda la jornada. El sorteo utiliza
los eventos individuales y sus intervalos, no ese índice agregado.

A4 nunca encarece el costo para alejar al dron. T2–T4 utilizan A1/A3 para el
costo resiliente; A2 se aplica al horario y A4 modifica las tareas. El campo
histórico CONASET `p_accidente` se mantiene separado y no duplica A4.

Para estudiar sensibilidad se pueden variar tasas y fracciones en el JSON y
repetir con varias semillas. Cada consulta calcula de nuevo P5 con su hora,
duración y configuración, y conserva esa instantánea para P7/P8. Cambiar el
JSON afecta a los planes nuevos; un plan existente conserva sus probabilidades,
eventos y firma `id_modelo`. P5 del plan proyecta las mismas fuentes seleccionadas
que P7 (una fuente desactivada deja de contribuir al índice). Las tablas persistidas son la referencia del último
pipeline; la capa P5 del sitio usa `plan_id` cuando hay un plan seleccionado.

## Sorteo y recuperación

Un único sorteo por evento se comparte entre las cuatro técnicas y todos sus
enlaces/nodos. `U` es uniforme en `[0,100)` y ocurre si `U < 100*p`. Un evento
activo confirmado no vuelve a sortearse. Las amenazas desactivadas en las
preferencias no entran al escenario; desactivar clima excluye A2.

Se conservan los eventos A1/A3 de toda la red para que un desvío fuera del
recorrido original tampoco atraviese un peligro conocido. A4 se limita al
área solicitada; A2 se selecciona por el área y recorridos de operación iniciales.

La implementación es una **replanificación antes del despegue**: el escenario
se conoce completo para comparar planes alternativos. No modela detección
en vuelo, posición instantánea ni batería restante en el momento del aviso.
Las zonas A1/A3 se excluyen conservadoramente durante toda la jornada. Un
nodo bloqueado elimina sus enlaces incidentes. Esto también se impone a T1.

Se conservan las bases, el orden de sectores y las asignaciones del plan
original. Se vuelven a ejecutar T1, T2, T3 y T4 sobre la instancia modificada;
no se etiqueta como Gurobi un simple desvío con Dijkstra. Los respaldos de T2
(HiGHS o solución T3) siguen informándose. T2/T4 tienen límites de tiempo:
la semilla reproduce eventos, pero no garantiza idénticos tiempos o resultados
de un solver limitado por tiempo de reloj.

Las cuatro técnicas comparten una red de vuelo bidireccional: `oneway`
describe tránsito vehicular y no restringe el movimiento del dron. La elección
de una orientación de tarea en el MILP (`z_a`) también permite ambos sentidos.
El planificador rechaza una red con costos negativos de sentido en la
infraestructura para evitar mezclar traslados dirigidos con tareas reversibles.
La cobertura sigue siendo por tramo; no certifica observación de ambas caras
de una señal ni deduce su orientación a partir del sentido vehicular.

A4 se asocia al área solicitada, añade tareas o prioriza una ya requerida.
Varios incidentes asociados a la misma arista generan una sola inspección en
esta evaluación previa al vuelo. Si esa arista coincide con un bloqueo A1/A3,
la seguridad prevalece y la demanda A4 queda pendiente. No se inventa que hay
una señal registrada en una calle nueva: se cuenta como solicitud de revisión.

A2 suspende cualquier salida cuyo intervalo se solape con un evento
meteorológico del área de operación. Es una política conservadora compartida
por las técnicas; no se aprovechan diferencias entre calles para esquivar el
clima. Las celdas meteorológicas coincidentes comparten un solo evento regional
con probabilidad igual al máximo de sus probabilidades. Esto evita que ampliar
el área multiplique la probabilidad por sorteos espaciales independientes:
nueve celdas con p=20 % mantienen p regional=20 %. Si alguna celda contiene un
pronóstico determinista no apto, la suspensión regional es determinista en ese
intervalo. Se particiona por todas las fronteras horarias para no duplicar
eventos simultáneos; intervalos distintos siguen siendo eventos independientes.
El máximo es una aproximación conservadora y no una calibración conjunta del
clima regional. Las salidas posteriores conservan su horario. Si no se ejecuta un
traslado entre bases, las salidas dependientes no se cuentan como realizadas.
No se programa automáticamente el día siguiente. El horizonte actual es una
jornada de hasta 24 h; esto no implementa una campaña dinámica de cinco días.

## Resiliencia y cumplimiento (punto 8)

La métrica principal conserva la unidad existente: señales registradas del
servicio original. No se retiran del denominador las tareas bloqueadas,
inalcanzables, suspendidas o fuera de jornada:

`R_recuperada = señales originales en salidas ejecutables del plan recuperado / señales originales requeridas`.

Una salida debe tener camino completo, retorno a base, alcance dentro de la
batería, horario dentro de la jornada y traslados previos ejecutables. No se
cuentan señales dos veces. Se informa también:

- `R_sin_recuperacion`: servicio que conserva el plan fijo bajo el mismo sorteo.
- `ganancia_recuperacion = R_recuperada - R_sin_recuperacion`.
- `cumplimiento_A4`: aristas de nueva inspección atendidas / aristas A4 solicitadas.
  Si no hay demanda, es NULL (no se informa un 100 % ficticio).
- Tareas originales y A4 pendientes, salidas suspendidas, diferencia de
  kilómetros planificados y tiempo de recálculo por técnica.

La comparación principal selecciona la mayor R recuperada y conserva empates.
**No demuestra superioridad en A4:** se debe consultar también su cumplimiento.
Se mantiene R esperada del plan fijo como referencia SIN recuperación; no se
presenta como esperanza del algoritmo adaptativo. No hay simulación Monte
Carlo de recuperación ni integral temporal de recuperación en este piloto.
Una sola semilla permite comparar ese escenario, no establecer un ganador
universal. Los tiempos de recálculo incluyen preparación común y trazado.

La interfaz conserva los originales y permite alternar a rutas recuperadas;
los checkboxes de T1–T4 siguen funcionando. Las salidas suspendidas se listan
pero no se dibujan como vuelos ejecutables. A4 se representa en verde.

## Ejecución

Después de actualizar el código, regenerar las probabilidades con
`python Probabilidades/generar.py` dentro del entorno ETL (o ejecutar el
pipeline habitual), reiniciar la web y calcular un plan nuevo. El endpoint
`POST /api/flota/simulacion` mantiene `{plan_id, semilla}` y devuelve además
las rutas recalculadas y el detalle de recuperación para cada técnica.
El sorteo y la replanificación no escriben la red ni probabilidades en la BD.
