# Módulo de Extracción de Amenazas: SOSAFE

Este módulo realiza la consulta `GET` automatizada a la API V3 de **SOSAFE** mediante credenciales en `.env`, guardando los datos en crudo y aplicando un filtrado por comuna.

---

## Estructura del Módulo

```text
Amenazas/sosafe/
├── .env                  # Credenciales de SOSAFE (ignorado en git)
├── .env.example          # Plantilla de configuración
├── extractor.py          # Lógica de conexión HTTP GET y guardado de JSONs
├── run.py                # Script de ejecución directa
└── data/                 # Salidas generadas
    ├── raw_reports.json         # Todos los reportes devueltos por la API
    └── filtered_reports.json    # Reportes filtrados exclusivamente para Maipú
```

---

## Ejecución

```bash
cd Inspeccion-vial-UAV/Amenazas/sosafe
python3 run.py
```
O directamente:
```bash
python3 extractor.py
```
