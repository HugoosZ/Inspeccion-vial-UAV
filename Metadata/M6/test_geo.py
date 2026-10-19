import json

# 1. Abrir el archivo GeoJSON
with open('aerodromos.geojson', 'r', encoding='utf-8') as f:
    data = json.load(f)

# 2. Contar cuántos elementos tiene
cantidad = len(data['features'])
print(f"Se cargaron {cantidad} aeródromos/puntos.")

# 3. Mostrar el sistema de coordenadas (en GeoJSON siempre es WGS84/EPSG:4326)
crs = data.get('crs', {'properties': {'name': 'EPSG:4326 (Por defecto)'}})
print(f"Sistema de coordenadas: {crs}")

# 4. Mostrar el primer aeródromo como ejemplo
primer_aerodromo = data['features'][0]
print("\nEjemplo del primer aeródromo:")
print(f"Nombre: {primer_aerodromo['properties']['name']}")
print(f"Coordenadas: {primer_aerodromo['geometry']['coordinates']}")