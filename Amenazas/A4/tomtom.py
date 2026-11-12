"""
==============================================================================
AMENAZA A4: Accidentes, cierres y obras viales (TomTom Traffic Incidents)
==============================================================================
Extract:
  TomTom Traffic API, Incident Details v5 (clave gratuita de desarrollador,
  2.500 solicitudes al mes sin tarjeta). Una sola consulta por el rectángulo
  de Maipú devuelve los incidentes vigentes con su geometría (punto o línea).
Transform:
  - Se conservan las categorías que afectan el vuelo o la señalética:
      1 Accidente, 3 Condiciones peligrosas, 7 Carril cerrado,
      8 Calle cerrada, 9 Obras, 14 Vehículo detenido.
    Las retenciones (6) y las categorías de clima (2, 4, 5, 10, 11) se
    descartan: la congestión no afecta al dron y el clima lo cubre A2.
  - Cada incidente conserva su geometría original y un radio de afectación
    (radio_afectacion_m): el ruteo bloquea o penaliza las aristas a menos de
    ese radio con ST_DWithin.
  - Los accidentes se marcan con revisar_senaletica = true: un choque puede
    derribar señales y semáforos (FHWA, Maintenance of Signs and Sign Supports).
Load (archivo):
  - data/tomtom_incidentes_maipu.geojson

Uso:
  python Amenazas/A4/tomtom.py
Variables de entorno:
  TOMTOM_API_KEY   clave de https://developer.tomtom.com (obligatoria)
"""

import os
import sys
from pathlib import Path

DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(DIR.parent))
import comun  # noqa: E402

SALIDA = DIR / "data" / "tomtom_incidentes_maipu.geojson"
URL = "https://api.tomtom.com/traffic/services/5/incidentDetails"
CAMPOS = ("{incidents{type,geometry{type,coordinates},properties{id,iconCategory,magnitudeOfDelay,"
          "events{description,code,iconCategory},startTime,endTime,from,to,length,delay,roadNumbers,"
          "timeValidity,probabilityOfOccurrence,numberOfReports,lastReportTime}}}")

# iconCategory -> (tipo_amenaza, nombre, nivel_riesgo, accion_grafo, radio_afectacion_m)
CATEGORIAS = {
    1: ("accidente", "Accidente", "ALTO", "BLOQUEAR_ARISTAS", 100),
    8: ("calle_cerrada", "Calle cerrada", "ALTO", "BLOQUEAR_ARISTAS", 60),
    9: ("construccion_vial", "Obras viales", "MEDIO", "PENALIZAR_ARISTAS", 50),
    7: ("carril_cerrado", "Carril cerrado", "MEDIO", "PENALIZAR_ARISTAS", 50),
    3: ("condicion_peligrosa", "Condición peligrosa en la vía", "MEDIO", "PENALIZAR_ARISTAS", 50),
    14: ("vehiculo_detenido", "Vehículo detenido", "BAJO", "PENALIZAR_ARISTAS", 30),
}
DEMORA = {0: "desconocida", 1: "menor", 2: "moderada", 3: "mayor", 4: "indefinida"}


def redondear(coords):
    if coords and isinstance(coords[0], (int, float)):
        return [round(coords[0], 6), round(coords[1], 6)]
    return [redondear(c) for c in coords]


def transformar(incidente):
    p = incidente.get("properties") or {}
    categoria = p.get("iconCategory")
    if categoria not in CATEGORIAS:
        return None
    tipo, nombre, nivel, accion, radio = CATEGORIAS[categoria]
    geom = incidente.get("geometry") or {}
    if geom.get("type") not in ("Point", "LineString") or not geom.get("coordinates"):
        return None
    eventos = [e.get("description") for e in (p.get("events") or []) if e.get("description")]
    tramo = " → ".join(x for x in (p.get("from"), p.get("to")) if x)
    return {"type": "Feature",
            "geometry": {"type": geom["type"], "coordinates": redondear(geom["coordinates"])},
            "properties": {
                "id_amenaza": f"TOMTOM-{p.get('id')}",
                "fuente": "TomTom Traffic Incidents",
                "id_tomtom": p.get("id"),
                "tipo_amenaza": tipo,
                "categoria": nombre,
                "descripcion": "; ".join(eventos) or nombre,
                "tramo": tramo or None,
                "vias": p.get("roadNumbers") or None,
                "largo_m": p.get("length"),
                "demora_s": p.get("delay"),
                "magnitud_demora": DEMORA.get(p.get("magnitudeOfDelay")),
                "probabilidad": p.get("probabilityOfOccurrence"),
                "n_reportes": p.get("numberOfReports"),
                "ultimo_reporte": p.get("lastReportTime"),
                "nivel_riesgo": nivel,
                "accion_grafo": accion,
                "radio_afectacion_m": radio,
                "revisar_senaletica": tipo == "accidente",
                "valido_desde": p.get("startTime"),
                "valido_hasta": p.get("endTime"),
            }}


def consultar(clave, bbox, intentos=3):
    """GET a Incident Details. Los mensajes de error no muestran la URL, que lleva la clave."""
    import time
    import requests

    params = {"key": clave, "bbox": bbox, "fields": CAMPOS, "language": "es-ES",
              "timeValidityFilter": "present"}
    ultimo = None
    for intento in range(1, intentos + 1):
        try:
            resp = requests.get(URL, params=params, timeout=60,
                                headers={"User-Agent": "Inspeccion-vial-UAV/1.0 (UDP CIT3435)"})
            if resp.status_code in (401, 403):
                raise RuntimeError(f"HTTP {resp.status_code}: la clave no es válida o no tiene acceso a Traffic API")
            if resp.status_code != 200:
                raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
            return resp.json()
        except requests.RequestException as error:
            ultimo = f"{type(error).__name__}"
        except (RuntimeError, ValueError) as error:
            ultimo = str(error)
            if "clave" in ultimo:
                break
        print(f"    [-] Intento {intento}/{intentos} falló: {ultimo}")
        time.sleep(2 ** intento)
    raise RuntimeError(ultimo)


def main():
    comun.cargar_env()
    clave = os.getenv("TOMTOM_API_KEY", "").strip()
    if not clave:
        print("[-] Falta TOMTOM_API_KEY en el .env (clave gratuita en https://developer.tomtom.com).")
        sys.exit(1)
    bbox = ",".join(str(x) for x in comun.AOI_MAIPU)
    print(f"[*] TomTom: incidentes vigentes en el rectángulo de Maipú ({bbox})...")
    try:
        datos = consultar(clave, bbox)
    except RuntimeError as error:
        print(f"[-] TomTom no respondió: {error}")
        print("[-] Se conserva el archivo anterior si existe.")
        sys.exit(1)

    incidentes = datos.get("incidents") or []
    features = [f for f in (transformar(i) for i in incidentes) if f]
    resumen = {}
    for f in features:
        resumen[f["properties"]["categoria"]] = resumen.get(f["properties"]["categoria"], 0) + 1
    print(f"    [+] {len(incidentes)} incidentes recibidos, {len(features)} relevantes: {resumen or 'ninguno'}")
    comun.guardar_geojson(SALIDA, "amenaza_a4_tomtom_incidentes_maipu", features, {
        "amenaza": "A4 - Accidentes, cierres y obras viales",
        "fuente": "TomTom Traffic API · Incident Details v5",
        "aoi_maipu_bbox": list(comun.AOI_MAIPU),
        "incidentes_recibidos": len(incidentes),
        "por_categoria": resumen,
        "categorias_descartadas": "retenciones (6) y clima (2, 4, 5, 10, 11)",
    })


if __name__ == "__main__":
    main()
