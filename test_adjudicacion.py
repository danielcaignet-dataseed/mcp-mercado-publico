"""Prueba local: verifica que _upsert_licitacion puebla adjudicacion_item
cuando la respuesta de la API trae el bloque Adjudicacion.

No consume cuota de ChileCompra: usa una respuesta sintetica con la forma
documentada del endpoint v1 de detalle.
"""
import os
import sys
import tempfile
from pathlib import Path

d = tempfile.mkdtemp()
os.environ["MP_HOME"] = d
os.environ["MP_DB"] = str(Path(d) / "t.duckdb")
os.environ["MP_CATALOGO"] = str(Path(d) / "cat.duckdb")
sys.path.insert(0, str(Path(__file__).parent / "src"))

from mp_mcp.config import Config          # noqa: E402
from mp_mcp.store import Store            # noqa: E402
from mp_mcp import cli                    # noqa: E402

# Forma real del endpoint v1: Listado[].Items.Listado[].Adjudicacion
LIC = {
    "CodigoExterno": "1509-5-LE26",
    "Nombre": "Suministro de conductores de cobre",
    "Estado": "Adjudicada",
    "Moneda": "CLP",
    "Tipo": "LE",
    "Comprador": {
        "CodigoOrganismo": "6945",
        "NombreOrganismo": "Municipalidad de Prueba",
        "RegionUnidad": "Region Metropolitana de Santiago",
        "ComunaUnidad": "Santiago",
    },
    "Fechas": {
        "FechaPublicacion": "2026-06-01T10:00:00",
        "FechaCierre": "2026-06-20T15:00:00",
        "FechaAdjudicacion": "2026-07-05T12:00:00",
    },
    "Items": {"Listado": [
        {
            "Correlativo": 1,
            "CodigoProducto": "26111600",
            "NombreProducto": "Cable de cobre desnudo",
            "Descripcion": "Conductor 4 AWG",
            "UnidadMedida": "Metro",
            "Cantidad": 500,
            "Adjudicacion": {
                "MontoUnitario": 3450,
                "CantidadAdjudicada": 500,
                "RutProveedor": "76123456-7",
                "NombreProveedor": "Electro Insumos SpA",
            },
        },
        {   # item sin adjudicar: NO debe crear fila en adjudicacion_item
            "Correlativo": 2,
            "CodigoProducto": "26111700",
            "NombreProducto": "Aislador ceramico",
            "UnidadMedida": "Unidad",
            "Cantidad": 20,
            "Adjudicacion": {},
        },
    ]},
}

store = Store(Config.from_env())
n = cli._upsert_licitacion(store, LIC)

lic = store.one("SELECT count(*) FROM licitacion")[0]
items = store.one("SELECT count(*) FROM licitacion_item")[0]
adj = store.one("SELECT count(*) FROM adjudicacion_item")[0]
fila = store.one(
    "SELECT rut_proveedor, nombre_proveedor, precio_unitario, "
    "cantidad_adjudicada, fecha_adjudicacion, unspsc_commodity "
    "FROM adjudicacion_item")

print(f"filas devueltas por _upsert_licitacion: {n}")
print(f"licitacion={lic}  licitacion_item={items}  adjudicacion_item={adj}")
print(f"fila adjudicada: {fila}")

fallos = []
if lic != 1:
    fallos.append(f"licitacion deberia ser 1, es {lic}")
if items != 2:
    fallos.append(f"licitacion_item deberia ser 2, es {items}")
if adj != 1:
    fallos.append(f"adjudicacion_item deberia ser 1 (solo el item con MontoUnitario), es {adj}")
if fila:
    if fila[0] != "76123456-7":
        fallos.append(f"rut_proveedor mal: {fila[0]}")
    if float(fila[2]) != 3450.0:
        fallos.append(f"precio_unitario mal: {fila[2]}")
    if float(fila[3]) != 500.0:
        fallos.append(f"cantidad_adjudicada mal: {fila[3]}")
    if str(fila[4]) != "2026-07-05":
        fallos.append(f"fecha_adjudicacion mal: {fila[4]}")
    if fila[5] != "26111600":
        fallos.append(f"unspsc_commodity mal: {fila[5]}")
else:
    fallos.append("no se inserto ninguna fila en adjudicacion_item")

store.close()
if fallos:
    print("\nFALLOS:")
    for f in fallos:
        print("  -", f)
    sys.exit(1)
print("\nOK: el cargador de adjudicaciones funciona. La tabla estaba vacia por")
print("    haber ingestado solo --estado activas, no por falta de codigo.")
