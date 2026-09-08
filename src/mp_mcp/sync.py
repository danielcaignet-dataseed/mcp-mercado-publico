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

import duckdb
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import bulk
from .api import ClienteAPI
from .config import Config, scrub
from .quota import Cuota
from .store import Store

# Techo de disco. [MEDIDO 2026-08-19] un mes de ambos bulk pesa 223,8 MB tipado
# en DuckDB, asi que 36 meses eran 7,87 GB y entraban en 10 GB.
#
# [MEDIDO 2026-09-07] ya no entran: `pragma database_size` sobre la base servida
# da 9,52 GB de datos REALES con solo 4,6 % de desperdicio. El dato legitimo
# crecio hasta el techo -- la corrida buena de las 07:00 paso con 27 MB de
# margen -- y las cuatro siguientes lo cruzaron. Sale por entorno para que
# subirlo no exija tocar un archivo dentro del contenedor.
#
# OJO: nada le pone techo a `licitacion` ni `licitacion_item` (aplicar_retencion
# solo poda las cuatro tablas bulk), asi que el archivo crece igual y esto
# vuelve. Subir el techo compra tiempo, no cierra el tema.
TECHO_DISCO_BYTES = int(float(os.environ.get("MP_TECHO_DISCO_GB", "13")) * 1024 ** 3)
MESES_RETENCION = 36

# Las cuatro tablas que carga el bulk y que la retencion y la guarda pueden podar.
TABLAS_BULK = ("oferta", "adjudicacion_item", "orden_compra", "orden_compra_item")

# La guarda no puede volver a dar 48 vueltas vaciando el almacen. Seis vueltas, y
# cada una tiene que recuperar algo medible o se corta.
TOPE_VUELTAS_GUARDA = 6
MIN_RECUPERADO_BYTES = 64 * 1024 ** 2


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

    # Antes de cargar montos: sin las listas de exclusion, un error de tipeo de un
    # municipio contamina cualquier ranking de gasto.
    res["exclusiones"] = bulk.cargar_excluidas(con)
    log("exclusiones de ChileCompra: %s" % res["exclusiones"])

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
    out = {"hits_usados_antes": cuota.usados_24h(), "errores": []}
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
                # No alcanza con loguear. Un upsert que revienta deja la
                # licitacion a medias y la corrida decia OK igual, porque
                # esto no llegaba a OMISIONES. Ahora si.
                out["errores"].append("%s: %s" % (cod, scrub(exc))[:200])
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


def refrescar_estados_rancios(store: Store, con, log=print,
                              tope_hits: int = 800) -> dict:
    """Pregunta el estado final de las licitaciones que cerraron y quedaron rancias.

    [MEDIDO 2026-09-08] 1.524 filas con estado='Publicada' y fecha_cierre YA
    PASADA: 752 cerradas el dia anterior, 46 hace mas de 30 dias, todas de
    `api-live`.

    POR QUE SE ENSUCIAN: `licitaciones_por_estado("activas")` lista SOLO las
    activas. Cuando una licitacion cierra DESAPARECE del listado, asi que ya
    no puede volver a aparecer en `cambiadas` de refrescar_api y su estado
    guardado queda en "Publicada" para siempre. Se corregia sola unicamente
    cuando llegaba el bulk de ese mes -- y el del mes en curso viene vacio.

    Hoy el buscador lo tapa filtrando `fecha_cierre >= as_of`, pero eso es una
    curita sobre un dato incorrecto: cualquier consulta sin ese filtro las
    muestra abiertas, y Demeter lee el mismo almacen.

    NO SE INFIERE EL ESTADO. Escribir "Cerrada" porque la fecha paso seria
    inventar un dato que ChileCompra no nos dijo, y este proyecto ya pago
    caro las salvedades falsas. Se paga el hit y se pregunta. Las que cerraron
    hace mas tiempo primero, acotado por `tope_hits` y por la cuota que quede.
    """
    from .cli import _upsert_licitacion

    cfg = Config.from_env()
    cuota = Cuota(store)
    out = {"errores": [], "candidatas": 0, "pedidas": 0, "filas_escritas": 0}
    try:
        rancias = [r[0] for r in con.execute(
            "SELECT codigo FROM licitacion "
            "WHERE estado = 'Publicada' AND fecha_cierre < current_date "
            "ORDER BY fecha_cierre ASC").fetchall()]
    except Exception as exc:                              # noqa: BLE001
        out["errores"].append("no se pudo listar las rancias: %s" % scrub(exc))
        return out

    out["candidatas"] = len(rancias)
    if not rancias:
        log("estados rancios: ninguno")
        return out

    # Margen de cuota: esto es higiene y NO puede canibalizar la ingesta del
    # dia siguiente. Se reservan 2.000 hits pase lo que pase.
    margen = max(cuota.disponibles("ingesta") - 2000, 0)
    pedir = rancias[:min(tope_hits, margen)]
    out["pedidas"] = len(pedir)
    if len(rancias) > len(pedir):
        out["pendientes"] = len(rancias) - len(pedir)
        log("estados rancios: %d candidatas, se piden %d; el resto entra en "
            "las proximas corridas" % (len(rancias), len(pedir)))
    if not pedir:
        return out

    cli = ClienteAPI(cfg, cuota)
    try:
        for i, cod in enumerate(pedir, 1):
            try:
                det = cli.licitacion(cod, motivo="ingesta")
                for lic in (det.get("Listado") or []):
                    out["filas_escritas"] += _upsert_licitacion(store, lic)
            except Exception as exc:                      # noqa: BLE001
                out["errores"].append("%s: %s" % (cod, scrub(exc))[:200])
            if i % 200 == 0:
                log("  rancias %d/%d cuota_restante=%d"
                    % (i, len(pedir), cuota.disponibles("ingesta")))
    finally:
        cli.close()

    log("estados rancios: %s"
        % {k: v for k, v in out.items() if k != "errores"})
    if out["errores"]:
        log("estados rancios: %d error(es), van a OMISIONES" % len(out["errores"]))
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


def espacio_suficiente(db_path: Path, factor: float = 2.5, log=print) -> dict:
    """Chequea el disco ANTES de bajar nada. Aborta la corrida si no da.

    La corrida necesita a la vez la base servida, el candidato (que es una copia
    de la servida) y, durante la compactacion, una tercera copia. Sin este
    chequeo se baja todo el bulk, se queman hasta 1.500 hits de la cuota de
    ChileCompra, y recien al final se descubre que no hay disco.

    [MEDIDO 2026-09-07] las cuatro corridas fallidas se comieron hasta 6.000
    hits de los 10.000/dia y no publicaron nada.
    """
    st = shutil.disk_usage(str(db_path.parent))
    base = _tam(db_path)
    necesario = int(base * factor)
    out = {"libre": st.free, "base": base, "necesario": necesario,
           "ok": st.free >= necesario}
    log("disco: libre %.1f GB, la corrida necesita ~%.1f GB (%.1fx la base de "
        "%.2f GB) -> %s" % (st.free / 1024 ** 3, necesario / 1024 ** 3, factor,
                            base / 1024 ** 3, "OK" if out["ok"] else "NO ALCANZA"))
    return out


def compactar(db_path: Path, log=print) -> dict:
    """Reescribe la base en un archivo nuevo para que el archivo refleje los datos.

    ESTE ES EL ARREGLO DE FONDO. Un DELETE en DuckDB no devuelve espacio al
    disco: [MEDIDO 2026-09-07] el candidato de la corrida fallida tenia 10,11 GB
    de archivo con 0,41 GB de datos reales, 95,9 % de desperdicio. Sin compactar,
    la guarda mide un numero que no se mueve y borra hasta vaciar el almacen.

    Y no, VACUUM no sirve para esto en DuckDB: no es Postgres, no libera el
    archivo. COPY FROM DATABASE a un archivo nuevo si.

    Se llama con el archivo CERRADO. Un ATTACH sobre una base abierta por el
    Store dejaria el .wal a medias.
    """
    nuevo = Path(str(db_path) + ".compacta")
    for f in (nuevo, Path(str(nuevo) + ".wal")):
        if f.exists():
            f.unlink()
    antes = _tam(db_path)
    con = duckdb.connect()
    try:
        con.execute("ATTACH '%s' AS origen (READ_ONLY)" % db_path)
        con.execute("ATTACH '%s' AS destino" % nuevo)
        con.execute("COPY FROM DATABASE origen TO destino")
        con.execute("CHECKPOINT")
    finally:
        con.close()

    # Un .wal que sobrevive a un cierre limpio significa datos sin consolidar.
    # Borrarlo perderia esas filas y moverlo aparte las reproduciria sobre otra
    # base: las dos opciones son peores que cortar. Ya nos paso una vez que un
    # .wal huerfano rompio la corrida (ver el comentario de sincronizar).
    wal_nuevo = Path(str(nuevo) + ".wal")
    if wal_nuevo.exists():
        raise RuntimeError(
            "compactar: %s quedo con .wal despues de cerrar. No se mueve una base "
            "a medio consolidar." % nuevo)

    os.replace(nuevo, db_path)
    # El .wal viejo pertenecia al archivo que acabamos de reemplazar: ahora es
    # huerfano y DuckDB lo intentaria reproducir sobre la base nueva.
    wal_viejo = Path(str(db_path) + ".wal")
    if wal_viejo.exists():
        wal_viejo.unlink()

    despues = _tam(db_path)
    log("compactada: %.2f GB -> %.2f GB (recupera %.2f GB)"
        % (antes / 1024 ** 3, despues / 1024 ** 3, (antes - despues) / 1024 ** 3))
    return {"antes": antes, "despues": despues, "recuperado": antes - despues}


def _periodos_bulk(con) -> list:
    """Los periodos bulk cargados, del MAS VIEJO al mas nuevo.

    [MEDIDO 2026-09-07] La version anterior hacia `ORDER BY 1` sobre el string
    'bulk-lic:2026-9'. Lexicograficamente '2026-10' < '2026-9', asi que borraba
    octubre antes que septiembre. Y como unia los prefijos bulk-lic: y bulk-oc:
    en un solo UNION, agotaba TODOS los periodos de licitaciones antes de tocar
    uno de ordenes. Se ordena por (anio, mes) numerico y se devuelve el periodo
    desnudo, para borrar el par lic+oc del mismo mes junto.
    """
    filas = con.execute(
        "SELECT DISTINCT _procedencia FROM ("
        "  SELECT _procedencia FROM oferta       WHERE _procedencia LIKE 'bulk-%'"
        "  UNION ALL"
        "  SELECT _procedencia FROM orden_compra WHERE _procedencia LIKE 'bulk-%')"
    ).fetchall()
    periodos = set()
    for fila in filas:
        proc = fila[0]
        if proc and ":" in proc:
            periodos.add(proc.split(":", 1)[1])

    def clave(per):
        try:
            a, m = per.split("-", 1)
            return (int(a), int(m))
        except (ValueError, TypeError):
            # Lo que no se puede interpretar va al final: no se borra primero
            # algo que no entendemos.
            return (9999, 99)

    return sorted(periodos, key=clave)


def guarda_disco(db_path: Path, techo: int = None, log=print) -> dict:
    """Compacta, mide, y si sigue sobre el techo borra el periodo mas viejo DE VERDAD.

    Tres diferencias con la version que fallo cuatro corridas seguidas:

      1. COMPACTA antes de medir. Sin eso el numero no se mueve y el bucle borra
         todo sin bajar del techo. Es el bug de fondo, no el techo chico.
      2. ACOTA a 6 vueltas y exige recuperar >=64 MB por vuelta. Si un borrado no
         encoge el archivo, corta y avisa en vez de seguir vaciando tablas.
      3. Se NIEGA a borrar un periodo si eso dejaria una tabla en 0 filas.

    Recibe la RUTA, no una conexion: compactar necesita el archivo cerrado.
    """
    techo = TECHO_DISCO_BYTES if techo is None else techo
    out = {"techo_bytes": techo, "borrados": [], "disparada": False,
           "compactaciones": [], "abortada": None}

    out["compactaciones"].append(compactar(db_path, log=log))
    tam = _tam(db_path)
    out["tamano_final"] = tam
    if tam <= techo:
        return out

    out["disparada"] = True
    log("GUARDA DE DISCO: %.2f GB sobre el techo de %.2f GB incluso compactada."
        % (tam / 1024 ** 3, techo / 1024 ** 3))

    for _ in range(TOPE_VUELTAS_GUARDA):
        con = duckdb.connect(str(db_path))
        try:
            periodos = _periodos_bulk(con)
            if not periodos:
                out["abortada"] = ("%d bytes sobre el techo y no queda periodo "
                                   "bulk que borrar. Requiere intervencion." % tam)
                break
            per = periodos[0]
            marcas = ["bulk-lic:%s" % per, "bulk-oc:%s" % per]

            # Nunca dejar una tabla en cero. La compuerta de completitud lo
            # atraparia despues, pero para entonces ya tiramos la corrida entera
            # -- que es exactamente lo que paso cuatro veces el 2026-09-07.
            vaciaria = []
            for tabla in TABLAS_BULK:
                resto = con.execute(
                    "SELECT count(*) FROM %s WHERE _procedencia NOT IN (?, ?)"
                    % tabla, marcas).fetchone()[0]
                if resto == 0:
                    vaciaria.append(tabla)
            if vaciaria:
                out["abortada"] = (
                    "borrar el periodo %s dejaria en 0 filas %s. NO se borra: un "
                    "almacen incompleto es peor que uno grande." % (per, vaciaria))
                break

            for tabla in TABLAS_BULK:
                con.execute("DELETE FROM %s WHERE _procedencia IN (?, ?)"
                            % tabla, marcas)
        finally:
            con.close()

        antes = tam
        out["compactaciones"].append(compactar(db_path, log=log))
        tam = _tam(db_path)
        out["tamano_final"] = tam
        out["borrados"].append(per)
        log("GUARDA DE DISCO: borrado el periodo %s. Archivo %.2f -> %.2f GB."
            % (per, antes / 1024 ** 3, tam / 1024 ** 3))

        if tam <= techo:
            break
        if antes - tam < MIN_RECUPERADO_BYTES:
            out["abortada"] = (
                "borrar %s recupero solo %d bytes: seguir borrando no baja el "
                "archivo. Corto para no vaciar el almacen." % (per, antes - tam))
            break
    else:
        out["abortada"] = ("se agotaron las %d vueltas y el archivo sigue sobre "
                           "el techo." % TOPE_VUELTAS_GUARDA)

    if out["abortada"]:
        log("GUARDA DE DISCO: " + out["abortada"])
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
    # Hay que borrar el .wal tambien, no solo el .duckdb. [MEDIDO 2026-08-19] un
    # WAL huerfano de una corrida anterior se intenta reproducir sobre la base
    # recien copiada y DuckDB muere con InternalException: "Could not find node in
    # column segment tree! Attempting to find row number 89250". Esto habria roto
    # el cron en su primera corrida, con el almacen servido intacto pero sin
    # actualizarse nunca mas.
    # Antes de bajar un solo byte y de gastar cuota de API: cabe o no cabe.
    previo = espacio_suficiente(destino, log=log)
    if not previo["ok"]:
        raise RuntimeError(
            "disco insuficiente: %d bytes libres, la corrida necesita ~%d. "
            "Se aborta ANTES de bajar el bulk y de quemar cuota de ChileCompra."
            % (previo["libre"], previo["necesario"]))

    for _f in (siguiente, Path(str(siguiente) + ".wal"),
               Path(str(siguiente) + ".compacta"),
               Path(str(siguiente) + ".compacta.wal")):
        if _f.exists():
            _f.unlink()
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
        informe["excluidas"] = bulk.marcar_excluidas(con)
        log("OC excluidas por la fuente: %s" % informe["excluidas"])
        log("conversion a CLP: %s" % informe["clp"])
        if con_api:
            informe["api"] = refrescar_api(store, con, log=log)
            log("api incremental: %s" % informe["api"])
            # Despues del incremental, y no antes: el incremental ya puede
            # haber corregido varias, asi que la lista de rancias sale mas
            # corta y se gastan menos hits.
            informe["estados_rancios"] = refrescar_estados_rancios(
                store, con, log=log)
        informe["retencion"] = aplicar_retencion(con, meses_retencion, log=log)
        informe["clp_pendientes"] = bulk.convertir_a_clp(con)
    finally:
        store.close()
        os.environ["MP_DB"] = str(destino)

    # La guarda corre con el archivo CERRADO porque compacta: reescribe la base
    # en un archivo nuevo y lo mueve encima, y eso no se puede hacer con el
    # Store vivo.
    #
    # Y por eso el conteo y el log_ingesta se mudaron DESPUES de la guarda: si
    # la guarda borra periodos, los conteos cambian, y la compuerta de
    # completitud tiene que mirar el estado FINAL. Contarlos antes era medir lo
    # que ya no es.
    informe["disco"] = guarda_disco(siguiente, log=log)

    os.environ["MP_DB"] = str(siguiente)
    store = Store(cfg_next)
    try:
        con = store.con
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

    # Una omision de carga NO puede reportarse como corrida OK. El 2026-08-20 dos
    # corridas dijeron OK habiendo perdido 240.619 ordenes de compra, porque el
    # error quedaba en una lista que nadie miraba.
    # Las fallas de la API tambien son omisiones. Antes solo contaban las del
    # bulk: un upsert que reventaba por clave duplicada quedaba en un renglon
    # del log que nadie mira y la corrida reportaba OK igual.
    omitidos = list((informe.get("bulk") or {}).get("errores") or [])
    for _clave in ("api", "estados_rancios"):
        omitidos += ["%s: %s" % (_clave, _e)
                     for _e in ((informe.get(_clave) or {}).get("errores") or [])]
    if omitidos:
        informe["OMISIONES"] = omitidos
        log("ATENCION: %d carga(s) OMITIDA(S). El almacen puede estar incompleto "
            "aunque ninguna tabla este vacia:" % len(omitidos))
        for o in omitidos:
            log("   - %s" % o[:300])

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
