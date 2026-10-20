# Metadata 1: Inventario de Paraderos

Este módulo contiene el proceso de extracción y limpieza de los paraderos oficiales de la comuna de Maipú, utilizando como fuente los datos del sistema de información geográfica municipal.

## Archivos incluidos

**extractor.py**: Script en Python que consulta la API REST de ArcGIS. Filtra los datos exclusivamente para la comuna de Maipú y extrae las coordenadas geográficas en formato WGS84 (Latitud y Longitud).
**paraderos_maipu.csv**: Archivo tabular que contiene las columnas esenciales para el algoritmo de ruteo (ID, Código, Latitud, Longitud, Tipo y Estado).
**paraderos_maipu.json**: Archivo JSON con formato legible que conserva la totalidad de la metadata original de los 942 paraderos, incluyendo sus atributos y geometría.

## Instrucciones de ejecución

Para regenerar los datos desde cero, ejecute el siguiente comando en la terminal:
python extractor.py