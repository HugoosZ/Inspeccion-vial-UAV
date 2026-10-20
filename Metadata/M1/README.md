# Paraderos de Maipú

Este módulo contiene la extracción, filtrado y documentación de los 942 paraderos de transporte público correspondientes a la comuna de Maipú.

## 1. Descripción

Los archivos `paraderos_maipu.json` y `paraderos_maipu.csv` contienen el catastro filtrado de 942 paraderos de transporte público correspondientes a la comuna de Maipú (Zona I). Representan la ubicación georreferenciada, identificación operativa y estado de infraestructura de los puntos de parada dentro de la comuna.

## 2. Fuente y extracción

- **Fuente:** Catastro comunal de paraderos de transporte público (con registros de origen e intervención asociados a SERVIU y UC).
- **Formato de salida:** JSON (`paraderos_maipu.json`) y CSV (`paraderos_maipu.csv`).
- **Área de interés:** Comuna de Maipú (`comuna = "MAIPé"`), correspondiente a la Zona I (`zona = "I"`).
- **Elementos consultados:** 942 registros filtrados de puntos de parada con identificador secuencial de ruteo (`ID_Ruteo` del 1 al 942).
- **Clasificaciones principales:** Por tipo de parada (`PS`, `ET`, `PD`), estado operativo (`ACTIVO`, `INACTIVO`, `ELIMINADO`) y condición de infraestructura (`CON REFUGIO`, `SIN REFUGIO`, `SàLO SE¥AL`, `SIN AND N`, entre otros).

## 3. Estructura general

El archivo `paraderos_maipu.json` se organiza en un objeto raíz que incluye el conteo total de registros (`total_filtrados_maipu`: 942) y una lista `features`. Cada elemento de `features` está compuesto por:

- `ID_Ruteo`: identificador correlativo asignado para tareas de ruteo y análisis.
- `attributes`: diccionario con los atributos técnicos, viales, administrativos y de infraestructura del paradero.
- `geometry`: coordenadas espaciales del paradero expresadas en latitud y longitud (`latitud`, `longitud`, `x_metro`, `y_metro`).

Por su parte, el archivo `paraderos_maipu.csv` presenta una vista tabular simplificada de los 942 registros con 7 columnas esenciales: `ID`, `Codigo`, `Latitud`, `Longitud`, `Tipo`, `Estado` y `Comuna`.

## 4. Atributos principales

### Archivo JSON (`paraderos_maipu.json`)

| Campo | Descripción |
|---|---|
| `ID_Ruteo` | Identificador secuencial único dentro del conjunto filtrado (1 a 942) |
| `FID` / `id` | Identificadores originales del registro en la base catastral |
| `codigo` | Código técnico del paradero (ej. `L-13-44-15-SN`) |
| `simt` | Código de parada para el usuario (ej. `PI1028`) |
| `tipo` | Tipología del paradero (`PS`: Parada Simple, `ET`: Estación de Transbordo, `PD`: Parada Doble) |
| `clasificac` | Estado administrativo/operativo (`ACTIVO`, `INACTIVO`, `ELIMINADO`) |
| `comuna` / `zona` | Comuna registrada y zona de transporte (`Zona I`) |
| `eje` | Calle o avenida principal donde se ubica el paradero |
| `desde` / `hacia` | Calles de referencia que delimitan el tramo vial |
| `sentido` | Sentido de circulación vial (`S-N`, `N-S`, `P-O`, `O-P`) |
| `nombre_ust` | Nombre estandarizado de la intersección o hito de parada |
| `x` / `y` | Coordenadas planas proyectadas (UTM, en metros) |
| `zona_paga` | Indicador de zona paga (`0` o `1`) |
| `corredor` / `ubica_corr` | Número de corredor de transporte y ubicación relativa (`INTERNO`, `EXTERNO`, etc.) |
| `rts` / `tipo_rts` | Cantidad y tipología del refugio transitorio (ej. `Refugio 4(m)`, `Refugio 9(m)`) |
| `rm` / `estado_rm` | Presencia de refugio mantenido (`0` o `1`) y su estado (`BUENO`, `REGULAR`, `MALO`) |
| `estado` | Condición general de infraestructura (`CON REFUGIO`, `SIN REFUGIO`, `SàLO SE¥AL`, etc.) |
| `existe_se` | Entidad, fecha y tipo de intervención de la señal (`SERVIU`, `UC`, instalación, reposición o retiro) |
| `geometry` | Coordenadas geográficas (`latitud`, `longitud`, `x_metro`, `y_metro`) |

El objeto `attributes` también conserva campos complementarios de trazabilidad histórica como `fila_super`, `fila_infer`, `grupo_de_p`, `identifica`, `n__et`, `codigo_ant`, `fecha_fich`, `cod_sol_pa` y `comentario`.

### Archivo CSV (`paraderos_maipu.csv`)

| Campo | Descripción |
|---|---|
| `ID` | Identificador correlativo equivalente a `ID_Ruteo` |
| `Codigo` | Código técnico del paradero (`codigo`) |
| `Latitud` | Coordenada geográfica de latitud (`geometry.latitud`) |
| `Longitud` | Coordenada geográfica de longitud (`geometry.longitud`) |
| `Tipo` | Tipología de parada (`PS`, `ET`, `PD`) |
| `Estado` | Condición física del paradero (`CON REFUGIO`, `SIN REFUGIO`, etc.) |
| `Comuna` | Nombre de la comuna asociada al registro |

## 5. Consideraciones

- Algunos atributos opcionales contienen cadenas vacías (`""`) o guiones (`"-"`) cuando el paradero no registra esa característica (por ejemplo, `simt` en paraderos inactivos, `corredor` o `comentario`).
- La base de datos incluye tanto paraderos vigentes (`ACTIVO`: 848) como registros históricos fuera de servicio (`INACTIVO`: 82 y `ELIMINADO`: 12), lo cual debe filtrarse en caso de requerir únicamente paradas operativas.
- Los textos de origen presentan caracteres heredados de codificación antigua en tildes y eñes (por ejemplo, `MAIPé` por `MAIPÚ`, `SàLO SE¥AL` por `SÓLO SEÑAL` o `BORGO¥O` por `BORGOÑO`), aspecto a considerar al realizar búsquedas por texto exacto.
```
