import os
from io import BytesIO
from pathlib import Path

import geopandas as gpd
import httpx
import pandas as pd
from dotenv import load_dotenv

# Rutas y configuración
BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR.parent

load_dotenv(PROJECT_DIR / ".env")

API_URL = "https://api.heigit.org/ohsome-api/v2"
API_KEY = os.getenv("OHSOME_API_KEY")

# Coordenadas de Maipú (xmin, ymin, xmax, ymax)
AOI_MAIPU = [-70.803533, -33.571997, -70.718327, -33.458093]

FILTRO = """
(
    highway=stop
    or highway=give_way
    or highway=traffic_signals
    or highway=crossing
)
and (geometry:point or geometry:line)
"""


def extraer_controles_viales():
    if not API_KEY:
        raise RuntimeError(
            "No se encontró OHSOME_API_KEY. Revisa el archivo .env."
        )

    url = f"{API_URL}/extraction/features.parquet"

    print("1. Preparando consulta a ohsome para Maipú...", flush=True)

    # Convertir AOI a string "lon1,lat1,lon2,lat2"
    bboxes_string = ",".join(map(str, AOI_MAIPU))

    response = httpx.post(
        url,
        json={
            "aoi": AOI_MAIPU,  # Lista de coordenadas [lon_min, lat_min, lon_max, lat_max]
            "filter": FILTRO,
            "time": "latest",
        },
        headers={"Authorization": API_KEY},
        timeout=240,
    )

    print(f"2. Respuesta HTTP: {response.status_code}", flush=True)

    # Si falla, muestra el mensaje de error de la API para depurar
    if response.status_code != 200:
        print("Error de la API:", response.text[:1000], flush=True)
        response.raise_for_status()

    print("3. Leyendo los datos GeoParquet...", flush=True)

    gdf = gpd.read_parquet(
        BytesIO(response.content),
        to_pandas_kwargs={"maps_as_pydicts": "strict"},
    )

    print(f"4. Elementos recibidos: {len(gdf)}", flush=True)

    # Convertir las etiquetas OSM a columnas simples
    if "tags" in gdf.columns:
        tags = pd.json_normalize(
            gdf["tags"].apply(
                lambda x: x if isinstance(x, dict) else {}
            )
        )
        tags.columns = [f"tag_{c}" for c in tags.columns]
        gdf = gdf.drop(columns=["tags"]).join(tags)

    # Evitar exportar columnas estructuradas problemáticas
    gdf = gdf.drop(columns=["bbox"], errors="ignore")

    salida = BASE_DIR / "data"
    salida.mkdir(parents=True, exist_ok=True)
    archivo = salida / "controles_viales_maipu.geojson"

    print("5. Guardando GeoJSON...", flush=True)
    gdf.to_file(archivo, driver="GeoJSON")

    print(f"6. ¡Proceso terminado! Elementos: {len(gdf)}", flush=True)
    print(f"Archivo generado: {archivo}", flush=True)



if __name__ == "__main__":
    print("Iniciando extracción de controles viales de Maipú...", flush=True)
    extraer_controles_viales()