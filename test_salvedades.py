"""Las salvedades son codigo y se podrian: esta prueba las verifica.

Hoy encontramos el mismo defecto en dos direcciones opuestas:

  - un control que FALTABA dejo pasar un numero falso al grafico: la I.
    Municipalidad de Rio Bueno aparecia tercera en el gasto nacional con $1.053
    mil millones por una orden de arriendo de vehiculos cargada como 5.294.933 UF;
  - un control RANCIO hizo que el agente subestimara la frescura 50 veces en un
    artefacto para cliente: la salvedad decia "rezago de hasta ~50 dias" cuando el
    rezago real es de 1 dia.

Las salvedades viajan hasta el pie del grafico que ve el comprador. Una salvedad
falsa es un bug de produccion, no una nota interna.

No sale a la red y no toca produccion.
"""
import inspect
import os
import re
import sys
import tempfile
from pathlib import Path

d = tempfile.mkdtemp()
os.environ["MP_HOME"] = d
os.environ["MP_DB"] = str(Path(d) / "mp.duckdb")
os.environ["MP_CATALOGO"] = str(Path(d) / "cat.duckdb")
os.environ["MP_CUOTA_LEDGER"] = str(Path(d) / "cuota")
sys.path.insert(0, str(Path(__file__).parent / "src"))

import duckdb                                                 # noqa: E402
from mp_mcp import bulk, semantic                             # noqa: E402

fallos = []

# ==========================================================================
# A. El invariante que la salvedad AFIRMA tiene que cumplirse en el dato
# ==========================================================================
print("A. la salvedad de exclusion dice que total_clp es NULL en las excluidas")

con = duckdb.connect(str(Path(d) / "t.duckdb"))
con.execute(Path(__file__).parent.joinpath("sql", "schema.sql").read_text(
    encoding="utf-8"))
ahora = "2026-08-19 00:00:00"
filas = [("OK-1", 1000.0), ("MALA-1", 999999.0), ("OK-2", 2000.0),
         ("MALA-2", 888888.0)]
for cod, monto in filas:
    con.execute(
        "INSERT INTO orden_compra (codigo, total, total_clp, _procedencia, "
        "_ingerido_en) VALUES (?, ?, ?, 'bulk-oc:test', ?)",
        [cod, monto, monto, ahora])
    con.execute(
        "INSERT INTO orden_compra_item (item_id, codigo_oc, precio_neto, "
        "precio_neto_clp, total_clp, _procedencia, _ingerido_en) "
        "VALUES (?, ?, ?, ?, ?, 'bulk-oc:test', ?)",
        [cod + "#1", cod, monto, monto, monto, ahora])
con.execute("""CREATE TABLE IF NOT EXISTS oc_excluida (
    codigo VARCHAR PRIMARY KEY, motivo VARCHAR, _fuente VARCHAR)""")
con.executemany("INSERT INTO oc_excluida VALUES (?, 'monto', 'prueba')",
                [("MALA-1",), ("MALA-2",)])

r = bulk.marcar_excluidas(con)
print("   marcar_excluidas -> %s" % {k: v for k, v in r.items()
                                     if k != "monto_original_excluido_mil_mill"})

mal = con.execute("""SELECT codigo, total_clp, excluida_por_fuente
                     FROM orden_compra ORDER BY codigo""").fetchall()
for cod, clp, exc in mal:
    esperado_excluida = cod.startswith("MALA")
    if exc != esperado_excluida:
        fallos.append("%s: excluida_por_fuente=%s, esperaba %s"
                      % (cod, exc, esperado_excluida))
    if esperado_excluida and clp is not None:
        fallos.append("%s esta excluida pero total_clp=%s, la salvedad dice NULL"
                      % (cod, clp))
    if not esperado_excluida and clp is None:
        fallos.append("%s NO esta excluida y total_clp quedo NULL" % cod)
print("   filas: %s" % mal)

suma = con.execute("SELECT sum(total_clp) FROM orden_compra").fetchone()[0]
if suma != 3000.0:
    fallos.append("sum(total_clp)=%s, esperaba 3000 (solo las buenas)" % suma)
print("   sum(total_clp) = %s  (solo las no excluidas: la salvedad se cumple)" % suma)

it = con.execute("""SELECT count(*) FROM orden_compra_item
                    WHERE excluida_por_fuente AND (precio_neto_clp IS NOT NULL
                       OR total_clp IS NOT NULL)""").fetchone()[0]
if it:
    fallos.append("%d items excluidos conservaron valor en CLP" % it)
con.close()

# ==========================================================================
# B. Las frases que se retiraron NO pueden volver
# ==========================================================================
print("\nB. frases retiradas por falsas, medidas contra la fuente")
fuente = Path(__file__).parent.joinpath("src", "mp_mcp", "semantic.py").read_text(
    encoding="utf-8")
RETIRADAS = [
    ("~50 dias\" para el rezago de OC",
     'rezago de hasta ~50 dias. No sirve para preguntas'),
    ("\"transacciones atipicas removidas\" como afirmacion",
     "convertido a pesos y CON TRANSACCIONES ATIPICAS REMOVIDAS"),
    ("adjudicacion_item \"derivada de OCDS\"",
     "usar la entidad adjudicacion_item, derivada de OCDS"),
]
for etiqueta, frase in RETIRADAS:
    presente = frase in fuente
    print("   %-46s %s" % (etiqueta, "VOLVIO" if presente else "ausente"))
    if presente:
        fallos.append("volvio una frase retirada por falsa: %s" % etiqueta)

# ==========================================================================
# C. Toda salvedad que dice [MEDIDO] tiene que traer la fecha
# ==========================================================================
print("\nC. trazabilidad: [MEDIDO] sin fecha es una afirmacion sin respaldo")
sin_fecha = []
total_medido = 0
for nombre, e in semantic.ENTIDADES.items():
    for cav in e.caveats:
        t = cav.texto
        for m in re.finditer(r"\[MEDIDO([^\]]*)\]", t):
            total_medido += 1
            if not re.search(r"\d{4}-\d{2}-\d{2}", m.group(1)):
                sin_fecha.append((nombre, m.group(0)))
print("   salvedades con [MEDIDO]: %d   sin fecha: %d" % (total_medido,
                                                          len(sin_fecha)))
for n, m in sin_fecha[:5]:
    print("      %s -> %s" % (n, m))
if sin_fecha:
    fallos.append("%d marcas [MEDIDO] sin fecha" % len(sin_fecha))

# ==========================================================================
# D. Las dos salvedades corregidas dicen lo medido
# ==========================================================================
print("\nD. las dos corregidas hoy")
oc = semantic.ENTIDADES["orden_compra"]
textos = " ".join(c.texto for c in oc.caveats)
COMPROBAR = [
    ("el rezago es de UN dia", "UN DIA"),
    ("los atipicos NO vienen removidos", "NO vienen removidas"),
    ("nombra la bandera de exclusion", "excluida_por_fuente"),
    ("cuantifica el efecto medido", "Rio Bueno"),
]
for etiqueta, aguja in COMPROBAR:
    ok = aguja in textos
    print("   %-40s %s" % (etiqueta, "OK" if ok else "FALTA"))
    if not ok:
        fallos.append("la salvedad de orden_compra no dice: %s" % etiqueta)

if fallos:
    print("\nFALLOS:")
    for f in fallos:
        print("  -", f)
    sys.exit(1)
print("\nOK: el invariante que la salvedad afirma se cumple en el dato, las frases")
print("    retiradas por falsas no volvieron, y cada [MEDIDO] trae su fecha.")
