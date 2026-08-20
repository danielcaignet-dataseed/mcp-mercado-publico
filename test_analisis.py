"""Las tools de cruce: contrato de salida y resolucion de siglas.

Existen porque [MEDIDO 2026-08-19] Demeter tardo 19 minutos en responder "a que
precio me ganaron": armo el cruce a mano con 26 archivos de paginacion, dos
scripts Python propios y 23.981 lineas de TSV. La consulta al MCP cuesta 0,11 s.

No sale a la red. Usa un almacen sintetico, asi que no depende de produccion.
"""
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
from mp_mcp import analisis as A                              # noqa: E402

fallos = []
store = Store(Config.from_env())
con = store.con

# --- almacen sintetico ----------------------------------------------------
# Dos lineas donde MI EMPRESA perdio y una donde gano, mas una licitacion de un
# solo oferente en su rubro donde no participo.
OF = [
    # (lic, item, unspsc, rut, nombre, seleccionada, precio_clp, n_of, org_cod, org)
    ("L-1", "1", "42295400", "11.111.111-1", "MI EMPRESA", False, 1000.0, 3,
     "O1", "FONDO NACIONAL DE SALUD"),
    ("L-1", "1", "42295400", "22.222.222-2", "RIVAL SA", True, 800.0, 3,
     "O1", "FONDO NACIONAL DE SALUD"),
    ("L-2", "1", "42295400", "11.111.111-1", "MI EMPRESA", False, 500.0, 2,
     "O1", "FONDO NACIONAL DE SALUD"),
    ("L-2", "1", "42295400", "33.333.333-3", "OTRO SPA", True, 250.0, 2,
     "O1", "FONDO NACIONAL DE SALUD"),
    ("L-3", "1", "42295400", "11.111.111-1", "MI EMPRESA", True, 900.0, 1,
     "O1", "FONDO NACIONAL DE SALUD"),
    ("L-4", "1", "42295400", "44.444.444-4", "AJENA LTDA", True, 700.0, 1,
     "O2", "CENTRAL DE ABASTECIMIENTO DEL SISTEMA NACIONAL"),
]
for lic, item, un, rut, nom, sel, pre, nof, ocod, org in OF:
    con.execute("""INSERT INTO oferta (oferta_id, codigo_licitacion,
        correlativo_item, unspsc_commodity, nombre_linea, rut_proveedor,
        nombre_proveedor, seleccionada, cantidad_ofertada, precio_unitario_clp,
        monto_adjudicado, n_oferentes, organismo_codigo, organismo_nombre,
        fecha_envio_oferta, _procedencia, _ingerido_en)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?, TIMESTAMP '2026-07-01 00:00:00',
                'test', TIMESTAMP '2026-07-01 00:00:00')""",
                [f"{lic}|{item}|{rut}", lic, item, un, "Insumo X", rut, nom, sel,
                 1.0, pre, pre if sel else None, nof, ocod, org])

for cod, org, prov, monto in (("OC-1", "FONDO NACIONAL DE SALUD", "RIVAL SA", 5000.0),
                              ("OC-2", "FONDO NACIONAL DE SALUD", "OTRO SPA", 3000.0)):
    con.execute("""INSERT INTO orden_compra (codigo, organismo_codigo,
        organismo_nombre, nombre_proveedor, total_clp, fecha_envio, _procedencia,
        _ingerido_en) VALUES (?, 'O1', ?, ?, ?, DATE '2026-07-01', 'test',
        TIMESTAMP '2026-07-01 00:00:00')""", [cod, org, prov, monto])

# --- 1. precio perdido ----------------------------------------------------
r = A.mp_precio_perdido(store, "11.111.111-1")
res = r["resumen"]
print("1. precio_perdido: %d filas | comparadas=%s sin_comparar=%s mediana=%s%%"
      % (len(r["data"]), res["lineas_comparadas"], res["lineas_sin_comparar"],
         res["sobreprecio_mediano_pct"]))
for f in r["data"]:
    print("   %-5s mio=%-7s ganador=%-7s (%s) sobreprecio=%s%%"
          % (f["codigo_licitacion"], f["mi_precio_clp"], f["precio_ganador_clp"],
             f["ganador"], f["sobreprecio_pct"]))
if len(r["data"]) != 2:
    fallos.append("esperaba 2 lineas perdidas, hubo %d" % len(r["data"]))
por_lic = {f["codigo_licitacion"]: f for f in r["data"]}
if por_lic.get("L-1", {}).get("sobreprecio_pct") != 25.0:
    fallos.append("L-1: 1000 vs 800 deberia dar 25%%, dio %s"
                  % por_lic.get("L-1", {}).get("sobreprecio_pct"))
if por_lic.get("L-2", {}).get("sobreprecio_pct") != 100.0:
    fallos.append("L-2: 500 vs 250 deberia dar 100%%, dio %s"
                  % por_lic.get("L-2", {}).get("sobreprecio_pct"))
if "L-3" in por_lic:
    fallos.append("L-3 la GANO y aparece como perdida")

# --- 2. sin competencia ---------------------------------------------------
r = A.mp_sin_competencia(store, proveedor="11.111.111-1")
print("\n2. sin_competencia (acotado a mi rubro, sin las mias): %s"
      % [f["codigo_licitacion"] for f in r["data"]])
if [f["codigo_licitacion"] for f in r["data"]] != ["L-4"]:
    fallos.append("esperaba solo L-4 (1 oferente, mi rubro, no participe); dio %s"
                  % [f["codigo_licitacion"] for f in r["data"]])

# --- 3. huella ------------------------------------------------------------
r = A.mp_huella(store, "11.111.111-1")
res = r["resumen"]
print("\n3. huella: ofertas=%s ganadas=%s tasa=%s%% unspsc=%s"
      % (res["ofertas_totales"], res["ganadas"], res["tasa_exito_pct"],
         res["codigos_unspsc"]))
if (res["ofertas_totales"], res["ganadas"]) != (3, 1):
    fallos.append("huella: esperaba 3 ofertas y 1 ganada, dio %s/%s"
                  % (res["ofertas_totales"], res["ganadas"]))
if res["tasa_exito_pct"] != 33.3:
    fallos.append("tasa de exito deberia ser 33.3, dio %s" % res["tasa_exito_pct"])

# --- 4. siglas ------------------------------------------------------------
print("\n4. resolucion de siglas")
CASOS = [("FONASA", "FONDO NACIONAL DE SALUD"),
         ("CENABAST", "CENTRAL DE ABASTECIMIENTO DEL SISTEMA NACIONAL"),
         ("FONDO NACIONAL DE SALUD", "FONDO NACIONAL DE SALUD")]
for q, esperado in CASOS:
    rr = A.resolver_organismo(store, q)
    got = (rr["resuelto"] or {}).get("nombre")
    ok = got == esperado
    print("   %-26s -> %-18s %s" % (q, rr["modo"], "OK" if ok else "FALLA (%s)" % got))
    if not ok:
        fallos.append("sigla %s -> %s, esperaba %s" % (q, got, esperado))

rr = A.resolver_organismo(store, "xyzzy")
if rr["resuelto"] is not None or rr["modo"] != "sin_coincidencia":
    fallos.append("una sigla inventada no puede resolver a nada")
print("   %-26s -> %-18s OK (no inventa)" % ("xyzzy", rr["modo"]))

# --- 5. el contrato de salida ---------------------------------------------
print("\n5. contrato de salida, igual en las cuatro")
for nombre, r in (("precio_perdido", A.mp_precio_perdido(store, "11.111.111-1")),
                  ("sin_competencia", A.mp_sin_competencia(store)),
                  ("huella", A.mp_huella(store, "11.111.111-1")),
                  ("comprador_perfil", A.mp_comprador_perfil(store, "FONASA"))):
    faltan = [k for k in ("titulo", "data", "columnas", "chart_hint", "meta")
              if k not in r]
    m = r.get("meta", {})
    faltan += ["meta." + k for k in ("procedencia", "as_of", "limitaciones",
                                     "atribucion") if k not in m]
    tipo = r.get("chart_hint", {}).get("tipo")
    sev = sorted({x["severidad"] for x in m.get("limitaciones", [])})
    print("   %-18s chart=%-7s limitaciones=%d %s %s"
          % (nombre, tipo, len(m.get("limitaciones", [])), sev,
             "OK" if not faltan else "FALTAN %s" % faltan))
    if faltan:
        fallos.append("%s: faltan %s" % (nombre, faltan))
    if not m.get("limitaciones"):
        fallos.append("%s no declara ninguna limitacion" % nombre)

# --- 6. el perfil por sigla declara COMO resolvio -------------------------
r = A.mp_comprador_perfil(store, "FONASA")
print("\n6. perfil por sigla: modo=%s nota=%s"
      % (r["resumen"].get("modo_de_resolucion"), bool(r.get("nota"))))
if r["resumen"].get("organismo_resuelto") != "FONDO NACIONAL DE SALUD":
    fallos.append("el perfil no dice a que organismo resolvio")
if not r.get("nota"):
    fallos.append("resolver por sigla sin avisarlo es presentar una coincidencia "
                  "difusa como exacta")

store.close()
if fallos:
    print("\nFALLOS:")
    for f in fallos:
        print("  -", f)
    sys.exit(1)
print("\nOK: los cruces dan el numero correcto, las siglas resuelven sin inventar,")
print("    y las cuatro devuelven la misma envoltura con sus limitaciones.")
