"""Prueba del volcado: como se analiza un universo grande sin truncarlo.

El planteo es de Daniel: si la tool devuelve 25 de 12.260, "eso no corta la
informacion? como se va a analizar algo truncado?".

La respuesta tiene dos partes y las dos se verifican aca:

  1. Los AGREGADOS no se truncan. `conteo_por_codigo_estado` se calcula sobre
     todas las filas que trajo la API, no sobre las que se devuelven. Con 500
     filas y limite=25, los conteos suman 500.
  2. Para las filas hay `volcar=true`: se guardan completas en el almacen y la
     respuesta trae solo el conteo. Despues se paginan con mp_volcado_leer sin
     gastar cuota. No se pierde nada y no se llena el contexto.

Y el caso que importa en produccion: el volcado tiene que funcionar con los
hechos en SOLO LECTURA, porque asi corre el servidor MCP.

No sale a la red.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

d = tempfile.mkdtemp()
os.environ["MP_HOME"] = d
os.environ["MP_DB"] = str(Path(d) / "mp.duckdb")
os.environ["MP_CATALOGO"] = str(Path(d) / "cat.duckdb")
os.environ["MP_CUOTA_LEDGER"] = str(Path(d) / "cuota")
sys.path.insert(0, str(Path(__file__).parent / "src"))

from mp_mcp.config import Config                              # noqa: E402
from mp_mcp.store import Store                                # noqa: E402
from mp_mcp.quota import Cuota                                # noqa: E402
from mp_mcp.api import ClienteAPI                             # noqa: E402
from mp_mcp import tools as T                                 # noqa: E402

fallos = []
cfg = Config.from_env()


class ApiEspia(ClienteAPI):
    def __init__(self, cfg, cuota, respuesta):
        super().__init__(cfg, cuota)
        self.respuesta = respuesta
        self.llamadas = 0

    def _get(self, path, params, motivo):
        self.llamadas += 1
        return self.respuesta


# 500 OC: 300 en estado 4, 150 en 8, 50 en 9. La API dice que hay 12.260.
ESTADOS = [4] * 300 + [8] * 150 + [9] * 50
FILAS = [{"Codigo": "1002-%04d-SE26" % i,
          "Nombre": ("Insumos medicos lote %d" % i) if i % 3 == 0
                    else ("Combustible partida %d" % i),
          "CodigoEstado": ESTADOS[i]} for i in range(500)]
RESP = {"Cantidad": 12260, "Listado": FILAS}

# El almacen tiene que existir antes de abrirlo en solo lectura.
Store(cfg).close()
# MODO PRODUCCION: hechos en solo-lectura.
store = Store(cfg, read_only=True)
cuota = Cuota(store)
api = ApiEspia(cfg, cuota, RESP)

# -- 1. Los agregados NO se truncan ----------------------------------------
r = T.mp_ordenes_vivo(api, cuota, fecha="28072026", store=store)
suma = sum(r["conteo_por_codigo_estado"].values())
print("1. devueltas=%s de cantidad_total=%s | conteo sobre %s filas, suma=%s"
      % (r["devueltas"], r["cantidad_total"], r["conteo_calculado_sobre"], suma))
print("   por estado: %s" % r["conteo_por_codigo_estado"])
if r["devueltas"] != 25:
    fallos.append("no trunco a 25")
if suma != 500 or r["conteo_calculado_sobre"] != 500:
    fallos.append("el conteo por estado se calculo sobre las devueltas, no sobre todas")
if r["conteo_por_codigo_estado"] != {"4": 300, "8": 150, "9": 50}:
    fallos.append("el conteo por estado no coincide: %s" % r["conteo_por_codigo_estado"])
if "volcado" in r:
    fallos.append("volco sin que se lo pidieran")

# -- 2. volcar=True guarda todo y devuelve solo el conteo ------------------
r = T.mp_ordenes_vivo(api, cuota, fecha="28072026", volcar=True, store=store)
v = r.get("volcado") or {}
print("\n2. volcado: filas=%s tabla=%s error=%s"
      % (v.get("filas"), v.get("tabla"), v.get("error")))
if v.get("filas") != 500:
    fallos.append("el volcado no guardo las 500 filas: %s" % v)
if r["devueltas"] != 25:
    fallos.append("con volcar=True dejo de truncar la respuesta")
bytes_resp = len(json.dumps(r, ensure_ascii=False, default=str))
bytes_todo = len(json.dumps({"ordenes": FILAS}, ensure_ascii=False))
print("   respuesta %d bytes vs %d si mandara las 500 (%.0f%% menos)"
      % (bytes_resp, bytes_todo, 100 - 100.0 * bytes_resp / bytes_todo))
if bytes_resp > bytes_todo / 2:
    fallos.append("la respuesta con volcado no es mucho mas chica que mandar todo")

# -- 3. Se lee y pagina sin gastar cuota -----------------------------------
antes = api.llamadas
r = T.mp_volcado_leer(store, "ordenes", limite=10)
print("\n3. leer: coinciden=%s devueltas=%s en_el_volcado=%s"
      % (r["coinciden"], r["devueltas"], r["filas_en_el_volcado"]))
if r["coinciden"] != 500 or r["devueltas"] != 10:
    fallos.append("leer_volcado no pagina bien: %s/%s" % (r["devueltas"], r["coinciden"]))
if api.llamadas != antes:
    fallos.append("mp_volcado_leer gasto un hit de cuota")

r2 = T.mp_volcado_leer(store, "ordenes", limite=10, offset=10)
if r2["filas"] and r["filas"] and r2["filas"][0]["codigo"] == r["filas"][0]["codigo"]:
    fallos.append("el offset no mueve la ventana")
print("   offset: pagina 1 empieza en %s, pagina 2 en %s"
      % (r["filas"][0]["codigo"], r2["filas"][0]["codigo"]))

# -- 4. Filtros locales sobre el volcado -----------------------------------
r = T.mp_volcado_leer(store, "ordenes", texto="insumos")
esperado = sum(1 for f in FILAS if "insumos" in f["Nombre"].lower())
print("\n4. texto='insumos': coinciden=%s (esperado %s)" % (r["coinciden"], esperado))
if r["coinciden"] != esperado:
    fallos.append("el filtro por texto no coincide: %s vs %s" % (r["coinciden"], esperado))

r = T.mp_volcado_leer(store, "ordenes", codigo_estado=8)
print("   codigo_estado=8: coinciden=%s (esperado 150)" % r["coinciden"])
if r["coinciden"] != 150:
    fallos.append("el filtro por codigo_estado no coincide: %s" % r["coinciden"])

# -- 5. Re-volcar la MISMA consulta reemplaza, no acumula -------------------
T.mp_ordenes_vivo(api, cuota, fecha="28072026", volcar=True, store=store)
r = T.mp_volcado_leer(store, "ordenes")
print("\n5. tras re-volcar lo mismo: filas_en_el_volcado=%s" % r["filas_en_el_volcado"])
if r["filas_en_el_volcado"] != 500:
    fallos.append("re-volcar duplico filas: %s" % r["filas_en_el_volcado"])

# -- 6. Dos volcados conviven SIN mezclarse --------------------------------
# Asi aparecio el bug: Demeter volco {"estado":"todos","fecha":"19082026"} (62
# filas) mientras yo tenia volcado {"fecha":"28072026"} (11.681), y una lectura
# por `tipo` devolvia 11.743 con los conteos por estado ya sin calzar contra la
# API. El agente habria informado un total que mezcla dos preguntas distintas.
CHICO = [{"Codigo": "9-%03d-SE26" % i, "Nombre": "Otra cosa %d" % i,
          "CodigoEstado": 4} for i in range(7)]
api_chico = ApiEspia(cfg, cuota, {"Cantidad": 7, "Listado": CHICO})
T.mp_ordenes_vivo(api_chico, cuota, estado="aceptada", volcar=True, store=store)

vol = store.volcados()
r = T.mp_volcado_leer(store, "ordenes")           # sin filtros: el mas reciente
print("\n6. dos volcados: %s" % [(x["filtros"], x["filas"]) for x in vol])
print("   sin filtros lee el mas reciente: %s -> coinciden=%s"
      % ((r.get("leyendo_volcado") or {}).get("filtros"), r["coinciden"]))
if len(vol) != 2:
    fallos.append("no conviven los dos volcados: %s" % len(vol))
if r["coinciden"] != 7:
    fallos.append("sin filtros no leyo el volcado mas reciente: coinciden=%s"
                  % r["coinciden"])
if not r.get("otros_volcados"):
    fallos.append("no avisa que hay otro volcado del mismo tipo")
sev = [x["severidad"] for x in r["meta"]["limitaciones"]]
if "alta" not in sev:
    fallos.append("con varios volcados no declara limitacion de severidad alta")

# Y el volcado grande sigue intacto y separado.
r = T.mp_volcado_leer(store, "ordenes", filtros=chr(123) + chr(34) + "fecha"
                      + chr(34) + ": " + chr(34) + "28072026" + chr(34) + chr(125))
print("   pidiendo el otro explicitamente -> coinciden=%s" % r["coinciden"])
if r["coinciden"] != 500:
    fallos.append("no se puede pedir un volcado especifico: coinciden=%s"
                  % r["coinciden"])
r = T.mp_volcado_leer(store, "licitaciones")
print("   tipo sin ningun volcado -> coinciden=%s nota=%s"
      % (r["coinciden"], bool(r.get("nota"))))
if r["coinciden"] != 0 or not r.get("nota"):
    fallos.append("un tipo sin volcado deberia dar 0 y explicar como volcarlo")

# -- 7. Tipo invalido no revienta y orienta --------------------------------
r = T.mp_volcado_leer(store, "cualquier-cosa")
print("\n7. tipo invalido -> error=%s disponibles=%s"
      % (bool(r.get("error")), len(r.get("volcados_disponibles") or [])))
if not r.get("error") or not r.get("volcados_disponibles"):
    fallos.append("un tipo invalido deberia dar error y listar los volcados")

# -- 8. Sin almacen, la consulta sigue respondiendo ------------------------
r = T.mp_ordenes_vivo(api, cuota, fecha="28072026", volcar=True, store=None)
print("\n8. volcar=True sin almacen -> volcado.error=%s pero devueltas=%s"
      % (bool((r.get("volcado") or {}).get("error")), r.get("devueltas")))
if r.get("devueltas") != 25 or not (r.get("volcado") or {}).get("error"):
    fallos.append("sin almacen deberia avisar del volcado pero responder igual")

# -- 9. El aviso de truncado ofrece la salida real -------------------------
r = T.mp_ordenes_vivo(api, cuota, fecha="28072026", store=store)
alta = " ".join(x["limitacion"] for x in r["meta"]["limitaciones"]
                if x["severidad"] == "alta")
print("\n9. el aviso de truncado menciona volcar=true: %s | ESTA tool: %s"
      % ("volcar=true" in alta, "ESTA tool" in alta))
if "volcar=true" not in alta or "ESTA tool" not in alta:
    fallos.append("el aviso de truncado no ofrece volcar=true o no aclara de quien "
                  "es el recorte")

# -- 10. El glosario de estados: nombrar lo medido, no lo supuesto ---------
# Demeter reporto "En proceso (5)" para una OC. El 5 aparece 19 veces en un dia
# cerrado y NINGUN filtro documentado de ChileCompra lo devuelve, asi que ese
# nombre salio de su propio conocimiento y se presento como verificado. Los
# codigos sin correspondencia medida se citan como numero, no como nombre.
MEZCLA = [{"Codigo": "x-%d" % i, "Nombre": "n%d" % i,
           "CodigoEstado": c} for i, c in enumerate([6] * 4 + [12] * 3 + [5] * 2)]
api_g = ApiEspia(cfg, cuota, {"Cantidad": 9, "Listado": MEZCLA})
r = T.mp_ordenes_vivo(api_g, cuota, fecha="28072026", store=store)
print("\n10. conteo_por_codigo_estado: %s" % r["conteo_por_codigo_estado"])
print("    conteo_por_estado (nombrado): %s" % r["conteo_por_estado"])
print("    codigos sin nombre: %s" % r["codigos_de_estado_sin_nombre"])
if r["conteo_por_estado"] != {"aceptada": 4, "recepcionconforme": 3}:
    fallos.append("no nombro los codigos medidos: %s" % r["conteo_por_estado"])
if r["codigos_de_estado_sin_nombre"] != ["5"]:
    fallos.append("no declaro el codigo sin nombre: %s"
                  % r["codigos_de_estado_sin_nombre"])
alta = " ".join(x["limitacion"] for x in r["meta"]["limitaciones"]
                if x["severidad"] == "alta")
if "NO les pongas" not in alta:
    fallos.append("no le pide al agente que no invente el nombre del estado")
if r["meta"].get("glosario_codigo_estado", {}).get("6") != "aceptada":
    fallos.append("el glosario de ordenes no viaja en la respuesta")

# La colision entre entidades: el 6 no significa lo mismo.
r_lic = T.mp_licitaciones_vivo(api_g, cuota, fecha="28072026", store=store)
print("    el 6 en licitaciones: %s | en ordenes: %s"
      % (r_lic["meta"]["glosario_codigo_estado"]["6"],
         r["meta"]["glosario_codigo_estado"]["6"]))
if r_lic["meta"]["glosario_codigo_estado"]["6"] != "cerrada":
    fallos.append("el glosario de licitaciones no distingue el 6")
if r_lic["conteo_por_estado"] != {"cerrada": 4, "publicada": 2}:
    fallos.append("el nombrado de licitaciones esta mal: %s"
                  % r_lic["conteo_por_estado"])

store.close()

if fallos:
    print("\nFALLOS:")
    for f in fallos:
        print("  -", f)
    sys.exit(1)
print("\nOK: los agregados son exactos sobre el universo aunque las filas se")
print("    trunquen; volcar=true guarda todo con los hechos en SOLO LECTURA y se")
print("    pagina y filtra despues sin gastar cuota.")
