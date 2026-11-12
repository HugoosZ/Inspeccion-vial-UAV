"""
==============================================================================
METADATA M3: Puentes, pasarelas y enlaces a desnivel (MOP)
==============================================================================
Extract (servidor SIT del Ministerio de Obras Públicas, sin autenticación):
  - Dirección de Vialidad, catastro de puentes de la red vial nacional
    (VIALIDAD/Puentes, capa 0).
  - Dirección General de Concesiones, elementos de las autopistas concesionadas:
    puentes (CCOP/ELEMENTOS, capa 8), pasarelas peatonales (capa 6) y enlaces
    a desnivel (CCOP/INFRA_DGC, capa 6).
Transform:
  - Se conservan las estructuras dentro del límite oficial de Maipú (MINVU).
  - Cada estructura queda como punto con su tipo (puente, pasarela, enlace) y
    los atributos que describen su tamaño (largo, ancho) y lo que cruza.
Load (archivo):
  - data/puentes_maipu.geojson

Cobertura: el MOP registra solo las estructuras de caminos a su cargo
(Vialidad y autopistas concesionadas). Los pasos sobre nivel de calles
municipales no aparecen en estos catastros.

Uso:
  python Metadata/M3/extractor.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import comun_arcgis as ag  # noqa: E402

SALIDA = Path(__file__).resolve().parent / "data" / "puentes_maipu.geojson"
SIT = "https://rest-sit.mop.gob.cl/arcgis/rest/services/"


def numero(valor):
    try:
        return round(float(valor), 2) if valor not in (None, "", " ") else None
    except (TypeError, ValueError):
        return None


def vialidad(a):
    nombre = ag.texto(a.get("NOMBRE_PUENTE")) or "Puente"
    return "puente", nombre.title(), {
        "codigo": ag.texto(a.get("CODIGO_PUENTE")),
        "camino": ag.texto(a.get("NOMBRE_CAMINO")),
        "rol_camino": ag.texto(a.get("ROL")),
        "cruza": ag.texto(a.get("CAUCE_QUEB")),
        "largo_m": numero(a.get("LARGO")),
        "ancho_total_m": numero(a.get("ANCHO_TOTAL")),
        "ancho_calzada_m": numero(a.get("ANCHO_CALZADA")),
        "anio_construccion": a.get("AÑO_CONTRUCCION"),
        "carpeta": ag.texto(a.get("PISO")),
        "n_cepas": a.get("NUM_CEPAS"),
        "n_vigas": a.get("NUM_VIGAS"),
        "tuicion": ag.texto(a.get("TUICION")),
    }


def concesion_puente(a):
    return "puente", ag.texto(a.get("NOMBRE")) or "Puente", {
        "codigo": a.get("ID_PUENTE"),
        "km": numero(a.get("KM")),
        "observacion": ag.texto(a.get("OBSERVACION")),
        "urbano": ag.texto(a.get("URBANO")),
        "actualizado": ag.fecha_iso(a.get("FECHA_ACTUALIZACION")),
    }


def pasarela(a):
    return "pasarela", ag.texto(a.get("NOMBRE")) or "Pasarela peatonal", {
        "codigo": a.get("ID_PASARELA"),
        "camino": ag.texto(a.get("CONTRATO")),
        "rol_camino": ag.texto(a.get("ROLVIA")),
        "km": numero(a.get("KMINI")),
    }


def enlace(a):
    return "enlace", ag.texto(a.get("NOM_ENLACE")) or "Enlace a desnivel", {
        "camino": ag.texto(a.get("RUTA")),
    }


CAPAS = [
    ("VIALIDAD/Puentes/MapServer/0", "MOP · Vialidad, catastro de puentes", vialidad),
    ("CCOP/ELEMENTOS/MapServer/8", "MOP · Concesiones, puentes", concesion_puente),
    ("CCOP/ELEMENTOS/MapServer/6", "MOP · Concesiones, pasarelas", pasarela),
    ("CCOP/INFRA_DGC/MapServer/6", "MOP · Concesiones, enlaces", enlace),
]


def main():
    print("[*] Límite comunal de Maipú (MINVU)...")
    limite = ag.limite_maipu()
    features, conteo, fallidas = [], {}, []
    for ruta, fuente, transformar in CAPAS:
        print(f"[*] {fuente}")
        try:
            filas = ag.consultar(SIT + ruta, bbox=ag.AOI_MAIPU)
        except RuntimeError as error:
            print(f"[-] {error}")
            fallidas.append(fuente)
            continue
        n = 0
        for fila in filas:
            geom = ag.a_geojson(fila.get("geometry"))
            if not geom or geom["type"] != "Point" or not ag.dentro_de(*geom["coordinates"], limite):
                continue
            tipo, nombre, extra = transformar(fila.get("attributes") or {})
            features.append({"type": "Feature", "geometry": geom, "properties": {
                "tipo": tipo, "nombre": nombre, "fuente": fuente, "servicio": SIT + ruta,
                **{k: v for k, v in extra.items() if v not in (None, "")}}})
            n += 1
        conteo[fuente] = n
        print(f"    [+] {len(filas)} en el rectángulo, {n} dentro de Maipú")

    if len(fallidas) == len(CAPAS):
        print("[-] Ningún servicio del MOP respondió; se conserva el archivo anterior si existe.")
        sys.exit(1)

    ag.guardar_geojson(SALIDA, "metadata_m3_puentes_maipu", features, {
        "metadata": "M3 - Puentes, pasarelas y enlaces a desnivel",
        "fuente": "Ministerio de Obras Públicas, servidor SIT (ArcGIS)",
        "por_capa": conteo,
        "capas_sin_respuesta": fallidas,
        "cobertura": "solo estructuras de caminos de Vialidad y autopistas concesionadas",
        "limite_comunal": "MINVU · División Político Administrativa (cod_com 13119)",
    })


if __name__ == "__main__":
    main()
