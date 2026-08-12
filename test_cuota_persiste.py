"""Prueba local del libro mayor de cuota.

Cubre las dos formas en que el contador mentia, las dos medidas en produccion
el 2026-08-12:

  1. el servidor MCP abre los hechos en SOLO LECTURA y `registrar()` hacia
     `return` en silencio -> el agente veia 0 usados tras gastar cuota real;
  2. `mp-ingest publicar` reemplaza mp.duckdb por un archivo construido desde
     cero -> cada publicacion borraba el libro mayor.

Y una tercera que antes no se podia ni notar: que el libro mayor NO se pueda
escribir tiene que salir en la respuesta de la tool, no solo en el repo.

No consume cuota: no sale a la red.
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
os.environ.pop("MP_CUOTA_LEDGER", None)
sys.path.insert(0, str(Path(__file__).parent / "src"))

from mp_mcp.config import Config      # noqa: E402
from mp_mcp.store import Store        # noqa: E402
from mp_mcp.quota import Cuota        # noqa: E402
from mp_mcp import tools as T         # noqa: E402

fallos = []


def _abrir(ro: bool) -> Store:
    return Store(Config.from_env(), read_only=ro)


# El almacen tiene que existir antes de abrirlo en solo lectura.
_abrir(False).close()

# -- 1. Un almacen en SOLO LECTURA persiste los hits -----------------------
st = _abrir(True)
c = Cuota(st)
c.registrar("publico/licitaciones.json", "on-demand", 200)
c.registrar("publico/licitaciones.json", "on-demand", 200)
c.registrar("publico/licitaciones.json", "on-demand", 429)

if c.usados_24h() != 2:
    fallos.append(f"con store read-only, usados_24h={c.usados_24h()}, esperaba 2")
if c.throttled_24h() != 1:
    fallos.append(f"throttled_24h={c.throttled_24h()}, esperaba 1")
if c.escrituras_fallidas:
    fallos.append(f"{c.escrituras_fallidas} escrituras fallaron: {c.ultimo_fallo}")
print(f"1. store read-only: usados={c.usados_24h()} throttled={c.throttled_24h()}")

# -- 2. Sobrevive al reinicio del servidor (nada en memoria) ---------------
st.close()
st = _abrir(True)
c2 = Cuota(st)
if c2.usados_24h() != 2:
    fallos.append(f"tras reabrir, usados_24h={c2.usados_24h()}, esperaba 2")
print(f"2. tras reiniciar el proceso: usados={c2.usados_24h()}")

# -- 3. Sobrevive a `publicar` (mp.duckdb reemplazado desde cero) ----------
st.close()
Path(os.environ["MP_DB"]).unlink()
nuevo = _abrir(False)                      # como el mp.next.duckdb de la ingesta
hits_en_snapshot = nuevo.one("SELECT count(*) FROM cuota_hit")[0]
c3 = Cuota(nuevo)
if hits_en_snapshot != 0:
    fallos.append(f"el snapshot nuevo trae {hits_en_snapshot} filas en cuota_hit")
if c3.usados_24h() != 2:
    fallos.append(f"tras publicar, usados_24h={c3.usados_24h()}, esperaba 2 "
                  f"(el libro mayor no debe vivir dentro del snapshot)")
print(f"3. tras reemplazar el snapshot: cuota_hit={hits_en_snapshot} "
      f"usados={c3.usados_24h()}")

# -- 4. En un almacen escribible la tabla sigue poblandose (sondas P-03/P-21)
c3.registrar("publico/licitaciones.json", "ingesta", 200)
en_tabla = nuevo.one("SELECT count(*) FROM cuota_hit")[0]
if en_tabla != 1:
    fallos.append(f"cuota_hit tiene {en_tabla} filas, esperaba 1: las sondas "
                  f"P-03 y P-21 consultan esa tabla")
if c3.usados_24h() != 3:
    fallos.append(f"usados_24h={c3.usados_24h()}, esperaba 3 (sin doble conteo "
                  f"entre el JSONL y la tabla)")
print(f"4. store escribible: cuota_hit={en_tabla} usados={c3.usados_24h()} "
      f"(sin doble conteo)")
nuevo.close()

# -- 5. Un libro mayor que no se puede escribir SE DECLARA -----------------
tapon = Path(d) / "no-soy-un-directorio"
tapon.write_text("x", encoding="utf-8")
os.environ["MP_CUOTA_LEDGER"] = str(tapon / "cuota")

st = _abrir(True)
c4 = Cuota(st)
c4.registrar("publico/licitaciones.json", "on-demand", 200)
est = c4.estado()
if est.get("contador_confiable") is not False:
    fallos.append("con el ledger no escribible, contador_confiable no es False")
if "ADVERTENCIA" not in est:
    fallos.append("el estado no trae ADVERTENCIA con el ledger no escribible")
if c4.escrituras_fallidas != 1:
    fallos.append(f"escrituras_fallidas={c4.escrituras_fallidas}, esperaba 1")

resp = T.mp_cuota_estado(c4)
lims = resp["meta"]["limitaciones"]
if not lims or lims[0].get("severidad") != "alta":
    fallos.append("la tool no expone la limitacion de severidad alta primero")
print(f"5. ledger no escribible: contador_confiable={est.get('contador_confiable')} "
      f"limitacion[0].severidad={lims[0].get('severidad') if lims else None}")

# Y que no reviente: la consulta ya se hizo y ya gasto cuota.
if "error" in resp:
    fallos.append("registrar() propago el error del ledger y rompio la tool")
st.close()

# -- 6. Purga por retencion ------------------------------------------------
os.environ["MP_CUOTA_LEDGER"] = str(Path(d) / "data" / "cuota")
st = _abrir(True)
c5 = Cuota(st)
viejo = c5.dir_ledger / "cuota-20200101.jsonl"
viejo.write_text('{"ts":"2020-01-01T00:00:00+00:00","status":200}\n', encoding="utf-8")
n = c5.purgar()
if viejo.exists() or n != 1:
    fallos.append(f"purgar() borro {n} archivos y viejo.exists()={viejo.exists()}")
if c5.usados_24h() != 3:      # 2 del paso 1 + 1 del paso 4; el del 5 nunca se escribio
    fallos.append(f"purgar() toco la ventana viva: usados={c5.usados_24h()}")
print(f"6. purga: {n} archivo(s) fuera de retencion, ventana viva intacta "
      f"(usados={c5.usados_24h()})")
st.close()

if fallos:
    print("\nFALLOS:")
    for f in fallos:
        print("  -", f)
    sys.exit(1)
print("\nOK: el libro mayor persiste con el almacen en solo lectura, sobrevive a")
print("    `publicar`, no dobla el conteo con la tabla, y cuando no se puede")
print("    escribir la tool lo declara en vez de reportar un 0 tranquilizador.")
