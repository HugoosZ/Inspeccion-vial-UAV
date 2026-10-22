# Censo 2024 por Manzana de Maipú

Este módulo contiene la extracción, cruce espacial y documentación de los datos demográficos y de vulnerabilidad social del Censo de Población y Vivienda 2024 (CPV24) del Instituto Nacional de Estadísticas (INE), integrados geográficamente con los polígonos censales a nivel de manzana de la comuna de Maipú (`CUT 13119`).

# Documentación: Censo 2024 por manzana de Maipú

## 1. Descripción

El archivo `manzanas_maipu_completo.geojson` (distribuido dentro de `manzanas_maipu_completo.zip`) y la planilla `datos_censo_maipu_original.xlsx` contienen la información territorial y demográfica a nivel de manzana censal de la comuna de Maipú, Región Metropolitana de Santiago (`CUT = 13119`). Estos datos permiten caracterizar la densidad poblacional y la distribución etaria en el territorio para apoyar la priorización en algoritmos de ruteo.

## 2. Fuente y extracción

- **Fuente:** Instituto Nacional de Estadísticas (INE) — Censo de Población y Vivienda 2024 (CPV24).
- **Insumos de origen:** Cartografía censal nacional (`Cartografia_censo2024_Pais.gpkg`) y tabulados comunales (`datos_censo_maipu_original.xlsx`).
- **Formato de salida:** GeoJSON comprimido (`manzanas_maipu_completo.zip` -> `manzanas_maipu_completo.geojson`).
- **Área de interés:** Comuna de Maipú (`COD_REGION = 13`, `PROVINCIA = "SANTIAGO"`, `CUT = 13119`, `COMUNA = "MAIPÚ"`).
- **Elementos consultados:** Polígonos de manzanas censales vinculadas mediante la llave única `MANZENT`.

## 3. Estructura general

El archivo `manzanas_maipu_completo.geojson` utiliza el estándar `FeatureCollection`. Cada elemento dentro de `features` corresponde a una manzana censal (`Feature`) compuesta por:

- `properties`: atributos de identificación geográfica-administrativa del INE y variables demográficas asociadas al Censo 2024.
- `geometry`: geometría poligonal (`Polygon` o `MultiPolygon`) que delimita el contorno espacial de la manzana en coordenadas geográficas.

Por su parte, `datos_censo_maipu_original.xlsx` almacena la matriz tabular original de atributos, cuya nomenclatura se encuentra documentada en `Diccionario_variables_geograficas_CPV24.xlsx`.

## 4. Atributos principales

| Campo | Descripción |
|---|---|
| `CONTENEDOR_COMUNAL` | Indicador de contenedor comunal en la estructura cartográfica del INE |
| `COD_REGION` / `REGION` | Código (`13`) y nombre de la región (`METROPOLITANA DE SANTIAGO`) |
| `PROVINCIA` | Nombre de la provincia (`SANTIAGO`) |
| `CUT` / `COMUNA` | Código Único Territorial comunal (`13119`) y nombre de la comuna (`MAIPÚ`) |
| `AREA_C` | Código de área censal (`1`: Urbano, `2`: Rural) |
| `MANZENT` | Código único nacional de manzana/entidad censal (llave primaria de 14 dígitos para el cruce GIS) |
| `DISTRITO` / `COD_DISTRITO` | Nombre del distrito censal (ej. `CINCO DE ABRIL`) y su código numérico |
| `LOCALIDAD` / `COD_LOCALIDAD` | Nombre de la localidad (ej. `GRAN SANTIAGO`) y su código identificador |
| `COD_ZONA` | Código de la zona censal dentro del distrito |
| `ENTIDAD` / `COD_ENTIDAD` | Nombre de la entidad (`MAIPÚ`) y su código correspondiente |
| `COD_MANZANA` | Número identificador de la manzana dentro de su zona censal |
| `TIPO_MZ` | Clasificación del tipo de manzana (ej. `URBANO`) |
| `CATEGORIA` / `COD_CATEGORIA` | Categoría de la entidad censal (ej. `Ciudad`, código `1`) |
| `ID_ENTIDAD` / `ID_LOCALIDAD` | Identificadores compuestos de nivel entidad y localidad |
| `ID_DISTRITO` / `ID_ZONA` | Identificadores compuestos de nivel distrito (ej. `1311901`) y zona censal |
| `n_per` | Total de personas residentes censadas en la manzana |
| `n_hombres` | Total de hombres censados en la manzana |
| `n_mujeres` | Total de mujeres censadas en la manzana |
| `n_edad_*` | Columnas de desagregación demográfica por tramos de edad (`n_edad_0_5`, `n_edad_6_13`, etc.) |
| `geometry` | Polígono espacial de la manzana censal (presente en el archivo GeoJSON) |

El conjunto de datos conserva además el resto de las variables socio-demográficas incluidas en el reporte comunal del CPV24, cuyo detalle completo puede consultarse en el archivo `Diccionario_variables_geograficas_CPV24.xlsx`.

## 5. Consideraciones

- **Formato de identificadores extensos:** En herramientas de hojas de cálculo como Excel, códigos numéricos largos como `MANZENT`, `ID_ENTIDAD`, `ID_LOCALIDAD` e `ID_ZONA` suelen visualizarse en notación científica (por ejemplo, `1,31E+13`). Al procesar los datos en Python (`procesar_censo.py`), deben tratarse como cadenas de texto (`str`) o enteros de 64 bits (`int64`) para evitar pérdida de precisión durante el cruce (`merge`/`join`).
- **Secreto estadístico y valores en cero:** Algunas manzanas pueden registrar valores igual a `0` o celdas con reserva estadística en subgrupos específicos cuando cuentan con baja o nula población residente (como zonas industriales, áreas verdes o equipamientos).
- **Compresión de datos:** Debido al tamaño del detalle poligonal y la cantidad de columnas por manzana, el archivo GeoJSON final se distribuye empaquetado en `manzanas_maipu_completo.zip`.
