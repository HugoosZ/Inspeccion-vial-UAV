"""Ámbito comunal de A1: origen del incendio dentro del límite oficial de Maipú."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "Metadata"))
import comun_arcgis as ag


def limite_maipu():
    # La copia oficial permite cargar y validar datos sin acceso a Internet.
    if ag.ARCHIVO_LIMITE.exists():
        return json.loads(ag.ARCHIVO_LIMITE.read_text(encoding="utf-8"))["geometry"]
    return ag.limite_maipu()  # Sin límite disponible se aborta; no se usa el bbox.


def pertenece(feature, limite):
    p = feature.get("properties", {})
    g = feature.get("geometry") or {}
    if p.get("tipo_amenaza") == "probabilidad_ignicion":
        # El pronóstico se selecciona por el centro de cada celda.
        anillo = g["coordinates"][0]
        lon = (min(x for x, y in anillo) + max(x for x, y in anillo)) / 2
        lat = (min(y for x, y in anillo) + max(y for x, y in anillo)) / 2
    elif p.get("lon") is not None and p.get("lat") is not None:
        lon, lat = p["lon"], p["lat"]
    elif g.get("type") == "Point":
        lon, lat = g["coordinates"][:2]
    else:
        return False  # No atribuir a Maipú un incendio sin origen conocido.
    return ag.dentro_de(float(lon), float(lat), limite)
