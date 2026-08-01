"""Las 7 herramientas. Agnosticas de rubro y de producto.

Nada de perfiles de mercado, dossiers ni watchlists: eso es capa de producto
(SPEC-001 §6). Aca solo hay acceso a datos.

Toda respuesta lleva la misma envoltura, y la envoltura es el mecanismo
anti-humo: procedencia, frescura, cobertura y limitaciones viajan con el dato.
Un agente no puede afirmar mas de lo que el payload sostiene.
"""

from __future__ import annotations

from typing import Any

from .config import scrub
from .query import (
    Consulta, ConsultaInvalida, chart_hint, limitaciones_aplicables,
    sql_cobertura, to_sql, validar, MAX_LIMIT,
)
from .quota import Cuota
from .semantic import ENTIDADES, VOCABULARIOS, describe
from .store import Store

ATRIBUCION = "Fuente: ChileCompra - Mercado Publico (https://www.mercadopublico.cl)"


def _envoltura(store: Store, cuota: Cuota, c: Consulta, e, cols, rows,
               modo: str, cobertura: dict | None) -> dict:
    truncado = len(rows) >= c.limit
    return {
        "data": [dict(zip(cols, r)) for r in rows],
        "columnas": cols,
        "chart_hint": chart_hint(c, e, cols, modo),
        "consulta": c.as_dict(),
        "query_id": c.query_id(),
        "meta": {
            "procedencia": e.procedencia,
            "as_of": store.as_of(),
            "filas": len(rows),
            "truncado": truncado,
            "siguiente_offset": (c.offset + c.limit) if truncado else None,
            "cobertura": cobertura,
            "limitaciones": limitaciones_aplicables(c, e),
            "cuota": cuota.estado(),
            "atribucion": ATRIBUCION,
        },
    }


def _error(msg: str, extra: dict | None = None) -> dict:
    return {"error": scrub(msg), **(extra or {})}


# --------------------------------------------------------------------------
# 1. mp_schema_describe
# --------------------------------------------------------------------------

def mp_schema_describe(store: Store, entity: str | None = None,
                       incluir_limitaciones: bool = True) -> dict:
    if entity and entity not in ENTIDADES:
        return _error(f"'{entity}' no es una entidad. Validas: {', '.join(ENTIDADES)}.")
    d = describe(entity, incluir_limitaciones)
    d["estado_almacen"] = store.as_of()
    d["nota_de_uso"] = (
        "Llama esta herramienta antes de construir una consulta. Los nombres de "
        "campo no se adivinan: si un campo no aparece aca, no existe. Las "
        "limitaciones marcadas 'NO VERIFICADO' no deben usarse como afirmacion "
        "en un entregable a cliente."
    )
    return d


# --------------------------------------------------------------------------
# 2. mp_codes_search
# --------------------------------------------------------------------------

_VOCAB_SQL = {
    "unspsc_commodity": ("unspsc", "codigo", "nombre", "nivel = 'commodity'"),
    "unspsc_clase": ("unspsc", "codigo", "nombre", "nivel = 'clase'"),
    "unspsc_familia": ("unspsc", "codigo", "nombre", "nivel = 'familia'"),
    "unspsc_segmento": ("unspsc", "codigo", "nombre", "nivel = 'segmento'"),
    "organismo": ("organismo", "codigo", "nombre", "TRUE"),
    "proveedor": ("proveedor", "rut", "nombre", "TRUE"),
    "region": ("organismo", "region", "region", "region IS NOT NULL"),
    "comuna": ("organismo", "comuna", "comuna", "comuna IS NOT NULL"),
}

# Como se mide el volumen de cada vocabulario. Es lo que permite al agente
# juzgar si el corte sirve antes de gastar una consulta grande.
_VOLUMEN_SQL = {
    "unspsc_commodity": ("licitacion_item", "unspsc_commodity"),
    "unspsc_clase": ("licitacion_item", "unspsc_clase"),
    "unspsc_familia": ("licitacion_item", "unspsc_familia"),
    "unspsc_segmento": ("licitacion_item", "unspsc_segmento"),
    "organismo": ("licitacion", "organismo_codigo"),
    "proveedor": ("adjudicacion_item", "rut_proveedor"),
    "region": ("licitacion", "region_comprador"),
    "comuna": ("licitacion", "comuna_comprador"),
}


def mp_codes_search(store: Store, vocabulary: str, texto: str = "",
                    limit: int = 25) -> dict:
    if vocabulary not in _VOCAB_SQL:
        return _error(f"Vocabulario '{vocabulary}' no existe. "
                      f"Validos: {', '.join(VOCABULARIOS)}.")
    tabla, col_cod, col_nom, filtro = _VOCAB_SQL[vocabulary]
    vol_tabla, vol_col = _VOLUMEN_SQL[vocabulary]
    limit = max(1, min(int(limit), 200))

    sql = f"""
        WITH vocab AS (
            SELECT DISTINCT {col_cod} AS codigo, {col_nom} AS nombre
            FROM {tabla} WHERE {filtro}
              -- Insensible a acentos: la familia se llama "Productos
              -- quirurgicos" con tilde y nadie la escribe asi en un buscador.
              AND (strip_accents(lower({col_cod})) LIKE strip_accents(lower(?))
                OR strip_accents(lower({col_nom})) LIKE strip_accents(lower(?)))
        ),
        vol AS (
            SELECT {vol_col} AS codigo, count(*) AS n
            FROM {vol_tabla} WHERE {vol_col} IS NOT NULL GROUP BY 1
        )
        SELECT v.codigo, v.nombre, COALESCE(vol.n, 0) AS registros
        FROM vocab v LEFT JOIN vol ON vol.codigo = v.codigo
        ORDER BY registros DESC, v.nombre
        LIMIT {limit}
    """
    patron = f"%{texto}%"
    try:
        cols, rows = store.rows(sql, [patron, patron])
    except Exception as exc:                                  # noqa: BLE001
        return _error(
            f"No se pudo consultar el vocabulario '{vocabulary}': {exc}. "
            f"Probable causa: la tabla '{tabla}' esta vacia. Corre la ingesta "
            f"(mp-ingest) antes de resolver codigos."
        )
    return {
        "data": [dict(zip(cols, r)) for r in rows],
        "meta": {
            "vocabulario": vocabulary,
            "filas": len(rows),
            "nota": ("'registros' es el volumen observado en el almacen, no el "
                     "universo real: depende de que periodos se hayan ingerido. "
                     "Sirve para comparar cortes entre si, no como cifra absoluta."),
            "as_of": store.as_of(),
            "atribucion": ATRIBUCION,
        },
    }


# --------------------------------------------------------------------------
# 3 y 4. mp_search / mp_aggregate
# --------------------------------------------------------------------------

def _ejecutar(store: Store, cuota: Cuota, consulta: dict, modo: str) -> dict:
    try:
        c = Consulta.parse(consulta)
        e = validar(c, modo)
    except ConsultaInvalida as exc:
        return _error(str(exc), {"ayuda": "Llama mp_schema_describe para ver campos validos."})

    sql, params, cols = to_sql(c, e, modo)
    try:
        _, rows = store.rows(sql, params)
    except Exception as exc:                                  # noqa: BLE001
        return _error(
            f"La consulta es valida contra el esquema pero fallo al ejecutarse: {exc}. "
            f"Si el mensaje menciona una tabla o columna inexistente, el almacen no "
            f"tiene esa parte ingerida todavia."
        )

    cobertura = None
    cob = sql_cobertura(c, e) if modo == "aggregate" else None
    if cob:
        try:
            r = store.one(cob[0], cob[1])
            total, sin_conv = int(r[0] or 0), int(r[1] or 0)
            cobertura = {
                "filas_consideradas": total,
                "excluidas_sin_conversion_moneda": sin_conv,
                "pct_con_valor_clp": round(100 * (total - sin_conv) / total, 2) if total else None,
                "nota": ("Las filas sin tipo de cambio quedan NULL y no entran en las "
                         "agregaciones monetarias. No se imputa ningun valor."),
            }
        except Exception:                                     # noqa: BLE001
            cobertura = {"error": "no se pudo calcular la cobertura"}

    return _envoltura(store, cuota, c, e, cols, rows, modo, cobertura)


def mp_search(store: Store, cuota: Cuota, consulta: dict) -> dict:
    return _ejecutar(store, cuota, consulta, "search")


def mp_aggregate(store: Store, cuota: Cuota, consulta: dict) -> dict:
    return _ejecutar(store, cuota, consulta, "aggregate")


# --------------------------------------------------------------------------
# 5. mp_get
# --------------------------------------------------------------------------

def mp_get(store: Store, cuota: Cuota, entity: str, key: str,
           incluir_relacionados: bool = True) -> dict:
    llaves = {
        "licitacion": ("licitacion", "codigo"),
        "orden_compra": ("orden_compra", "codigo"),
        "organismo": ("organismo", "codigo"),
        "proveedor": ("proveedor", "rut"),
    }
    if entity not in llaves:
        return _error(f"mp_get no soporta '{entity}'. Soportadas: "
                      f"{', '.join(llaves)}. Para lineas usa mp_search.")
    tabla, col = llaves[entity]
    cols, rows = store.rows(f"SELECT * FROM {tabla} WHERE {col} = ?", [key])
    if not rows:
        return _error(
            f"No hay '{entity}' con {col}='{key}' en el almacen. Puede ser que no "
            f"exista, o que el periodo no este ingerido. Revisa meta.as_of.",
            {"as_of": store.as_of()},
        )
    obj: dict[str, Any] = dict(zip(cols, rows[0]))
    relacionados: dict[str, Any] = {}

    if incluir_relacionados and entity == "licitacion":
        for nombre, sql, p in (
            ("items", "SELECT * FROM licitacion_item WHERE codigo_licitacion = ? "
                       "ORDER BY correlativo", [key]),
            ("adjudicaciones", "SELECT * FROM adjudicacion_item WHERE codigo_licitacion = ? "
                               "ORDER BY correlativo", [key]),
            ("ordenes_compra", "SELECT * FROM orden_compra WHERE codigo_licitacion = ? "
                               "ORDER BY fecha_envio", [key]),
        ):
            try:
                cc, rr = store.rows(sql, p)
                relacionados[nombre] = [dict(zip(cc, r)) for r in rr]
            except Exception:                                 # noqa: BLE001
                relacionados[nombre] = []
    elif incluir_relacionados and entity == "orden_compra":
        try:
            cc, rr = store.rows(
                "SELECT * FROM orden_compra_item WHERE codigo_oc = ? ORDER BY correlativo",
                [key])
            relacionados["items"] = [dict(zip(cc, r)) for r in rr]
        except Exception:                                     # noqa: BLE001
            relacionados["items"] = []

    e = ENTIDADES[entity]
    return {
        "data": obj,
        "relacionados": relacionados,
        "meta": {
            "procedencia": obj.get("_procedencia") or e.procedencia,
            "as_of": store.as_of(),
            "limitaciones": [c.as_dict() for c in e.caveats],
            "cuota": cuota.estado(),
            "atribucion": ATRIBUCION,
        },
    }


# --------------------------------------------------------------------------
# 6 y 7. mp_query_save / mp_query_run
# --------------------------------------------------------------------------

def mp_query_save(store: Store, nombre: str, consulta: dict, modo: str,
                  descripcion: str = "", etiquetas: list[str] | None = None) -> dict:
    if modo not in ("search", "aggregate"):
        return _error("modo debe ser 'search' o 'aggregate'.")
    try:
        c = Consulta.parse(consulta)
        validar(c, modo)
    except ConsultaInvalida as exc:
        return _error(f"No se guarda una consulta invalida: {exc}")
    qid = c.query_id()
    store.guardar_consulta(qid, nombre, descripcion, modo, c.as_dict(), etiquetas or [])
    return {
        "query_id": qid,
        "nombre": nombre,
        "guardada": True,
        "nota": ("Una consulta guardada es la primitiva de dashboard: la capa de "
                 "producto fija esta referencia y la re-ejecuta programada."),
    }


def mp_query_run(store: Store, cuota: Cuota, ref: str,
                 sobrescribir: dict | None = None) -> dict:
    g = store.leer_consulta(ref)
    if not g:
        disponibles = [q["nombre"] for q in store.listar_consultas()][:30]
        return _error(f"No hay consulta guardada '{ref}'.",
                      {"disponibles": disponibles})
    consulta = {**g["consulta"], **(sobrescribir or {})}
    if "limit" in consulta:
        consulta["limit"] = min(int(consulta["limit"]), MAX_LIMIT)
    out = _ejecutar(store, cuota, consulta, g["modo"])
    if "error" not in out:
        store.marcar_corrida(g["query_id"])
        out["meta"]["consulta_guardada"] = {
            "query_id": g["query_id"], "nombre": g["nombre"],
            "descripcion": g["descripcion"], "modo": g["modo"],
            "sobrescrito": sobrescribir or {},
        }
    return out


def mp_query_list(store: Store, etiqueta: str | None = None) -> dict:
    return {"data": store.listar_consultas(etiqueta),
            "meta": {"nota": "Biblioteca de consultas. Ver seeds/consultas.yaml."}}
