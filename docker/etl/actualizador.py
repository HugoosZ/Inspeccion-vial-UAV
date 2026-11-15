"""Actualización manual previa a una salida; servicio HTTP interno de la red Docker.

No descarga al importar ni expone comandos arbitrarios. Cada fuente intenta su
extractor conocido, valida el archivo final y carga la fuente en PostGIS. Si la
descarga falla, conserva el archivo previo y lo reutiliza. La vigencia de los
reportes se decide después en P5, no aquí. Nunca se publican logs de extractores
porque pueden contener URLs o respuestas con datos privados/credenciales.
"""

from datetime import datetime, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request


RAIZ = Path(__file__).resolve().parents[2]
CERROJO = threading.Lock()
MARCA_A3 = "ACTUALIZACION_A3_JSON="
FUENTES = {
    "A1": {
        "extractor": "Amenazas/A1/extractor.py", "cargador": "Amenazas/A1/cargar.py",
        "archivo": "Amenazas/A1/data/incendios_maipu.geojson",
    },
    "A2": {
        "extractor": "Amenazas/A2/extractor.py", "cargador": "Amenazas/A2/cargar.py",
        "archivo": "Amenazas/A2/data/clima_maipu.geojson",
    },
    "A3": {
        "extractor": None, "cargador": "Amenazas/A3/cargar.py",
        "archivo": "Amenazas/A3/data/threats_geofences.geojson",
        "auxiliares": ["Amenazas/A3/data/" + nombre for nombre in (
            "raw_reports.json", "filtered_reports.json", "classified_threats.json",
            "active_threats_graph.json")],
    },
    "A4": {
        "extractor": "Amenazas/A4/tomtom.py", "cargador": "Amenazas/A4/cargar_tomtom.py",
        "archivo": "Amenazas/A4/data/tomtom_incidentes_maipu.geojson",
    },
}


def ahora():
    return datetime.now(timezone.utc).isoformat()


def timeout_configurado():
    try:
        valor = float(os.getenv("ACTUALIZACION_TIMEOUT_S", "120"))
        if not 5 <= valor <= 600:
            raise ValueError
    except ValueError as exc:
        raise ValueError("ACTUALIZACION_TIMEOUT_S debe estar entre 5 y 600 segundos") from exc
    return valor


def ejecutar(argumentos, timeout):
    """Ejecuta Python sin shell, con tiempo acotado y sin devolver logs al cliente."""
    proceso = subprocess.Popen(
        [sys.executable, *map(str, argumentos)], cwd=RAIZ,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
        start_new_session=os.name != "nt",
    )
    try:
        salida, _ = proceso.communicate(timeout=timeout)
        return proceso.returncode, salida, False
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            proceso.kill()
        else:
            # Detiene también descendientes si un extractor incorpora procesos.
            try:
                os.killpg(proceso.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        salida, _ = proceso.communicate()
        return proceso.returncode, salida, True


def geojson_valido(ruta):
    try:
        contenido = ruta.read_bytes()
        datos = json.loads(contenido)
        if datos.get("type") != "FeatureCollection" or not isinstance(datos.get("features"), list):
            return None
        for feature in datos["features"]:
            if (not isinstance(feature, dict) or feature.get("type") != "Feature"
                    or not isinstance(feature.get("properties"), dict)
                    or not isinstance(feature.get("geometry"), dict)):
                return None
        return contenido
    except (OSError, ValueError, AttributeError, TypeError):
        return None


def restaurar(copias):
    """Restaura únicamente los archivos conocidos de esta fuente tras un fallo."""
    for ruta, contenido in copias.items():
        if contenido is None:
            ruta.unlink(missing_ok=True)
        else:
            temporal = ruta.with_name(ruta.name + ".restaurar")
            temporal.write_bytes(contenido)
            temporal.replace(ruta)


def actualizar_fuente(codigo, timeout):
    config = FUENTES[codigo]
    archivo = RAIZ / config["archivo"]
    rutas = [archivo, *(RAIZ / p for p in config.get("auxiliares", []))]
    copias = {ruta: ruta.read_bytes() if ruta.exists() else None for ruta in rutas}
    habia_local = geojson_valido(archivo) is not None
    argumentos = ([Path(__file__).resolve(), "--a3"] if codigo == "A3" else
                  [RAIZ / config["extractor"]])
    inicio = time.monotonic()
    remoto, parcial = False, False
    causa = "No se pudo actualizar la fuente remota."
    try:
        rc, salida, agotado = ejecutar(argumentos, timeout)
        valido = geojson_valido(archivo) is not None
        if rc == 0 and not agotado and valido:
            remoto = True
            if codigo == "A3":
                # El extractor original puede devolver datos acumulados y exit 0
                # aunque TODOS los HTTP hayan fallado. No confundirlo con éxito.
                estado = salida.rsplit(MARCA_A3, 1)
                marca = json.loads(estado[1].splitlines()[0]) if len(estado) == 2 else {}
                remoto = int(marca.get("consultas_exitosas", 0)) > 0
                parcial = bool(marca.get("consultas_fallidas", 0)) and remoto
            elif codigo == "A2":
                parcial = "Sin ensamble" in salida
            elif codigo == "A1":
                parcial = "no disponible" in salida
        elif agotado:
            causa = "La actualización remota excedió el tiempo permitido."
        elif not valido:
            causa = "La fuente remota no dejó un GeoJSON final válido."
        if not remoto:
            restaurar(copias)
            if not habia_local:
                return {"estado": "error", "detalle": causa + " No hay archivo local válido.",
                        "segundos": round(time.monotonic() - inicio, 3)}
        rc, _, agotado = ejecutar([RAIZ / config["cargador"]], timeout)
        if rc != 0 or agotado:
            # Los cargadores usan transacciones y no dejan una fuente a medias.
            return {"estado": "error", "detalle": "No se pudo cargar la fuente en la base; "
                    "se mantiene la versión previamente cargada.",
                    "archivo_remoto": remoto, "segundos": round(time.monotonic() - inicio, 3)}
        contenido = geojson_valido(archivo)
        return {
            "estado": "actualizado" if remoto else "local",
            "detalle": ("Datos remotos consultados y cargados."
                        + (" Actualización parcial; consultar cobertura disponible." if parcial else "")
                        if remoto else causa + " Se cargó el archivo local conservado."),
            "parcial": parcial,
            "sha256": hashlib.sha256(contenido).hexdigest() if contenido is not None else None,
            "segundos": round(time.monotonic() - inicio, 3),
        }
    except (OSError, ValueError, TypeError, subprocess.SubprocessError):
        # No propagar mensajes que puedan incluir una URL con claves.
        restaurar(copias)
        return {"estado": "error", "detalle": "Falló la actualización; se conservaron los archivos locales "
                "y la versión cargada previamente en la base.",
                "segundos": round(time.monotonic() - inicio, 3)}


def actualizar(fuentes):
    if (not isinstance(fuentes, list) or any(not isinstance(f, str) or f not in FUENTES for f in fuentes)
            or len(fuentes) != len(set(fuentes))):
        raise ValueError("fuentes debe ser una lista sin duplicados de A1, A2, A3 y A4")
    timeout = timeout_configurado()
    inicio = ahora()
    if not CERROJO.acquire(timeout=1):
        return None
    try:
        resultados = {f: actualizar_fuente(f, timeout) for f in sorted(fuentes)}
        return {"inicio": inicio, "fin": ahora(), "fuentes": resultados}
    finally:
        CERROJO.release()


def ejecutar_sosafe():
    """Conserva el pipeline A3, midiendo HTTP exitosos sin mostrar sus logs."""
    sys.path.insert(0, str(RAIZ / "Amenazas" / "A3"))
    from extractor import fetch_sosafe_reports
    from classifier import run_classification
    original = urllib.request.urlopen
    conteo = {"consultas_exitosas": 0, "consultas_fallidas": 0}

    class RespuestaSosafe:
        """Solo acredita éxito si la respuesta contiene una lista de reportes."""
        def __init__(self, respuesta):
            self.respuesta = respuesta

        def __enter__(self):
            self.respuesta.__enter__()
            return self

        def __exit__(self, *args):
            return self.respuesta.__exit__(*args)

        def read(self, *args, **kwargs):
            contenido = self.respuesta.read(*args, **kwargs)
            try:
                datos = json.loads(contenido)
                if not isinstance(datos, dict) or not isinstance(datos.get("reports"), list):
                    raise ValueError("Respuesta SOSAFE sin reportes")
            except (ValueError, TypeError):
                conteo["consultas_fallidas"] += 1
                raise
            conteo["consultas_exitosas"] += 1
            return contenido

    def consultar(req, *args, **kwargs):
        url = req.full_url if isinstance(req, urllib.request.Request) else str(req)
        sosafe = urllib.parse.urlparse(url).hostname == "api-v3.sosafeapp.com"
        try:
            respuesta = original(req, *args, **kwargs)
            return RespuestaSosafe(respuesta) if sosafe else respuesta
        except Exception:
            if sosafe:
                conteo["consultas_fallidas"] += 1
            raise

    urllib.request.urlopen = consultar
    try:
        reportes = fetch_sosafe_reports()
        if reportes is None and not (RAIZ / "Amenazas/A3/data/filtered_reports.json").exists():
            raise RuntimeError("No hay reportes disponibles")
        if run_classification() is None:
            raise RuntimeError("No se pudo clasificar")
    finally:
        urllib.request.urlopen = original
    print(MARCA_A3 + json.dumps(conteo))


class Handler(BaseHTTPRequestHandler):
    def responder(self, estado, cuerpo):
        datos = json.dumps(cuerpo, ensure_ascii=False).encode("utf-8")
        self.send_response(estado)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(datos)))
        self.end_headers()
        self.wfile.write(datos)

    def do_GET(self):
        if self.path != "/health":
            return self.responder(404, {"error": "Ruta no encontrada"})
        return self.responder(200, {"status": "ok", "servicio": "actualizador_amenazas"})

    def do_POST(self):
        if self.path != "/actualizar":
            return self.responder(404, {"error": "Ruta no encontrada"})
        try:
            longitud = int(self.headers.get("Content-Length", "0"))
            if not 0 < longitud <= 16_384:
                return self.responder(413, {"error": "Cuerpo ausente o demasiado grande"})
            cuerpo = json.loads(self.rfile.read(longitud))
            if not isinstance(cuerpo, dict) or set(cuerpo) != {"fuentes"}:
                raise ValueError("Se requiere únicamente el campo fuentes")
            resultado = actualizar(cuerpo["fuentes"])
        except (ValueError, TypeError):
            return self.responder(400, {"error": "Solicitud inválida; use fuentes: [A1, A2, A3, A4]"})
        except Exception:
            return self.responder(500, {"error": "No se pudo completar la actualización"})
        if resultado is None:
            return self.responder(409, {"error": "Ya hay una actualización en curso"})
        return self.responder(200, resultado)

    def log_message(self, formato, *args):
        # Nunca registrar cuerpos ni parámetros proporcionados por el cliente.
        pass


def main():
    if sys.argv[1:] == ["--a3"]:
        ejecutar_sosafe()
        return
    if sys.argv[1:]:
        raise SystemExit("Argumento no reconocido")
    puerto = int(os.getenv("ACTUALIZADOR_PORT", "8090"))
    servidor = ThreadingHTTPServer(("0.0.0.0", puerto), Handler)
    print(f"Actualizador de amenazas listo en el puerto interno {puerto}", flush=True)
    servidor.serve_forever()


if __name__ == "__main__":
    main()
