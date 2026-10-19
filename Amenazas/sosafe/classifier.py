"""
==============================================================================
MÓDULO: Clasificador de Amenazas Aeronáuticas con IA (Gemini)
==============================================================================
Analiza semánticamente los reportes ciudadanos de SOSAFE para identificar
únicamente amenazas físicas o normativas reales al vuelo de drones (UAVs):
- Fuegos artificiales / Pirotecnia
- Disparos / Balaceras
- Cables eléctricos cortados o colgando
- Incendios y columnas térmicas violentas
- Helicópteros y aeronaves de emergencia a baja cota

Descarta expresamente quejas menores, falsos disturbios (ej. niños jugando,
peleas verbales) y avisos comunitarios.

Funciona sin dependencias externas (utiliza urllib de la librería estándar).
"""

import os
import json
import math
import urllib.request
import urllib.parse
import urllib.error
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
DATA_DIR = CURRENT_DIR / "data"

# ==============================================================================
# PROMPT DE SISTEMA: EVALUACIÓN DE RIESGO AERONÁUTICO PARA DRONES
# ==============================================================================
SYSTEM_INSTRUCTION = """
Eres un especialista en seguridad operacional aeronáutica y navegación de Vehículos Aéreos No Tripulados (UAVs / Drones).
Tu misión es analizar reportes ciudadanos comunitarios urbanos y discriminar con criterio técnico estricto si un evento representa una amenaza real para una aeronave volando entre 30 y 120 metros de altitud.

CRITERIO DE AMENAZA REAL (es_amenaza = true):
Debes clasificar como amenaza ÚNICAMENTE eventos con riesgo cinético, térmico, electromagnético o de espacio aéreo:
1. PIROTECNIA_FUEGOS_ARTIFICIALES: Cualquier mención de fuegos artificiales, fuegos de artificio, petardos de altura o bengalas. (Riesgo: proyectiles explosivos que alcanzan la cota de 30-100m).
2. DISPAROS_BALACERA: Detonaciones de armas de fuego, disparos al aire, tiroteos o balaceras activas. (Riesgo: proyectiles balísticos ascendentes/descendentes).
3. OBSTACULO_CABLES_POSTES: Cables de tendido eléctrico arrancados, colgando, buses/camiones enganchados con cables, postes o semáforos caídos. (Riesgo: colisión física fatal con hélices).
4. INCENDIO_COLUMNA_TERMICA: Incendios estructurales, vehiculares o de pastizales con fuego activo o humo negro/cenizas denso. (Riesgo: corrientes ascendentes violentas, ceguera de sensores ópticos).
5. AERONAVE_EMERGENCIA: Presencia de helicópteros policiales (Carabineros) o de rescate a baja cota. (Riesgo: colisión en vuelo; obligación de aterrizaje inmediato).

CRITERIO DE EXCLUSIÓN / RUIDO (es_amenaza = false):
Debes clasificar como NO amenaza (es_amenaza = false, nivel_riesgo = NINGUNO):
- Falsos disturbios y quejas vecinales menores: niños jugando, vecinos discutiendo, música fuerte, personas en situación de calle, quejas por frecuencia de buses o paraderos.
- Avisos comunitarios, comercio vecinal, ventas, mascotas perdidas.
- Problemas de alcantarillado, basura o baches en calzada (a menos que involucre fuego o cables).
- NOTA DE ALCANCE: Si un reporte menciona que una señalética, letrero o semáforo está caído o dañado, márcalo como categoria "INFRAESTRUCTURA_VIAL_DANADA" y es_amenaza = false (estos eventos representan futuras oportunidades de inspección/recompensa, no una amenaza al vuelo en crucero).

FORMATO DE SALIDA:
Devuelve un JSON estricto con un array de objetos con las siguientes claves exactas:
- id: entero (ID del reporte)
- es_amenaza: booleano (true o false)
- nivel_riesgo: "CRITICO", "ALTO", "MEDIO", "NINGUNO"
- categoria: una de las categorías anteriores o "RUIDO_IRRELEVANTE" o "INFRAESTRUCTURA_VIAL_DANADA"
- radio_buffer_metros: entero (ej: 400 para pirotecnia/disparos, 500 para incendios/aeronaves, 300 para cables, 0 para no amenaza)
- justificacion: explicación técnica breve y directa enfocada en el impacto en el dron
"""


def load_env_file():
    """Carga variables desde .env si existen."""
    env_paths = [
        CURRENT_DIR / ".env",
        CURRENT_DIR.parent.parent / ".env",
        CURRENT_DIR / ".env.example",
    ]
    for p in env_paths:
        if p.exists():
            with open(p, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        if k.strip() not in os.environ:
                            os.environ[k.strip()] = v.strip()
            break


def create_circular_polygon(lon, lat, radius_meters, num_points=32):
    """Genera coordenadas GeoJSON de un polígono circular alrededor de un punto."""
    coords = []
    r_lat = radius_meters / 111320.0
    r_lon = radius_meters / (111320.0 * math.cos(math.radians(lat)))

    for i in range(num_points + 1):
        angle = 2 * math.pi * i / num_points
        d_lon = r_lon * math.cos(angle)
        d_lat = r_lat * math.sin(angle)
        coords.append([round(lon + d_lon, 6), round(lat + d_lat, 6)])

    return [coords]


def classify_with_rule_based_fallback(reports):
    """
    Clasificador heurístico de respaldo cuando no se cuenta con GEMINI_API_KEY.
    Aplica el mismo criterio de exclusión estricta de ruido.
    """
    classified = []
    keywords_fuegos = ["fuego artificial", "fuegos artificiales", "pirotecnia", "bengala", "estruendo", "petardo"]
    keywords_disparos = ["disparo", "disparos", "balacera", "balazo", "tiroteo", "arma de fuego"]
    keywords_cables = ["cable", "cables", "enganchado", "tendido electrico", "poste caido", "transformador"]
    keywords_incendio = ["incendio", "quemando", "fuego", "humo negro", "bomberos"]
    keywords_aeronave = ["helicoptero", "aeronave"]
    keywords_senales = ["senaletica", "señaletica", "letrero", "disco pare", "semaforo apagado", "semaforo malo"]

    for r in reports:
        desc = (r.get("description") or "").lower()
        title = (r.get("title") or "").lower()
        full_text = f"{title} {desc}"

        es_amenaza = False
        nivel = "NINGUNO"
        categoria = "RUIDO_IRRELEVANTE"
        buffer_m = 0
        justificacion = "Incidencia sin impacto cinético, térmico o de espacio aéreo para el dron."

        # 1. Pirotecnia
        if any(k in full_text for k in keywords_fuegos):
            es_amenaza = True
            nivel = "CRITICO"
            categoria = "PIROTECNIA_FUEGOS_ARTIFICIALES"
            buffer_m = 400
            justificacion = "Pirotecnia explosiva a 30-100m de altura; riesgo directo de destrucción por impacto."
        # 2. Disparos
        elif any(k in full_text for k in keywords_disparos):
            es_amenaza = True
            nivel = "CRITICO"
            categoria = "DISPAROS_BALACERA"
            buffer_m = 400
            justificacion = "Proyectiles balísticos en trayectoria vertical y presencia armada activa."
        # 3. Cables / Postes
        elif any(k in full_text for k in keywords_cables):
            es_amenaza = True
            nivel = "CRITICO"
            categoria = "OBSTACULO_CABLES_POSTES"
            buffer_m = 300
            justificacion = "Cables cortados o postes en caída representan colisión física fatal con rotores."
        # 4. Aeronaves a baja cota
        elif any(k in full_text for k in keywords_aeronave):
            es_amenaza = True
            nivel = "CRITICO"
            categoria = "AERONAVE_EMERGENCIA"
            buffer_m = 500
            justificacion = "Presencia de helicóptero a baja cota; obligación normativa de descender."
        # 5. Incendios
        elif any(k in full_text for k in keywords_incendio) and r.get("type") == 19:
            es_amenaza = True
            nivel = "ALTO"
            categoria = "INCENDIO_COLUMNA_TERMICA"
            buffer_m = 500
            justificacion = "Humo denso y columnas térmicas que desestabilizan el control de actitud del dron."
        # 6. Señalética / Infraestructura dañada (Fuera de alcance de amenaza - Futura ganancia)
        elif any(k in full_text for k in keywords_senales):
            es_amenaza = False
            nivel = "NINGUNO"
            categoria = "INFRAESTRUCTURA_VIAL_DANADA"
            buffer_m = 0
            justificacion = "Elemento vial dañado. Fuera de alcance de amenaza; futura oportunidad de inspección."

        classified.append({
            "id": r.get("id"),
            "uuid": r.get("uuid"),
            "tipo_original": r.get("title"),
            "descripcion": r.get("description"),
            "location": r.get("location"),
            "coordenadas": [r.get("latitude"), r.get("longitude")],
            "fecha": r.get("created_at"),
            "es_amenaza": es_amenaza,
            "nivel_riesgo": nivel,
            "categoria": categoria,
            "radio_buffer_metros": buffer_m,
            "justificacion": justificacion
        })

    return classified


def classify_with_gemini(reports, api_key):
    """
    Envía los reportes a Gemini 3.8 Flash mediante REST para clasificarlos semánticamente
    siguiendo las directrices del system prompt.
    """
    model_name = os.getenv("GEMINI_MODEL", "gemini-3.8-flash").strip()
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"

    input_list = [
        {
            "id": r.get("id"),
            "tipo": r.get("title"),
            "descripcion": r.get("description"),
            "location": r.get("location"),
        }
        for r in reports
    ]

    prompt_content = (
        "Analiza la siguiente lista de reportes ciudadanos de SOSAFE. "
        "Aplica el criterio técnico estricto de seguridad para drones y descarta todo el ruido irrelevante.\n\n"
        f"REPORTES A ANALIZAR:\n{json.dumps(input_list, ensure_ascii=False)}"
    )

    payload = {
        "system_instruction": {
            "parts": [{"text": SYSTEM_INSTRUCTION}]
        },
        "contents": [
            {"parts": [{"text": prompt_content}]}
        ],
        "generationConfig": {
            "response_mime_type": "application/json",
            "temperature": 0.1
        }
    }

    req_body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=req_body,
        headers={"Content-Type": "application/json"},
        method="POST"
    )

    print(f"[*] Evaluando {len(reports)} reportes con Gemini AI ({model_name})...")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
            res_json = json.loads(body)

        text_response = res_json["candidates"][0]["content"]["parts"][0]["text"]
        ai_classifications = json.loads(text_response)

        class_map = {item["id"]: item for item in ai_classifications if "id" in item}

        merged = []
        for r in reports:
            cid = r.get("id")
            ai_info = class_map.get(cid, {})
            merged.append({
                "id": cid,
                "uuid": r.get("uuid"),
                "tipo_original": r.get("title"),
                "descripcion": r.get("description"),
                "location": r.get("location"),
                "coordenadas": [r.get("latitude"), r.get("longitude")],
                "fecha": r.get("created_at"),
                "es_amenaza": ai_info.get("es_amenaza", False),
                "nivel_riesgo": ai_info.get("nivel_riesgo", "NINGUNO"),
                "categoria": ai_info.get("categoria", "RUIDO_IRRELEVANTE"),
                "radio_buffer_metros": ai_info.get("radio_buffer_metros", 0),
                "justificacion": ai_info.get("justificacion", "Clasificado por Gemini AI.")
            })
        print(f"[+] Clasificación semántica de Gemini AI ({model_name}) completada.")
        return merged

    except Exception as e:
        print(f"[!] Error al invocar Gemini API ({e}). Usando clasificador heurístico de respaldo...")
        return classify_with_rule_based_fallback(reports)


def export_geojson_geofences(classified_reports):
    """
    Genera un archivo GeoJSON FeatureCollection con:
    - Puntos de las amenazas confirmadas.
    - Polígonos circulares de las No-Fly Zones de exclusión aérea para Leaflet.
    """
    features = []

    for item in classified_reports:
        if not item.get("es_amenaza"):
            continue

        coords = item.get("coordenadas", [0, 0])
        lat, lon = coords[0], coords[1]
        buffer_m = item.get("radio_buffer_metros", 300)
        nivel = item.get("nivel_riesgo", "MEDIO")

        color_map = {
            "CRITICO": "#d90429",  # Rojo intenso (fuegos artificiales, balaceras, cables)
            "ALTO": "#f77f00",     # Naranja (incendios activos)
            "MEDIO": "#fcbf49",    # Amarillo
        }
        color = color_map.get(nivel, "#d90429")

        # 1. Marcador del punto de amenaza
        features.append({
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [lon, lat]
            },
            "properties": {
                "id": item["id"],
                "tipo": item["tipo_original"],
                "categoria": item["categoria"],
                "nivel_riesgo": nivel,
                "descripcion": item["descripcion"],
                "justificacion": item["justificacion"],
                "radio_buffer": buffer_m,
                "marker_color": color,
                "tipo_geometria": "amenaza_punto"
            }
        })

        # 2. Polígono circular de la No-Fly Zone
        if buffer_m > 0:
            circle_poly = create_circular_polygon(lon, lat, buffer_m)
            features.append({
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": circle_poly
                },
                "properties": {
                    "id": item["id"],
                    "geofence_id": f"nfz_{item['id']}",
                    "nivel_riesgo": nivel,
                    "categoria": item["categoria"],
                    "fillColor": color,
                    "color": color,
                    "weight": 2,
                    "fillOpacity": 0.35,
                    "radio_metros": buffer_m,
                    "tipo_geometria": "geocerca_exclusion"
                }
            })

    geojson_doc = {
        "type": "FeatureCollection",
        "name": "uav_threat_geofences_maipu",
        "features": features
    }

    out_path = DATA_DIR / "threats_geofences.geojson"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(geojson_doc, f, indent=2, ensure_ascii=False)

    print(f"[+] GeoJSON de No-Fly Zones exportado: {out_path} ({len(features)} geometrías)")
    return geojson_doc


def export_active_threats_for_graph(amenazas_reales):
    """
    Genera un archivo JSON filtrado y estructurado exclusivamente para el algoritmo
    de ruteo (pgRouting, NetworkX o Dijkstra).
    
    Contiene únicamente las amenazas activas confirmadas (descartando todo el ruido),
    con coordenadas, radio de afectación y la directiva de penalización de aristas.
    """
    graph_threats = []
    for item in amenazas_reales:
        coords = item.get("coordenadas", [0, 0])
        lat, lon = coords[0], coords[1]
        buffer_m = item.get("radio_buffer_metros", 300)
        nivel = item.get("nivel_riesgo", "MEDIO")

        # Política de penalización para el grafo:
        # - CRITICO / ALTO: Bloqueo total de la arista (costo infinito / eliminación)
        # - MEDIO: Penalización severa multiplicativa (ej: x5 o x10 del costo base)
        accion = "BLOQUEAR_ARISTAS" if nivel in ["CRITICO", "ALTO"] else "PENALIZAR_COSTO"
        factor_penalizacion = "INF" if accion == "BLOQUEAR_ARISTAS" else 10.0

        graph_threats.append({
            "id": item["id"],
            "uuid": item.get("uuid"),
            "categoria": item["categoria"],
            "nivel_riesgo": nivel,
            "latitud": lat,
            "longitud": lon,
            "coordenadas": [lat, lon],
            "radio_buffer_metros": buffer_m,
            "accion_grafo": accion,
            "factor_penalizacion": factor_penalizacion,
            "descripcion": item.get("descripcion", ""),
            "justificacion": item.get("justificacion", "")
        })

    out_path = DATA_DIR / "active_threats_graph.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(graph_threats, f, indent=2, ensure_ascii=False)

    print(f"[+] JSON limpio para el grafo exportado: {out_path} ({len(graph_threats)} amenazas activas)")
    return graph_threats


def run_classification():
    """Ejecuta la clasificación sobre data/filtered_reports.json."""
    load_env_file()
    filtered_path = DATA_DIR / "filtered_reports.json"

    if not filtered_path.exists():
        print(f"[-] No existe {filtered_path}. Ejecuta primero 'extractor.py'.")
        return None

    with open(filtered_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    reports = data.get("reports", [])
    if not reports:
        print("[!] No hay reportes en filtered_reports.json para clasificar.")
        return None

    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if api_key and not api_key.startswith("tu_api_key"):
        classified = classify_with_gemini(reports, api_key)
    else:
        print("[*] GEMINI_API_KEY no detectada. Usando clasificador heurístico de amenazas reales...")
        classified = classify_with_rule_based_fallback(reports)

    # Asegurar que el directorio data/ exista
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # Guardar JSON con las amenazas clasificadas (auditoría completa: amenazas + descartados)
    classified_path = DATA_DIR / "classified_threats.json"
    amenazas_reales = [c for c in classified if c["es_amenaza"]]
    ruido_descartado = [c for c in classified if not c["es_amenaza"]]

    resumen = {
        "total_analizados": len(classified),
        "total_amenazas_confirmadas": len(amenazas_reales),
        "total_ruido_descartado": len(ruido_descartado),
        "amenazas": amenazas_reales,
        "descartados": ruido_descartado
    }

    with open(classified_path, "w", encoding="utf-8") as f:
        json.dump(resumen, f, indent=2, ensure_ascii=False)

    print(f"[+] Archivo clasificado guardado: {classified_path}")
    print(f"    - Amenazas reales detectadas: {len(amenazas_reales)}")
    print(f"    - Eventos descartados (ruido): {len(ruido_descartado)}")

    # 1. Exportar GeoJSON para Leaflet y consultas PostGIS espaciales
    export_geojson_geofences(classified)

    # 2. Exportar JSON limpio exclusivamente con amenazas activas para el algoritmo de ruteo
    export_active_threats_for_graph(amenazas_reales)

    return classified


if __name__ == "__main__":
    run_classification()
