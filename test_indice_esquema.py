"""Prueba local del indice de esquema y de la superficie de tools.

Verifica que:
  - sin `entity`, mp_schema_describe NO devuelve nombres de campo pero SI todas
    las limitaciones (la propiedad de honestidad no se toca);
  - con `entity`, el detalle completo vuelve;
  - las tools vivas estan registradas en el servidor.

No consume cuota: no sale a la red.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

d = tempfile.mkdtemp()
os.environ["MP_HOME"] = d
os.environ["MP_DB"] = str(Path(d) / "t.duckdb")
os.environ["MP_CATALOGO"] = str(Path(d) / "cat.duckdb")
sys.path.insert(0, str(Path(__file__).parent / "src"))

from mp_mcp.config import Config   # noqa: E402
from mp_mcp.store import Store     # noqa: E402
from mp_mcp import tools as T      # noqa: E402

store = Store(Config.from_env())
fallos = []

idx = T.mp_schema_describe(store)
det = T.mp_schema_describe(store, "licitacion")

b_idx = len(json.dumps(idx, ensure_ascii=False))
b_det = len(json.dumps(det, ensure_ascii=False))
print(f"indice: {b_idx} bytes   detalle de una entidad: {b_det} bytes")

ents = idx.get("entidades", {})
print(f"entidades en el indice: {len(ents)}")

# 1. El indice no trae nombres de campo
for n, e in ents.items():
    if "dimensiones" in e or "medidas" in e:
        fallos.append(f"el indice trae detalle de columnas en '{n}'")
    if "detalle" not in e:
        fallos.append(f"'{n}' no declara el puntero 'detalle'")

# 2. Las limitaciones SI estan (esto es lo que no se negocia)
con_lim = [n for n, e in ents.items() if e.get("limitaciones")]
print(f"entidades con limitaciones en el indice: {len(con_lim)}/{len(ents)}")
if not con_lim:
    fallos.append("el indice perdio TODAS las limitaciones")

# 3. Con entity, vuelve el detalle
lic = det.get("entidades", {}).get("licitacion", {})
if not lic.get("dimensiones"):
    fallos.append("con entity='licitacion' no volvieron las dimensiones")
if not lic.get("limitaciones"):
    fallos.append("con entity='licitacion' no volvieron las limitaciones")

# 4. El ahorro es real
if b_idx >= b_det * 6:
    fallos.append(f"el indice no ahorra lo esperado ({b_idx} vs {b_det})")

# A partir de aca no se usa mas el store: se cierra para que el servidor pueda
# abrir el mismo archivo en solo-lectura (DuckDB rechaza configuraciones mixtas).
store.close()

# 5. Las tools vivas estan registradas
import mp_mcp.server as S  # noqa: E402
esperadas = ["mp_licitacion_vivo", "mp_oc_vivo", "mp_proveedor_resolver",
             "mp_comprador_catalogo", "mp_cuota_estado"]
faltan = [t for t in esperadas if not hasattr(S, t)]
if faltan:
    fallos.append(f"tools no registradas en server.py: {faltan}")
else:
    print(f"tools vivas registradas: {len(esperadas)}")

# 6. Los endpoints nuevos existen en la capa de API
from mp_mcp.api import ClienteAPI  # noqa: E402
met = ["licitaciones_por_organismo", "licitaciones_por_proveedor",
       "orden_compra", "buscar_proveedor", "buscar_comprador"]
faltan_api = [m for m in met if not hasattr(ClienteAPI, m)]
if faltan_api:
    fallos.append(f"metodos ausentes en ClienteAPI: {faltan_api}")
else:
    print(f"endpoints nuevos en la capa de API: {len(met)}")

# 7. licitaciones_por_fecha acepta estado
import inspect  # noqa: E402
if "estado" not in inspect.signature(ClienteAPI.licitaciones_por_fecha).parameters:
    fallos.append("licitaciones_por_fecha no acepta 'estado'")

if fallos:
    print("\nFALLOS:")
    for f in fallos:
        print("  -", f)
    sys.exit(1)
print("\nOK: indice sin campos pero con limitaciones, detalle bajo demanda,")
print("    5 tools vivas y 5 endpoints nuevos registrados.")
