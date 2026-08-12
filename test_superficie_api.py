"""Valida la superficie COMPLETA de la API v1 (menos Compra Agil, excluida).

Dos capas, verificadas una por una:

  A. `ClienteAPI` -> que cada metodo pegue al endpoint correcto con el nombre
     EXACTO de parametro que documenta ChileCompra. Un `codigoOrganismo` en
     minuscula no falla: devuelve el dia corriente completo y el agente cree que
     filtro. Eso solo se detecta comparando el query string.
  B. Las tools del MCP -> que las 9 combinaciones pedidas lleguen a la capa A,
     que el truncado exista y que las limitaciones digan la verdad.

No sale a la red: `_get` esta interceptado y se registra lo que se HABRIA pedido.
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
from mp_mcp.api import ClienteAPI, dv_calza, normalizar_rut   # noqa: E402
from mp_mcp import tools as T                                 # noqa: E402

fallos = []
store = Store(Config.from_env())
cuota = Cuota(store)


class ApiEspia(ClienteAPI):
    """Registra path y params en vez de salir a la red."""

    def __init__(self, cfg, cuota, respuesta=None):
        super().__init__(cfg, cuota)
        self.llamadas = []
        self.respuesta = respuesta if respuesta is not None else {"Listado": [],
                                                                 "Cantidad": 0}

    def _get(self, path, params, motivo):
        self.llamadas.append((path, dict(params), motivo))
        return self.respuesta

    @property
    def ultima(self):
        return self.llamadas[-1]


cfg = Config.from_env()

# ==========================================================================
# A. Los 12 endpoints+parametro de la API v1 (Compra Agil excluida)
# ==========================================================================
print("A. superficie de ClienteAPI")
api = ApiEspia(cfg, cuota)

CASOS = [
    # (etiqueta, llamada, path esperado, params esperados)
    ("licitaciones?codigo",
     lambda: api.licitacion("948355-24-LE26"),
     "publico/licitaciones.json", {"codigo": "948355-24-LE26"}),
    ("licitaciones?fecha",
     lambda: api.licitaciones_por_fecha("28072026"),
     "publico/licitaciones.json", {"fecha": "28072026"}),
    ("licitaciones?estado",
     lambda: api.licitaciones_por_estado("adjudicada"),
     "publico/licitaciones.json", {"estado": "adjudicada"}),
    ("licitaciones?fecha&estado",
     lambda: api.licitaciones_por_fecha("28072026", "adjudicada"),
     "publico/licitaciones.json", {"fecha": "28072026", "estado": "adjudicada"}),
    ("licitaciones?CodigoOrganismo",
     lambda: api.licitaciones_por_organismo("111870"),
     "publico/licitaciones.json", {"CodigoOrganismo": "111870"}),
    ("licitaciones?CodigoProveedor",
     lambda: api.licitaciones_por_proveedor("47740"),
     "publico/licitaciones.json", {"CodigoProveedor": "47740"}),
    ("ordenesdecompra?codigo",
     lambda: api.orden_compra("1002-336-SE26"),
     "publico/ordenesdecompra.json", {"codigo": "1002-336-SE26"}),
    ("ordenesdecompra?fecha",
     lambda: api.ordenes_por_fecha("28072026"),
     "publico/ordenesdecompra.json", {"fecha": "28072026"}),
    ("ordenesdecompra?estado",
     lambda: api.ordenes_por_estado("aceptada"),
     "publico/ordenesdecompra.json", {"estado": "aceptada"}),
    ("ordenesdecompra?CodigoProveedor",
     lambda: api.ordenes_por_proveedor("47740"),
     "publico/ordenesdecompra.json", {"CodigoProveedor": "47740"}),
    ("ordenesdecompra?CodigoOrganismo",
     lambda: api.ordenes_por_organismo("111870"),
     "publico/ordenesdecompra.json", {"CodigoOrganismo": "111870"}),
    ("BuscarProveedor?rut",
     lambda: api.buscar_proveedor("96756540-7"),
     "Publico/Empresas/BuscarProveedor", {"rutempresaproveedor": "96.756.540-7"}),
    ("BuscarComprador",
     lambda: api.buscar_comprador(),
     "Publico/Empresas/BuscarComprador", {}),
]

for etiqueta, fn, path_esp, params_esp in CASOS:
    fn()
    path, params, _ = api.ultima
    params.pop("ticket", None)
    ok = path == path_esp and params == params_esp
    print("   %-34s %s" % (etiqueta, "OK" if ok else "FALLA"))
    if not ok:
        fallos.append("%s -> path=%s params=%s (esperaba %s / %s)"
                      % (etiqueta, path, params, path_esp, params_esp))

print("   %d de %d combinaciones documentadas" % (len(CASOS), len(CASOS)))

# ==========================================================================
# B. normalizacion de RUT: el unico formato que la API acepta
# ==========================================================================
print("\nB. normalizar_rut")
for entrada in ("96756540-7", "96756540", "96.756.540-7", "967565407",
                " 96.756.540 - 7 "):
    got = normalizar_rut(entrada)
    ok = got == "96.756.540-7"
    print("   %-18s -> %-14s %s" % (repr(entrada), got, "OK" if ok else "FALLA"))
    if not ok:
        fallos.append("normalizar_rut(%r) = %r" % (entrada, got))
# El caso ambiguo: 96756540 es cuerpo completo, NO 9675654-0. Modulo 11 desempata.
if normalizar_rut("96756540") != "96.756.540-7":
    fallos.append("no desempato el RUT sin digito verificador con modulo 11")
# Un DV que no calza NO se corrige: es dato del usuario.
if normalizar_rut("9876543-K") != "9.876.543-K":
    fallos.append("normalizar_rut corrigio un DV que no calza en vez de respetarlo")
if dv_calza("9876543-K") is not False:
    fallos.append("dv_calza no detecta un digito verificador incorrecto")
if dv_calza("96.756.540-7") is not True:
    fallos.append("dv_calza rechaza un RUT correcto")
if normalizar_rut("basura") != "basura":
    fallos.append("normalizar_rut deberia devolver la entrada si no la reconoce")
print("   96756540 (sin DV) -> %s   9876543-K (DV malo) -> %s / dv_calza=%s"
      % (normalizar_rut("96756540"), normalizar_rut("9876543-K"),
         dv_calza("9876543-K")))

# ==========================================================================
# C. "no hay resultados" NO es "no pude preguntar", y viceversa
# ==========================================================================
print("\nC. _error_chilecompra")
CASOS_ERR = [
    ("ticket invalido", {"Codigo": 203, "Mensaje": "Ticket no valido."}, True),
    ("sin resultados de empresas",
     {"Codigo": 10200, "Mensaje": "No hay resultados de empresas."}, False),
    ("sobre listaEmpresas", {"listaEmpresas": [{"CodigoEmpresa": "47740"}]}, False),
    ("listado vacio", {"Listado": [], "Cantidad": 0}, False),
]
for etiqueta, cuerpo, espera_error in CASOS_ERR:
    err = T._error_chilecompra(cuerpo)
    ok = bool(err) == espera_error
    print("   %-28s error=%-5s %s" % (etiqueta, bool(err), "OK" if ok else "FALLA"))
    if not ok:
        fallos.append("_error_chilecompra(%s) -> %r" % (etiqueta, err))

# ==========================================================================
# D. Las 9 combinaciones nuevas del MCP
# ==========================================================================
print("\nD. las 9 combinaciones por las tools del MCP")
NUEVE = [
    ("licitaciones?fecha", T.mp_licitaciones_vivo, {"fecha": "28072026"},
     "publico/licitaciones.json", {"fecha": "28072026"}),
    ("licitaciones?estado", T.mp_licitaciones_vivo, {"estado": "adjudicada"},
     "publico/licitaciones.json", {"estado": "adjudicada"}),
    ("licitaciones?fecha&estado", T.mp_licitaciones_vivo,
     {"fecha": "28072026", "estado": "adjudicada"},
     "publico/licitaciones.json", {"fecha": "28072026", "estado": "adjudicada"}),
    ("licitaciones?CodigoOrganismo", T.mp_licitaciones_vivo,
     {"codigo_organismo": "111870"},
     "publico/licitaciones.json", {"CodigoOrganismo": "111870"}),
    ("licitaciones?CodigoProveedor", T.mp_licitaciones_vivo,
     {"codigo_proveedor": "47740"},
     "publico/licitaciones.json", {"CodigoProveedor": "47740"}),
    ("ordenesdecompra?fecha", T.mp_ordenes_vivo, {"fecha": "28072026"},
     "publico/ordenesdecompra.json", {"fecha": "28072026"}),
    ("ordenesdecompra?estado", T.mp_ordenes_vivo, {"estado": "aceptada"},
     "publico/ordenesdecompra.json", {"estado": "aceptada"}),
    ("ordenesdecompra?CodigoProveedor", T.mp_ordenes_vivo,
     {"codigo_proveedor": "47740"},
     "publico/ordenesdecompra.json", {"CodigoProveedor": "47740"}),
    ("ordenesdecompra?CodigoOrganismo", T.mp_ordenes_vivo,
     {"codigo_organismo": "111870"},
     "publico/ordenesdecompra.json", {"CodigoOrganismo": "111870"}),
]
for etiqueta, tool, kwargs, path_esp, params_esp in NUEVE:
    r = tool(api, cuota, **kwargs)
    path, params, motivo = api.ultima
    params.pop("ticket", None)
    ok = (path == path_esp and params == params_esp and "error" not in r
          and motivo == "mcp-vivo")
    print("   %-34s %s" % (etiqueta, "OK" if ok else "FALLA"))
    if not ok:
        fallos.append("tool %s -> path=%s params=%s error=%s"
                      % (etiqueta, path, params, r.get("error")))
print("   %d de 9" % len(NUEVE))

# ==========================================================================
# E. El truncado, que es lo que evita llenar la ventana de contexto
# ==========================================================================
print("\nE. truncado y limites")
muchas = {"Cantidad": 12265,
          "Listado": [{"Codigo": "x-%d" % i, "Nombre": "OC %d" % i,
                       "CodigoEstado": 4} for i in range(500)]}
api_grande = ApiEspia(cfg, cuota, respuesta=muchas)

r = T.mp_ordenes_vivo(api_grande, cuota, fecha="28072026")
print("   por omision: devueltas=%s truncado=%s" % (r["devueltas"], r["truncado"]))
if r["devueltas"] != 25 or not r["truncado"]:
    fallos.append("el limite por omision no es 25 o no marca truncado")
sev = [x["severidad"] for x in r["meta"]["limitaciones"]]
if "alta" not in sev:
    fallos.append("un resultado truncado no declara limitacion de severidad alta")
if r["cantidad_total"] != 12265:
    fallos.append("cantidad_total no refleja el universo de la API")

r = T.mp_ordenes_vivo(api_grande, cuota, fecha="28072026", limite=9999)
print("   limite=9999 -> devueltas=%s (tope)" % r["devueltas"])
if r["devueltas"] != 200:
    fallos.append("el tope de %d no se aplica: devolvio %s" % (200, r["devueltas"]))

r = T.mp_ordenes_vivo(api_grande, cuota, fecha="28072026", limite=0)
if r["devueltas"] != 1:
    fallos.append("limite=0 deberia acotarse a 1, devolvio %s" % r["devueltas"])

r = T.mp_licitaciones_vivo(api_grande, cuota)
print("   sin filtros -> error=%s" % bool(r.get("error")))
if not r.get("error") or "estados_validos" not in r:
    fallos.append("sin filtros deberia rechazar y listar los estados validos")
n_llamadas = len(api_grande.llamadas)
T.mp_licitaciones_vivo(api_grande, cuota)
if len(api_grande.llamadas) != n_llamadas:
    fallos.append("el rechazo sin filtros GASTO un hit de cuota")
print("   el rechazo sin filtros no gasta cuota: OK")

# ==========================================================================
# F. mp_proveedor_resolver estructurado
# ==========================================================================
print("\nF. mp_proveedor_resolver")
api_p = ApiEspia(cfg, cuota, respuesta={"listaEmpresas": [
    {"CodigoEmpresa": "47740", "NombreEmpresa": "B BRAUN MEDICAL SPA"}]})
r = T.mp_proveedor_resolver(api_p, cuota, "96756540-7")
print("   encontrado=%s codigo=%s nombre=%s" % (
    r["encontrado"], r["codigo_empresa"], r["nombre_empresa"]))
if not (r["encontrado"] and r["codigo_empresa"] == "47740"):
    fallos.append("no extrajo CodigoEmpresa de listaEmpresas")
if r["rut_enviado_a_la_api"] != "96.756.540-7":
    fallos.append("no normalizo el RUT antes de consultar")

api_v = ApiEspia(cfg, cuota, respuesta={"Codigo": 10200,
                                        "Mensaje": "No hay resultados de empresas."})
r = T.mp_proveedor_resolver(api_v, cuota, "11111111-1")
print("   sin coincidencia -> encontrado=%s error=%s" % (
    r.get("encontrado"), "error" in r))
if r.get("encontrado") is not False or "error" in r:
    fallos.append("un RUT sin coincidencia deberia dar encontrado=False, no error")

# ==========================================================================
# G. Registro en el servidor y costo del listado de tools
# ==========================================================================
print("\nG. registro en server.py")
store.close()
import mp_mcp.server as S                                     # noqa: E402
esperadas = ["mp_licitaciones_vivo", "mp_ordenes_vivo", "mp_licitacion_vivo",
             "mp_oc_vivo", "mp_proveedor_resolver", "mp_comprador_catalogo",
             "mp_cuota_estado"]
faltan = [t for t in esperadas if not hasattr(S, t)]
if faltan:
    fallos.append("no registradas en server.py: %s" % faltan)
print("   tools vivas registradas: %d" % (len(esperadas) - len(faltan)))

if fallos:
    print("\nFALLOS:")
    for f in fallos:
        print("  -", f)
    sys.exit(1)
print("\nOK: 13 combinaciones en ClienteAPI, las 9 nuevas alcanzables por tool,")
print("    RUT normalizado, 'sin resultados' distinguido de 'fallo', truncado")
print("    con limitacion de severidad alta y rechazo sin filtros sin gastar cuota.")
