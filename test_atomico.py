"""La prueba que habria atrapado la perdida de 240.619 ordenes de compra.

[MEDIDO 2026-08-21] Dos corridas del cron reportaron OK habiendo borrado datos sin
reemplazarlos: orden_compra cayo de 1.721.976 a 1.481.357 filas y el dato mas nuevo
retrocedio de 2026-08-18 a 2026-06-30.

Cadena: `excluida_por_fuente` entro al schema ANTES de `_procedencia` pero a la base
viva por ALTER TABLE, que la pone AL FINAL -> dos ordenes de columnas distintos ->
el INSERT posicional metia "bulk-oc:2026-8" en una columna TIMESTAMP -> DuckDB
abortaba -> pero el DELETE del periodo YA se habia comprometido.

Esta prueba cubre las dos mitades:
  1. una base con el orden de columnas de PRODUCCION (columna al final) carga bien;
  2. si el INSERT falla, el DELETE se REVIERTE y los datos anteriores siguen ahi.

No sale a la red.
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
sys.path.insert(0, "/app/src")

import duckdb                                                 # noqa: E402
from mp_mcp import bulk                                       # noqa: E402

fallos = []
SQL = Path("/app/sql/schema.sql").read_text(encoding="utf-8")

CSV = Path(d) / "oc.csv"
CABECERA = ("Codigo;Nombre;Descripcion/Obervaciones;CodigoLicitacion;Estado;"
            "CodigoAbreviadoTipoOC;TipoMonedaOC;FormaPago;TipoDespacho;MontoTotalOC;"
            "MontoTotalOC_PesosChilenos;TotalNetoOC;Impuestos;PromedioCalificacion;"
            "RutSucursal;NombreProveedor;CodigoOrganismoPublico;OrganismoPublico;"
            "RegionUnidadCompra;CiudadUnidadCompra;FechaEnvio;FechaAceptacion;"
            "IDItem;codigoProductoONU;cantidad;precioNeto;totalLineaNeto;"
            "EspecificacionComprador;UnidadMedida;monedaItem;codigoEstado;"
            "EsCompraAgil;ActividadProveedor;ComunaProveedor;RegionProveedor;"
            "ActividadComprador;CodigoProveedor")


def fila(cod, item):
    return (f"{cod};OC de prueba;obs;LIC-1;Aceptada;SE;CLP;30 dias;Inmediato;1000;"
            f"1000;900;100;5;76.111.111-1;PROVEEDOR SPA;O1;ORGANISMO UNO;"
            f"Metropolitana;Santiago;2026-08-01;2026-08-02;{item};42295400;2;500;"
            f"1000;espec;Unidad;CLP;6;0;act;comuna;region;actcomp;C1")


def escribir_csv(codigos):
    CSV.write_text(CABECERA + "\n" + "\n".join(
        fila(c, f"{c}#1") for c in codigos) + "\n", encoding="utf-8")


def base_como_produccion():
    """Base con el orden de columnas REAL de produccion: la bandera al final,
    como la deja ALTER TABLE."""
    p = Path(d) / f"prod{os.urandom(4).hex()}.duckdb"
    con = duckdb.connect(str(p))
    # Se crea el schema SIN la columna y se agrega por ALTER, igual que paso en el
    # servidor. Asi la prueba reproduce el orden que rompio, no el del repo.
    sql = SQL.replace("    excluida_por_fuente   BOOLEAN DEFAULT FALSE,\n", "")
    sql = sql.replace("    excluida_por_fuente      BOOLEAN DEFAULT FALSE,\n", "")
    con.execute(sql)
    for t in ("orden_compra", "orden_compra_item"):
        con.execute(f"ALTER TABLE {t} ADD COLUMN excluida_por_fuente BOOLEAN")
    return con


print("1. orden de columnas de produccion (bandera AL FINAL)")
con = base_como_produccion()
cols = [r[1] for r in con.execute("PRAGMA table_info(orden_compra)").fetchall()]
print("   ultimas 3: %s" % cols[-3:])
if cols[-1] != "excluida_por_fuente":
    fallos.append("la prueba no reprodujo el orden de produccion")

escribir_csv(["OC-1", "OC-2", "OC-3"])
try:
    r = bulk.cargar_oc(con, CSV, "2026-8")
    print("   carga: %s" % {k: v for k, v in r.items() if k != "filas_csv"})
    if r["orden_compra"] != 3:
        fallos.append("cargo %s ordenes, esperaba 3" % r["orden_compra"])
except Exception as exc:                                      # noqa: BLE001
    fallos.append("la carga fallo con el orden de produccion: %s" % str(exc)[:160])
    print("   FALLA: %s" % str(exc)[:200])

print("\n2. el DELETE se revierte si el INSERT falla")
antes = con.execute("SELECT count(*) FROM orden_compra").fetchone()[0]
maxfecha_antes = con.execute("SELECT max(fecha_envio) FROM orden_compra").fetchone()[0]
print("   antes del intento fallido: %d filas, dato mas nuevo %s" % (antes, maxfecha_antes))

# Se rompe el CSV a proposito: una columna requerida desaparece, asi que
# _exigir_columnas levanta DESPUES de que el DELETE ya corrio en versiones sin
# transaccion. Con transaccion, el conteo tiene que quedar igual.
CSV.write_text(CABECERA.replace("MontoTotalOC_PesosChilenos;", "") + "\n"
               + "\n".join(fila(c, f"{c}#1").replace("1000;900;", "900;", 1)
                           for c in ["OC-1", "OC-2", "OC-3"]) + "\n",
               encoding="utf-8")
try:
    bulk.cargar_oc(con, CSV, "2026-8")
    fallos.append("la carga con CSV roto NO fallo: la validacion no sirve")
except Exception as exc:                                      # noqa: BLE001
    print("   fallo como corresponde: %s" % str(exc)[:110])

despues = con.execute("SELECT count(*) FROM orden_compra").fetchone()[0]
maxfecha_despues = con.execute("SELECT max(fecha_envio) FROM orden_compra").fetchone()[0]
print("   despues: %d filas, dato mas nuevo %s" % (despues, maxfecha_despues))
if despues != antes:
    fallos.append("SE PERDIERON DATOS: %d -> %d filas. El rollback no funciono."
                  % (antes, despues))
else:
    print("   OK: el rollback dejo los datos como estaban")

print("\n3. recarga del mismo periodo: reemplaza, no duplica")
escribir_csv(["OC-1", "OC-2", "OC-3", "OC-4"])
r = bulk.cargar_oc(con, CSV, "2026-8")
total = con.execute("SELECT count(*) FROM orden_compra").fetchone()[0]
print("   tras recargar con 4: %d filas" % total)
if total != 4:
    fallos.append("recargar dejo %d filas, esperaba 4" % total)

print("\n4. la bandera de exclusion queda en su columna, no corrida")
con.execute("""CREATE TABLE IF NOT EXISTS oc_excluida (
    codigo VARCHAR PRIMARY KEY, motivo VARCHAR, _fuente VARCHAR)""")
con.execute("INSERT INTO oc_excluida VALUES ('OC-2','monto','prueba')")
bulk.marcar_excluidas(con)
r = con.execute("""SELECT codigo, total_clp, excluida_por_fuente, _procedencia
                   FROM orden_compra ORDER BY codigo""").fetchall()
for row in r:
    print("   %-6s total_clp=%-8s excluida=%-6s procedencia=%s" % row)
mal = [x for x in r if not str(x[3] or "").startswith("bulk-oc:")]
if mal:
    fallos.append("_procedencia quedo corrido: %s" % mal[:2])
excl = {x[0]: x[2] for x in r}
if excl.get("OC-2") is not True or excl.get("OC-1") is not False:
    fallos.append("la bandera no cayo en la fila correcta: %s" % excl)

con.close()
if fallos:
    print("\nFALLOS:")
    for f in fallos:
        print("  -", f)
    sys.exit(1)
print("\nOK: carga con el orden de columnas de produccion, rollback verificado,")
print("    recarga sin duplicar y la bandera en su columna.")
