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

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from . import tools as T
from .config import Config, scrub
from .quota import Cuota
from .store import Store

mcp = MCPServer(
    name="mercado-publico",
    title="Mercado Publico (Chile) - acceso a datos",
    version="0.1.0",
    instructions=(
        "Datos de compras publicas chilenas. Llama mp_schema_describe antes de "
        "construir cualquier consulta: los nombres de campo no se adivinan. "
        "Cada respuesta trae meta.procedencia, meta.as_of y meta.limitaciones; "
        "no afirmes nada que el payload no sostenga."
    ),
)

# Todas las herramientas son de solo lectura y no destructivas salvo las de
# consultas guardadas, que son idempotentes. Declararlo explicitamente permite
# al cliente decidir si necesita confirmacion humana.
_RO = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
_RW = ToolAnnotations(readOnlyHint=False, destructiveHint=False,
                      idempotentHint=True, openWorldHint=False)

# El servidor NO escribe los hechos. MP_MCP_ESCRITURA=1 solo para desarrollo
# local con un unico proceso.
#
# CAMBIO DE POSTURA 2026-08-12: hasta hoy este servidor no salia a la red, y esa
# propiedad estaba documentada como control de seguridad ("el MCP no expone
# ninguna herramienta que haga HTTP"). Ya no es cierta: las tools *_vivo llaman a
# ChileCompra. El cambio se toma a conciencia y se acota asi:
#   - el cliente HTTP se crea PEREZOSAMENTE, al primer uso de una tool viva;
#   - un despliegue sin proxy ni ticket sigue sirviendo el snapshot sin romperse,
#     y solo esas tools fallan, con un error legible;
#   - todo hit pasa por Cuota, que aplica el techo diario;
#   - el ticket sigue viviendo en Agent Vault: el proceso nunca lo conoce.
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

@mcp.tool(annotations=_RO)
def mp_schema_describe(entity: str | None = None,
                       incluir_limitaciones: bool = True) -> str:
    """Devuelve que se puede consultar: entidades, dimensiones, medidas y sus
    limitaciones conocidas.

    LLAMA ESTO PRIMERO sin argumentos: devuelve el INDICE de entidades con su
    grano, su estado de carga y sus limitaciones. El indice NO trae nombres de
    campo.

    Despues, para la entidad que vas a consultar, llama de nuevo con
    entity='<nombre>': ahi vienen los nombres exactos de dimensiones y medidas.
    Los nombres de campo no se adivinan; si un campo no aparece en el detalle de
    SU entidad, no existe y la consulta va a fallar.

    Cada entidad declara su 'grano' (que representa una fila). Respetarlo es
    critico: sumar un monto de cabecera sobre 'licitacion_item' lo multiplica por
    el numero de lineas de la licitacion.

    Cada limitacion trae 'verificado_por'. Si dice NO VERIFICADO, no la uses como
    afirmacion en un entregable a cliente.

    entity: opcional, para describir una sola entidad y ahorrar contexto.
    """
    return _run(T.mp_schema_describe, _store, entity, incluir_limitaciones)


@mcp.tool(annotations=_RO)
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


@mcp.tool(annotations=_RO)
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


@mcp.tool(annotations=_RO)
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


@mcp.tool(annotations=_RO)
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


@mcp.tool(annotations=_RW)
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


@mcp.tool(annotations=_RW)
def mp_query_run(ref: str, sobrescribir: dict | None = None) -> str:
    """Re-ejecuta una consulta guardada por nombre o query_id.

    'sobrescribir' permite cambiar partes de la consulta sin reconstruirla, por
    ejemplo {"filters": [...]} para mover la ventana de fechas o el organismo.
    Es como se parametriza un dashboard por cliente sin duplicar definiciones.
    """
    return _run(T.mp_query_run, _store, _cuota, ref, sobrescribir)


@mcp.tool(annotations=_RO)
def mp_query_list(etiqueta: str | None = None) -> str:
    """Lista las consultas guardadas disponibles, opcionalmente por etiqueta.

    Empieza por aca antes de construir una consulta desde cero: la biblioteca
    trae las preguntas canonicas ya resueltas y parametrizadas, y usarlas cuesta
    una llamada en vez de varias.
    """
    return _run(T.mp_query_list, _store, etiqueta)


def main() -> None:
    """Arranca el servidor.

    MP_TRANSPORT=stdio            (defecto) subproceso del cliente
    MP_TRANSPORT=streamable-http  servicio HTTP independiente

    El modo HTTP existe por una razon de aislamiento, no de comodidad: un
    servidor stdio es un SUBPROCESO del agente y hereda su entorno. Medido en
    D-012 de este proyecto: Hermes inyecta /opt/data/.env a sus hijos MCP, asi
    que un MCP stdio recibe el token de Agent Vault del agente y puede hablar
    por el proxy haciendose pasar por el. Si el MCP se trata como componente de
    tercero, no puede correr como hijo stdio.
    """
    transporte = os.environ.get("MP_TRANSPORT", "stdio")
    if transporte == "stdio":
        mcp.run(transport="stdio")
        return
    import uvicorn
    host = os.environ.get("MP_HTTP_HOST", "127.0.0.1")
    port = int(os.environ.get("MP_HTTP_PORT", "8756"))
    app = mcp.streamable_http_app(host=host)
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()


# --------------------------------------------------------------------------
# Tools EN VIVO. Ver la nota de "CAMBIO DE POSTURA" arriba antes de agregar mas.
# --------------------------------------------------------------------------

_api_cliente = None


def _api():
    """Cliente HTTP creado al primer uso de una tool viva (perezoso a proposito)."""
    global _api_cliente
    if _api_cliente is None:
        from .api import ClienteAPI
        _api_cliente = ClienteAPI(_cfg, _cuota)
    return _api_cliente


@mcp.tool(annotations=_RO)
def mp_licitacion_vivo(codigo: str) -> str:
    """Detalle de una licitacion consultando ChileCompra EN VIVO.

    Usala cuando el codigo no esta en el almacen local (mp_get devolvio vacio) o
    cuando necesitas el estado de HOY de algo que el snapshot tiene viejo. El
    almacen es una foto con fecha; esto no.

    Cuesta 1 hit del techo diario de 10.000. Si vas a mirar muchas licitaciones,
    consulta antes mp_cuota_estado.

    Los nombres de campo son los de ChileCompra (CodigoExterno, MontoUnitario,
    RutProveedor), NO los del almacen. No los mezcles con salidas de mp_search o
    mp_aggregate sin normalizar primero.
    """
    return _run(T.mp_licitacion_vivo, _api(), _cuota, codigo)


@mcp.tool(annotations=_RO)
def mp_oc_vivo(codigo: str) -> str:
    """Detalle de una ORDEN DE COMPRA consultando ChileCompra en vivo.

    Es la unica via para ver totales, items y proveedor de una OC: el almacen
    local todavia no tiene esa tabla poblada. Codigo con formato 2097-241-SE14.

    Cuesta 1 hit. Sirve para responder "cuanto se pago finalmente" de un caso
    puntual, no para benchmarks: para eso hace falta la ingesta masiva.
    """
    return _run(T.mp_oc_vivo, _api(), _cuota, codigo)


@mcp.tool(annotations=_RO)
def mp_licitaciones_vivo(fecha: str | None = None, estado: str | None = None,
                         codigo_organismo: str | None = None,
                         codigo_proveedor: str | None = None,
                         limite: int | None = None,
                         volcar: bool = False) -> str:
    """BUSCA licitaciones EN VIVO en todo Chile. Los filtros son combinables.

    Es la contraparte de mp_search: mp_search consulta el snapshot (rapido, con
    todos los campos, pero con fecha de corte) y esto consulta a ChileCompra hoy.

    - `fecha`: un dia, formato ddmmaaaa (28072026). SIN fecha, la API responde
      sobre el dia corriente.
    - `estado`: activas, publicada, cerrada, adjudicada, desierta, revocada,
      suspendida. Los siete verificados 2026-08-12.
    - `codigo_organismo`: codigo del comprador (mp_comprador_catalogo lo da).
    - `codigo_proveedor`: CODIGO de empresa, no RUT (mp_proveedor_resolver).
    - `limite`: cuantas devolver, por omision 25, tope 200.
    - `volcar`: guarda el listado COMPLETO en el almacen y devuelve solo el
      conteo. Es la via para trabajar un universo grande sin truncar nada:
      despues se pagina y filtra con mp_volcado_leer, que no gasta cuota.

    Hace falta al menos un filtro. Cuesta 1 hit sea cual sea el tamano del
    resultado. El listado trae solo 4 campos por licitacion: para monto,
    comprador o adjudicacion hay que pedir mp_licitacion_vivo por codigo.

    Ejemplo util: fecha='28072026', estado='adjudicada' devolvio 259 -- asi se
    encuentra que se adjudico un dia sin ingerir nada.
    """
    return _run(T.mp_licitaciones_vivo, _api(), _cuota, fecha, estado,
                codigo_organismo, codigo_proveedor, limite, volcar, _store)


@mcp.tool(annotations=_RO)
def mp_ordenes_vivo(fecha: str | None = None, estado: str | None = None,
                    codigo_organismo: str | None = None,
                    codigo_proveedor: str | None = None,
                    limite: int | None = None,
                    volcar: bool = False) -> str:
    """BUSCA ordenes de compra EN VIVO. Los filtros son combinables.

    Responde "que le compro el Estado a esta empresa" y "que compro este
    organismo", que el almacen local no puede: su tabla de OC esta vacia.

    - `fecha`: ddmmaaaa. `[MEDIDO]` un dia cualquiera son ~12.000 OC.
    - `estado`: enviadaproveedor, aceptada, cancelada, recepcionconforme, todos.
      Los cinco verificados 2026-08-12.
    - `codigo_organismo` / `codigo_proveedor`: codigos, no nombres ni RUT.
    - `limite`: por omision 25, tope 200.
    - `volcar`: guarda las ~12.000 OC del dia completas en el almacen y
      devuelve solo el conteo. Despues, mp_volcado_leer sin gastar cuota.

    Hace falta al menos un filtro. 1 hit por llamada. El listado trae solo
    Codigo, Nombre y CodigoEstado -- para totales, items y proveedor hay que
    pedir mp_oc_vivo por codigo.

    Encadenado tipico: mp_proveedor_resolver(rut) -> codigo_proveedor aca ->
    mp_oc_vivo del codigo que interese.
    """
    return _run(T.mp_ordenes_vivo, _api(), _cuota, fecha, estado,
                codigo_organismo, codigo_proveedor, limite, volcar, _store)


@mcp.tool(annotations=_RO)
def mp_proveedor_resolver(rut: str) -> str:
    """Resuelve un RUT de proveedor a su codigo y nombre de empresa.

    ChileCompra identifica proveedores por un codigo interno, no por RUT. Si el
    usuario te da un RUT y necesitas cruzarlo, esta es la traduccion. Cuesta 1 hit.

    El RUT se normaliza aca: da igual que escribas 96756540-7, 96.756.540-7 o
    96756540. `[MEDIDO]` el endpoint solo acepta la forma con puntos y guion, y
    sin normalizar las otras devuelven vacio.

    `encontrado: false` significa que la empresa no esta inscrita como proveedor
    del Estado, no que no exista.
    """
    return _run(T.mp_proveedor_resolver, _api(), _cuota, rut)


@mcp.tool(annotations=_RO)
def mp_comprador_catalogo() -> str:
    """Catalogo COMPLETO de organismos compradores del Estado. Un solo hit.

    El almacen local solo conoce los organismos que aparecieron en las
    licitaciones ingeridas. Esto trae todos, sirve para resolver nombres que no
    estan en el snapshot y para ofrecer opciones cuando el usuario nombra un
    organismo de forma ambigua.
    """
    return _run(T.mp_comprador_catalogo, _api(), _cuota)


@mcp.tool(annotations=_RW)
def mp_volcado_leer(tipo: str, filtros: str | None = None,
                    texto: str | None = None,
                    codigo_estado: int | None = None,
                    limite: int = 50, offset: int = 0) -> str:
    """Lee un listado que ya fue volcado con volcar=true. NO consume cuota.

    Es la forma de recorrer un universo grande sin truncarlo: se volca una vez
    (1 hit) y despues se pagina y filtra cuantas veces haga falta, gratis.

    - `tipo`: "licitaciones" u "ordenes".
    - `filtros`: cual de los volcados de ese tipo leer, tal cual aparece en
      `otros_volcados`. Si lo omitis se lee el MAS RECIENTE. Importa cuando
      hay varios: los numeros son de UN volcado, no de la suma.
    - `texto`: subcadena en el nombre, sin distinguir mayusculas.
    - `codigo_estado`: el numerico que trae el listado de la API.
    - `limite` / `offset`: paginacion, tope 200 por pagina.

    Es una FOTO del momento del volcado, no el estado de ahora, y trae solo los
    campos del listado: ni monto, ni comprador, ni proveedor.
    """
    return _run(T.mp_volcado_leer, _store, tipo, filtros, texto,
                codigo_estado, limite, offset)


@mcp.tool(annotations=_RO)
def mp_precio_perdido(proveedor: str, desde: str | None = None,
                      hasta: str | None = None, limite: int = 25) -> str:
    """A que precio gano el competidor en las lineas que este proveedor PERDIO.

    Es la pregunta comercial que no se puede responder navegando mercadopublico.cl,
    porque no hay pantalla que agregue oferta perdedora contra oferta ganadora.

    - `proveedor`: RUT (mejor) o parte del nombre. El RUT evita mezclar empresas
      con prefijo parecido.
    - `desde` / `hasta`: fechas AAAA-MM-DD sobre la fecha de envio de la oferta.
    - `limite`: filas de detalle, por omision 25, tope 200.

    Devuelve el detalle linea por linea ordenado por plata dejada en la mesa, mas
    un `resumen` con el sobreprecio mediano y cuantas lineas NO se pudieron
    comparar. Si `lineas_sin_comparar` es alto respecto de `lineas_comparadas`, el
    resultado es de una parte y hay que decirlo.

    La comparacion es dentro de la MISMA linea de la MISMA licitacion: es la unica
    valida. No la presentes como "precio de mercado" del producto.
    """
    return _run(T.mp_precio_perdido, _store, proveedor, desde, hasta, limite)


@mcp.tool(annotations=_RO)
def mp_sin_competencia(proveedor: str | None = None, unspsc: str | None = None,
                       organismo: str | None = None, desde: str | None = None,
                       hasta: str | None = None, limite: int = 25) -> str:
    """Licitaciones donde hubo UN SOLO oferente: donde se entra sin pelear precio.

    Con `proveedor`, acota a su huella real -- los UNSPSC donde el ya oferta -- y
    saca las licitaciones donde el unico oferente fue el mismo, que ya son suyas.

    Sin filtros devuelve el universo, que es grande: [MEDIDO 2026-08-19] 1.657 de
    7.273 licitaciones de un mes tuvieron un solo oferente, el 22,8%.

    Un solo oferente NO garantiza adjudicacion: el organismo puede declararla
    desierta igual.
    """
    return _run(T.mp_sin_competencia, _store, proveedor, unspsc, organismo,
                desde, hasta, limite)


@mcp.tool(annotations=_RO)
def mp_huella(proveedor: str, limite: int = 20) -> str:
    """Donde compite un proveedor: sus UNSPSC, con volumen y tasa de exito.

    Sirve para responder "en que soy fuerte y en que no" y para acotar cualquier
    otro analisis a su rubro real, en vez de a una clasificacion declarada.

    `tasa_exito_pct` es sobre LINEAS ofertadas, no sobre licitaciones ganadas.
    """
    return _run(T.mp_huella, _store, proveedor, limite)


@mcp.tool(annotations=_RO)
def mp_comprador_perfil(organismo: str, limite: int = 15) -> str:
    """Que compra un organismo, a quien y por cuanto.

    Responde "quien le vende hoy a este comprador" -- el punto de partida de
    cualquier estrategia de entrada. El monto ya excluye las ordenes que
    ChileCompra saca de sus cifras oficiales por error de monto o de moneda.
    """
    return _run(T.mp_comprador_perfil, _store, organismo, limite)


@mcp.tool(annotations=_RO)
def mp_cuota_estado() -> str:
    """Cuanto queda del techo diario de ChileCompra. NO consume cuota.

    Consultala antes de una tanda de llamadas vivas. El contador es local: mide
    lo que gasto este almacen, asi que si otro cliente comparte el ticket el
    consumo real es mayor.
    """
    return _run(T.mp_cuota_estado, _cuota)
