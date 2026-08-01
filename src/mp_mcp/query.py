"""Consulta canonica: validacion, SQL, chart_hint y cobertura.

La consulta canonica es el objeto que hace que un dashboard sea "un conjunto de
consultas guardadas". Toda respuesta del MCP la devuelve, para que la capa de
producto pueda fijarla y re-ejecutarla sin reconstruirla.

Seguridad: ningun identificador SQL viene del agente. Los nombres de campo se
resuelven contra el registro semantico y se emite el `sql` declarado ahi. Los
valores van como parametros ligados.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, asdict

from .semantic import (
    ENTIDADES, GRANOS_TEMPORALES, OPS, Entity, Measure,
)

MAX_LIMIT = 5_000
DEFAULT_LIMIT = 100

_FN_SQL = {
    "count": "count({c})",
    "count_distinct": "count(DISTINCT {c})",
    "sum": "sum({c})",
    "avg": "avg({c})",
    "min": "min({c})",
    "max": "max({c})",
    "stddev": "stddev_samp({c})",
    "p10": "quantile_cont({c}, 0.10)",
    "p25": "quantile_cont({c}, 0.25)",
    "p50": "quantile_cont({c}, 0.50)",
    "p75": "quantile_cont({c}, 0.75)",
    "p90": "quantile_cont({c}, 0.90)",
}

_GRANO_SQL = {
    "dia": "date_trunc('day', {c})",
    "semana": "date_trunc('week', {c})",
    "mes": "date_trunc('month', {c})",
    "trimestre": "date_trunc('quarter', {c})",
    "ano": "date_trunc('year', {c})",
}

_OP_SQL = {
    "eq": "{c} = ?", "ne": "{c} <> ?",
    "gt": "{c} > ?", "gte": "{c} >= ?",
    "lt": "{c} < ?", "lte": "{c} <= ?",
    "contains": "{c} ILIKE ?", "starts_with": "{c} ILIKE ?",
    "is_null": "{c} IS NULL", "not_null": "{c} IS NOT NULL",
}


class ConsultaInvalida(ValueError):
    """Error de validacion con mensaje accionable.

    Anthropic (Writing effective tools for agents): el mensaje debe redirigir al
    agente a una estrategia mejor, no devolver un codigo opaco. Por eso cada
    error enumera las opciones validas.
    """


@dataclass
class Filtro:
    field: str
    op: str
    value: object = None


@dataclass
class MedidaPedida:
    fn: str
    field: str
    alias: str | None = None

    def nombre(self) -> str:
        return self.alias or f"{self.fn}_{self.field}"


@dataclass
class Orden:
    alias: str
    dir: str = "desc"


@dataclass
class Consulta:
    entity: str
    filters: list[Filtro] = field(default_factory=list)
    group_by: list[str] = field(default_factory=list)
    measures: list[MedidaPedida] = field(default_factory=list)
    fields: list[str] = field(default_factory=list)      # solo mp_search
    grain: str | None = None                            # granularidad temporal
    grain_field: str | None = None                      # sobre que fecha
    order_by: list[Orden] = field(default_factory=list)
    limit: int = DEFAULT_LIMIT
    offset: int = 0

    @classmethod
    def parse(cls, d: dict) -> "Consulta":
        if not isinstance(d, dict) or "entity" not in d:
            raise ConsultaInvalida(
                "La consulta necesita al menos 'entity'. Entidades validas: "
                + ", ".join(ENTIDADES) + ". Llama mp_schema_describe primero."
            )
        return cls(
            entity=d["entity"],
            filters=[Filtro(**f) for f in d.get("filters", [])],
            group_by=list(d.get("group_by", [])),
            measures=[MedidaPedida(**m) for m in d.get("measures", [])],
            fields=list(d.get("fields", [])),
            grain=d.get("grain"),
            grain_field=d.get("grain_field"),
            order_by=[Orden(**o) for o in d.get("order_by", [])],
            limit=int(d.get("limit", DEFAULT_LIMIT)),
            offset=int(d.get("offset", 0)),
        )

    def as_dict(self) -> dict:
        d = asdict(self)
        return {k: v for k, v in d.items() if v not in ([], None, 0) or k == "limit"}

    def query_id(self) -> str:
        blob = json.dumps(self.as_dict(), sort_keys=True, ensure_ascii=False)
        return "q_" + hashlib.sha256(blob.encode()).hexdigest()[:16]


# --------------------------------------------------------------------------
# Validacion
# --------------------------------------------------------------------------

def _entidad(nombre: str) -> Entity:
    e = ENTIDADES.get(nombre)
    if e is None:
        raise ConsultaInvalida(
            f"'{nombre}' no es una entidad. Validas: {', '.join(ENTIDADES)}."
        )
    return e


def validar(c: Consulta, modo: str) -> Entity:
    """modo: 'aggregate' | 'search'. Devuelve la entidad resuelta."""
    e = _entidad(c.entity)

    for f in c.filters:
        if f.op not in OPS:
            raise ConsultaInvalida(
                f"Operador '{f.op}' no existe. Validos: {', '.join(OPS)}."
            )
        if e.campo(f.field) is None:
            raise ConsultaInvalida(
                f"'{f.field}' no es un campo de '{e.name}'. "
                f"Dimensiones: {', '.join(e.dims)}. "
                f"Medidas: {', '.join(e.measures)}. "
                f"Atributos: {', '.join(e.attrs) or '(ninguno)'}."
            )
        if f.op in ("in", "not_in") and not isinstance(f.value, (list, tuple)):
            raise ConsultaInvalida(
                f"El operador '{f.op}' sobre '{f.field}' espera una lista."
            )
        if f.op == "between" and (not isinstance(f.value, (list, tuple)) or len(f.value) != 2):
            raise ConsultaInvalida(
                f"'between' sobre '{f.field}' espera exactamente [desde, hasta]."
            )

    if c.grain:
        if c.grain not in GRANOS_TEMPORALES:
            raise ConsultaInvalida(
                f"Grano '{c.grain}' no existe. Validos: {', '.join(GRANOS_TEMPORALES)}."
            )
        temporales = [d.name for d in e.dims.values() if d.kind == "temporal"]
        if not c.grain_field:
            raise ConsultaInvalida(
                f"Usaste grain='{c.grain}' sin grain_field. Campos temporales de "
                f"'{e.name}': {', '.join(temporales) or '(ninguno)'}."
            )
        d = e.dims.get(c.grain_field)
        if d is None or d.kind != "temporal":
            raise ConsultaInvalida(
                f"'{c.grain_field}' no es un campo temporal de '{e.name}'. "
                f"Validos: {', '.join(temporales) or '(ninguno)'}."
            )

    if modo == "aggregate":
        if not c.measures:
            raise ConsultaInvalida(
                f"mp_aggregate necesita al menos una medida. Medidas de '{e.name}': "
                + ", ".join(f"{m.name} ({'/'.join(m.fns)})" for m in e.measures.values())
            )
        for g in c.group_by:
            if g not in e.dims:
                raise ConsultaInvalida(
                    f"No se puede agrupar por '{g}': no es dimension de '{e.name}'. "
                    f"Dimensiones: {', '.join(e.dims)}."
                )
        for m in c.measures:
            med = e.measures.get(m.field)
            if med is None:
                raise ConsultaInvalida(
                    f"'{m.field}' no es una medida agregable de '{e.name}'. "
                    f"Medidas: {', '.join(e.measures)}. "
                    f"(Si querias filtrar o agrupar por eso, usa filters o group_by.)"
                )
            if m.fn not in med.fns:
                raise ConsultaInvalida(
                    f"La funcion '{m.fn}' no aplica a '{m.field}'. "
                    f"Permitidas: {', '.join(med.fns)}."
                )
        alias = [m.nombre() for m in c.measures]
        if len(set(alias)) != len(alias):
            raise ConsultaInvalida(f"Alias de medida duplicados: {alias}. Usa 'alias'.")
        validos = set(alias) | set(c.group_by) | ({"periodo"} if c.grain else set())
        for o in c.order_by:
            if o.alias not in validos:
                raise ConsultaInvalida(
                    f"order_by '{o.alias}' no esta en la salida. "
                    f"Disponibles: {', '.join(sorted(validos))}."
                )
    else:
        if c.measures:
            raise ConsultaInvalida(
                "mp_search no acepta 'measures' (es nivel fila). Para agregar usa "
                "mp_aggregate."
            )
        for f_ in c.fields:
            if e.campo(f_) is None:
                raise ConsultaInvalida(
                    f"'{f_}' no es un campo de '{e.name}'. "
                    f"Dimensiones: {', '.join(e.dims)}. "
                    f"Atributos: {', '.join(e.attrs) or '(ninguno)'}."
                )

    if not (1 <= c.limit <= MAX_LIMIT):
        raise ConsultaInvalida(f"limit debe estar entre 1 y {MAX_LIMIT}.")
    return e


# --------------------------------------------------------------------------
# SQL
# --------------------------------------------------------------------------

def _q(nombre: str) -> str:
    """Cita un alias de salida. Los nombres vienen del registro, no del agente."""
    return '"' + nombre.replace('"', "") + '"'


def _where(e: Entity, filtros: list[Filtro]) -> tuple[str, list]:
    partes, params = [], []
    for f in filtros:
        col = e.campo(f.field).sql
        if f.op in ("is_null", "not_null"):
            partes.append(_OP_SQL[f.op].format(c=col))
        elif f.op == "in":
            partes.append(f"{col} IN ({', '.join('?' * len(f.value))})")
            params.extend(f.value)
        elif f.op == "not_in":
            partes.append(f"{col} NOT IN ({', '.join('?' * len(f.value))})")
            params.extend(f.value)
        elif f.op == "between":
            partes.append(f"{col} BETWEEN ? AND ?")
            params.extend(f.value)
        elif f.op == "contains":
            partes.append(_OP_SQL[f.op].format(c=col))
            params.append(f"%{f.value}%")
        elif f.op == "starts_with":
            partes.append(_OP_SQL[f.op].format(c=col))
            params.append(f"{f.value}%")
        else:
            partes.append(_OP_SQL[f.op].format(c=col))
            params.append(f.value)
    return (" AND ".join(partes) if partes else "TRUE"), params


def to_sql(c: Consulta, e: Entity, modo: str) -> tuple[str, list, list[str]]:
    """Devuelve (sql, params, nombres_de_columna)."""
    where, params = _where(e, c.filters)

    def _orden(validos: list[str]) -> str:
        piezas = [
            _q(o.alias) + (" ASC" if o.dir.lower() == "asc" else " DESC")
            for o in c.order_by if o.alias in validos
        ]
        return ("ORDER BY " + ", ".join(piezas)) if piezas else ""

    if modo == "search":
        campos = c.fields or (list(e.dims) + list(e.attrs))
        sel = [e.campo(n).sql + " AS " + _q(n) for n in campos]
        sql = (f"SELECT {', '.join(sel)} FROM {e.table} WHERE {where} "
               f"{_orden(campos)} LIMIT {c.limit} OFFSET {c.offset}")
        return sql, params, campos

    sel, cols = [], []
    if c.grain:
        col = e.dims[c.grain_field].sql
        sel.append(_GRANO_SQL[c.grain].format(c=col) + " AS " + _q("periodo"))
        cols.append("periodo")
    for g in c.group_by:
        sel.append(e.dims[g].sql + " AS " + _q(g))
        cols.append(g)
    dims_sel = list(cols)
    for m in c.measures:
        med: Measure = e.measures[m.field]
        sel.append(_FN_SQL[m.fn].format(c=med.sql) + " AS " + _q(m.nombre()))
        cols.append(m.nombre())

    grupo = ("GROUP BY " + ", ".join(_q(g) for g in dims_sel)) if dims_sel else ""
    orden = _orden(cols)
    if not orden and c.grain:
        orden = "ORDER BY " + _q("periodo") + " ASC"

    sql = (f"SELECT {', '.join(sel)} FROM {e.table} WHERE {where} {grupo} {orden} "
           f"LIMIT {c.limit} OFFSET {c.offset}")
    return sql, params, cols


def sql_cobertura(c: Consulta, e: Entity) -> tuple[str, list] | None:
    """SQL que cuenta filas consideradas y filas perdidas por conversion de moneda.

    Es el mecanismo anti-humo a nivel de payload: si el 40 % de las filas no tuvo
    tipo de cambio, la respuesta lo dice en vez de devolver un promedio sesgado.
    """
    campos_clp = [m.field for m in c.measures
                  if m.field in e.measures and e.measures[m.field].unidad == "CLP"]
    if not campos_clp:
        return None
    where, params = _where(e, c.filters)
    nulos = " + ".join(
        f"CASE WHEN {e.measures[f].sql} IS NULL THEN 1 ELSE 0 END" for f in campos_clp
    )
    sql = (f"SELECT count(*) AS filas, "
           f"sum(CASE WHEN ({nulos}) > 0 THEN 1 ELSE 0 END) AS sin_conversion "
           f"FROM {e.table} WHERE {where}")
    return sql, params


# --------------------------------------------------------------------------
# chart_hint
# --------------------------------------------------------------------------

def chart_hint(c: Consulta, e: Entity, cols: list[str], modo: str) -> dict:
    """Encoding sugerido. El MCP NO renderiza: eso es de la capa de presentacion."""
    if modo == "search":
        return {"tipo": "tabla", "dimensiones": cols, "medidas": []}

    dims = (["periodo"] if c.grain else []) + list(c.group_by)
    medidas = [m.nombre() for m in c.measures]

    if c.grain:
        tipo = "linea"
    elif len(dims) == 0:
        tipo = "indicador"
    elif len(dims) == 1:
        tipo = "barra"
    elif len(dims) == 2:
        tipo = "barra_agrupada"
    else:
        tipo = "tabla"

    return {
        "tipo": tipo,
        "eje_x": dims[0] if dims else None,
        "serie": dims[1] if len(dims) > 1 else None,
        "dimensiones": dims,
        "medidas": [
            {
                "columna": m.nombre(),
                "label": e.measures[m.field].label,
                "unidad": e.measures[m.field].unidad,
                "formato": ("moneda_clp" if e.measures[m.field].unidad == "CLP"
                            else "entero" if e.measures[m.field].unidad == "conteo"
                            else "decimal"),
            }
            for m in c.measures
        ],
        "advertencia_orden": ("Serie temporal: no reordenar por medida, romperia el eje."
                              if c.grain else None),
    }


def limitaciones_aplicables(c: Consulta, e: Entity) -> list[dict]:
    """Caveats de la entidad + de las medidas usadas. Van en cada respuesta."""
    vistos, salida = set(), []
    for cav in e.caveats:
        if cav.texto not in vistos:
            vistos.add(cav.texto)
            salida.append(cav.as_dict())
    for m in c.measures:
        med = e.measures.get(m.field)
        if med:
            for cav in med.caveats:
                if cav.texto not in vistos:
                    vistos.add(cav.texto)
                    salida.append(cav.as_dict())
    return salida
