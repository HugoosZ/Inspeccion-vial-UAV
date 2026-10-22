import json
import math

# 1. Fórmula matemática para calcular la distancia entre dos coordenadas (en km)
def calcular_distancia(lat1, lon1, lat2, lon2):
    R = 6371.0 # Radio de la Tierra en km
    
    # Convertir grados a radianes
    lat1_rad = math.radians(lat1)
    lon1_rad = math.radians(lon1)
    lat2_rad = math.radians(lat2)
    lon2_rad = math.radians(lon2)
    
    dlat = lat2_rad - lat1_rad
    dlon = lon2_rad - lon1_rad
    
    # Fórmula de Haversine
    a = math.sin(dlat / 2)**2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    
    return R * c

# 2. Cargar los datos
with open('aerodromos.geojson', 'r', encoding='utf-8') as f:
    data_aerodromos = json.load(f)

with open('../M1/paraderos_maipu.csv', 'r', encoding='utf-8') as f:
    lineas = f.readlines()

# ==========================================
# PRUEBA 1: Evaluar un paradero real (El primero)
# ==========================================
print("=== PRUEBA 1: EVALUANDO UN PARADERO REAL ===")
paradero = lineas[1].strip().split(',')
lat_p = float(paradero[2])
lon_p = float(paradero[3])
nombre_p = paradero[1]

print(f"Analizando el paradero: {nombre_p} (Lat: {lat_p}, Lon: {lon_p})")
aerodromos_cercanos = 0

for aerodromo in data_aerodromos['features']:
    coords = aerodromo['geometry']['coordinates']
    lon_a, lat_a = coords[0], coords[1]
    nombre_a = aerodromo['properties'].get('name', 'Desconocido')
    
    distancia = calcular_distancia(lat_p, lon_p, lat_a, lon_a)
    
    if distancia <= 2.0:
        print(f"  ¡ALERTA! El aeródromo '{nombre_a}' está a {distancia:.2f} km.")
        aerodromos_cercanos += 1

if aerodromos_cercanos == 0:
    print("  Todo en orden. No hay aeródromos a menos de 2 km.\n")


# ==========================================
# PRUEBA 3: Simular el dron en el Aeródromo Peldehue
# ==========================================
print("=== PRUEBA 3: SIMULANDO EL DRON EN UN AERÓDROMO ===")
primer_aerodromo = data_aerodromos['features'][0]
lat_sim = primer_aerodromo['geometry']['coordinates'][1]
lon_sim = primer_aerodromo['geometry']['coordinates'][0]
nombre_sim = primer_aerodromo['properties']['name']

print(f"Simulando que el dron está en: {nombre_sim} (Lat: {lat_sim}, Lon: {lon_sim})")
aerodromos_cercanos_sim = 0

for aerodromo in data_aerodromos['features']:
    coords = aerodromo['geometry']['coordinates']
    lon_a, lat_a = coords[0], coords[1]
    nombre_a = aerodromo['properties'].get('name', 'Desconocido')
    
    distancia = calcular_distancia(lat_sim, lon_sim, lat_a, lon_a)
    
    if distancia <= 2.0:
        print(f"  ¡ALERTA! El aeródromo '{nombre_a}' está a {distancia:.2f} km.")
        aerodromos_cercanos_sim += 1

if aerodromos_cercanos_sim == 0:
    print("  Todo en orden. No hay aeródromos a menos de 2 km.")