import urllib.request
import urllib.parse
import json
import math
import os

def main():
    base_url = "https://services9.arcgis.com/kKJR3Qt68ohAWuet/arcgis/rest/services/Paraderos_Transantiago/FeatureServer/0"
    where_clause = "comuna LIKE '%Maip%'"
    
    # Codificamos la cláusula WHERE para que viaje segura por la URL
    encoded_where = urllib.parse.quote(where_clause)
    query_url = f"{base_url}/query?where={encoded_where}&outFields=*&outSR=4326&f=json"

    print("Descargando datos y asegurando que todos sean de Maipú...")

    try:
        # 1. Hacer la petición HTTP
        req = urllib.request.Request(query_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=60) as response:
            json_response = response.read().decode('utf-8')

        # 2. Parsear la respuesta a un diccionario de Python
        data = json.loads(json_response)
        features = data.get("features", [])

        csv_lines = []
        csv_lines.append("ID,Codigo,Latitud,Longitud,Tipo,Estado,Comuna")

        features_finales = []
        id_ruteo = 1

        # 3. Recorrer los features y filtrar
        for feature in features:
            attributes = feature.get("attributes", {})
            comuna_original = str(attributes.get("comuna", ""))
            
            # Verificación estricta de la comuna (pasamos a minúsculas y buscamos "maip")
            # En Python no hace falta quitar tildes si usamos lower() porque 'MAIPÚ'.lower() -> 'maipú'
            if "maip" not in comuna_original.lower():
                continue

            # Extraer coordenadas de la geometría
            lat = 0.0
            lon = 0.0
            geometry = feature.get("geometry")
            if geometry:
                lat = round(geometry.get("y", 0.0), 6)
                lon = round(geometry.get("x", 0.0), 6)

            # Preparar datos para el CSV
            codigo = str(attributes.get("codigo", "-"))
            tipo = str(attributes.get("tipo", "-"))
            estado = str(attributes.get("estado", "-"))
            
            # Formatear a 6 decimales (f-string en Python)
            lat_str = f"{lat:.6f}"
            lon_str = f"{lon:.6f}"
            
            # Escribir fila CSV (las comillas dobles en la comuna son para proteger si tiene comas)
            csv_lines.append(f'{id_ruteo},{codigo},{lat_str},{lon_str},{tipo},{estado},"{comuna_original}"')

            # --- CONSTRUCCIÓN DEL NUEVO JSON ---
            # En Python, al ser diccionarios, no hay problema de "parent" ni necesidad de DeepClone
            # (creamos un diccionario nuevo armado con los datos)
            geocopia = {}
            if geometry:
                geocopia["x_metro"] = geometry.get("x")
                geocopia["y_metro"] = geometry.get("y")
                geocopia["latitud"] = lat
                geocopia["longitud"] = lon

            nuevo_feature = {
                "ID_Ruteo": id_ruteo,
                "attributes": attributes,
                "geometry": geocopia
            }

            features_finales.append(nuevo_feature)
            id_ruteo += 1

        # 4. Armar el JSON final
        json_final = {
            "total_filtrados_maipu": id_ruteo - 1,
            "features": features_finales
        }

        # 5. Guardar archivos
        csv_path = "paraderos_maipu.csv"
        with open(csv_path, "w", encoding="utf-8") as f:
            f.write("\n".join(csv_lines))

        json_path = "paraderos_maipu.json"
        with open(json_path, "w", encoding="utf-8") as f:
            # ensure_ascii=False permite que los acentos (como Maipú) se guarden bien
            # indent=2 es el equivalente a WriteIndented = true en C#
            json.dump(json_final, f, ensure_ascii=False, indent=2)

        # 6. Mostrar resultados
        print("\n¡Éxito! Archivos guardados.")
        print(f"- CSV: {os.path.abspath(csv_path)}")
        print(f"- JSON: {os.path.abspath(json_path)}")
        print(f"Total de paraderos VERIFICADOS de Maipú: {id_ruteo - 1}")
        print("\nEjemplo del primer registro en CSV:")
        if len(csv_lines) > 1:
            print(csv_lines[1])

    except Exception as e:
        print(f"\nError: {e}")

if __name__ == "__main__":
    main()