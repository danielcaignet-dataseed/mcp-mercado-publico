"""Ingesta y utilidades. `mp-ingest <comando>`.

Honestidad sobre el estado de cada cargador:

  live-*        IMPLEMENTADO. La forma del JSON de la API esta documentada en los
                diccionarios de datos, asi que el mapeo se puede escribir sin ver
                una respuesta real.
  ocds          SCAFFOLDING + `--inspect`. No conozco las rutas exactas del JSONL
                de ChileCompra. El mapeo se escribe DESPUES de inspeccionar un
                archivo real, no antes: escribirlo a ciegas seria inventar
                nombres de campo, que es como se cuela el humo.
  oc-csv        SCAFFOLDING + `--inspect`. Igual: los nombres de columna del CSV
                mensual se leen del archivo, no se adivinan.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .api import ClienteAPI
from .config import Config, scrub
from .quota import Cuota
from .query import Consulta
from .store import Store

RAIZ = Path(__file__).resolve().parents[2]
SEEDS = RAIZ / "seeds" / "consultas.yaml"


def _ahora():
    return datetime.now(timezone.utc)


def _b(v) -> bool | None:
    if v is None or v == "":
        return None
    return str(v) in ("1", "True", "true", "Si", "SI", "2")


def _f(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _fecha(v) -> str | None:
    if not v:
        return None
    return str(v)[:10]


def _unspsc_niveles(codigo) -> tuple[str | None, ...]:
    """Descompone un commodity de 8 digitos en sus cuatro niveles."""
    c = ("" if codigo is None else str(codigo)).strip()
    if not c.isdigit():
        return (None, None, None, None)
    c = c.zfill(8)[:8]
    return (c, c[:6] + "00", c[:4] + "0000", c[:2] + "000000")


# --------------------------------------------------------------------------
# Ingesta desde la API (implementada)
# --------------------------------------------------------------------------

def _insertar(store: Store, tabla: str, fila: dict) -> None:
    """INSERT con columnas nombradas.

    El INSERT posicional se rompio en silencio (27 columnas, 26 placeholders) y
    quemo 200 hits de cuota escribiendo cero filas. El smoke test no lo agarro
    porque insertaba con columnas nombradas: la prueba no ejercitaba el mismo
    camino que produccion. Con nombres, agregar una columna al esquema ya no
    rompe la ingesta.
    """
    cols = ", ".join(fila)
    marcas = ", ".join("?" * len(fila))
    # OR REPLACE y no INSERT pelado: [MEDIDO 2026-08-20] la licitacion
    # 1057384-125-L126 abortaba con "Duplicate key item_id: ...#4" y sus items no
    # entraban. Un detalle de la API puede traer dos veces el mismo correlativo, y
    # reprocesar una licitacion ya cargada es normal en el refresco incremental.
    store.con.execute(f"INSERT OR REPLACE INTO {tabla} ({cols}) VALUES ({marcas})",
                      list(fila.values()))


def _upsert_licitacion(store: Store, lic: dict) -> int:
    comprador = lic.get("Comprador") or {}
    fechas = lic.get("Fechas") or {}
    adj = lic.get("Adjudicacion") or {}
    codigo = lic.get("CodigoExterno")
    if not codigo:
        return 0

    store.con.execute("DELETE FROM licitacion WHERE codigo = ?", [codigo])
    _insertar(store, "licitacion", {
        "codigo": codigo,
        "nombre": lic.get("Nombre"),
        "descripcion": lic.get("Descripcion"),
        "estado": lic.get("Estado"),
        "tipo": lic.get("Tipo"),
        "moneda": lic.get("Moneda"),
        "es_obra": _b(lic.get("Obras")),
        "toma_razon": _b(lic.get("TomaRazon")),
        "subcontratacion": _b(lic.get("SubContratacion")),
        "extension_plazo": _b(lic.get("ExtensionPlazo")),
        "visibilidad_monto": _b(lic.get("VisibilidadMonto")),
        "estimacion": lic.get("Estimacion"),
        "monto_estimado": _f(lic.get("MontoEstimado")),
        "monto_estimado_clp": None,       # lo llena `mp-ingest convertir`
        "n_oferentes": adj.get("NumeroOferentes"),
        "cantidad_reclamos": lic.get("CantidadReclamos"),
        "organismo_codigo": comprador.get("CodigoOrganismo"),
        "organismo_nombre": comprador.get("NombreOrganismo"),
        "region_comprador": comprador.get("RegionUnidad"),
        "comuna_comprador": comprador.get("ComunaUnidad"),
        "fecha_publicacion": _fecha(fechas.get("FechaPublicacion")),
        "fecha_cierre": _fecha(fechas.get("FechaCierre")),
        "fecha_adjudicacion": _fecha(fechas.get("FechaAdjudicacion")),
        "url_ficha": ("https://www.mercadopublico.cl/Procurement/Modules/RFB/"
                      f"DetailsAcquisition.aspx?idlicitacion={codigo}"),
        "url_acta": adj.get("UrlActa"),
        "_procedencia": "api-live",
        "_ingerido_en": _ahora(),
    })

    n = 1
    store.con.execute("DELETE FROM licitacion_item WHERE codigo_licitacion = ?", [codigo])
    store.con.execute("DELETE FROM adjudicacion_item WHERE codigo_licitacion = ?", [codigo])
    for it in ((lic.get("Items") or {}).get("Listado") or []):
        corr = it.get("Correlativo")
        com, cla, fam, seg = _unspsc_niveles(it.get("CodigoProducto"))
        _insertar(store, "licitacion_item", {
            "item_id": f"{codigo}#{corr}", "codigo_licitacion": codigo,
            "correlativo": corr,
            "unspsc_commodity": com, "unspsc_clase": cla,
            "unspsc_familia": fam, "unspsc_segmento": seg,
            "categoria_texto": it.get("Categoria"),
            "nombre_producto": it.get("NombreProducto"),
            "descripcion": it.get("Descripcion"),
            "unidad_medida": it.get("UnidadMedida"),
            "cantidad": _f(it.get("Cantidad")),
            "estado": lic.get("Estado"),
            "organismo_codigo": comprador.get("CodigoOrganismo"),
            "organismo_nombre": comprador.get("NombreOrganismo"),
            "region_comprador": comprador.get("RegionUnidad"),
            "comuna_comprador": comprador.get("ComunaUnidad"),
            "fecha_publicacion": _fecha(fechas.get("FechaPublicacion")),
            "fecha_cierre": _fecha(fechas.get("FechaCierre")),
            "_procedencia": "api-live", "_ingerido_en": _ahora(),
        })
        n += 1

        a = it.get("Adjudicacion") or {}
        if a.get("MontoUnitario") is not None:
            _insertar(store, "adjudicacion_item", {
                "adjudicacion_id": f"{codigo}#{corr}#{a.get('RutProveedor')}",
                "codigo_licitacion": codigo, "correlativo": corr,
                "unspsc_commodity": com, "unspsc_clase": cla,
                "unspsc_familia": fam, "unspsc_segmento": seg,
                "nombre_producto": it.get("NombreProducto"),
                "unidad_medida": it.get("UnidadMedida"),
                "moneda": lic.get("Moneda"),
                "precio_unitario": _f(a.get("MontoUnitario")),
                "precio_unitario_clp": None,
                "cantidad_adjudicada": _f(a.get("CantidadAdjudicada")),
                "rut_proveedor": a.get("RutProveedor"),
                "nombre_proveedor": a.get("NombreProveedor"),
                "tipo": lic.get("Tipo"),
                "organismo_codigo": comprador.get("CodigoOrganismo"),
                "organismo_nombre": comprador.get("NombreOrganismo"),
                "region_comprador": comprador.get("RegionUnidad"),
                "comuna_comprador": comprador.get("ComunaUnidad"),
                "fecha_adjudicacion": _fecha(fechas.get("FechaAdjudicacion")),
                "atribucion_ambigua": False,
                "_procedencia": "api-live", "_ingerido_en": _ahora(),
            })
            n += 1
    return n


def cmd_live_listado(a) -> None:
    cfg = Config.from_env()
    store = Store(cfg)
    cli = ClienteAPI(cfg, Cuota(store))
    d = (cli.licitaciones_por_fecha(a.fecha, a.estado) if a.fecha
         else cli.licitaciones_por_estado(a.estado or "activas"))
    codigos = [x.get("CodigoExterno") for x in (d.get("Listado") or [])]
    print(f"listado: {len(codigos)} licitaciones (Cantidad={d.get('Cantidad')})")

    if a.solo_listado:
        store.log_ingesta("api-listado", a.fecha or a.estado, len(codigos), True)
        store.close()
        return

    if a.limite and a.limite < len(codigos):
        # Muestreo sistematico, no los primeros N: los primeros de un listado
        # suelen estar sesgados por orden de publicacion u organismo.
        paso = len(codigos) / a.limite
        codigos = [codigos[int(i * paso)] for i in range(a.limite)]
        print(f"MUESTRA de {len(codigos)} (1 de cada ~{paso:.0f}, muestreo sistematico). "
              f"Cualquier estadistica sobre esto es de la muestra, no del universo.")

    cuota = Cuota(store)
    cuota.exigir(len(codigos), "ingesta")
    total = 0
    for i, cod in enumerate(codigos, 1):
        try:
            det = cli.licitacion(cod, motivo="ingesta")
            for lic in (det.get("Listado") or []):
                total += _upsert_licitacion(store, lic)
        except Exception as exc:                              # noqa: BLE001
            print(f"  [{i}/{len(codigos)}] {cod}: {scrub(exc)}")
        # Cortafuegos: si las primeras 5 no escribieron nada, algo esta roto y
        # seguir solo quema cuota. Fue exactamente lo que paso el 2026-07-31.
        if i == 5 and total == 0:
            store.log_ingesta("api-detalle", "abortado: 0 filas en las primeras 5",
                              0, False)
            raise SystemExit(
                "ABORTADO: 5 licitaciones procesadas y 0 filas escritas. La ingesta "
                "esta rota; revisa el error de arriba. Se evito gastar los "
                f"{len(codigos) - 5} hits restantes.")
        if i % 50 == 0:
            print(f"  {i}/{len(codigos)}  filas={total}  "
                  f"cuota_restante={cuota.disponibles('ingesta')}")
    store.log_ingesta("api-detalle", a.fecha or a.estado, total, True)
    print(f"filas escritas: {total}")
    cli.close()
    store.close()


def cmd_live_detalle(a) -> None:
    cfg = Config.from_env()
    store = Store(cfg)
    cli = ClienteAPI(cfg, Cuota(store))
    d = cli.licitacion(a.codigo, motivo="on-demand")
    n = sum(_upsert_licitacion(store, lic) for lic in (d.get("Listado") or []))
    print(f"{a.codigo}: {n} filas")
    cli.close()
    store.close()


# --------------------------------------------------------------------------
# Bulk: inspeccionar antes de mapear
# --------------------------------------------------------------------------

def _rutas_json(o, pre="", out=None, prof=0):
    out = out if out is not None else {}
    if prof > 8:
        return out
    if isinstance(o, dict):
        for k, v in o.items():
            _rutas_json(v, f"{pre}{k}.", out, prof + 1)
    elif isinstance(o, list):
        out[pre.rstrip(".") + "[]"] = f"list({len(o)})"
        if o:
            _rutas_json(o[0], f"{pre}0.", out, prof + 1)
    else:
        out[pre.rstrip(".")] = type(o).__name__
    return out


def cmd_ocds(a) -> None:
    """Inspecciona o carga el bulk OCDS.

    El mapeo a las tablas de negocio se escribe DESPUES de correr --inspect sobre
    un archivo real. Ver P-07: hay una contradiccion documentada sobre la
    cobertura del bloque implementation que solo se resuelve mirando el archivo.
    """
    import gzip
    p = Path(a.file)
    if not p.exists():
        raise SystemExit(f"no existe {p}")
    abrir = gzip.open if p.suffix == ".gz" else open
    rutas: dict[str, str] = {}
    con_contracts = con_impl = n = 0
    with abrir(p, "rt", encoding="utf-8") as fh:
        for linea in fh:
            if n >= a.lineas:
                break
            try:
                o = json.loads(linea)
            except ValueError:
                continue
            n += 1
            _rutas_json(o, out=rutas)
            contratos = o.get("contracts") or []
            if contratos:
                con_contracts += 1
                if any(c.get("implementation") for c in contratos):
                    con_impl += 1
    print(f"lineas leidas: {n}")
    print(f"con contracts[]: {con_contracts}  con implementation: {con_impl}")
    print(f"rutas distintas: {len(rutas)}")
    if a.inspect:
        for k in sorted(rutas):
            print(f"  {k}: {rutas[k]}")
        print("\nEscribe el mapeo en cmd_ocds() usando estas rutas reales.")
        return
    raise SystemExit(
        "Carga no implementada a proposito. Corre primero:\n"
        f"  mp-ingest ocds --file {p} --inspect\n"
        "y escribe el mapeo con las rutas reales. Adivinar nombres de campo es "
        "exactamente lo que este proyecto no hace."
    )


def cmd_oc_csv(a) -> None:
    """Inspecciona o carga el CSV mensual de ordenes de compra (UTF-8, sep ';')."""
    import duckdb
    p = Path(a.file)
    if not p.exists():
        raise SystemExit(f"no existe {p}")
    con = duckdb.connect()
    rel = con.execute(
        f"SELECT * FROM read_csv_auto('{p.as_posix()}', delim=';', header=true, "
        f"sample_size=20000) LIMIT 5")
    cols = [d[0] for d in rel.description]
    n = con.execute(
        f"SELECT count(*) FROM read_csv_auto('{p.as_posix()}', delim=';', "
        f"header=true, sample_size=20000)").fetchone()[0]
    print(f"filas: {n}\ncolumnas ({len(cols)}):")
    for c in cols:
        print(f"  {c}")
    if a.inspect:
        print("\nEscribe el mapeo en cmd_oc_csv() con estos nombres reales.")
        return
    raise SystemExit(
        "Carga no implementada a proposito. Corre con --inspect y escribe el mapeo. "
        "Recuerda P-10: este archivo esta procesado por ChileCompra (convertido a "
        "CLP y con transacciones atipicas removidas); no usarlo para benchmark de "
        "precios."
    )


# --------------------------------------------------------------------------
# Derivados y utilidades
# --------------------------------------------------------------------------

def cmd_derivar(a) -> None:
    """Puebla unspsc, organismo y proveedor desde lo observado en los hechos."""
    cfg = Config.from_env()
    store = Store(cfg)
    store.con.execute("DELETE FROM unspsc WHERE _origen = 'observado'")
    # Un mismo codigo puede aparecer en dos niveles: los compradores usan codigos
    # de clase o familia (terminados en 00) como CodigoProducto. Sin deduplicar,
    # el UNION viola la PK. Se conserva el nivel mas especifico observado.
    store.con.execute("""
        INSERT INTO unspsc
        WITH obs AS (
            SELECT unspsc_commodity AS c, unspsc_clase AS cl, unspsc_familia AS f,
                   unspsc_segmento AS s, any_value(categoria_texto) AS txt
            FROM licitacion_item WHERE unspsc_commodity IS NOT NULL
            GROUP BY 1,2,3,4
        ),
        todos AS (
            SELECT c AS codigo, 'commodity' AS nivel, txt AS nombre, s, f, cl, 1 AS rank
            FROM obs
            -- `Categoria` viene como "segmento / familia / clase". Sin esto,
            -- los niveles padre quedan sin nombre y mp_codes_search a nivel
            -- familia -- el nivel recomendado para segmentar mercado -- no
            -- encuentra nada por texto.
            UNION ALL SELECT cl, 'clase',
                   trim(split_part(txt, '/', 3)), s, f, cl, 2 FROM obs
            UNION ALL SELECT f,  'familia',
                   trim(split_part(txt, '/', 2)), s, f, NULL, 3 FROM obs
            UNION ALL SELECT s,  'segmento',
                   trim(split_part(txt, '/', 1)), s, NULL, NULL, 4 FROM obs
        )
        SELECT codigo, nivel, nombre, s, f, cl, 'observado' FROM todos
        QUALIFY row_number() OVER (PARTITION BY codigo ORDER BY rank) = 1
    """)
    store.con.execute("DELETE FROM organismo")
    store.con.execute("""
        INSERT INTO organismo
        SELECT organismo_codigo, any_value(organismo_nombre), any_value(region_comprador),
               any_value(comuna_comprador), NULL
        FROM licitacion WHERE organismo_codigo IS NOT NULL GROUP BY 1
    """)
    store.con.execute("DELETE FROM proveedor")
    store.con.execute("""
        INSERT INTO proveedor
        SELECT rut_proveedor, any_value(nombre_proveedor), NULL, NULL, NULL
        FROM adjudicacion_item WHERE rut_proveedor IS NOT NULL GROUP BY 1
    """)
    for t in ("unspsc", "organismo", "proveedor"):
        print(f"  {t}: {store.one(f'SELECT count(*) FROM {t}')[0]} filas")
    store.close()


def cmd_convertir(a) -> None:
    """Recalcula los montos en CLP usando tipo_cambio. Sin fila -> NULL, no se imputa."""
    cfg = Config.from_env()
    store = Store(cfg)
    store.con.execute("""
        UPDATE licitacion SET monto_estimado_clp = CASE
            WHEN moneda = 'CLP' THEN monto_estimado
            ELSE monto_estimado * (
                SELECT a_clp FROM tipo_cambio tc
                WHERE tc.moneda = licitacion.moneda
                  AND tc.fecha = licitacion.fecha_publicacion)
        END
    """)
    store.con.execute("""
        UPDATE adjudicacion_item SET precio_unitario_clp = CASE
            WHEN moneda = 'CLP' THEN precio_unitario
            ELSE precio_unitario * (
                SELECT a_clp FROM tipo_cambio tc
                WHERE tc.moneda = adjudicacion_item.moneda
                  AND tc.fecha = adjudicacion_item.fecha_adjudicacion)
        END
    """)
    r = store.one("SELECT count(*), count(monto_estimado_clp) FROM licitacion")
    print(f"licitacion: {r[1]}/{r[0]} con monto en CLP")
    r = store.one("SELECT count(*), count(precio_unitario_clp) FROM adjudicacion_item")
    print(f"adjudicacion_item: {r[1]}/{r[0]} con precio en CLP")
    print("Las filas sin tipo de cambio quedan NULL a proposito (ver P-13).")
    store.close()


def cmd_seed(a) -> None:
    """Carga la biblioteca de consultas canonicas."""
    cfg = Config.from_env()
    store = Store(cfg)
    for q in yaml.safe_load(SEEDS.read_text(encoding="utf-8")):
        c = Consulta.parse(q["consulta"])
        store.guardar_consulta(c.query_id(), q["nombre"], q.get("descripcion", ""),
                               q["modo"], c.as_dict(), q.get("etiquetas", []))
        print(f"  {q['nombre']}")
    store.close()


def cmd_set_ticket(a) -> None:
    """Guarda el ticket leyendolo SIN eco. No usar `$env:MP_TICKET = "..."`.

    Motivo: la asignacion por linea de comandos queda en el historial de la shell
    y en cualquier transcript que se copie. Fue la quinta fuga del proyecto.
    """
    import getpass
    import stat
    from .config import ruta_ticket

    cfg = Config.from_env()
    raiz = Path(cfg.db_path).parent
    f = ruta_ticket(raiz)
    v = getpass.getpass("Ticket de Mercado Publico (no se muestra): ").strip()
    if not v:
        raise SystemExit("vacio, no se guardo nada")
    f.write_text(v, encoding="utf-8")
    try:
        f.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    print(f"guardado en {f}")
    print(f"largo: {len(v)} caracteres  ultimos 4: ...{v[-4:]}")
    print("\nAviso: es texto plano en disco. Paliativo de desarrollo. En produccion")
    print("el ticket no vive en el proceso: lo sustituye Agent Vault (README).")
    if os.name == "nt":
        print("En Windows os.chmod no restringe ACLs. Para hacerlo de verdad:")
        print(f'  icacls "{f}" /inheritance:r /grant:r "%USERNAME%:R"')


def cmd_sync(a) -> None:
    """Ciclo completo: bulk historico + API para el dia en curso.

    Es lo que corre el cron. Todo se construye en mp.duckdb.next y solo el ultimo
    paso toca la base servida, con rename atomico. Si quedan tablas vacias NO
    publica: preferimos servir el almacen anterior antes que uno incompleto.
    """
    import json as _json
    from .sync import sincronizar
    informe = sincronizar(meses_bulk=a.meses, meses_retencion=a.retencion,
                          con_api=not a.sin_api, publicar=not a.no_publicar)
    print(_json.dumps(informe, indent=2, ensure_ascii=False, default=str))
    if informe.get("tablas_vacias"):
        raise SystemExit("tablas vacias: %s" % informe["tablas_vacias"])
    if informe.get("OMISIONES"):
        raise SystemExit("cargas omitidas (%d): el almacen puede estar incompleto. %s"
                         % (len(informe["OMISIONES"]), informe["OMISIONES"][:2]))


def cmd_publicar(a) -> None:
    """Publica el snapshot construido, con rename atomico.

    La ingesta debe correr con MP_DB apuntando a mp.next.duckdb. Este comando lo
    mueve encima de mp.duckdb. En el mismo filesystem el rename es atomico: el
    servidor MCP nunca ve un archivo a medio escribir, y su siguiente consulta
    detecta el cambio y reabre (Store.refrescar).
    """
    import shutil
    cfg = Config.from_env()
    origen = Path(a.desde) if a.desde else Path(str(cfg.db_path) + ".next")
    destino = Path(a.hacia) if a.hacia else cfg.db_path
    if not origen.exists():
        raise SystemExit(f"no existe el snapshot {origen}")
    if origen.stat().st_size < 1024:
        raise SystemExit(f"{origen} pesa {origen.stat().st_size} bytes: "
                         "no se publica un snapshot vacio")
    st = Store(Config.from_env().__class__(
        db_path=origen, catalog_path=cfg.catalog_path, data_dir=cfg.data_dir,
        usar_agent_vault=cfg.usar_agent_vault, _ticket=None))
    n = st.one("SELECT count(*) FROM licitacion")[0]
    st.close()
    if n == 0:
        raise SystemExit("el snapshot tiene 0 licitaciones: no se publica")
    if origen.parent != destino.parent:
        raise SystemExit("origen y destino deben estar en el mismo filesystem "
                         "para que el rename sea atomico")
    shutil.move(str(origen), str(destino))
    print(f"publicado {destino}  ({n} licitaciones)")


def cmd_estado(a) -> None:
    cfg = Config.from_env()
    store = Store(cfg)
    print(json.dumps(store.as_of(), indent=2, ensure_ascii=False, default=str))
    print(json.dumps(Cuota(store).estado(), indent=2, ensure_ascii=False))
    print(f"ticket configurado: {cfg.tiene_ticket()} "
          f"(via Agent Vault: {cfg.usar_agent_vault})")
    store.close()


def main() -> None:
    ap = argparse.ArgumentParser(prog="mp-ingest")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("live-listado", help="listado del dia + detalle de cada una")
    p.add_argument("--estado", default=None,
                   help="activas|Publicada|Cerrada|Adjudicada|Desierta|Revocada|"
                        "Suspendida|todos. Por defecto 'activas'. SE COMBINA con "
                        "--fecha: '--fecha 28072026 --estado adjudicada' devuelve "
                        "las adjudicadas de ese dia (MEDIDO 2026-08-12: 259). Sin "
                        "--fecha el endpoint responde sobre el dia corriente, por "
                        "eso '--estado adjudicada' a secas da 0.")
    p.add_argument("--fecha", help="ddmmaaaa")
    p.add_argument("--solo-listado", action="store_true")
    p.add_argument("--limite", type=int,
                   help="muestrea N licitaciones del listado (muestreo sistematico) "
                        "para no gastar un hit por cada una")
    p.set_defaults(fn=cmd_live_listado)

    p = sub.add_parser("sync", help="bulk + api + retencion + publicar (el cron)")
    p.add_argument("--meses", type=int, default=2,
                   help="cuantos meses de bulk refrescar (default 2: el actual y "
                        "el anterior, porque el mes en curso se rearma a diario)")
    p.add_argument("--retencion", type=int, default=36,
                   help="meses a conservar (default 36 = 7,87 GB medidos)")
    p.add_argument("--sin-api", action="store_true",
                   help="omite el refresco por API; no gasta cuota")
    p.add_argument("--no-publicar", action="store_true",
                   help="construye el .next y NO hace el rename")
    p.set_defaults(fn=cmd_sync)

    p = sub.add_parser("publicar", help="rename atomico del snapshot construido")
    p.add_argument("--desde")
    p.add_argument("--hacia")
    p.set_defaults(fn=cmd_publicar)

    p = sub.add_parser("live-detalle")
    p.add_argument("--codigo", required=True)
    p.set_defaults(fn=cmd_live_detalle)

    p = sub.add_parser("ocds", help="bulk OCDS: --inspect primero")
    p.add_argument("--file", required=True)
    p.add_argument("--inspect", action="store_true")
    p.add_argument("--lineas", type=int, default=2000)
    p.set_defaults(fn=cmd_ocds)

    p = sub.add_parser("oc-csv", help="CSV mensual de OC: --inspect primero")
    p.add_argument("--file", required=True)
    p.add_argument("--inspect", action="store_true")
    p.set_defaults(fn=cmd_oc_csv)

    for nombre, fn, ayuda in (
        ("set-ticket", cmd_set_ticket, "guarda el ticket sin eco (NO usar $env:)"),
        ("derivar", cmd_derivar, "puebla unspsc, organismo y proveedor"),
        ("convertir", cmd_convertir, "recalcula montos en CLP"),
        ("seed-consultas", cmd_seed, "carga la biblioteca de consultas"),
        ("estado", cmd_estado, "frescura, cuota y configuracion"),
    ):
        sub.add_parser(nombre, help=ayuda).set_defaults(fn=fn)

    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
