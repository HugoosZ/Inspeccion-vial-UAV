# Metadata 6: Restricciones Aéreas

Este módulo contiene la información geoespacial oficial de la DGAC sobre aeródromos y zonas de exclusión aérea, con el objetivo de evitar que los drones operen en zonas restringidas.

## Archivos incluidos

**procesar_aerodromos.py**: Script que lee el archivo GeoJSON de los aeródromos y cruza la ubicación de los paraderos con las zonas de exclusión, calculando un radio de seguridad mediante la fórmula de Haversine.
**aerodromos.geojson**: Archivo GeoJSON oficial descargado del visor de la DGAC, que contiene los aeródromos y pistas de la Región Metropolitana.
**paraderos_seguros.csv**: Dataset final de la Metadata 1 filtrado. Contiene únicamente los paraderos que se encuentran a más de 2 km de cualquier aeródromo, quedando listos para el ruteo.
**test_distancias.py** y **test_geo.py**: Scripts de prueba que validan el cálculo correcto de distancias y la lectura del archivo GeoJSON.

## Instrucciones de ejecución

1. Ejecute el script de cruce de datos:
python procesar_aerodromos.py