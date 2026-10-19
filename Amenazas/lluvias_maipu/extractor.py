"""
==============================================================================
AMENAZA T5: Emergencias municipales por lluvia (Municipalidad de Maipú)
==============================================================================
Extract:
  - Capa pública ArcGIS "Reporte_Lluvias" de la Municipalidad de Maipú
    (anegamientos, desbordes de canal, colapsos de alcantarillado, casas
    afectadas). Se pagina de a 1000 registros.
  - Por privacidad SOLO se piden campos no personales: se excluyen CONTACTO
    (AFECTADOS), NÚMERO de domicilio, Creator, Editor, inspector y comentarios.
Transform:
  - Cada reporte se clasifica según su ESTADO y si es PUNTO CRÍTICO.
  - Es "activa" si no está en SITUACION CONTROLADA y fue editada dentro de la
    ventana LLUVIAS_VENTANA_DIAS (defecto 7). Las inactivas se conservan como
    historial de recurrencia.
  - Coordenadas redondeadas a 4 decimales (~11 m) para no apuntar a un domicilio.
Load (archivo):
  - data/lluvias_maipu.geojson  (puntos, EPSG:4326)

Uso:
  python extractor.py
Variables opcionales: LLUVIAS_VENTANA_DIAS (7), LLUVIAS_RADIO_M (100).
"""

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import comun  # noqa: E402

DATA_DIR = Path(__file__).resolve().parent / "data"
SALIDA = DATA_DIR / "lluvias_maipu.geojson"
SERVICIO = ("https://services6.arcgis.com/dPivrbIaflr2oHb4/arcgis/rest/services/"
            "service_76553a6268c0473cad084325616e002c/FeatureServer/0/query")
CAMPOS = "objectid,BARRIO,CALLE,ESTADOS,PUNTO_CRITICO,CreationDate,EditDate,REF_INSP"
PAGINA = 1000

# Nivel base por estado del reporte
NIVEL_ESTADO = {
    "DESBORDE DE CANAL": "ALTO",
    "RIESGO DE DESBORDE": "MEDIO",
    "COLAPSO DE ALCANTARILLADO": "MEDIO",
    "CASAS AFECTADAS": "MEDIO",
    "TRABAJOS EN DESARROLLO": "MEDIO",
    "FERIA": "MEDIO",                      # código de "ZONA AFECTACION FERIA"
    "ZONA AFECTACION FERIA": "MEDIO",
    "SITUACION CONTROLADA": "BAJO",
}
SUBIR = {"BAJO": "BAJO", "MEDIO": "ALTO", "ALTO": "CRITICO", "CRITICO": "CRITICO"}


def epoch_ms_a_iso(valor):
    if valor in (None, ""):
        return None
    return datetime.fromtimestamp(int(valor) / 1000, tz=timezone.utc).replace(microsecond=0).isoformat()


def descargar():
    features, offset = [], 0
    while True:
        datos = comun.http_get(SERVICIO, params={
            "where": "1=1", "outFields": CAMPOS, "returnGeometry": "true",
            "outSR": 4326, "orderByFields": "objectid", "resultOffset": offset,
            "resultRecordCount": PAGINA, "f": "geojson",
        })
        lote = datos.get("features", [])
        features += lote
        print(f"    [+] {len(lote)} registros (offset {offset})")
        if not lote or not (datos.get("exceededTransferLimit")
                            or datos.get("properties", {}).get("exceededTransferLimit")):
            break
        offset += PAGINA
    return features


def main():
    comun.cargar_env()
    ventana = int(os.getenv("LLUVIAS_VENTANA_DIAS", "7"))
    radio_m = int(os.getenv("LLUVIAS_RADIO_M", "100"))
    limite = datetime.now(timezone.utc) - timedelta(days=ventana)

    print("[*] Municipalidad de Maipú: descargando capa Reporte_Lluvias...")
    crudos = descargar()

    features = []
    for f in crudos:
        g = f.get("geometry") or {}
        if g.get("type") != "Point":
            continue
        lon, lat = g["coordinates"][:2]
        p = f.get("properties") or {}
        estado = (p.get("ESTADOS") or "SIN ESTADO").strip().upper()
        critico = (p.get("PUNTO_CRITICO") or "").strip().upper() == "SI"
        nivel = NIVEL_ESTADO.get(estado, "MEDIO")
        if critico:
            nivel = SUBIR[nivel]

        editado = epoch_ms_a_iso(p.get("EditDate"))
        reciente = editado is not None and datetime.fromisoformat(editado) >= limite
        activa = estado != "SITUACION CONTROLADA" and reciente
        if not activa:
            accion = "NINGUNA"
        elif nivel in ("ALTO", "CRITICO"):
            accion = "BLOQUEAR_ARISTAS"
        else:
            accion = "POSTERGAR_ARISTAS"

        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [round(lon, 4), round(lat, 4)]},
            "properties": {
                "id_amenaza": f"LLUVIA-{p.get('objectid')}",
                "fuente": "Municipalidad de Maipú - Reporte_Lluvias",
                "tipo_amenaza": "emergencia_lluvia",
                "objectid": p.get("objectid"),
                "barrio": p.get("BARRIO"),
                "calle": p.get("CALLE"),
                "estado": estado,
                "punto_critico": critico,
                "nivel_riesgo": nivel if activa else "BAJO",
                "nivel_si_se_activa": nivel,
                "activa": activa,
                "accion_grafo": accion,
                "factor_penalizacion": "INF" if accion == "BLOQUEAR_ARISTAS" else (2.0 if accion == "POSTERGAR_ARISTAS" else 1.0),
                "radio_afectacion_m": radio_m,
                "fecha_creacion": epoch_ms_a_iso(p.get("CreationDate")),
                "fecha_edicion": editado,
                "fecha_ultima_inspeccion": epoch_ms_a_iso(p.get("REF_INSP")),
            },
        })

    activas = sum(1 for f in features if f["properties"]["activa"])
    print(f"    [+] {len(features)} reportes, {activas} activos (ventana {ventana} días)")
    comun.guardar_geojson(SALIDA, "amenaza_t5_lluvias_maipu", features, {
        "amenaza": "T5 - Emergencias municipales por lluvia",
        "fuente": "ArcGIS Municipalidad de Maipú, capa Reporte_Lluvias",
        "url_servicio": SERVICIO.rsplit("/query", 1)[0],
        "ventana_activa_dias": ventana,
        "radio_afectacion_m": radio_m,
        "total_activas": activas,
        "campos_excluidos_por_privacidad": ["AFECTADOS", "NUMERO", "Creator", "Editor",
                                            "REF_OPERADOR", "REF_COMENTARIOS"],
    })


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as error:
        print(f"[-] {error}")
        sys.exit(1)
