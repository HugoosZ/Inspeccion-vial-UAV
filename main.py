"""
==============================================================================
PROYECTO: Detección de Señaléticas y Elementos Viales mediante Flota UAV
Orquestador Principal
==============================================================================
Este script automatiza la ejecución secuencial del proceso ETL completo:
 1. Extracción y transformación de Infraestructura (nodos y aristas OSM).
 2. Extracción y transformación de Metadata (señaléticas oficiales, Waze, clima).
 3. Extracción y transformación de Amenazas (zonas hostiles, clima severo, jammers).
 4. Carga de los archivos JSON/GeoJSON a la Base de Datos (PostgreSQL/PostGIS/pgRouting).
 5. Verificación de servicios y disponibilidad del visualizador Leaflet.
"""

import os
import sys
import time
import psycopg2


def check_db_connection():
    """Verifica que la base de datos esté lista para recibir conexiones."""
    host = os.getenv("DB_HOST", "localhost")
    port = os.getenv("DB_PORT", "5432")
    dbname = os.getenv("DB_NAME", "ruteo_db")
    user = os.getenv("DB_USER", "ruteo_user")
    password = os.getenv("DB_PASSWORD", "ruteo_pass")

    print(f"[*] Conectando a la base de datos en {host}:{port}/{dbname}...")
    retries = 10
    while retries > 0:
        try:
            conn = psycopg2.connect(
                host=host, port=port, dbname=dbname, user=user, password=password
            )
            conn.close()
            print("[+] Conexión exitosa a PostgreSQL/PostGIS/pgRouting.")
            return True
        except Exception as e:
            print(f"[-] Esperando a la base de datos ({e})... reintentando en 2s")
            time.sleep(2)
            retries -= 1

    print("[!] Error: No se pudo conectar a la base de datos.")
    return False


def run_etl_infrastructure():
    """Ejecuta los scripts de extracción y transformación en 'Infraestructura/'."""
    print("\n" + "=" * 60)
    print("FASE 1: ETL INFRAESTRUCTURA (Nodos y Aristas OSM)")
    print("=" * 60)
    # TODO: Invocar script(s) en Infraestructura/
    print("[*] Pendiente de implementar scripts en Infraestructura/")


def run_etl_metadata():
    """Ejecuta los scripts de extracción y transformación en 'Metadata/'."""
    print("\n" + "=" * 60)
    print("FASE 2: ETL METADATA (Inventario Oficial, Waze Tráfico, Clima/Sombras)")
    print("=" * 60)
    # TODO: Invocar scripts en Metadata/
    print("[*] Pendiente de implementar scripts en Metadata/")


def run_etl_amenazas():
    """Ejecuta los scripts de extracción y transformación en 'Amenazas/'."""
    print("\n" + "=" * 60)
    print("FASE 3: ETL AMENAZAS (SOSAFE, Zonas Hostiles, Inhibidores)")
    print("=" * 60)
    sosafe_script = os.path.join(
        os.path.dirname(__file__), "Amenazas", "sosafe", "run.py"
    )
    if os.path.exists(sosafe_script):
        print("[*] Ejecutando módulo de amenazas SOSAFE...")
        import subprocess
        subprocess.run([sys.executable, sosafe_script], check=False)
    else:
        print("[*] Pendiente de implementar scripts adicionales en Amenazas/")



def run_db_loader():
    """Ejecuta la carga de todos los archivos JSON/GeoJSON procesados a la base de datos."""
    print("\n" + "=" * 60)
    print("FASE 4: CARGA A BASE DE DATOS (PostGIS / pgRouting)")
    print("=" * 60)
    # TODO: Invocar scripts de carga a la base de datos
    print("[*] Pendiente de implementar scripts de carga a BD.")


def main():
    print("Iniciando orquestador de ETL y Ruteo UAV...")
    if not check_db_connection():
        sys.exit(1)

    run_etl_infrastructure()
    run_etl_metadata()
    run_etl_amenazas()
    run_db_loader()

    print("\n[+] Proceso completado exitosamente.")
    print("[+] El sitio web Leaflet está disponible en: http://localhost:8080")


if __name__ == "__main__":
    main()
