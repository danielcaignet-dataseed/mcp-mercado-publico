"""Sincronizacion completa del almacen: bulk historico + API para el dia en curso.

Es lo que corre el cron dos veces al dia. El orden importa y esta razonado:

  1. paridades     -- primero, porque todo lo demas convierte a CLP con ellas
  2. bulk lic/oc   -- 0 hits, un dia de desfase, mes en curso y el anterior
  3. API incremental -- solo lo que el bulk no puede tener: HOY
  4. retencion     -- deja la ventana de meses configurada
  5. guarda de disco -- corta si se pasa del techo, borrando el mes mas viejo
  6. publicar      -- rename atomico sobre la base que sirve el MCP

Todo se construye en `mp.next.duckdb` y el paso 6 es el unico que toca la base
servida. Si algo falla antes, la base vieja sigue en pie sin tocarse.
"""

from __future__ import annotations

import os
import shutil
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import bulk
from .api import ClienteAPI
from .config import Config, scrub
from .quota import Cuota
from .store import Store

# Techo de disco que fijo Daniel. [MEDIDO 2026-08-19] un mes de ambos bulk pesa
# 223,8 MB tipado en DuckDB, asi que 36 meses son 7,87 GB y entran. La guarda
# existe para que un cambio de volumen en la fuente no llene el disco del VPS.
TECHO_DISCO_BYTES = 10 * 1024 ** 3
MESES_RETENCION = 36


def _meses(hasta: date, n: int) -> list[tuple[int, int]]:
    """Los ultimos n meses, del mas nuevo al mas viejo."""
    out, a, m = [], hasta.year, hasta.month
    for _ in range(n):
        out.append((a, m))
        m -= 1
        if m == 0:
            a, m = a - 1, 12
    return out


def _periodo(a: int, m: int) -> str:
    return "%d-%d" % (a, m)


def _tam(p: Path) -> int:
    return p.stat().st_size if p.exists() else 0


# --------------------------------------------------------------- bulk

def sincronizar_bulk(con, tmp: Path, meses: list[tuple[int, int]],
                     log=print) -> dict:
    """Descarga y carga los meses pedidos de lic-da y oc-da. 0 hits de cuota."""
    tmp.mkdir(parents=True, exist_ok=True)
    res = {"paridad": None, "meses": {}, "errores": []}

    par_csv = tmp / "ParidadMoneda.utf8.csv"
    import httpx
    raw = httpx.get(bulk.URL_PARIDAD, timeout=180.0, follow_redirects=True).content
    par_csv.write_bytes(raw.decode("cp1252", errors="replace").encode("utf-8"))
    res["paridad"] = bulk.cargar_paridad(con, par_csv)
    par_csv.unlink()
    log("paridades: %s" % res["paridad"])

    for a, m in meses:
        per = _periodo(a, m)
        res["meses"][per] = {}
        for contenedor, cargador in ((bulk.CONTENEDOR_LIC, bulk.cargar_lic),
                                     (bulk.CONTENEDOR_OC, bulk.cargar_oc)):
            zp = tmp / f"{contenedor}-{per}.zip"
            cp = tmp / f"{contenedor}-{per}.csv"
            try:
                n = bulk.descargar(bulk.url_mes(contenedor, a, m), zp)
                bulk.transcodificar(zp, cp)
                r = cargador(con, cp, per)
                r["zip_bytes"] = n
                res["meses"][per][contenedor] = r
                log("  %s %s -> %s" % (contenedor, per, r))
            except Exception as exc:                      # noqa: BLE001
                # Se atrapa TODO a proposito, por mes y por fuente. Un mes que no
                # existe todavia no es un fallo (el bulk tiene un dia de desfase),
                # y un CSV malformado tampoco puede matar a los otros once: la
                # primera corrida de 12 meses se cayo en el sexto archivo y perdio
                # el trabajo de los cinco anteriores. El informe lista lo omitido y
                # la compuerta de tablas vacias sigue protegiendo la publicacion.
                res["errores"].append("%s %s: %s" % (contenedor, per, scrub(exc)))
                log(("  %s %s OMITIDO: %s" % (contenedor, per, scrub(exc)))[:240])
            finally:
                zp.unlink(missing_ok=True)
                cp.unlink(missing_ok=True)
    return res


# ------------------------------------------------------- API incremental

def refrescar_api(store: Store, con, log=print, tope_hits: int = 1500) -> dict:
    """Trae de la API SOLO lo que el bulk no puede tener: el dia en curso.

    Incremental de verdad: compara el listado vivo contra lo guardado y pide
    detalle unicamente de las nuevas y las que cambiaron de estado o de fecha de
    cierre. [MEDIDO 2026-08-19] con 13 dias de atraso el delta era 3.357 de 4.393;
    en regimen, cada 12 h, es una fraccion de eso.

    `tope_hits` es un cortafuegos: si el delta es mayor, se recorta y se dice.
    Sin esto, un cambio masivo de estado en la fuente podria vaciar la cuota.
    """
    from .cli import _upsert_licitacion

    cfg = Config.from_env()
    cuota = Cuota(store)
    cli = ClienteAPI(cfg, cuota)
    out = {"hits_usados_antes": cuota.usados_24h()}
    try:
        d = cli.licitaciones_por_estado("activas", motivo="ingesta")
        vivo = {}
        for x in (d.get("Listado") or []):
            fc = str(x.get("FechaCierre") or "")[:10] or None
            vivo[x.get("CodigoExterno")] = (x.get("CodigoEstado"), fc)
        out["listado"] = len(vivo)

        guard = {}
        try:
            for k, e, f in con.execute(
                    "SELECT codigo, estado, fecha_cierre FROM licitacion").fetchall():
                guard[k] = (e, str(f)[:10] if f else None)
        except Exception:                                     # noqa: BLE001
            pass

        # El estado guardado es texto ("Publicada") y el vivo es numero (5). Se
        # compara en la MISMA representacion: sin esto el delta daba 1.223 falsos
        # positivos que eran solo diferencia de formato.
        glos = {5: "Publicada", 6: "Cerrada", 7: "Desierta", 8: "Adjudicada",
                15: "Revocada"}
        nuevas = [k for k in vivo if k not in guard]
        cambiadas = [k for k in vivo
                     if k in guard and (glos.get(vivo[k][0]), vivo[k][1]) != guard[k]]
        pedir = nuevas + cambiadas
        out.update({"nuevas": len(nuevas), "cambiadas": len(cambiadas),
                    "sin_cambio": len(vivo) - len(pedir)})

        if len(pedir) > tope_hits:
            out["recortado_de"] = len(pedir)
            pedir = pedir[:tope_hits]
            log("AVISO: el delta era %d y se recorto a %d por el cortafuegos de "
                "cuota. Las que quedan afuera entran en la proxima corrida."
                % (out["recortado_de"], tope_hits))

        filas = 0
        for i, cod in enumerate(pedir, 1):
            try:
                det = cli.licitacion(cod, motivo="ingesta")
                for lic in (det.get("Listado") or []):
                    filas += _upsert_licitacion(store, lic)
            except Exception as exc:                          # noqa: BLE001
                log("  %s: %s" % (cod, scrub(exc))[:160])
            if i % 200 == 0:
                log("  api %d/%d filas=%d cuota_restante=%d"
                    % (i, len(pedir), filas, cuota.disponibles("ingesta")))
        out["detalles_pedidos"] = len(pedir)
        out["filas_escritas"] = filas
    finally:
        cli.close()
    out["hits_usados_despues"] = cuota.usados_24h()
    out["hits_gastados"] = out["hits_usados_despues"] - out["hits_usados_antes"]
    return out


# ------------------------------------------------ retencion y disco

def aplicar_retencion(con, meses: int = MESES_RETENCION, log=print) -> dict:
    """Deja solo los ultimos `meses` periodos de cada tabla cargada por bulk.

    Borra, no archiva: el dato se puede volver a bajar gratis desde ChileCompra,
    asi que guardarlo dos veces es gastar disco sin comprar nada.
    """
    vivos = {"bulk-lic:%s" % _periodo(a, m) for a, m in _meses(date.today(), meses)}
    vivos |= {"bulk-oc:%s" % _periodo(a, m) for a, m in _meses(date.today(), meses)}
    out = {}
    for tabla in ("oferta", "adjudicacion_item", "orden_compra", "orden_compra_item"):
        antes = con.execute("SELECT count(*) FROM " + tabla).fetchone()[0]
        con.execute(
            f"DELETE FROM {tabla} WHERE _procedencia LIKE 'bulk-%' "
            f"AND _procedencia NOT IN ({','.join('?' * len(vivos))})", list(vivos))
        despues = con.execute("SELECT count(*) FROM " + tabla).fetchone()[0]
        out[tabla] = {"antes": antes, "despues": despues, "borradas": antes - despues}
    log("retencion (%d meses): %s" % (meses, out))
    return out


def guarda_disco(con, db_path: Path, techo: int = TECHO_DISCO_BYTES,
                 log=print) -> dict:
    """Si la base supera el techo, borra el periodo mas viejo y reintenta.

    Es un corte duro, no la politica normal: [MEDIDO] 36 meses son 7,87 GB. Si
    esto se dispara, algo cambio en el volumen de la fuente y hay que mirarlo.
    """
    out = {"techo_bytes": techo, "borrados": [], "disparada": False}
    for _ in range(48):
        con.execute("CHECKPOINT")
        tam = _tam(db_path)
        out["tamano_final"] = tam
        if tam <= techo:
            break
        out["disparada"] = True
        viejo = con.execute("""
            SELECT _procedencia FROM (
                SELECT _procedencia FROM oferta WHERE _procedencia LIKE 'bulk-%'
                UNION ALL
                SELECT _procedencia FROM orden_compra WHERE _procedencia LIKE 'bulk-%')
            GROUP BY 1 ORDER BY 1 LIMIT 1""").fetchone()
        if not viejo:
            log("GUARDA DE DISCO: %d bytes sobre el techo y no queda periodo bulk "
                "que borrar. Requiere intervencion." % tam)
            break
        for tabla in ("oferta", "adjudicacion_item", "orden_compra",
                      "orden_compra_item"):
            con.execute(f"DELETE FROM {tabla} WHERE _procedencia = ?", [viejo[0]])
        out["borrados"].append(viejo[0])
        log("GUARDA DE DISCO: %.2f GB > techo. Borrado el periodo %s."
            % (tam / 1024 ** 3, viejo[0]))
    return out


# --------------------------------------------------------------- todo

def sincronizar(meses_bulk: int = 2, meses_retencion: int = MESES_RETENCION,
                con_api: bool = True, publicar: bool = True, log=print) -> dict:
    """El ciclo completo. Devuelve un informe con todo lo medido."""
    cfg = Config.from_env()
    destino = Path(cfg.db_path)
    siguiente = Path(str(destino) + ".next")
    tmp = Path(cfg.data_dir) / "bulk-tmp"

    # Se parte de una COPIA de la base servida, no de cero: si se construyera de
    # cero, cada corrida perderia los meses ya cargados y habria que rebajar todo.
    if siguiente.exists():
        siguiente.unlink()
    if destino.exists():
        shutil.copy2(destino, siguiente)

    cfg_next = Config(db_path=siguiente, catalog_path=Path(cfg.catalog_path),
                      data_dir=Path(cfg.data_dir),
                      usar_agent_vault=cfg.usar_agent_vault, _ticket=None)
    os.environ["MP_DB"] = str(siguiente)
    store = Store(cfg_next)
    con = store.con
    informe = {"iniciado": datetime.now(timezone.utc).isoformat()}
    try:
        informe["bulk"] = sincronizar_bulk(
            con, tmp, _meses(date.today(), meses_bulk), log=log)
        informe["clp"] = bulk.convertir_a_clp(con)
        log("conversion a CLP: %s" % informe["clp"])
        if con_api:
            informe["api"] = refrescar_api(store, con, log=log)
            log("api incremental: %s" % informe["api"])
        informe["retencion"] = aplicar_retencion(con, meses_retencion, log=log)
        informe["clp_pendientes"] = bulk.convertir_a_clp(con)
        informe["disco"] = guarda_disco(con, siguiente, log=log)
        informe["conteos"] = {
            t: con.execute("SELECT count(*) FROM " + t).fetchone()[0]
            for t in ("licitacion", "licitacion_item", "oferta", "adjudicacion_item",
                      "orden_compra", "orden_compra_item", "proveedor", "organismo",
                      "tipo_cambio")}
        vacias = [t for t, n in informe["conteos"].items() if n == 0]
        informe["tablas_vacias"] = vacias
        store.log_ingesta("sync", "bulk+api", sum(informe["conteos"].values()),
                          not vacias)
    finally:
        store.close()
        os.environ["MP_DB"] = str(destino)

    if publicar:
        if informe.get("tablas_vacias"):
            informe["publicado"] = False
            informe["motivo"] = ("NO se publico: quedaron tablas vacias %s. Antes "
                                 "que servir un almacen incompleto, se conserva el "
                                 "anterior." % informe["tablas_vacias"])
            log(informe["motivo"])
        else:
            shutil.move(str(siguiente), str(destino))
            informe["publicado"] = True
            log("publicado %s" % destino)
    informe["terminado"] = datetime.now(timezone.utc).isoformat()
    return informe
