"""Arnes de verificacion.

CAPABILITIES.md se GENERA desde probes/probes.yaml + la tabla probe_result. Una
afirmacion sin corrida queda NO VERIFICADO y el documento la marca como prohibida
en material comercial.

Uso:
    mp-probe run            # corre las sondas cuyos requisitos se cumplen
    mp-probe run --solo P-08,P-11
    mp-probe report         # regenera CAPABILITIES.md desde lo ya medido
    mp-probe status         # que se puede correr hoy y que falta
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .api import ClienteAPI, ErrorAPI
from .config import Config, scrub
from .quota import Cuota
from .store import Store

RAIZ = Path(__file__).resolve().parents[2]
PROBES_YAML = RAIZ / "probes" / "probes.yaml"
CAPABILITIES = RAIZ / "CAPABILITIES.md"

_IMPL: dict[str, callable] = {}


def sonda(pid: str):
    def deco(fn):
        _IMPL[pid] = fn
        return fn
    return deco


class Ctx:
    def __init__(self, cfg: Config, store: Store, cuota: Cuota):
        self.cfg, self.store, self.cuota = cfg, store, cuota
        self._api: ClienteAPI | None = None

    @property
    def api(self) -> ClienteAPI:
        if self._api is None:
            self._api = ClienteAPI(self.cfg, self.cuota)
        return self._api

    def licitacion_muestra(self, estado: str = "activas") -> str | None:
        d = self.api.licitaciones_por_estado(estado, motivo="sonda")
        listado = (d.get("Listado") or d.get("listado") or [])
        return listado[0].get("CodigoExterno") if listado else None


def _r(veredicto: str, medido: dict, evidencia: str = "") -> dict:
    return {"veredicto": veredicto, "medido": medido, "evidencia": scrub(evidencia)}


# --------------------------------------------------------------------------
# Sondas contra la API
# --------------------------------------------------------------------------

@sonda("P-01")
def _p01(ctx: Ctx) -> dict:
    d = ctx.api.licitaciones_por_estado("activas", motivo="sonda")
    listado = d.get("Listado") or []
    if not listado:
        return _r("UNKNOWN", {"cantidad": d.get("Cantidad")},
                  "El listado vino vacio; repetir en dia habil.")
    claves = sorted(listado[0].keys())
    esperadas = {"CodigoExterno", "Nombre", "CodigoEstado", "FechaCierre"}
    return _r("PASS" if set(claves) <= esperadas | {"Estado"} else "FAIL",
              {"claves_reales": claves, "cantidad_listada": d.get("Cantidad"),
               "claves_esperadas": sorted(esperadas)},
              f"Un elemento del Listado trae {len(claves)} claves.")


@sonda("P-02")
def _p02(ctx: Ctx) -> dict:
    cod = ctx.licitacion_muestra()
    if not cod:
        return _r("UNKNOWN", {}, "Sin licitacion de muestra.")
    d = ctx.api.licitacion(cod, motivo="sonda")
    lic = (d.get("Listado") or [{}])[0]
    def aplanar(o, pre=""):
        out = []
        if isinstance(o, dict):
            for k, v in o.items():
                out += aplanar(v, f"{pre}{k}.")
        elif isinstance(o, list) and o:
            out += aplanar(o[0], f"{pre}0.")
        else:
            out.append(pre.rstrip("."))
        return out
    campos = sorted(set(aplanar(lic)))
    return _r("PASS", {"n_campos_reales": len(campos), "campos": campos},
              f"Detalle de {cod}: {len(campos)} rutas de campo.")


@sonda("V-01")
def _v01(ctx: Ctx) -> dict:
    cod = ctx.licitacion_muestra()
    if not cod:
        return _r("UNKNOWN", {}, "Sin licitacion de muestra.")
    lic = (ctx.api.licitacion(cod, motivo="sonda").get("Listado") or [{}])[0]
    items = ((lic.get("Items") or {}).get("Listado") or [])
    con_cod = [i for i in items if i.get("CodigoProducto")]
    return _r("PASS" if con_cod else "FAIL",
              {"codigo": cod, "n_items": len(items), "n_con_unspsc": len(con_cod),
               "muestra": {k: con_cod[0].get(k) for k in
                           ("CodigoProducto", "CodigoCategoria", "Categoria",
                            "NombreProducto", "UnidadMedida")} if con_cod else None},
              f"{len(con_cod)}/{len(items)} items con CodigoProducto.")


@sonda("V-02")
def _v02(ctx: Ctx) -> dict:
    cod = ctx.licitacion_muestra("adjudicada")
    if not cod:
        return _r("UNKNOWN", {}, "Sin licitacion adjudicada de muestra.")
    lic = (ctx.api.licitacion(cod, motivo="sonda").get("Listado") or [{}])[0]
    items = ((lic.get("Items") or {}).get("Listado") or [])
    con_precio = [i for i in items
                  if (i.get("Adjudicacion") or {}).get("MontoUnitario") is not None]
    return _r("PASS" if con_precio else "FAIL",
              {"codigo": cod, "n_items": len(items), "n_con_precio": len(con_precio),
               "muestra": con_precio[0].get("Adjudicacion") if con_precio else None},
              f"{len(con_precio)}/{len(items)} lineas con MontoUnitario adjudicado.")


@sonda("V-07")
def _v07(ctx: Ctx) -> dict:
    out = {}
    try:
        c = ctx.api.compradores(motivo="sonda")
        out["BuscarComprador"] = {"ok": True, "claves": sorted(c.keys())[:20]}
    except ErrorAPI as exc:
        out["BuscarComprador"] = {"ok": False, "error": scrub(str(exc))}
    return _r("PASS" if out["BuscarComprador"]["ok"] else "FAIL", out)


@sonda("P-09")
def _p09(ctx: Ctx) -> dict:
    """Confirma la AUSENCIA de criterios de evaluacion y de ofertas no adjudicadas."""
    r = _p02(ctx)
    if r["veredicto"] == "UNKNOWN":
        return r
    campos = r["medido"]["campos"]
    # Primera version de esta sonda uso el patron laxo
    # "criterio|evaluac|ponderac|puntaje|oferta" y dio FALSO POSITIVO: matcheo
    # EstadoPublicidadOfertas (un booleano de visibilidad), FechaTiempoEvaluacion
    # (una fecha) y UnidadTiempoEvaluacion (una unidad de tiempo). Ninguno es un
    # criterio de evaluacion. Ahora se buscan los conceptos reales y se excluyen
    # fechas, unidades y banderas de visibilidad.
    patron = re.compile(r"criterio|ponderac|puntaje|puntuac|rubrica", re.IGNORECASE)
    ruido = re.compile(r"fecha|unidadtiempo|publicidad|justificacion", re.IGNORECASE)
    hallados = [c for c in campos if patron.search(c) and not ruido.search(c)]
    # Segunda condicion: un listado de ofertas de participantes, no solo la
    # adjudicada. Seria "Oferentes", "Ofertas.Listado" o similar.
    listados_oferta = [c for c in campos
                       if re.search(r"oferta|oferente", c, re.IGNORECASE)
                       and not ruido.search(c)]
    confirmada = not hallados and not [c for c in listados_oferta
                                       if "Adjudicacion" not in c]
    return _r("PASS" if confirmada else "FAIL",
              {"campos_de_criterio_o_ponderacion": hallados,
               "campos_de_oferta_no_adjudicada": [c for c in listados_oferta
                                                  if "Adjudicacion" not in c],
               "descartados_por_ruido": [c for c in campos if ruido.search(c)
                                         and re.search(r"evaluac|oferta", c, re.I)],
               "n_campos_revisados": len(campos)},
              "La limitacion se confirma cuando no hay criterios/ponderaciones NI "
              "listado de ofertas de participantes no adjudicados.")


@sonda("P-18")
def _p18(ctx: Ctx) -> dict:
    """Confirma la ausencia de campos de garantia."""
    r = _p02(ctx)
    if r["veredicto"] == "UNKNOWN":
        return r
    patron = re.compile(r"garant|boleta|cauci", re.IGNORECASE)
    hallados = [c for c in r["medido"]["campos"] if patron.search(c)]
    return _r("PASS" if not hallados else "FAIL",
              {"campos_relacionados_hallados": hallados},
              "La limitacion se confirma cuando no hay campos de garantia.")


@sonda("P-03")
def _p03(ctx: Ctx) -> dict:
    est = ctx.cuota.estado()
    cols, rows = ctx.store.rows(
        "SELECT status, count(*) FROM cuota_hit "
        "WHERE ts >= now() - INTERVAL 24 HOUR GROUP BY 1 ORDER BY 1")
    return _r("UNKNOWN" if not any(r[0] in (429, 403) for r in rows) else "PASS",
              {"cuota": est, "status_observados": {str(r[0]): r[1] for r in rows}},
              "Veredicto UNKNOWN mientras no se observe un rechazo real. "
              "No se agota la cuota a proposito.")


@sonda("P-21")
def _p21(ctx: Ctx) -> dict:
    """Mide el limite de tasa de corto plazo, no documentado.

    Metodo: mismo request repetido a intervalos crecientes; se busca el intervalo
    mas chico con cero rechazos. Se usa el listado por fecha antigua, que es
    barato y estable. Cuesta ~15 hits.
    """
    import time
    from datetime import timedelta
    from .api import ClienteAPI
    from .config import Config

    fecha = (datetime.now(timezone.utc).date() - timedelta(days=1830)).strftime("%d%m%Y")
    resultados = {}
    N = 4
    for intervalo in (0.1, 0.3, 0.5, 0.75, 1.0, 1.5):
        # CRUDO: sin reintento y sin adaptacion. Con backoff activo el cliente
        # siempre termina teniendo exito y la medicion da un falso negativo:
        # eso fue justo lo que arruino la primera corrida de esta sonda.
        crudo = ClienteAPI(Config.from_env(), ctx.cuota)
        crudo.max_reintentos = 0
        crudo.adaptar_intervalo = False
        crudo._intervalo = intervalo
        marca = datetime.now(timezone.utc)
        for _ in range(N):
            try:
                crudo.licitaciones_por_fecha(fecha, motivo="sonda-P21")
            except Exception:                                 # noqa: BLE001
                pass
        crudo.close()
        # Fuente de verdad: el ledger, no lo que devolvio el cliente.
        _, cnt = ctx.store.rows(
            "SELECT status, count(*) FROM cuota_hit WHERE ts >= ? AND motivo = ? "
            "GROUP BY 1", [marca, "sonda-P21"])
        d = {str(s): n for s, n in cnt}
        r429 = d.get("429", 0)
        resultados[f"{intervalo}s"] = {"requests": N, "por_status": d,
                                       "rechazos_429": r429}
        if r429 == 0:
            break
        time.sleep(3)                                          # enfriar entre tramos

    limpio = next((k for k, v in resultados.items() if v["rechazos_429"] == 0), None)
    seg = float(limpio[:-1]) if limpio else None
    from . import api as _api
    return _r("PASS" if limpio else "UNKNOWN",
              {"metodo": f"{N} requests seguidos por tramo, cliente sin reintento; "
                         f"conteo tomado del ledger cuota_hit",
               "por_intervalo": resultados,
               "intervalo_minimo_sin_rechazo_s": seg,
               "valor_configurado_en_cliente_s": _api.MIN_INTERVALO_S,
               "requests_por_minuto_sostenibles": round(60 / seg, 1) if seg else None,
               "carga_inicial_4348_licitaciones_minutos":
                   round(4348 * seg / 60) if seg else None},
              "El limite de tasa NO esta documentado por ChileCompra. Es un segundo "
              "techo, independiente de la cuota diaria de 10.000. Si ningun tramo da "
              "cero rechazos, el limite es mas lento que 1,5 s por request.")


@sonda("P-15")
def _p15(ctx: Ctx) -> dict:
    from datetime import timedelta
    hoy = datetime.now(timezone.utc).date()
    medido = {}
    for etiqueta, dias in (("hace_1_mes", 30), ("hace_1_ano", 365), ("hace_5_anos", 1825)):
        f = hoy - timedelta(days=dias)
        try:
            d = ctx.api.licitaciones_por_fecha(f.strftime("%d%m%Y"), motivo="sonda")
            medido[etiqueta] = {"fecha": f.isoformat(), "cantidad": d.get("Cantidad")}
        except ErrorAPI as exc:
            medido[etiqueta] = {"fecha": f.isoformat(), "error": scrub(str(exc))}
    return _r("PASS", medido)


@sonda("P-16")
def _p16(ctx: Ctx) -> dict:
    """Distingue STOCK de FLUJO, que es donde se equivoco el presupuesto de cuota.

    `estado=activas` devuelve el conjunto de licitaciones abiertas en este momento
    (stock). El presupuesto diario depende de las licitaciones NUEVAS (flujo), que
    se miden con `fecha=ddmmaaaa`. Confundirlos sobreestima el costo de la ingesta
    en estado estacionario y subestima el costo de la carga inicial.
    """
    from datetime import timedelta
    stock = ctx.api.licitaciones_por_estado("activas", motivo="sonda").get("Cantidad")

    flujo = {}
    hoy = datetime.now(timezone.utc).date()
    d = hoy
    while len(flujo) < 3:
        if d.weekday() < 5:                       # solo dias habiles
            try:
                r = ctx.api.licitaciones_por_fecha(d.strftime("%d%m%Y"), motivo="sonda")
                flujo[d.isoformat()] = r.get("Cantidad")
            except ErrorAPI as exc:
                flujo[d.isoformat()] = f"error: {scrub(exc)}"
        d -= timedelta(days=1)

    numeros = [v for v in flujo.values() if isinstance(v, int)]
    prom = round(sum(numeros) / len(numeros), 1) if numeros else None
    ctx.store.log_ingesta("sonda-P-16-stock", "estado=activas", stock or 0, True)
    if numeros:
        ctx.store.log_ingesta("sonda-P-16-flujo", "fecha=ddmmaaaa", numeros[0], True)

    interpretacion = None
    if isinstance(stock, int) and prom:
        ratio = round(stock / prom, 1)
        interpretacion = (
            f"stock/flujo = {ratio}x. "
            + ("Consistente con que 'activas' sea un STOCK de licitaciones abiertas."
               if ratio > 3 else
               "Ratio bajo: 'activas' podria ser flujo diario. Revisar semantica.")
        )
    return _r("PASS" if numeros else "UNKNOWN",
              {"stock_activas": stock,
               "flujo_por_dia_habil": flujo,
               "flujo_promedio": prom,
               "estimacion_previa_inferida_flujo": 1150,
               "interpretacion": interpretacion,
               "costo_carga_inicial_hits": stock,
               "costo_ingesta_diaria_hits": prom,
               "pct_cuota_carga_inicial":
                   round(100 * stock / 10_000, 1) if isinstance(stock, int) else None,
               "pct_cuota_diaria":
                   round(100 * prom / 10_000, 1) if prom else None},
              "El presupuesto de cuota se dimensiona con el FLUJO, no con el stock. "
              "El stock es un costo unico de carga inicial.")


# --------------------------------------------------------------------------
# Sondas de red (bulk)
# --------------------------------------------------------------------------

_URLS = {
    "V-03": "https://datos-abiertos.chilecompra.cl/descargas/procesos-ocds",
    "V-04": "https://datos-abiertos.chilecompra.cl/descargas",
    "V-05": "https://datos-abiertos.chilecompra.cl/descargas/compra-agil",
}


_CONTROL_NEGATIVO = ("https://datos-abiertos.chilecompra.cl/"
                     "ruta-que-no-existe-control-negativo-mp")


def _sonda_red(pid: str):
    @sonda(pid)
    def _f(ctx: Ctx) -> dict:
        """HTTP 200 NO prueba que el dataset exista.

        Primera corrida (2026-07-31): las tres URLs devolvieron 200 con el MISMO
        cuerpo de 1051 bytes y el mismo Last-Modified. Es el shell de una SPA, que
        responde 200 a cualquier ruta. Mis tres PASS eran falsos positivos.

        Por eso ahora hay control negativo: se pide tambien una ruta inventada. Si
        devuelve lo mismo, la sonda no puede concluir nada.
        """
        import hashlib
        import httpx
        import re as _re
        url = _URLS[pid]
        try:
            r = httpx.get(url, timeout=30, follow_redirects=True)
            ctrl = httpx.get(_CONTROL_NEGATIVO, timeout=30, follow_redirects=True)
        except Exception as exc:                              # noqa: BLE001
            return _r("FAIL", {"url": url}, scrub(str(exc)))

        h = hashlib.sha256(r.content).hexdigest()[:16]
        h_ctrl = hashlib.sha256(ctrl.content).hexdigest()[:16]
        cuerpo = r.text if "text" in (r.headers.get("content-type") or "") else ""
        enlaces = _re.findall(r'[^"\'\s>]+\.(?:csv|zip|gz|jsonl|xlsx)', cuerpo)[:15]

        medido = {"url": url, "status": r.status_code,
                  "content_type": r.headers.get("content-type"),
                  "bytes": len(r.content), "sha256_16": h,
                  "last_modified": r.headers.get("last-modified"),
                  "control_negativo": {"url": _CONTROL_NEGATIVO,
                                       "status": ctrl.status_code,
                                       "bytes": len(ctrl.content),
                                       "sha256_16": h_ctrl},
                  "enlaces_a_datos_encontrados": enlaces}

        if r.status_code != 200:
            return _r("FAIL", medido, f"HTTP {r.status_code}.")
        if h == h_ctrl:
            return _r("UNKNOWN", medido,
                      "El control negativo devuelve EXACTAMENTE el mismo cuerpo: el "
                      "portal es una SPA que responde 200 a cualquier ruta. El 200 no "
                      "prueba que el dataset exista. Hay que renderizar el portal o "
                      "encontrar el endpoint real de descarga.")
        if not enlaces:
            return _r("UNKNOWN", medido,
                      "Ruta distinta del control negativo, pero el HTML no lista "
                      "ningun archivo de datos. Probablemente el listado se carga por "
                      "JavaScript.")
        return _r("PASS", medido,
                  f"Ruta distinta del control negativo y {len(enlaces)} enlaces a "
                  f"archivos de datos en el HTML.")
    return _f


for _pid in _URLS:
    _sonda_red(_pid)


# --------------------------------------------------------------------------
# Sondas sobre el almacen
# --------------------------------------------------------------------------

def _vacio(ctx: Ctx, tabla: str) -> dict | None:
    if ctx.store.tabla_vacia(tabla):
        return _r("UNKNOWN", {"tabla": tabla, "filas": 0},
                  f"'{tabla}' esta vacia: corre la ingesta antes de esta sonda.")
    return None


@sonda("P-07")
def _p07(ctx: Ctx) -> dict:
    """Resuelve la contradiccion 74.931 contratos vs 4,9 M adjudicaciones.

    Requiere un JSONL de OCDS local. Ruta en MP_OCDS_FILE.
    """
    import gzip
    import os
    ruta = os.environ.get("MP_OCDS_FILE")
    if not ruta or not Path(ruta).exists():
        return _r("UNKNOWN", {"MP_OCDS_FILE": ruta},
                  "Define MP_OCDS_FILE apuntando a un procesos-ocds .jsonl(.gz) "
                  "descargado. Sin el archivo la contradiccion no se puede resolver.")
    p = Path(ruta)
    abrir = gzip.open if p.suffix == ".gz" else open
    n = con_awards = con_contracts = con_impl = con_tx = 0
    bloques: dict[str, int] = {}
    with abrir(p, "rt", encoding="utf-8") as fh:
        for linea in fh:
            if n >= 5000:
                break
            try:
                o = json.loads(linea)
            except ValueError:
                continue
            n += 1
            for k in o:
                bloques[k] = bloques.get(k, 0) + 1
            if o.get("awards"):
                con_awards += 1
            contratos = o.get("contracts") or []
            if contratos:
                con_contracts += 1
                impl = [c for c in contratos if c.get("implementation")]
                if impl:
                    con_impl += 1
                    if any((c["implementation"].get("transactions") or [])
                           for c in impl):
                        con_tx += 1
    pct = lambda x: round(100 * x / n, 2) if n else None       # noqa: E731
    return _r("PASS" if n else "UNKNOWN",
              {"archivo": p.name, "lineas_leidas": n,
               "bloques_presentes": bloques,
               "pct_con_awards": pct(con_awards),
               "pct_con_contracts": pct(con_contracts),
               "pct_con_implementation": pct(con_impl),
               "pct_con_transactions": pct(con_tx)},
              "Si pct_con_implementation es bajo, las OC historicas solo existen en "
              "el CSV procesado (P-10) y el analisis de ejecucion contractual no se "
              "puede prometer.")


@sonda("P-08")
def _p08(ctx: Ctx) -> dict:
    if (v := _vacio(ctx, "licitacion_item")):
        return v
    total = ctx.store.one("SELECT count(*) FROM licitacion_item")[0]
    sin = ctx.store.one(
        "SELECT count(*) FROM licitacion_item WHERE unspsc_commodity IS NULL "
        "OR unspsc_commodity = ''")[0]
    _, largos = ctx.store.rows(
        "SELECT length(unspsc_commodity) AS largo, count(*) FROM licitacion_item "
        "WHERE unspsc_commodity IS NOT NULL GROUP BY 1 ORDER BY 1")
    _, top = ctx.store.rows(
        "SELECT unspsc_commodity, any_value(nombre_producto), count(*) AS n "
        "FROM licitacion_item WHERE unspsc_commodity IS NOT NULL "
        "GROUP BY 1 ORDER BY n DESC LIMIT 20")
    _, genericos = ctx.store.rows(
        "SELECT count(*) FROM licitacion_item "
        "WHERE unspsc_commodity LIKE '%00' AND unspsc_commodity IS NOT NULL")
    pct_sin = round(100 * sin / total, 2) if total else None
    pct_gen = round(100 * genericos[0][0] / total, 2) if total else None
    # Veredicto contra umbral. Antes devolvia PASS por el solo hecho de haber
    # podido medir, que es como una sonda confirma cualquier cosa.
    confirmada = (pct_sin or 0) > 5 or (pct_gen or 0) > 20
    return _r("PASS" if confirmada else "FAIL",
              {"umbral_para_confirmar": "sin_codigo > 5% o generico_00 > 20%",
               "n_items": total,
               "pct_sin_codigo": pct_sin,
               "distribucion_largo": {str(l): c for l, c in largos},
               "pct_commodity_generico_termina_en_00": pct_gen,
               "top20_commodities": [
                   {"codigo": c, "ejemplo": n, "n": k,
                    "pct": round(100 * k / total, 2)} for c, n, k in top]},
              "Concentracion alta en pocos commodities o mucho '...00' indica "
              "clasificacion perezosa del comprador.")


@sonda("P-11")
def _p11(ctx: Ctx) -> dict:
    if (v := _vacio(ctx, "licitacion")):
        return v
    _, por_moneda = ctx.store.rows(
        "SELECT COALESCE(moneda,'(nulo)'), count(*), "
        "sum(CASE WHEN monto_estimado_clp IS NULL THEN 1 ELSE 0 END) "
        "FROM licitacion GROUP BY 1 ORDER BY 2 DESC")
    total = sum(r[1] for r in por_moneda)
    return _r("PASS",
              {"total_licitaciones": total,
               "por_moneda": [
                   {"moneda": m, "n": n, "pct": round(100 * n / total, 2),
                    "sin_conversion_clp": s} for m, n, s in por_moneda]},
              "Cuanto mas peso tengan CLF/UTM/USD/EUR, mas critica es la tabla "
              "tipo_cambio.")


@sonda("P-12")
def _p12(ctx: Ctx) -> dict:
    if (v := _vacio(ctx, "licitacion")):
        return v
    _, filas = ctx.store.rows(
        "SELECT COALESCE(tipo,'(nulo)'), count(*), "
        "sum(CASE WHEN visibilidad_monto THEN 1 ELSE 0 END), "
        "sum(CASE WHEN monto_estimado IS NOT NULL THEN 1 ELSE 0 END), "
        "sum(CASE WHEN estimacion = 3 THEN 1 ELSE 0 END) "
        "FROM licitacion GROUP BY 1 ORDER BY 2 DESC")
    return _r("PASS",
              {"por_tipo": [
                  {"tipo": t, "n": n, "con_monto_visible": v_, "con_monto": m,
                   "no_estimable": ne,
                   "pct_con_monto": round(100 * m / n, 2) if n else None}
                  for t, n, v_, m, ne in filas]},
              "El pct_con_monto es la cobertura real de cualquier cifra de "
              "'tamano de mercado'.")


@sonda("P-13")
def _p13(ctx: Ctx) -> dict:
    """OJO con la convencion: para una LIMITACION, PASS = la limitacion existe.

    La afirmacion es "ninguna fuente de ChileCompra entrega la serie de UF/UTM/
    tipo de cambio; hay que integrar una externa". Eso es cierto siempre. Lo que
    varia es si ya la integramos o no, y eso va en `mitigado`.
    """
    _, filas = ctx.store.rows(
        "SELECT moneda, count(*), min(fecha), max(fecha) FROM tipo_cambio GROUP BY 1")
    _, afectadas = ctx.store.rows(
        "SELECT count(*) FROM licitacion WHERE moneda IS NOT NULL AND moneda <> 'CLP'")
    n_afectadas = afectadas[0][0] if afectadas else 0
    return _r("PASS",
              {"limitacion_confirmada": True,
               "mitigado": bool(filas),
               "series_cargadas": [{"moneda": m, "n": n, "desde": str(a), "hasta": str(b)}
                                   for m, n, a, b in filas],
               "filas_en_moneda_no_CLP_afectadas": n_afectadas},
              ("Serie externa cargada: la conversion es posible donde haya fecha."
               if filas else
               "SIN MITIGAR: no hay serie cargada, toda medida CLP sobre documentos "
               "en otra moneda queda NULL. Integrar una fuente externa (SII/BCCh)."))


@sonda("V-06")
def _v06(ctx: Ctx) -> dict:
    if (v := _vacio(ctx, "orden_compra")):
        return v
    total = ctx.store.one("SELECT count(*) FROM orden_compra")[0]
    con_lic = ctx.store.one(
        "SELECT count(*) FROM orden_compra WHERE codigo_licitacion IS NOT NULL "
        "AND codigo_licitacion <> ''")[0]
    match = ctx.store.one(
        "SELECT count(*) FROM orden_compra oc JOIN licitacion l "
        "ON l.codigo = oc.codigo_licitacion")[0]
    return _r("PASS" if match else "FAIL",
              {"n_oc": total, "con_codigo_licitacion": con_lic,
               "con_match_en_licitacion": match,
               "pct_match": round(100 * match / total, 2) if total else None},
              "El resto son compras sin licitacion: convenio marco, compra agil, "
              "trato directo.")


@sonda("P-17")
def _p17(ctx: Ctx) -> dict:
    if (v := _vacio(ctx, "orden_compra")):
        return v
    _, filas = ctx.store.rows(
        "SELECT date_trunc('month', fecha_envio) AS mes, count(*) "
        "FROM orden_compra WHERE fecha_envio IS NOT NULL GROUP BY 1 ORDER BY 1")
    return _r("PASS",
              {"por_mes": [{"mes": str(m), "n": n} for m, n in filas],
               "promedio_dia_habil_estimado":
                   round(sum(n for _, n in filas) / max(1, len(filas)) / 21, 1)},
              "Compara este numero contra la cuota diaria para decidir si las OC "
              "pueden venir por API.")


# --------------------------------------------------------------------------
# Corrida y reporte
# --------------------------------------------------------------------------

def cargar_declaraciones() -> list[dict]:
    return yaml.safe_load(PROBES_YAML.read_text(encoding="utf-8"))


def requisitos_ok(decl: dict, cfg: Config) -> tuple[bool, str]:
    req = decl.get("requiere") or []
    if "ticket" in req and not cfg.tiene_ticket():
        return False, "falta MP_TICKET o MP_USAR_AGENT_VAULT"
    if "agente" in req:
        return False, "requiere una corrida de agente (set de evaluacion)"
    if "agent_vault" in req:
        return False, "requiere Agent Vault configurado"
    if decl["id"] not in _IMPL:
        return False, "sin implementacion automatica: verificacion manual"
    return True, ""


def correr(solo: list[str] | None = None) -> list[dict]:
    cfg = Config.from_env()
    store = Store(cfg)
    ctx = Ctx(cfg, store, Cuota(store))
    resultados = []
    for decl in cargar_declaraciones():
        pid = decl["id"]
        if solo and pid not in solo:
            continue
        ok, razon = requisitos_ok(decl, cfg)
        if not ok:
            print(f"  {pid:6} SKIP     {razon}")
            continue
        try:
            r = _IMPL[pid](ctx)
        except Exception as exc:                              # noqa: BLE001
            r = _r("UNKNOWN", {}, f"la sonda fallo: {type(exc).__name__}: {exc}")
        store.con.execute(
            "INSERT OR REPLACE INTO probe_result VALUES (?, ?, ?, ?, ?, ?, ?)",
            [pid, datetime.now(timezone.utc), decl["afirmacion"].strip(),
             decl["metodo"].strip(), r["veredicto"],
             json.dumps(r["medido"], ensure_ascii=False, default=str),
             r["evidencia"]])
        print(f"  {pid:6} {r['veredicto']:8} {r['evidencia'][:90]}")
        resultados.append({**decl, **r})
    store.close()
    return resultados


def ultimos_resultados(store: Store) -> dict[str, dict]:
    _, rows = store.rows(
        "SELECT probe_id, ts, veredicto, medido, evidencia FROM probe_result "
        "QUALIFY row_number() OVER (PARTITION BY probe_id ORDER BY ts DESC) = 1")
    return {r[0]: {"ts": str(r[1]), "veredicto": r[2],
                   "medido": json.loads(r[3]) if r[3] else {}, "evidencia": r[4]}
            for r in rows}


def _bloque(decl: dict, res: dict | None) -> str:
    v = (res or {}).get("veredicto", "NO VERIFICADO")
    sello = {"PASS": "VERIFICADO", "FAIL": "REFUTADO",
             "UNKNOWN": "NO CONCLUYENTE"}.get(v, "NO VERIFICADO")
    sev = decl.get("severidad")
    out = [f"### {decl['id']} — {sello}" + (f" · severidad {sev}" if sev else "")]
    out.append("")
    out.append(decl["afirmacion"].strip())
    out.append("")
    out.append(f"- **Fuente declarada:** {decl.get('fuente_declarada', '—')}")
    out.append(f"- **Metodo:** {decl['metodo'].strip()}")
    if decl.get("habilita"):
        out.append(f"- **Habilita:** {decl['habilita'].strip()}")
    if decl.get("impacto"):
        out.append(f"- **Impacto:** {decl['impacto'].strip()}")
    if decl.get("nota"):
        out.append(f"- **Nota:** {decl['nota'].strip()}")
    if decl.get("bloquea_venta_de"):
        marca = "PROHIBIDO PROMETER" if v == "PASS" else "NO AFIRMAR HASTA MEDIR"
        out.append(f"- **{marca}:** {decl['bloquea_venta_de'].strip()}")
    if res:
        out.append(f"- **Medido {res['ts']}:**")
        out.append("")
        out.append("```json")
        out.append(json.dumps(res["medido"], ensure_ascii=False, indent=2)[:2500])
        out.append("```")
        if res.get("evidencia"):
            out.append(f"- **Evidencia:** {res['evidencia']}")
    else:
        out.append("- **Medido:** nunca. Corre `mp-probe run`.")
    out.append("")
    return "\n".join(out)


def reporte() -> str:
    cfg = Config.from_env()
    store = Store(cfg)
    decls = cargar_declaraciones()
    res = ultimos_resultados(store)

    n_ver = sum(1 for d in decls if res.get(d["id"], {}).get("veredicto") == "PASS")
    pot = [d for d in decls if d["tipo"] == "potencialidad"]
    lim = [d for d in decls if d["tipo"] == "limitacion"]

    L = []
    L.append("# CAPABILITIES.md — potencialidades y limitaciones del MCP Mercado Publico")
    L.append("")
    L.append("> **ARCHIVO GENERADO.** No editar a mano: se sobrescribe con "
             "`mp-probe report`. La fuente de las afirmaciones es "
             "`probes/probes.yaml`; la de las mediciones, la tabla `probe_result`.")
    L.append("")
    L.append(f"Generado: {datetime.now(timezone.utc).isoformat(timespec='seconds')}  ")
    L.append(f"Afirmaciones declaradas: **{len(decls)}** "
             f"({len(pot)} potencialidades, {len(lim)} limitaciones)  ")
    L.append(f"Verificadas con medicion: **{n_ver}/{len(decls)}**")
    L.append("")
    L.append("## Como leer esto")
    L.append("")
    L.append("**La convencion es contraintuitiva y hay que leerla dos veces:** para una "
             "LIMITACION, `VERIFICADO` significa que **la limitacion existe y esta "
             "confirmada** — es mala noticia comprobada, no buena. Para una "
             "POTENCIALIDAD, `VERIFICADO` es buena noticia comprobada.")
    L.append("")
    L.append("| Sello | Potencialidad (`V-*`) | Limitacion (`P-*`) | Uso comercial |")
    L.append("|---|---|---|---|")
    L.append("| `VERIFICADO` | la capacidad existe | **la limitacion existe** | "
             "Se puede afirmar, citando la medicion |")
    L.append("| `REFUTADO` | la capacidad NO existe | la limitacion no aplica | "
             "Revisar el diseno: una de las dos suposiciones estaba mal |")
    L.append("| `NO CONCLUYENTE` | sonda corrida sin datos suficientes | idem | "
             "No afirmar |")
    L.append("| `NO VERIFICADO` | nunca se corrio | idem | "
             "**Prohibido en material comercial** |")
    L.append("")

    L.append("## Tabla resumen")
    L.append("")
    L.append("| ID | Tipo | Sello | Afirmacion (resumen) |")
    L.append("|---|---|---|---|")
    for d in decls:
        v = res.get(d["id"], {}).get("veredicto", "NO VERIFICADO")
        sello = {"PASS": "VERIFICADO", "FAIL": "REFUTADO",
                 "UNKNOWN": "NO CONCLUYENTE"}.get(v, "NO VERIFICADO")
        resumen = " ".join(d["afirmacion"].split())[:110]
        L.append(f"| {d['id']} | {d['tipo'][:3]} | {sello} | {resumen}... |")
    L.append("")

    L.append("## Que NO se puede prometer")
    L.append("")
    L.append("Lista derivada automaticamente de las limitaciones con "
             "`bloquea_venta_de`. Es la lista de humo prohibido.")
    L.append("")
    hay = False
    for d in lim:
        if not d.get("bloquea_venta_de"):
            continue
        v = res.get(d["id"], {}).get("veredicto", "NO VERIFICADO")
        estado = {"PASS": "**limitacion confirmada por medicion**",
                  "FAIL": "medida y NO confirmada: revisar",
                  "UNKNOWN": "sonda corrida, sin datos suficientes",
                  "NO VERIFICADO": "sin medir"}[v]
        L.append(f"- **{d['id']}** ({estado}): "
                 f"{' '.join(d['bloquea_venta_de'].split())}")
        hay = True
    if not hay:
        L.append("- (ninguna declarada)")
    L.append("")

    L.append("## Potencialidades")
    L.append("")
    for d in pot:
        L.append(_bloque(d, res.get(d["id"])))
    L.append("## Limitaciones")
    L.append("")
    for d in sorted(lim, key=lambda x: {"alta": 0, "media": 1, "baja": 2}
                    .get(x.get("severidad", "media"), 1)):
        L.append(_bloque(d, res.get(d["id"])))

    store.close()
    return "\n".join(L)


def main() -> None:
    ap = argparse.ArgumentParser(prog="mp-probe")
    ap.add_argument("accion", choices=["run", "report", "status"])
    ap.add_argument("--solo", help="lista de ids separada por coma")
    a = ap.parse_args()

    if a.accion == "status":
        cfg = Config.from_env()
        for d in cargar_declaraciones():
            ok, razon = requisitos_ok(d, cfg)
            print(f"  {d['id']:6} {'LISTA' if ok else 'BLOQUEADA':10} {razon}")
        return

    if a.accion == "run":
        solo = [s.strip() for s in a.solo.split(",")] if a.solo else None
        correr(solo)

    CAPABILITIES.write_text(reporte(), encoding="utf-8")
    print(f"\nEscrito {CAPABILITIES}")


if __name__ == "__main__":
    sys.exit(main())
