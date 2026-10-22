import geopandas as gpd
import pandas as pd
import json
import os

def main():
    print("=== PROCESANDO CENSO 2024 PARA MAIPÚ ===")
    
    archivo_excel = "13119_manzana_entidad_CPV24.xlsx" 
    archivo_geo = "Cartografia_censo2024_Pais.gpkg" 
    
    # 1. LEER EL EXCEL
    print("\n1. Leyendo el Excel...")
    df_censo = pd.read_excel(archivo_excel, header=0) 
    df_censo.columns = df_censo.columns.str.strip().str.lower()
    df_censo['manzent'] = df_censo['manzent'].astype(str).str.strip()

    # 2. LEER EL GEOPACKAGE (Capa correcta: Manzanas_CPV24)
    print("2. Leyendo la capa 'Manzanas_CPV24' (esto tarda un poco)...")
    gdf_carto = gpd.read_file(archivo_geo, layer="Manzanas_CPV24")
    
    print(f"   -> Total de manzanas en el archivo: {len(gdf_carto)}")

    # 3. FILTRAR MAIPÚ
    print("3. Buscando Maipú...")
    gdf_carto['CUT'] = gdf_carto['CUT'].astype(str).str.strip()
    gdf_maipu = gdf_carto[gdf_carto['CUT'] == '13119'].copy()
    
    print(f"   -> Manzanas encontradas en Maipú: {len(gdf_maipu)}")

    if len(gdf_maipu) == 0:
        print("Error: No se encontraron manzanas de Maipú.")
        return

    # 4. CRUZAR DATOS
    print("4. Cruzando datos con el Excel...")
    # Buscamos la columna 'ENTIDAD' en el mapa
    col_mapa = 'ENTIDAD' 
    gdf_maipu[col_mapa] = gdf_maipu[col_mapa].astype(str).str.strip()
    
    gdf_final = gdf_maipu.merge(df_censo, left_on=col_mapa, right_on='manzent', how="left")
    print(f"   -> Filas después del cruce: {len(gdf_final)}")

    # 5. GUARDAR JSON LEGIBLE
    print("5. Guardando el archivo final...")
    datos_json = json.loads(gdf_final.to_json())
    archivo_salida = "manzanas_maipu_completo.geojson"
    
    with open(archivo_salida, 'w', encoding='utf-8') as f:
        json.dump(datos_json, f, indent=4, ensure_ascii=False)
        
    print(f"\n¡LISTO! Archivo guardado: {archivo_salida}")

if __name__ == "__main__":
    main()