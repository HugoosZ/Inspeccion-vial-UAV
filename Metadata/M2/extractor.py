"""
==============================================================================
METADATA M2: Lugares públicos candidatos a droneport (MINVU · Equipamientos)
==============================================================================
Extract:
  Servicios públicos del Ministerio de Vivienda y Urbanismo (MINVU) con
  equipamientos urbanos de Chile (ArcGIS FeatureServer, sin autenticación):
    EQUIPAMIENTOS, capa 2               Salud pública (DEIS: CESFAM, CECOSF, SAPU, postas,
                                        hospitales), con nombre oficial y dependencia
    EQUIPAMIENTOS, capa 3               Colegios (directorio MINEDUC al 30-04-2021), con
                                        nombre y RBD
    Equipamientos_prod_vista_, capa 20  Cuarteles de Carabineros
    Equipamientos_prod_vista_, capa 22  Compañías de Bomberos
Transform:
  - Se conservan solo los puntos dentro del límite oficial de Maipú (MINVU).
  - Educación: solo dependencia pública (TIPO_DEPEN 1 = municipal,
    4 = corporación de administración delegada, 5 = Servicio Local de Educación).
    Los colegios particulares no son recintos públicos.
  - Salud: dependencia municipal (atención primaria) o del Servicio de Salud.
  - Cada lugar queda con categoría, dependencia (municipal o estatal), nombre y
    dirección para mostrarlos en el sitio y elegir entre ellos las bases.
Load (archivo):
  - data/lugares_publicos_maipu.geojson

Uso:
  python Metadata/M2/extractor.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import comun_arcgis as ag  # noqa: E402

SALIDA = Path(__file__).resolve().parent / "data" / "lugares_publicos_maipu.geojson"
MINVU = "https://services3.arcgis.com/cTnMkBRk4HWkUCRo/arcgis/rest/services/"
URL_SALUD = MINVU + "EQUIPAMIENTOS/FeatureServer/2"
URL_COLEGIOS = MINVU + "EQUIPAMIENTOS/FeatureServer/3"
URL_CARABINEROS = MINVU + "Equipamientos_prod_vista_/FeatureServer/20"
URL_BOMBEROS = MINVU + "Equipamientos_prod_vista_/FeatureServer/22"

# Códigos de dependencia de MINEDUC (COD_DEPE2) presentes en la capa de educación
DEPENDENCIA_EDUCACION = {1: "Municipal", 2: "Particular subvencionado", 3: "Particular pagado",
                         4: "Corporación de administración delegada", 5: "Servicio Local de Educación"}
EDUCACION_PUBLICA = {1: "municipal", 4: "estatal", 5: "estatal"}


def _feature(attrs, geom, categoria, dependencia, nombre, direccion, capa, extra=None):
    return {
        "type": "Feature",
        "geometry": geom,
        "properties": {
            "categoria": categoria,
            "dependencia": dependencia,
            "nombre": nombre,
            "direccion": direccion,
            "fuente": "MINVU · Equipamientos",
            "capa_origen": capa,
            "id_origen": attrs.get("OBJECTID"),
            **(extra or {}),
        },
    }


def salud(attrs):
    tipo = ag.texto(attrs.get("TIPO_ESTAB")) or ag.texto(attrs.get("TIPO_ESTRA")) or "Establecimiento de salud"
    nombre = ag.texto(attrs.get("NOM_OFIC")) or ag.texto(attrs.get("NOMBRE")) or tipo
    dependencia = ag.texto(attrs.get("DEPENDEN"))
    return ("salud_publica", "municipal" if dependencia == "Municipal" else "estatal", nombre,
            ag.texto(attrs.get("DIRECCION_")),
            {"tipo_establecimiento": tipo, "dependencia_deis": dependencia,
             "nivel_atencion": ag.texto(attrs.get("NIV_ATE")),
             "codigo_deis": ag.texto(attrs.get("COD_NVO")) or ag.texto(attrs.get("COD_ANTIG"))})


def educacion(attrs):
    codigo = attrs.get("TIPO_DEPEN")
    if codigo not in EDUCACION_PUBLICA:
        return None
    direccion = " ".join(x for x in (ag.texto(attrs.get("DIRECCION")), str(attrs.get("NUMERO") or "").strip())
                         if x and x != "0") or None
    nombre = ag.texto(attrs.get("NOM_RBD")) or f"Establecimiento educacional {direccion or ''}".strip()
    return ("educacion_publica", EDUCACION_PUBLICA[codigo], nombre.title(),
            direccion.title() if direccion else None,
            {"rbd": attrs.get("RBD"), "tipo_dependencia": DEPENDENCIA_EDUCACION[codigo],
             "matricula": attrs.get("MAT_TOTAL"), "anio_catastro": attrs.get("AGNO")})


def carabineros(attrs):
    nombre = ag.texto(attrs.get("NOMBRE_UNI")) or "Unidad de Carabineros"
    direccion = " ".join(x for x in (ag.texto(attrs.get("NOMBRE_DE")), ag.texto(attrs.get("NUMERO"))) if x) or None
    return ("carabineros", "estatal", nombre.title(), direccion.title() if direccion else None,
            {"tipo_unidad": ag.texto(attrs.get("TIPO_DE_UN")), "prefectura": ag.texto(attrs.get("PREFECTURA"))})


def bomberos(attrs):
    nombre = ag.texto(attrs.get("Compañia")) or "Compañía de Bomberos"
    return ("bomberos", "estatal", f"Bomberos · {nombre}", ag.texto(attrs.get("Direccion")),
            {"codigo_compania": ag.texto(attrs.get("CUT_Cia"))})


CAPAS = [
    (URL_SALUD, "Salud pública", salud),
    (URL_COLEGIOS, "Colegios", educacion),
    (URL_CARABINEROS, "Cuarteles de Carabineros", carabineros),
    (URL_BOMBEROS, "Compañías de Bomberos", bomberos),
]


def main():
    print("[*] Límite comunal de Maipú (MINVU)...")
    limite = ag.limite_maipu()
    features, conteo = [], {}
    for url, nombre_capa, transformar in CAPAS:
        print(f"[*] MINVU: {nombre_capa}")
        try:
            filas = ag.consultar(url, bbox=ag.AOI_MAIPU)
        except RuntimeError as error:
            print(f"[-] {error}")
            sys.exit(1)  # sin una capa el catálogo de bases queda incompleto: se conserva el archivo anterior
        n = 0
        for fila in filas:
            geom = ag.a_geojson(fila.get("geometry"))
            if not geom or not ag.dentro_de(*geom["coordinates"], limite):
                continue
            resultado = transformar(fila.get("attributes") or {})
            if resultado is None:
                continue
            categoria, dependencia, nombre, direccion, extra = resultado
            features.append(_feature(fila["attributes"], geom, categoria, dependencia, nombre, direccion,
                                     nombre_capa, extra))
            n += 1
        conteo[nombre_capa] = n
        print(f"    [+] {len(filas)} en el rectángulo, {n} dentro de Maipú")

    ag.guardar_geojson(SALIDA, "metadata_m2_lugares_publicos_maipu", features, {
        "metadata": "M2 - Lugares públicos candidatos a droneport",
        "fuente": "MINVU · Equipamientos (ArcGIS)",
        "servicios": [url for url, _, _ in CAPAS],
        "limite_comunal": "MINVU · División Político Administrativa (cod_com 13119)",
        "por_capa": conteo,
        "criterio_educacion": "solo dependencia pública (TIPO_DEPEN 1, 4 y 5)",
    })


if __name__ == "__main__":
    main()
