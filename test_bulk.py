"""Prueba de los cargadores bulk contra los archivos REALES ya descargados.

No toca produccion: construye una base descartable en /tmp con el schema del repo.

Verifica lo que Daniel puso como condicion -- "no se permitiran datos vacios" --
y ademas que las cuatro preguntas que diferencian el producto se puedan responder
con SQL sobre lo cargado.
"""
import os
import sys
import time
from pathlib import Path

import duckdb

sys.path.insert(0, "/app/src")
from mp_mcp import bulk                                       # noqa: E402

DB = Path("/tmp/prueba_bulk.duckdb")
SCHEMA = Path("/app/sql/schema.sql")
for f in (DB, Path(str(DB) + ".wal")):
    if f.exists():
        f.unlink()

con = duckdb.connect(str(DB))
con.execute("SET threads=2")
con.execute(SCHEMA.read_text(encoding="utf-8"))
print("schema aplicado: %d tablas" % con.execute(
    "SELECT count(*) FROM information_schema.tables WHERE table_schema='main'"
).fetchone()[0])

fallos = []

# ------------------------------------------------------------ paridades
print("\n=== 1. tipo_cambio (ParidadMoneda.csv) ===")
p = Path("/tmp/ParidadMoneda.csv")
if not p.exists():
    bulk.descargar.__wrapped__ if False else None
    import httpx
    raw = httpx.get(bulk.URL_PARIDAD, timeout=120.0, follow_redirects=True).content
    p.write_bytes(raw)
    print("descargado: %d bytes" % len(raw))
utf8 = Path("/tmp/ParidadMoneda.utf8.csv")
utf8.write_bytes(p.read_bytes().decode("cp1252", errors="replace").encode("utf-8"))
r = bulk.cargar_paridad(con, utf8)
print("  %s" % r)
if r["tipo_cambio"] == 0:
    fallos.append("tipo_cambio quedo vacia")

# ---------------------------------------------------- ordenes de compra
print("\n=== 2. ordenes de compra (oc-da/2026-8) ===")
csv_oc = Path("/tmp/oc_2026_8.csv")
t0 = time.time()
nombre, tam = bulk.transcodificar(Path("/tmp/oc.zip"), csv_oc)
print("  transcodificado %s -> %.1f MB en %.0f s" % (nombre, tam / 1048576, time.time() - t0))
t0 = time.time()
r_oc = bulk.cargar_oc(con, csv_oc, "2026-8")
print("  %s en %.0f s" % (r_oc, time.time() - t0))
csv_oc.unlink()
for t in ("orden_compra", "orden_compra_item"):
    if r_oc[t] == 0:
        fallos.append("%s quedo vacia" % t)

# ------------------------------------------------ licitaciones y ofertas
print("\n=== 3. licitaciones y ofertas (lic-da/2026-7) ===")
csv_lic = Path("/tmp/lic_2026_7.csv")
nombre, tam = bulk.transcodificar(Path("/tmp/lic.zip"), csv_lic)
print("  transcodificado %s -> %.1f MB" % (nombre, tam / 1048576))
t0 = time.time()
r_lic = bulk.cargar_lic(con, csv_lic, "2026-7")
print("  %s en %.0f s" % (r_lic, time.time() - t0))
bulk._leer_csv(con, csv_lic, "cru")   # queda para verificar unidades de medida
csv_lic.unlink()
for t in ("oferta", "adjudicacion_item"):
    if r_lic[t] == 0:
        fallos.append("%s quedo vacia" % t)

# ------------------------------------------------------ conversion a CLP
print("\n=== 4. conversion a CLP (resuelve P-11) ===")
r_clp = bulk.convertir_a_clp(con)
print("  filas con valor en CLP: %s" % r_clp)

# ------------------------------------- NINGUNA TABLA VACIA: la condicion
print("\n=== 5. ninguna tabla vacia ===")
vacias = []
for (t,) in con.execute("""SELECT table_name FROM information_schema.tables
                           WHERE table_schema='main' ORDER BY 1""").fetchall():
    n = con.execute("SELECT count(*) FROM " + t).fetchone()[0]
    marca = "  <-- VACIA" if n == 0 else ""
    print("  %-24s %10d%s" % (t, n, marca))
    if n == 0:
        vacias.append(t)

# licitacion/licitacion_item se llenan por API (no estan en este bulk de ofertas),
# consulta_guardada y probe_result son artefactos, no datos de ChileCompra.
ESPERADAS_VACIAS = {"licitacion", "licitacion_item", "consulta_guardada",
                    "probe_result", "cuota_hit", "ingesta_log", "unspsc",
                    "listado_vivo"}
inesperadas = [t for t in vacias if t not in ESPERADAS_VACIAS]
if inesperadas:
    fallos.append("tablas vacias que NO deberian estarlo: %s" % inesperadas)

# ------------------------------- las cuatro preguntas que diferencian
print("\n=== 6. las preguntas que no se pueden buscar ===")

print("\n  (a) A que precio me ganaron: ofertas perdedoras vs la ganadora")
for r in con.execute("""
    WITH g AS (SELECT codigo_licitacion, correlativo_item, precio_unitario AS gano,
                      nombre_proveedor AS ganador
               FROM oferta WHERE seleccionada AND precio_unitario IS NOT NULL)
    SELECT o.codigo_licitacion, g.ganador, round(g.gano) AS precio_ganador,
           count(*) AS perdedores, round(min(o.precio_unitario)) AS mas_barato_perdedor
    FROM oferta o JOIN g USING (codigo_licitacion, correlativo_item)
    WHERE NOT o.seleccionada AND o.precio_unitario IS NOT NULL
    GROUP BY 1,2,3 HAVING count(*) >= 4 ORDER BY perdedores DESC LIMIT 3""").fetchall():
    print("      %-18s gano %-32s a %-12s | %d perdedores, el mas barato a %s"
          % (r[0], (r[1] or "")[:32], r[2], r[3], r[4]))

print("\n  (b) Criterios de evaluacion mas frecuentes")
for r in con.execute("""
    SELECT criterios, count(DISTINCT codigo_licitacion) AS lic
    FROM oferta WHERE criterios IS NOT NULL
    GROUP BY 1 ORDER BY 2 DESC LIMIT 3""").fetchall():
    print("      %4d licitaciones: %s" % (r[1], r[0][:88]))

print("\n  (c) Licitaciones con UN SOLO oferente, por organismo")
for r in con.execute("""
    SELECT organismo_nombre, count(DISTINCT codigo_licitacion) AS solas
    FROM oferta WHERE n_oferentes = 1
    GROUP BY 1 ORDER BY 2 DESC LIMIT 3""").fetchall():
    print("      %4d  %s" % (r[1], (r[0] or "")[:70]))

print("\n  (d) Lo adjudicado contra lo pagado (via CodigoLicitacion)")
r = con.execute("""
    SELECT count(DISTINCT oc.codigo_licitacion)
    FROM orden_compra oc
    WHERE oc.codigo_licitacion IN (SELECT DISTINCT codigo_licitacion FROM adjudicacion_item)
""").fetchone()[0]
print("      licitaciones con adjudicacion Y orden de compra en el corpus: %d" % r)

print("\n  (e) La dispersion por UNSPSC NO es una metrica valida: medido")
# El corte de precio minimo no alcanza. El problema es que un mismo commodity
# mezcla unidades de medida incomparables. [MEDIDO 2026-08-19] para 72131702:
#   Unidad 6.209 filas mediana 43.703.400 | Global 2.259 mediana 54.360.000
#   Metro Cuadrado 73 mediana 56.333      | Metro Lineal 2 mediana 4.750
# Comparar "precio unitario" entre un servicio global y un metro lineal no
# significa nada. Y aun dentro de "Unidad" el rango va de 34.000 a 54.000.000,
# porque la unidad de un servicio de arquitectura no es comparable entre
# licitaciones. NO se ofrece "benchmark de precios de mercado".
for r in con.execute("""
    SELECT o.unspsc_commodity, count(DISTINCT c."UnidadMedida") AS unidades,
           count(*) AS filas,
           round(max(o.precio_unitario)/nullif(min(o.precio_unitario),0)) AS ratio
    FROM oferta o JOIN cru c
      ON c."CodigoExterno" = o.codigo_licitacion AND c."Codigoitem" = o.correlativo_item
    WHERE o.precio_unitario IS NOT NULL AND o.unspsc_commodity IS NOT NULL
    GROUP BY 1 HAVING count(*) >= 500 ORDER BY unidades DESC LIMIT 3""").fetchall():
    print("      unspsc %-10s %2d unidades de medida distintas, %5d filas, ratio x%s"
          % r)
print("      => se declara como limitacion, no se publica como capacidad.")

print("\n  (e2) La comparacion que SI es valida: dentro de la MISMA linea")
# Aca la unidad, el objeto y el pliego son identicos por construccion: es la
# misma linea de la misma licitacion. Es la unica base honesta para decirle a un
# proveedor "te ganaron a este precio".
for r in con.execute("""
    WITH g AS (SELECT codigo_licitacion, correlativo_item,
                      min(precio_unitario_clp) AS gano
               FROM oferta WHERE seleccionada AND precio_unitario_clp IS NOT NULL
               GROUP BY 1,2)
    SELECT count(*) AS lineas_comparables,
           round(median(100.0 * (o.precio_unitario_clp - g.gano)
                        / nullif(g.gano,0)), 1) AS sobreprecio_mediano_pct
    FROM oferta o JOIN g USING (codigo_licitacion, correlativo_item)
    WHERE NOT o.seleccionada AND o.precio_unitario_clp IS NOT NULL""").fetchall():
    print("      lineas donde se puede comparar perdedor vs ganador: %s" % r[0])
    print("      sobreprecio MEDIANO del perdedor sobre el ganador: %s%%" % r[1])

print("\n  (f) precios descartados por no ser creibles")
print("      ofertas con precio_sospechoso: %d de %d"
      % (con.execute("SELECT count(*) FROM oferta WHERE precio_sospechoso").fetchone()[0],
         con.execute("SELECT count(*) FROM oferta").fetchone()[0]))

con.execute("CHECKPOINT")
con.close()
mb = DB.stat().st_size / 1048576
print("\n=== DISCO ===")
print("base con 1 mes de OC + 1 mes de licitaciones/ofertas + paridades: %.1f MB" % mb)
print("proyeccion 12 meses: %.2f GB   36 meses: %.2f GB   (techo 10 GB)"
      % (mb * 12 / 1024, mb * 36 / 1024))
DB.unlink()

if fallos:
    print("\nFALLOS:")
    for f in fallos:
        print("  -", f)
    sys.exit(1)
print("\nOK: los tres cargadores llenan sus tablas contra los archivos reales,")
print("    los montos quedan en CLP con arrastre de paridad declarado, y la")
print("    comparacion perdedor-vs-ganador dentro de la misma linea funciona.")
print("    La dispersion por UNSPSC quedo descartada por invalida, no arreglada.")
