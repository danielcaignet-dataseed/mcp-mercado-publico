"""Las herramientas. Agnosticas de rubro y de producto.

Ocho leen el snapshot local y siete salen a la red: la superficie completa de la
API v1 de ChileCompra menos Compra Agil, excluida a proposito.

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
from .analisis import (                                # noqa: F401
    mp_comprador_perfil, mp_huella, mp_precio_perdido,
    mp_sin_competencia,
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
    # Indice por defecto: sin `entity` se devuelve el catalogo de entidades con
    # su grano, su estado y sus limitaciones COMPLETAS, pero sin el detalle
    # columna por columna. El detalle se pide por entidad.
    #
    # Las limitaciones NUNCA se omiten: son la fuente de honestidad del agente y
    # recortarlas seria cambiar contexto por confiabilidad. Lo que se omite son
    # nombres de campo que el agente no necesita hasta elegir la entidad.
    # MEDIDO 2026-08-12: el payload completo son 24.426 bytes, de los cuales
    # 23.516 son `entidades`; el indice baja eso ~45 %.
    if entity is None:
        for _n, _e in d.get("entidades", {}).items():
            _dims = _e.pop("dimensiones", {}) or {}
            _meds = _e.pop("medidas", {}) or {}
            _e.pop("atributos", None)
            _e["detalle"] = (
                f"{len(_dims)} dimensiones, {len(_meds)} medidas. "
                f"Llama mp_schema_describe(entity='{_n}') "
                "para los nombres exactos de campo."
            )
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


# --------------------------------------------------------------------------
# 9. Consultas EN VIVO a la API de ChileCompra
#
# Son las UNICAS funciones de este modulo que salen a la red; el resto lee el
# snapshot local. Devuelven la respuesta de la API tal cual: re-mapear los
# campos aca crearia una segunda definicion que puede divergir de la capa
# semantica, y este proyecto tiene una sola fuente de verdad por diseno.
#
# Cada llamada consume cuota real de ChileCompra (techo 10.000/dia por ticket).
# --------------------------------------------------------------------------

_LIM_VIVO = [{
    "limitacion": (
        "Respuesta cruda de la API v1: los nombres de campo son los del "
        "proveedor del dato (CodigoExterno, MontoUnitario, RutProveedor), NO los "
        "de la capa semantica del almacen. No mezclar con salidas de mp_search o "
        "mp_aggregate sin normalizar."),
    "severidad": "media",
    "verificado_por": "MEDIDO 2026-08-12",
}]


def _meta_vivo(cuota) -> dict:
    return {
        "procedencia": "api-live",
        "as_of": "consulta en vivo a ChileCompra; NO proviene del snapshot local",
        "cuota": cuota.estado(),
        "limitaciones": _LIM_VIVO,
    }


# Sobres de exito. `BuscarProveedor` no usa `Listado` sino `listaEmpresas`:
# omitirlo hacia que una respuesta valida cayera al camino de error por suerte,
# solo porque no traia `Mensaje`.
_SOBRES_OK = ("Listado", "listaEmpresas")

# "No hay resultados de empresas." La consulta se hizo, se autentico y devolvio
# cero filas. Es un resultado vacio, NO un fallo. MEDIDO 2026-08-12.
_CODIGO_SIN_RESULTADOS = 10200


def _error_chilecompra(d: dict) -> str | None:
    """Detecta el sobre de error que ChileCompra devuelve CON HTTP 2xx.

    MEDIDO 2026-08-12: un ticket ausente o invalido responde HTTP 203 con cuerpo
    {"Codigo":203,"Mensaje":"Ticket no valido."} y SIN clave `Listado`. Sin esta
    deteccion, la ausencia de `Listado` se confunde con "no existe" y el agente
    le afirma al usuario que una licitacion no existe cuando en realidad la
    consulta nunca se autentico. Distinguir "no hay dato" de "no pude preguntar"
    no es un detalle: es la diferencia entre informar y mentir.

    Y la simetria tambien importa: `Codigo=10200` ("No hay resultados de
    empresas") es una consulta EXITOSA con cero filas. Tratarla como error
    invierte el mismo error hacia el otro lado -- el agente diria "no pude
    preguntar" cuando preguntó bien y la respuesta es que no hay.
    """
    if any(k in d for k in _SOBRES_OK):
        return None
    try:
        if int(d.get("Codigo") or 0) == _CODIGO_SIN_RESULTADOS:
            return None
    except (TypeError, ValueError):
        pass
    msg = d.get("Mensaje") or d.get("mensaje")
    if msg:
        return f"ChileCompra respondio Codigo={d.get('Codigo')}: {msg}"
    return None


def mp_licitacion_vivo(api, cuota, codigo: str) -> dict:
    """Detalle de CUALQUIER licitacion, este o no en el snapshot local."""
    d = api.licitacion(codigo, motivo="mcp-vivo")
    err = _error_chilecompra(d)
    if err:
        return {"error": err, "codigo": codigo,
                "nota": "NO es que la licitacion no exista: la consulta fallo.",
                "meta": _meta_vivo(cuota)}
    listado = d.get("Listado") or []
    return {"encontrada": bool(listado), "codigo": codigo,
            "cantidad": d.get("Cantidad"),
            "licitacion": listado[0] if listado else None,
            "meta": _meta_vivo(cuota)}


def mp_oc_vivo(api, cuota, codigo: str) -> dict:
    """Detalle de una orden de compra: totales, items y proveedor."""
    d = api.orden_compra(codigo, motivo="mcp-vivo")
    err = _error_chilecompra(d)
    if err:
        return {"error": err, "codigo": codigo,
                "nota": "NO es que la orden no exista: la consulta fallo.",
                "meta": _meta_vivo(cuota)}
    listado = d.get("Listado") or []
    return {"encontrada": bool(listado), "codigo": codigo,
            "cantidad": d.get("Cantidad"),
            "orden_compra": listado[0] if listado else None,
            "meta": _meta_vivo(cuota)}


def mp_proveedor_resolver(api, cuota, rut: str) -> dict:
    """RUT -> codigo y nombre de empresa proveedora.

    El codigo que devuelve es lo que piden `codigo_proveedor` en
    mp_licitaciones_vivo y mp_ordenes_vivo: la API no filtra por RUT.
    """
    from .api import dv_calza, normalizar_rut
    d = api.buscar_proveedor(rut, motivo="mcp-vivo")
    err = _error_chilecompra(d)
    if err:
        return {"error": err, "rut": rut,
                "nota": "NO es que el RUT no exista: la consulta fallo.",
                "meta": _meta_vivo(cuota)}
    empresas = d.get("listaEmpresas") or d.get("Listado") or []
    e = empresas[0] if empresas else {}
    out = {
        "rut_consultado": rut,
        "rut_enviado_a_la_api": normalizar_rut(rut),
        "encontrado": bool(empresas),
        "codigo_empresa": e.get("CodigoEmpresa"),
        "nombre_empresa": e.get("NombreEmpresa"),
        "coincidencias": len(empresas),
        "meta": _meta_vivo(cuota),
    }
    if not empresas:
        out["nota"] = ("La consulta se hizo bien y ChileCompra respondio que no hay "
                       "empresa con ese RUT en su registro de proveedores. Eso NO "
                       "significa que la empresa no exista: significa que no esta "
                       "inscrita como proveedor del Estado.")
        if dv_calza(rut) is False:
            out["nota"] += (" ADEMAS el digito verificador no calza con el cuerpo "
                            "del RUT: revisa si hay un error de tipeo antes de "
                            "concluir algo. No se corrigio por cuenta propia.")
    return out


# Tope duro de items devueltos. NO es una preferencia de estilo:
# [MEDIDO 2026-08-12] ordenesdecompra.json?fecha=28072026 devolvio Cantidad=12265
# y las 12.265 en el MISMO cuerpo -- la API no pagina. Devolver eso a un agente
# le llena la ventana de contexto con una sola llamada.
_LIMITE_DEF = 25
_LIMITE_MAX = 200

_ESTADOS_LICITACION = ("activas", "publicada", "cerrada", "adjudicada",
                       "desierta", "revocada", "suspendida")
_ESTADOS_OC = ("enviadaproveedor", "aceptada", "cancelada",
               "recepcionconforme", "todos")

# CodigoEstado -> nombre, DERIVADO POR MEDICION, no de conocimiento previo.
# Metodo [MEDIDO 2026-08-19]: por cada valor textual de `estado` que documenta
# ChileCompra se pidio el listado y se miro que codigos numericos volvian. Si un
# filtro textual devuelve un unico codigo, la correspondencia queda establecida.
#
# Existe porque el listado trae el codigo numerico y NADA de texto, y tres veces
# seguidas el agente resolvio eso poniendo el nombre de su propio conocimiento y
# presentandolo como verificado. Dos de esas veces acerto; una invento el nombre
# del codigo 5 de OC, que ningun filtro documentado devuelve.
#
# TRAMPA MEDIDA: el mismo numero significa cosas distintas segun la entidad.
# El 6 es "cerrada" en licitaciones y "aceptada" en ordenes de compra. Por eso el
# glosario esta separado por entidad y no hay una tabla unica.
#
# TRAMPA MEDIDA: `activas` NO es un estado, es un filtro. Tanto estado=activas
# como estado=publicada devuelven CodigoEstado 5.
_GLOSARIO_ESTADO = {
    "licitaciones": {"5": "publicada", "6": "cerrada", "7": "desierta",
                     "8": "adjudicada", "15": "revocada"},
    "ordenes": {"4": "enviadaproveedor", "6": "aceptada", "9": "cancelada",
                "12": "recepcionconforme"},
}

# Codigos vistos en un dia cerrado que NINGUN filtro documentado devuelve, asi que
# no se les pone nombre: licitaciones 16 (1 caso) y ordenes 5 (19 casos).
# `suspendida` y `cancelada` de licitaciones no devolvieron filas al medir.


def _listado_vivo(api, cuota, *, que: str, fecha, estado, codigo_organismo,
                  codigo_proveedor, limite, volcar=False, store=None) -> dict:
    """Cuerpo comun de las dos tools de listado en vivo.

    Exige al menos un filtro. Sin ninguno, la API responde sobre el dia corriente
    completo, que son decenas de miles de filas: es una llamada que un agente no
    quiere hacer sin saberlo.
    """
    if not any((fecha, estado, codigo_organismo, codigo_proveedor)):
        validos = _ESTADOS_LICITACION if que == "licitaciones" else _ESTADOS_OC
        return {"error": "Hace falta al menos un filtro.",
                "nota": (f"Sin filtro la API responde el dia corriente completo "
                         f"(MEDIDO: hasta 12.265 filas en un solo cuerpo). Para "
                         f"'lo de hoy' usa estado='{validos[0]}'. Filtros: fecha "
                         f"(ddmmaaaa), estado, codigo_organismo, codigo_proveedor; "
                         f"son combinables."),
                "estados_validos": list(validos)}

    n = _LIMITE_DEF if limite is None else max(1, min(int(limite), _LIMITE_MAX))
    if que == "licitaciones":
        d = api.licitaciones_listado(fecha=fecha, estado=estado,
                                     codigo_organismo=codigo_organismo,
                                     codigo_proveedor=codigo_proveedor,
                                     motivo="mcp-vivo")
        clave, campos = "licitaciones", "CodigoExterno, Nombre, CodigoEstado, FechaCierre"
        detalle = "mp_licitacion_vivo(codigo)"
    else:
        d = api.ordenes_listado(fecha=fecha, estado=estado,
                                codigo_organismo=codigo_organismo,
                                codigo_proveedor=codigo_proveedor,
                                motivo="mcp-vivo")
        clave, campos = "ordenes_compra", "Codigo, Nombre, CodigoEstado"
        detalle = "mp_oc_vivo(codigo)"

    err = _error_chilecompra(d)
    if err:
        return {"error": err,
                "nota": "NO es que no haya resultados: la consulta fallo.",
                "meta": _meta_vivo(cuota)}

    todas = d.get("Listado") or []
    devueltas = todas[:n]
    truncado = len(todas) > len(devueltas)

    # Conteo por estado sobre TODAS las filas, no sobre las devueltas. Es la
    # respuesta a "como se analiza algo truncado": lo que se recorta son las
    # filas, no los agregados. Estos numeros son exactos aunque se vean 25.
    por_estado = {}
    for f in todas:
        k = str(f.get("CodigoEstado"))
        por_estado[k] = por_estado.get(k, 0) + 1
    glos = _GLOSARIO_ESTADO[que]
    sin_nombre = sorted(k for k in por_estado if k not in glos)

    meta = _meta_vivo(cuota)
    meta["limitaciones"] = list(meta["limitaciones"]) + [{
        "limitacion": (
            f"El listado trae solo {campos}. Ni monto, ni comprador, ni "
            f"proveedor, ni items: para eso hay que pedir el detalle con "
            f"{detalle}, y cuesta 1 hit por codigo."),
        "severidad": "media",
        "verificado_por": "MEDIDO 2026-08-12"}]
    if sin_nombre:
        meta["limitaciones"].append({
            "limitacion": (
                "Los codigos de estado %s aparecen en el resultado y NO tienen "
                "nombre verificado: ningun filtro documentado de ChileCompra los "
                "devuelve, asi que el MCP no sabe como se llaman. NO les pongas "
                "un nombre de tu propio conocimiento y lo presentes como dato: "
                "citalos como codigo numerico. El glosario de los que SI estan "
                "medidos viene en `glosario_codigo_estado`. Y ojo, el mismo "
                "numero significa distinto segun la entidad: el 6 es cerrada en "
                "licitaciones y aceptada en ordenes de compra."
                % ", ".join(sin_nombre)),
            "severidad": "alta",
            "verificado_por": "MEDIDO 2026-08-19"})
    meta["glosario_codigo_estado"] = glos

    if truncado:
        meta["limitaciones"].append({
            "limitacion": (
                f"Se devolvieron {len(devueltas)} de {len(todas)}. Son las "
                f"PRIMERAS en el orden que entrega ChileCompra, NO una muestra "
                f"representativa: no calcules estadisticas sobre esto ni digas "
                f"'las mas grandes'. Para el universo, acota el filtro o subi "
                f"`limite` (tope {_LIMITE_MAX}) -- pero la salida BUENA es "
                f"volcar=true, que guarda las {len(todas)} filas completas en el "
                f"almacen y devuelve solo el conteo: no se pierde nada y no te "
                f"llena el contexto. Y ojo: conteo_por_codigo_estado YA esta "
                f"calculado sobre las {len(todas)}, no sobre las {len(devueltas)} "
                f"que ves, asi que para contar por estado no te falta nada. "
                f"IMPORTANTE al citarlo: el recorte lo hace ESTA tool para no "
                f"llenarte el contexto, NO ChileCompra -- su API devuelve todo "
                f"junto y sin paginar. No le atribuyas a ChileCompra un limite "
                f"de {_LIMITE_MAX} que no tiene."),
            "severidad": "alta",
            "verificado_por": "determinado por el truncado de esta respuesta"})

    filtros = {k: v for k, v in (("fecha", fecha), ("estado", estado),
                                 ("codigo_organismo", codigo_organismo),
                                 ("codigo_proveedor", codigo_proveedor)) if v}
    out = {
        "filtros": filtros,
        "cantidad_total": d.get("Cantidad"),
        "conteo_por_codigo_estado": por_estado,
        "conteo_por_estado": {glos[k]: v for k, v in por_estado.items()
                              if k in glos},
        "codigos_de_estado_sin_nombre": sin_nombre,
        "conteo_calculado_sobre": len(todas),
        "devueltas": len(devueltas),
        "truncado": truncado,
        clave: devueltas,
        "meta": meta,
    }

    if volcar:
        if store is None:
            out["volcado"] = {"error": "sin almacen: el volcado no esta disponible"}
        else:
            import json as _j
            try:
                v = store.volcar_listado(
                    que,
                    _j.dumps(filtros, sort_keys=True, ensure_ascii=False),
                    todas)
                v["como_consultarlo"] = (
                    "mp_volcado_leer(tipo=" + chr(39) + que + chr(39) + ", texto=..., "
                    "codigo_estado=..., limite=..., offset=...) -- local, 0 hits")
                out["volcado"] = v
            except Exception as exc:                              # noqa: BLE001
                out["volcado"] = {"error": scrub("%s: %s" % (type(exc).__name__, exc))}
    return out


def mp_licitaciones_vivo(api, cuota, fecha=None, estado=None, codigo_organismo=None,
                         codigo_proveedor=None, limite=None, volcar=False,
                         store=None) -> dict:
    """Listado de licitaciones EN VIVO. Los cuatro filtros son combinables."""
    return _listado_vivo(api, cuota, que="licitaciones", fecha=fecha, estado=estado,
                         codigo_organismo=codigo_organismo,
                         codigo_proveedor=codigo_proveedor, limite=limite,
                         volcar=volcar, store=store)


def mp_ordenes_vivo(api, cuota, fecha=None, estado=None, codigo_organismo=None,
                    codigo_proveedor=None, limite=None, volcar=False,
                    store=None) -> dict:
    """Listado de ordenes de compra EN VIVO. Los cuatro filtros son combinables."""
    return _listado_vivo(api, cuota, que="ordenes", fecha=fecha, estado=estado,
                         codigo_organismo=codigo_organismo,
                         codigo_proveedor=codigo_proveedor, limite=limite,
                         volcar=volcar, store=store)


def mp_volcado_leer(store, tipo: str, filtros=None, texto=None, codigo_estado=None,
                    limite=50, offset=0) -> dict:
    """Lee UN listado ya volcado. 0 hits: no sale a la red."""
    if tipo not in ("licitaciones", "ordenes"):
        return {"error": "tipo debe ser licitaciones u ordenes",
                "volcados_disponibles": store.volcados()}
    n = max(1, min(int(limite or 50), _LIMITE_MAX))
    r = store.leer_volcado(tipo, filtros=filtros, texto=texto,
                           codigo_estado=codigo_estado,
                           limite=n, offset=max(0, int(offset or 0)))
    r["meta"] = {
        "procedencia": "volcado local de una consulta en vivo previa",
        "as_of": r.get("volcado_capturado_en"),
        "cuota": "no consume: esta llamada no sale a la red",
        "limitaciones": [{
            "limitacion": (
                "Es una FOTO del momento en que se volco, no el estado de ahora. "
                "Si paso tiempo, volve a llamar la tool en vivo con volcar=true. "
                "Y trae solo los campos del listado de la API: ni monto, ni "
                "comprador, ni proveedor."),
            "severidad": "media",
            "verificado_por": "MEDIDO 2026-08-12"}],
    }
    r["meta"]["glosario_codigo_estado"] = _GLOSARIO_ESTADO[tipo]
    otros = r.get("otros_volcados") or []
    if otros:
        r["meta"]["limitaciones"].append({
            "limitacion": (
                "Hay %d volcado(s) MAS de tipo '%s' con otros filtros. Estos "
                "numeros son SOLO del volcado que dice `leyendo_volcado`, no de "
                "todos juntos. Si querias otro, pasalo en `filtros` tal cual "
                "aparece en `otros_volcados`." % (len(otros), tipo)),
            "severidad": "alta",
            "verificado_por": "MEDIDO 2026-08-19"})
    return r


def mp_comprador_catalogo(api, cuota) -> dict:
    """Catalogo completo de organismos compradores del Estado. Un solo hit."""
    d = api.buscar_comprador(motivo="mcp-vivo")
    err = _error_chilecompra(d)
    if err:
        return {"error": err, "meta": _meta_vivo(cuota)}
    return {"respuesta": d, "meta": _meta_vivo(cuota)}


def mp_cuota_estado(cuota) -> dict:
    """Cuanto queda del techo diario de ChileCompra. No consume cuota."""
    est = cuota.estado()
    lims = [{
        "limitacion": (
            "El contador es local: mide lo que gasto ESTE almacen. Si otro "
            "cliente usa el mismo ticket, el consumo real contra ChileCompra "
            "es mayor que este numero."),
        "severidad": "media",
        "verificado_por": "MEDIDO 2026-08-12"}]
    # Si el libro mayor no se puede escribir, el numero es indefendible y el
    # agente tiene que verlo aca, no solo en la documentacion del repo.
    if not est.get("contador_confiable", True):
        lims.insert(0, {
            "limitacion": est.get("ADVERTENCIA", "El libro mayor no es escribible."),
            "severidad": "alta",
            "verificado_por": "comprobado al responder esta llamada"})
    return {"cuota": est, "meta": {
        "procedencia": ("contador local sobre el libro mayor "
                        f"{est.get('libro_mayor', {}).get('ledger')}"),
        "limitaciones": lims}}
