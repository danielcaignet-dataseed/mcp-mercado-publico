"""Smoke test del nucleo del MCP, sin el SDK de mcp ni ticket.

Todos los valores de credencial de este archivo son inventados. No usar
fragmentos de tokens reales como fixture, ni siquiera rotados.
"""
import json, os, sys, tempfile, pathlib

RAIZ = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))
tmp = tempfile.mkdtemp()
os.environ["MP_HOME"] = tmp

from mp_mcp.config import Config, scrub                      # noqa: E402
from mp_mcp.store import Store                               # noqa: E402
from mp_mcp.quota import Cuota                               # noqa: E402
from mp_mcp.query import Consulta, validar                   # noqa: E402
from mp_mcp import tools as T                                # noqa: E402
import yaml                                                  # noqa: E402

fallos = []
def check(nombre, cond, extra=""):
    print(("  OK   " if cond else "  FALLA") + f" {nombre}" + (f"  {extra}" if extra else ""))
    if not cond:
        fallos.append(nombre)

cfg = Config.from_env()
store = Store(cfg)
cuota = Cuota(store)
print("\n== 1. esquema ==")
tablas = [r[0] for r in store.rows("SHOW TABLES")[1]]
check("13 tablas creadas", len(tablas) == 13, f"{len(tablas)}: {sorted(tablas)}")

print("\n== 2. scrub ==")
s = scrub("GET https://api.mercadopublico.cl/x.json?estado=activas&ticket=ABCD-1234-EF "
          "AGENT_VAULT_TOKEN=av_agt_FIXTURE0000FALSO http://user:pw@h/x")
check("ticket enmascarado", "ABCD-1234-EF" not in s, s)
check("token enmascarado", "FIXTURE0000FALSO" not in s)
check("userinfo enmascarado", "user:pw" not in s)

print("\n== 3. datos de prueba ==")
store.con.execute("""INSERT INTO licitacion (codigo,nombre,estado,tipo,moneda,
    visibilidad_monto,estimacion,monto_estimado,monto_estimado_clp,organismo_codigo,
    organismo_nombre,region_comprador,fecha_publicacion,fecha_cierre) VALUES
    ('1-1-LE26','Aseo edificio','Publicada','LE','CLP',TRUE,1,8000000,8000000,'AB1',
     'Municipalidad X','Valparaiso','2026-07-01','2026-08-10'),
    ('2-2-LP26','Equipos medicos','Adjudicada','LP','USD',TRUE,2,50000,NULL,'CD2',
     'Hospital Y','Metropolitana','2026-05-02','2026-06-01')""")
store.con.execute("""INSERT INTO licitacion_item (item_id,codigo_licitacion,correlativo,
    unspsc_commodity,unspsc_clase,unspsc_familia,unspsc_segmento,categoria_texto,
    nombre_producto,unidad_medida,cantidad,estado,organismo_codigo,organismo_nombre,
    region_comprador,fecha_publicacion,fecha_cierre) VALUES
    ('1-1-LE26#1','1-1-LE26',1,'76111501','761115','7611','76000000','Aseo > Limpieza',
     'Servicio de aseo','Mes',12,'Publicada','AB1','Municipalidad X','Valparaiso',
     '2026-07-01','2026-08-10'),
    ('2-2-LP26#1','2-2-LP26',1,'42181501','421815','4218','42000000','Salud > Equipos',
     'Monitor multiparametro','Unidad',4,'Adjudicada','CD2','Hospital Y',
     'Metropolitana','2026-05-02','2026-06-01')""")
store.con.execute("""INSERT INTO adjudicacion_item (adjudicacion_id,codigo_licitacion,
    correlativo,unspsc_commodity,unspsc_clase,unspsc_familia,unspsc_segmento,
    nombre_producto,unidad_medida,moneda,precio_unitario,precio_unitario_clp,
    cantidad_adjudicada,rut_proveedor,nombre_proveedor,tipo,organismo_codigo,
    organismo_nombre,region_comprador,fecha_adjudicacion) VALUES
    ('a1','2-2-LP26',1,'42181501','421815','4218','42000000','Monitor','Unidad','USD',
     1200,NULL,4,'76.111.222-3','MedCorp','LP','CD2','Hospital Y','Metropolitana',
     '2026-06-15'),
    ('a2','2-2-LP26',2,'42181501','421815','4218','42000000','Monitor','Unidad','CLP',
     1150000,1150000,2,'77.222.333-4','SaludSpA','LP','CD2','Hospital Y',
     'Metropolitana','2026-06-15')""")
check("filas insertadas", store.one("SELECT count(*) FROM adjudicacion_item")[0] == 2)

print("\n== 4. mp_schema_describe ==")
d = T.mp_schema_describe(store)
lic = d["entidades"]["licitacion"]
check("8 entidades", len(d["entidades"]) == 8, str(list(d["entidades"])))
check("licitacion declara grano", bool(lic["grano"]))
check("monto_estimado_clp trae limitaciones",
      "limitaciones" in lic["medidas"]["monto_estimado_clp"],
      json.dumps(lic["medidas"]["monto_estimado_clp"]["limitaciones"][0], ensure_ascii=False)[:120])
adj = d["entidades"]["adjudicacion_item"]
check("caveat con verificado_por",
      any(c["verificado_por"] != "NO VERIFICADO" for c in adj["limitaciones"]))
check("caveat sin sonda dice NO VERIFICADO",
      any(c["verificado_por"] == "NO VERIFICADO"
          for c in d["entidades"]["proveedor"]["limitaciones"]))

print("\n== 5. mp_aggregate valido ==")
q = {"entity": "adjudicacion_item",
     "filters": [{"field": "unspsc_clase", "op": "in", "value": ["421815"]}],
     "group_by": ["unidad_medida"],
     "measures": [{"fn": "p50", "field": "precio_unitario_clp", "alias": "mediana"},
                  {"fn": "count", "field": "n_adjudicaciones", "alias": "n"}],
     "order_by": [{"alias": "n", "dir": "desc"}]}
r = T.mp_aggregate(store, cuota, q)
check("sin error", "error" not in r, json.dumps(r)[:200])
check("devuelve data", len(r["data"]) == 1, json.dumps(r["data"], default=str))
check("chart_hint barra", r["chart_hint"]["tipo"] == "barra", r["chart_hint"]["tipo"])
cob = r["meta"]["cobertura"]
check("cobertura detecta fila sin conversion",
      cob["excluidas_sin_conversion_moneda"] == 1 and cob["pct_con_valor_clp"] == 50.0,
      json.dumps(cob, ensure_ascii=False))
check("limitaciones en meta", len(r["meta"]["limitaciones"]) >= 3,
      str(len(r["meta"]["limitaciones"])))
check("query_id estable", r["query_id"] == Consulta.parse(q).query_id(), r["query_id"])
check("atribucion presente", "ChileCompra" in r["meta"]["atribucion"])

print("\n== 6. errores accionables ==")
for caso, esperado in [
    ({"entity": "licitacion_item",
      "measures": [{"fn": "sum", "field": "monto_estimado_clp"}]}, "no es una medida"),
    ({"entity": "adjudicacion_item",
      "measures": [{"fn": "p50", "field": "n_adjudicaciones"}]}, "no aplica"),
    ({"entity": "licitacion", "group_by": ["inventado"],
      "measures": [{"fn": "count", "field": "n_licitaciones"}]}, "no es dimension"),
    ({"entity": "licitacion", "grain": "mes",
      "measures": [{"fn": "count", "field": "n_licitaciones"}]}, "sin grain_field"),
    ({"entity": "no_existe"}, "no es una entidad"),
]:
    r2 = T.mp_aggregate(store, cuota, caso)
    ok = "error" in r2 and esperado in r2["error"]
    check(f"error '{esperado}'", ok, r2.get("error", "")[:150])

print("\n== 7. mp_search y grain temporal ==")
r3 = T.mp_search(store, cuota, {"entity": "licitacion",
                                "fields": ["codigo", "nombre", "organismo", "fecha_cierre"],
                                "order_by": [{"alias": "fecha_cierre", "dir": "asc"}]})
check("search 2 filas", len(r3["data"]) == 2, json.dumps(r3["data"], default=str)[:200])
r4 = T.mp_aggregate(store, cuota, {"entity": "licitacion", "grain": "mes",
                                   "grain_field": "fecha_publicacion",
                                   "measures": [{"fn": "count", "field": "n_licitaciones",
                                                 "alias": "n"}]})
check("grain -> linea", r4["chart_hint"]["tipo"] == "linea", json.dumps(r4["data"], default=str))
check("columna periodo", "periodo" in r4["columnas"], str(r4["columnas"]))

print("\n== 8. mp_get con relacionados ==")
r5 = T.mp_get(store, cuota, "licitacion", "2-2-LP26")
check("trae items", len(r5["relacionados"]["items"]) == 1)
check("trae adjudicaciones", len(r5["relacionados"]["adjudicaciones"]) == 2)
r6 = T.mp_get(store, cuota, "licitacion", "NO-EXISTE")
check("error menciona as_of", "error" in r6 and "as_of" in r6, r6.get("error", "")[:120])

print("\n== 9. mp_codes_search ==")
store.con.execute("""INSERT INTO unspsc VALUES
    ('7611','familia','Servicios de limpieza','76000000','7611',NULL,'observado'),
    ('4218','familia','Equipos medicos','42000000','4218',NULL,'observado')""")
r7 = T.mp_codes_search(store, "unspsc_familia", "limpieza")
check("resuelve familia", len(r7["data"]) == 1 and r7["data"][0]["codigo"] == "7611",
      json.dumps(r7["data"], default=str))
check("trae volumen", r7["data"][0]["registros"] == 1, str(r7["data"][0]["registros"]))
r8 = T.mp_codes_search(store, "inventado")
check("vocabulario invalido -> error", "error" in r8, r8.get("error", "")[:100])

print("\n== 10. consultas guardadas ==")
r9 = T.mp_query_save(store, "bench_test", q, "aggregate", "prueba", ["precio"])
check("guarda", r9.get("guardada") is True, json.dumps(r9)[:120])
r10 = T.mp_query_run(store, cuota, "bench_test")
check("re-ejecuta", "error" not in r10 and len(r10["data"]) == 1)
check("meta.consulta_guardada", r10["meta"]["consulta_guardada"]["nombre"] == "bench_test")
r11 = T.mp_query_run(store, cuota, "bench_test",
                     {"filters": [{"field": "unspsc_clase", "op": "in",
                                   "value": ["761115"]}]})
check("sobrescribir filtra distinto", len(r11["data"]) == 0,
      json.dumps(r11["data"], default=str))
r12 = T.mp_query_run(store, cuota, "no_existe")
check("ref inexistente lista disponibles", "disponibles" in r12, json.dumps(r12)[:140])
r13 = T.mp_query_save(store, "malo", {"entity": "licitacion",
                                      "measures": [{"fn": "sum", "field": "xx"}]},
                      "aggregate")
check("no guarda consulta invalida", "error" in r13, r13.get("error", "")[:110])

print("\n== 11. biblioteca de seeds valida ==")
seeds = yaml.safe_load((RAIZ / "seeds" / "consultas.yaml").read_text(encoding="utf-8"))
malos = []
for s_ in seeds:
    try:
        validar(Consulta.parse(s_["consulta"]), s_["modo"])
    except Exception as exc:
        malos.append(f"{s_['nombre']}: {exc}")
check(f"{len(seeds)} consultas semilla validan", not malos, "; ".join(malos)[:400])

print("\n== 12. cuota ==")
est = cuota.estado()
check("cuota reporta limite documentado", est["limite_documentado_dia"] == 10000)
check("reserva on-demand aplicada",
      est["disponibles_ingesta"] == est["disponibles_on_demand"] - 1000)
try:
    cuota.exigir(20000, "ingesta"); ok = False; msg = ""
except Exception as exc:
    ok, msg = "no es modificable" in str(exc), str(exc)
check("rechazo de cuota es accionable", ok, msg[:160])

print("\n== 13. probes.yaml ==")
probes = yaml.safe_load((RAIZ / "probes" / "probes.yaml").read_text(encoding="utf-8"))
ids = [p["id"] for p in probes]
check("27 sondas declaradas", len(probes) == 27, str(len(probes)))
check("8 potencialidades / 19 limitaciones",
      sum(1 for p_ in probes if p_["tipo"]=="potencialidad")==8 and
      sum(1 for p_ in probes if p_["tipo"]=="limitacion")==19)
check("ids unicos", len(set(ids)) == len(ids))
check("todas con metodo y afirmacion",
      all(p.get("metodo") and p.get("afirmacion") for p in probes))
check("limitaciones alta severidad tienen bloquea_venta_de o impacto",
      all(p.get("bloquea_venta_de") or p.get("impacto")
          for p in probes if p["tipo"] == "limitacion"))
from mp_mcp import probe as P
sin_impl = [p["id"] for p in probes if p["id"] not in P._IMPL]
check("solo faltan las de verificacion manual",
      set(sin_impl) == {"V-08","P-04","P-10","P-14","P-19","P-20"}, f"sin impl: {sorted(sin_impl)}")

store.close()
print("\n" + "=" * 60)
print("FALLOS:", fallos if fallos else "ninguno")
sys.exit(1 if fallos else 0)
