"""
==============================================================================
METADATA M4: Zonas de siniestros de tránsito (CONASET)
==============================================================================
Extract:
  Base de siniestros 2020-2025 de la Comisión Nacional de Seguridad de Tránsito
  (CONASET), publicada en su portal de datos abiertos (ArcGIS FeatureServer,
  sin autenticación). Se consultan solo los siniestros con COMUNA = 'Maipú'.
Transform:
  1. Cada siniestro se marca según su causa (campo CAUSA_SINI):
       desobediencia_senal  desobedecer PARE, ceda el paso, luz roja u otra señal
       senal_defectuosa     señal o semáforo en mal estado, mal instalado o no visible
     (la base escribe "SENALIZACION" y "SEÑALIZACION"; se unifican antes de comparar)
  2. Los siniestros se agrupan en celdas de 100 x 100 m (SINIESTROS_CELDA_M),
     del orden de una intersección. Se conservan las celdas con 2 o más
     siniestros o con al menos uno ligado a la señalización.
  3. Cada celda recibe un nivel por ranking de frecuencia (identificación de
     puntos críticos, Cheng y Washington, 2005): CRITICA = 5 % de celdas con más
     siniestros, ALTA = siguiente 15 %, MEDIA = resto.
Load (archivos):
  - data/siniestros_maipu.geojson        un punto por siniestro (campos útiles)
  - data/zonas_siniestros_maipu.geojson  celdas agregadas (es lo que se carga en la BD)

Uso:
  python Metadata/M4/extractor.py
Variables de entorno opcionales:
  SINIESTROS_CELDA_M   lado de la celda en metros (defecto 100)
"""

import math
import os
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import comun_arcgis as ag  # noqa: E402

DATA = Path(__file__).resolve().parent / "data"
SALIDA_PUNTOS = DATA / "siniestros_maipu.geojson"
SALIDA_ZONAS = DATA / "zonas_siniestros_maipu.geojson"
URL = ("https://services3.arcgis.com/vaJl1B5HEzZj7154/arcgis/rest/services/"
       "Base_SINIESTROS_2020_2025/FeatureServer/0")
CAMPOS = ("ID,AÑO,FECHA,Hora,TIPO__CONA,TIPO_SINIE,CAUSA_SINI,CAUSA_NUEV,ZONA,"
          "Calle_Uno,Calle_Dos,Número,FALLECIDOS,GRAVES,MENOS_GRAV,LEVES,TOTAL_LESI")
LAT_REF = -33.51  # latitud media de Maipú, para pasar grados a metros


def causa_normalizada(texto):
    return (texto or "").upper().replace("SENALIZACION", "SEÑALIZACION").replace("SENAL", "SEÑAL")


def clasificar_causa(causa):
    desobediencia = causa.startswith("SEÑALIZACION DESOBEDECER") and "CARABINERO" not in causa
    defectuosa = (causa.startswith("SEÑALIZACION") and not desobediencia
                  and any(t in causa for t in ("MAL ESTADO", "DEFICIENTE", "MAL INSTALADA", "NO VISIBLE")))
    return desobediencia, defectuosa


def entero(valor):
    try:
        return int(valor or 0)
    except (TypeError, ValueError):
        return 0


def a_punto(fila):
    a = fila.get("attributes") or {}
    geom = ag.a_geojson(fila.get("geometry"))
    if not geom:
        return None
    causa = causa_normalizada(a.get("CAUSA_SINI"))
    desobediencia, defectuosa = clasificar_causa(causa)
    calles = " con ".join(c for c in (ag.texto(a.get("Calle_Uno")), ag.texto(a.get("Calle_Dos"))) if c)
    return {"type": "Feature", "geometry": geom, "properties": {
        "id": a.get("ID"),
        "anio": a.get("AÑO"),
        "fecha": (ag.fecha_iso(a.get("FECHA")) or "")[:10] or None,
        "hora": ag.texto(a.get("Hora")),
        "tipo": ag.texto(a.get("TIPO__CONA")),
        "subtipo": ag.texto(a.get("TIPO_SINIE")),
        "causa": causa or None,
        "causa_agrupada": ag.texto(a.get("CAUSA_NUEV")),
        "zona": ag.texto(a.get("ZONA")),
        "ubicacion": calles or None,
        "fallecidos": entero(a.get("FALLECIDOS")),
        "graves": entero(a.get("GRAVES")),
        "menos_graves": entero(a.get("MENOS_GRAV")),
        "leves": entero(a.get("LEVES")),
        "desobediencia_senal": desobediencia,
        "senal_defectuosa": defectuosa,
    }}


def agrupar(puntos, celda_m):
    mx = 111_320 * math.cos(math.radians(LAT_REF))
    my = 110_574
    celdas = defaultdict(list)
    for p in puntos:
        lon, lat = p["geometry"]["coordinates"]
        celdas[(math.floor(lon * mx / celda_m), math.floor(lat * my / celda_m))].append(p["properties"])

    zonas = []
    for (i, j), lista in celdas.items():
        n_senal = sum(p["desobediencia_senal"] for p in lista)
        n_defectuosa = sum(p["senal_defectuosa"] for p in lista)
        if len(lista) < 2 and not (n_senal or n_defectuosa):
            continue
        x0, y0 = i * celda_m / mx, j * celda_m / my
        x1, y1 = (i + 1) * celda_m / mx, (j + 1) * celda_m / my
        ubicaciones = defaultdict(int)
        for p in lista:
            if p["ubicacion"]:
                ubicaciones[p["ubicacion"]] += 1
        principal = max(ubicaciones, key=ubicaciones.get) if ubicaciones else None
        anios = sorted({p["anio"] for p in lista if p["anio"]})
        zonas.append({"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[
            [round(x0, 6), round(y0, 6)], [round(x1, 6), round(y0, 6)], [round(x1, 6), round(y1, 6)],
            [round(x0, 6), round(y1, 6)], [round(x0, 6), round(y0, 6)]]]}, "properties": {
            "id_celda": f"{i}_{j}",
            "ubicacion_principal": principal,
            "n_siniestros": len(lista),
            "n_desobediencia_senal": n_senal,
            "n_senal_defectuosa": n_defectuosa,
            "fallecidos": sum(p["fallecidos"] for p in lista),
            "graves": sum(p["graves"] for p in lista),
            "menos_graves": sum(p["menos_graves"] for p in lista),
            "leves": sum(p["leves"] for p in lista),
            "anios": f"{anios[0]}-{anios[-1]}" if anios else None,
        }})

    # Nivel por ranking de frecuencia
    zonas.sort(key=lambda z: -z["properties"]["n_siniestros"])
    total = len(zonas)
    corte_critica = zonas[max(0, math.ceil(total * 0.05) - 1)]["properties"]["n_siniestros"] if zonas else 0
    corte_alta = zonas[max(0, math.ceil(total * 0.20) - 1)]["properties"]["n_siniestros"] if zonas else 0
    for z in zonas:
        n = z["properties"]["n_siniestros"]
        z["properties"]["nivel"] = "CRITICA" if n >= corte_critica else "ALTA" if n >= corte_alta else "MEDIA"
    return zonas, {"CRITICA": corte_critica, "ALTA": corte_alta}


def main():
    celda_m = float(os.getenv("SINIESTROS_CELDA_M", "100"))
    print("[*] CONASET: siniestros 2020-2025 en Maipú...")
    try:
        filas = ag.consultar(URL, where="COMUNA='Maipú'", campos=CAMPOS)
    except RuntimeError as error:
        print(f"[-] {error}\n[-] Se conserva el archivo anterior si existe.")
        sys.exit(1)
    puntos = [p for p in (a_punto(f) for f in filas) if p]
    print(f"    [+] {len(puntos)} siniestros con coordenadas "
          f"({sum(p['properties']['desobediencia_senal'] for p in puntos)} por desobedecer una señal, "
          f"{sum(p['properties']['senal_defectuosa'] for p in puntos)} por señal defectuosa)")

    zonas, cortes = agrupar(puntos, celda_m)
    comunes = {"fuente": "CONASET · Base de siniestros 2020-2025 (ArcGIS)", "url": URL,
               "filtro": "COMUNA = 'Maipú'"}
    ag.guardar_geojson(SALIDA_PUNTOS, "metadata_m4_siniestros_maipu", puntos,
                       {**comunes, "metadata": "M4 - Siniestros de tránsito (un punto por siniestro)"},
                       indent=None)
    ag.guardar_geojson(SALIDA_ZONAS, "metadata_m4_zonas_siniestros_maipu", zonas, {
        **comunes,
        "metadata": "M4 - Zonas de siniestros de tránsito",
        "celda_m": celda_m,
        "criterio_celdas": "2 o más siniestros, o al menos uno ligado a la señalización",
        "criterio_nivel": "ranking de frecuencia: CRITICA 5 % superior, ALTA siguiente 15 %, MEDIA resto",
        "cortes_n_siniestros": cortes,
        "total_siniestros": len(puntos),
    })


if __name__ == "__main__":
    main()
