"""Persistencia local de campañas y progreso operativo, independiente de P7/P8."""

from datetime import datetime
import json
import threading
import time


def _json(valor):
    if isinstance(valor, datetime):
        return {'__fecha_operativa__': valor.isoformat()}
    if isinstance(valor, set):
        return {'__conjunto_operativo__': sorted(valor)}
    # Los índices de las matrices no se guardan; los escalares numpy sí pueden
    # aparecer en los diccionarios de la instancia.
    if hasattr(valor, 'item'):
        return valor.item()
    raise TypeError(f'Valor no persistible: {type(valor).__name__}')


def _objeto(valor):
    if set(valor) == {'__fecha_operativa__'}:
        return datetime.fromisoformat(valor['__fecha_operativa__'])
    if set(valor) == {'__conjunto_operativo__'}:
        return set(valor['__conjunto_operativo__'])
    return valor


def _tabla(conn):
    with conn.cursor() as cur:
        cur.execute('''CREATE TABLE IF NOT EXISTS campanas_operativas (
            id TEXT PRIMARY KEY, registro JSONB NOT NULL,
            actualizado TIMESTAMPTZ NOT NULL DEFAULT now())''')


def guardar(conn, plan_id, registro):
    # Locks y el reloj monotónico solo tienen significado dentro del proceso.
    datos = {k: v for k, v in registro.items() if k not in
             ('creado', 'cerrojo', 'cerrojo_operacion')}
    cuerpo = json.dumps(datos, default=_json, ensure_ascii=False, allow_nan=False)
    _tabla(conn)
    with conn.cursor() as cur:
        cur.execute('''INSERT INTO campanas_operativas(id,registro) VALUES (%s,%s::jsonb)
            ON CONFLICT(id) DO UPDATE SET registro=excluded.registro, actualizado=now()''',
                    (plan_id, cuerpo))
    conn.commit()


def cargar(conn, plan_id):
    _tabla(conn)
    with conn.cursor() as cur:
        cur.execute('SELECT registro::text FROM campanas_operativas WHERE id=%s', (plan_id,))
        fila = cur.fetchone()
    conn.commit()
    if fila is None:
        return None
    r = json.loads(fila[0], object_hook=_objeto)
    for clave in ('huellas', 'requeridas'):
        r[clave] = {int(k): v for k, v in r[clave].items()}
    modelo = r.get('modelo')
    if modelo:
        for clave in ('aristas', 'nodos'):
            modelo[clave] = {int(k): v for k, v in modelo[clave].items()}
        for clave, filas in modelo.get('probabilidades', {}).items():
            modelo['probabilidades'][clave] = {int(k): v for k, v in filas.items()}
    for estado in r.get('operacion', {}).values():
        for clave in ('colas', 'bases', 'cursores', 'pendientes', 'tareas'):
            estado[clave] = {int(k): v for k, v in estado[clave].items()}
        # JSON no conserva referencias compartidas: restaurar el vínculo del
        # job pendiente con la cabeza de la cola que validará confirmar().
        rev = estado.get('revision')
        if rev:
            d = int(rev['publico']['dron'])
            rev['job'] = estado['colas'][d][0]
    # No conservar copias de matrices ni dependencias temporales del proceso.
    r.update(creado=time.monotonic(), cerrojo=threading.Lock(), cerrojo_operacion=threading.Lock())
    return r
