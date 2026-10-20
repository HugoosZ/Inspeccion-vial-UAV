
# Metadata 5: Censo 2024 por Manzana

Este módulo contiene los datos de vulnerabilidad social extraídos del Censo 2024 del Instituto Nacional de Estadísticas (INE), cruzados geográficamente con los polígonos de las manzanas de Maipú.

## Archivos incluidos

**procesar_censo.py**: Script ETL que lee el GeoPackage nacional del INE, filtra exclusivamente las manzanas de la comuna de Maipú y cruza los datos demográficos utilizando el código identificador de manzana.
**datos_censo_maipu_original.xlsx**: Archivo Excel original descargado del portal del INE, que contiene las variables demográficas de la comuna.
**manzanas_maipu_completo.zip**: Archivo comprimido que contiene el GeoJSON final de las manzanas de Maipú. Incluye la geometría de cada manzana junto con sus datos de población, listos para ser utilizados en la priorización del ruteo.
**Diccionario_variables_geograficas_CPV24.xlsx**: Documento oficial del INE que detalla el significado de cada columna presente en los datos del censo.

## Instrucciones de ejecución

1. Asegúrese de tener el archivo `Cartografia_censo2024_Pais.gpkg` descargado del INE en esta carpeta.
2. Ejecute el script:
python procesar_censo.py