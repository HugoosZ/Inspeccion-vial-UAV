import json
import math
import csv

# 1. Fórmula matemática para calcular la distancia (Haversine)
def calcular_distancia(lat1, lon1, lat2, lon2):
    R = 6371.0
    lat1_rad, lon1_rad = math.radians(lat1), math.radians(lon1)
    lat2_rad, lon2_rad = math.radians(lat2), math.radians(lon2)
    dlat = lat2_rad - lat1_rad
    dlon = lon2_rad - lon1_rad
    a = math.sin(dlat / 2)**2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

# 2. Cargar los aeródromos
with open('aerodromos.geojson', 'r', encoding='utf-8') as f:
    data_aerodromos = json.load(f)

# Extraemos una lista limpia de (lat, lon, nombre) de los aeródromos para hacer el código más rápido
lista_aerodromos = []
for aero in data_aerodromos['features']:
    coords = aero['geometry']['coordinates']
    lon = coords[0]
    lat = coords[1]
    nombre = aero['properties'].get('name', 'Desconocido')
    lista_aerodromos.append((lat, lon, nombre))

print(f"Se cargaron {len(lista_aerodromos)} aeródromos en memoria.")

# 3. Leer los paraderos
paradero_seguros = []
paraderos_bloqueados = 0
total_paraderos = 0

with open('../M1/paraderos_maipu.csv', 'r', encoding='utf-8') as f:
    lector = csv.reader(f)
    encabezado = next(lector) # Leer encabezado
    
    # Agregar columna "Alerta_Aerodromo" al nuevo encabezado
    nuevo_encabezado = encabezado + ["Alerta_Aerodromo"]
    paradero_seguros.append(nuevo_encabezado)
    
    for fila in lector:
        total_paraderos += 1
        id_p = fila[0]
        codigo = fila[1]
        lat_p = float(fila[2])
        lon_p = float(fila[3])
        
        esta_en_peligro = False
        motivo = "Seguro"
        
        # Comparar contra todos los aeródromos
        for lat_a, lon_a, nombre_a in lista_aerodromos:
            distancia = calcular_distancia(lat_p, lon_p, lat_a, lon_a)
            
            # Si está a menos de 2 km, se marca como peligroso
            if distancia <= 2.0:
                esta_en_peligro = True
                motivo = f"Bloqueado por {nombre_a} ({distancia:.2f} km)"
                break # Si ya encontramos un aeródromo cerca, no hace falta seguir buscando
        
        if esta_en_peligro:
            paraderos_bloqueados += 1
        else:
            # Guardamos la fila original + el motivo
            paradero_seguros.append(fila + [motivo])

# 4. Guardar el nuevo CSV
with open('paraderos_seguros.csv', 'w', encoding='utf-8', newline='') as f:
    escritor = csv.writer(f)
    escritor.writerows(paradero_seguros)

# 5. Mostrar resumen en consola
print("\n--- RESUMEN DE PROCESAMIENTO ---")
print(f"Total de paraderos evaluados: {total_paraderos}")
print(f"Paraderos seguros (Válidos para ruteo): {total_paraderos - paraderos_bloqueados}")
print(f"Paraderos bloqueados (Cerca de aeródromos): {paraderos_bloqueados}")
print(f"\nArchivo guardado como: paraderos_seguros.csv")