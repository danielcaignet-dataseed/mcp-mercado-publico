"""Servidor MCP (stdio).

Las descripciones de herramienta se escriben como se le explicaria a alguien que
entra al equipo, con el contexto implicito hecho explicito. No son adorno: en el
benchmark de Anthropic, refinar descripciones movio resultados de forma
significativa, y aca cargan las advertencias que evitan que un agente agregue
cantidades de unidades distintas o sume montos estimados no publicos.
"""

from __future__ import annotations

import json
import os
from typing import Any

from mcp.server.fastmcp import FastMCP

from . import tools as T
from .config import Config, scrub
from .quota import Cuota
from .store import Store

mcp = FastMCP("mercado-publico")

# El servidor MCP NO llama a la API y NO escribe los hechos: solo consulta.
# MP_MCP_ESCRITURA=1 solo para desarrollo local con un unico proceso.
_cfg = Config.from_env()
_solo_lectura = os.environ.get("MP_MCP_ESCRITURA", "").lower() not in ("1", "true", "yes")
_store = Store(_cfg, read_only=_solo_lectura)
_cuota = Cuota(_store)


def _ok(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)


def _run(fn, *a, **kw) -> str:
    try:
        return _ok(fn(*a, **kw))
    except Exception as exc:                                  # noqa: BLE001
        return _ok({"error": scrub(f"{type(exc).__name__}: {exc}")})


# --------------------------------------------------------------------------

@mcp.tool()
def mp_schema_describe(entity: str | None = None,
                       incluir_limitaciones: bool = True) -> str:
    """Devuelve que se puede consultar: entidades, dimensiones, medidas y sus
    limitaciones conocidas.

    LLAMA ESTO PRIMERO. Los nombres de campo no se adivinan; si un campo no
    aparece aca, no existe y la consulta va a fallar.

    Cada entidad declara su 'grano' (que representa una fila). Respetarlo es
    critico: sumar un monto de cabecera sobre 'licitacion_item' lo multiplica por
    el numero de lineas de la licitacion.

    Cada limitacion trae 'verificado_por'. Si dice NO VERIFICADO, no la uses como
    afirmacion en un entregable a cliente.

    entity: opcional, para describir una sola entidad y ahorrar contexto.
    """
    return _run(T.mp_schema_describe, _store, entity, incluir_limitaciones)


@mcp.tool()
def mp_codes_search(vocabulary: str, texto: str = "", limit: int = 25) -> str:
    """Traduce lenguaje natural a codigos: UNSPSC, organismos, proveedores,
    regiones y comunas.

    Devuelve codigo, nombre y 'registros' (volumen observado en el almacen), para
    que puedas juzgar si el corte sirve antes de gastar una consulta grande.

    Para segmentar por mercado el nivel util es unspsc_familia (4 digitos) o
    unspsc_clase (6). El segmento (2) es demasiado grueso y el commodity (8) solo
    conviene para comparar precios.

    Aviso: los codigos los digita el organismo comprador y hay items mal
    clasificados. Un filtro solo por codigo pierde casos reales; combinalo con
    filtros de texto sobre nombre_producto o categoria_texto.

    vocabulary: unspsc_commodity | unspsc_clase | unspsc_familia |
        unspsc_segmento | organismo | proveedor | region | comuna
    """
    return _run(T.mp_codes_search, _store, vocabulary, texto, limit)


@mcp.tool()
def mp_search(consulta: dict) -> str:
    """Consulta a nivel de fila. Devuelve registros individuales, no agregados.

    Forma de 'consulta':
      {"entity": "licitacion",
       "filters": [{"field": "estado", "op": "eq", "value": "publicada"},
                   {"field": "fecha_cierre", "op": "between",
                    "value": ["2026-08-01", "2026-08-31"]}],
       "fields": ["codigo", "nombre", "organismo", "fecha_cierre"],
       "order_by": [{"alias": "fecha_cierre", "dir": "asc"}],
       "limit": 50}

    Operadores: eq, ne, in, not_in, gt, gte, lt, lte, between, contains,
    starts_with, is_null, not_null.

    Para contar, promediar o sacar percentiles usa mp_aggregate: esta herramienta
    no agrega.
    """
    return _run(T.mp_search, _store, _cuota, consulta)


@mcp.tool()
def mp_aggregate(consulta: dict) -> str:
    """Consulta agregada. Es el motor de graficos y de todo analisis.

    Forma de 'consulta':
      {"entity": "adjudicacion_item",
       "filters": [{"field": "unspsc_clase", "op": "in", "value": ["761015"]},
                   {"field": "fecha_adjudicacion", "op": "between",
                    "value": ["2025-01-01", "2026-06-30"]}],
       "group_by": ["region_comprador", "unidad_medida"],
       "measures": [{"fn": "p50", "field": "precio_unitario_clp", "alias": "mediana"},
                    {"fn": "p90", "field": "precio_unitario_clp", "alias": "p90"},
                    {"fn": "count", "field": "n_adjudicaciones", "alias": "n"}],
       "order_by": [{"alias": "n", "dir": "desc"}],
       "limit": 50}

    Series temporales: usa "grain" (dia|semana|mes|trimestre|ano) junto con
    "grain_field" (el campo de fecha). La columna resultante se llama 'periodo'.

    Funciones: count, count_distinct, sum, avg, min, max, p10, p25, p50, p75,
    p90, stddev. Cada medida declara cuales acepta.

    Dos trampas que el esquema advierte y conviene leer:
      - Los precios y cantidades solo son comparables dentro de una misma
        unidad_medida. Agrupa por unidad_medida o el numero no significa nada.
      - meta.cobertura informa cuantas filas quedaron fuera por falta de tipo de
        cambio. Un p50 sobre 40 % de cobertura no es representativo, y el payload
        te lo dice: no lo reportes como si lo fuera.

    La respuesta incluye 'chart_hint' con el encoding sugerido y 'consulta' con
    la consulta canonica, que puedes guardar con mp_query_save.
    """
    return _run(T.mp_aggregate, _store, _cuota, consulta)


@mcp.tool()
def mp_get(entity: str, key: str, incluir_relacionados: bool = True) -> str:
    """Trae una entidad completa por su clave natural, con lo relacionado unido
    en la misma llamada: no hace falta encadenar consultas.

    Para una licitacion devuelve cabecera + items con UNSPSC + adjudicaciones por
    linea + ordenes de compra derivadas.

    entity: licitacion | orden_compra | organismo | proveedor
    key: el codigo externo de la licitacion, el codigo de la OC, el codigo del
         organismo o el RUT del proveedor.
    """
    return _run(T.mp_get, _store, _cuota, entity, key, incluir_relacionados)


@mcp.tool()
def mp_query_save(nombre: str, consulta: dict, modo: str,
                  descripcion: str = "", etiquetas: list[str] | None = None) -> str:
    """Guarda una consulta canonica con un nombre, para re-ejecutarla despues.

    Es la primitiva de dashboard: un dashboard es un conjunto de consultas
    guardadas. Cuando el usuario dice 'fijame este grafico', se guarda la
    consulta que lo produjo, no la imagen.

    modo: 'search' o 'aggregate', segun con que herramienta se construyo.
    Valida antes de guardar: una consulta invalida no se persiste.
    """
    return _run(T.mp_query_save, _store, nombre, consulta, modo, descripcion, etiquetas)


@mcp.tool()
def mp_query_run(ref: str, sobrescribir: dict | None = None) -> str:
    """Re-ejecuta una consulta guardada por nombre o query_id.

    'sobrescribir' permite cambiar partes de la consulta sin reconstruirla, por
    ejemplo {"filters": [...]} para mover la ventana de fechas o el organismo.
    Es como se parametriza un dashboard por cliente sin duplicar definiciones.
    """
    return _run(T.mp_query_run, _store, _cuota, ref, sobrescribir)


@mcp.tool()
def mp_query_list(etiqueta: str | None = None) -> str:
    """Lista las consultas guardadas disponibles, opcionalmente por etiqueta.

    Empieza por aca antes de construir una consulta desde cero: la biblioteca
    trae las preguntas canonicas ya resueltas y parametrizadas, y usarlas cuesta
    una llamada en vez de varias.
    """
    return _run(T.mp_query_list, _store, etiqueta)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
