"""Capa semantica: la unica fuente de verdad sobre que se puede consultar.

Tres propiedades de diseno, en orden de importancia:

1. `mp_schema_describe` y el validador de consultas leen ESTE registro. No hay
   dos definiciones que puedan divergir.
2. Las limitaciones viajan con el esquema. Cada entidad y cada medida lleva sus
   caveats, y `mp_schema_describe` los devuelve. La limitacion llega al agente
   en el momento de consultar, no solo en un documento que nadie abre.
3. Cada caveat declara con que sonda se verifico. `sonda=None` significa NO
   VERIFICADO, y eso aparece asi en la respuesta.

Ningun identificador SQL viene de entrada del usuario: todo sale de aca. Esa es
la razon por la que la gramatica generica no abre superficie de inyeccion.
"""

from __future__ import annotations

from dataclasses import dataclass, field

FNS_NUMERICAS = (
    "count", "count_distinct", "sum", "avg", "min", "max",
    "p10", "p25", "p50", "p75", "p90", "stddev",
)
FNS_CONTEO = ("count", "count_distinct")

GRANOS_TEMPORALES = ("dia", "semana", "mes", "trimestre", "ano")

OPS = (
    "eq", "ne", "in", "not_in", "gt", "gte", "lt", "lte",
    "between", "contains", "starts_with", "is_null", "not_null",
)


@dataclass(frozen=True)
class Caveat:
    """Una limitacion conocida. `sonda` la ata a una verificacion medible."""
    texto: str
    sonda: str | None = None          # id en probes/probes.yaml, o None
    severidad: str = "media"          # baja | media | alta

    def as_dict(self) -> dict:
        return {
            "limitacion": self.texto,
            "severidad": self.severidad,
            "verificado_por": self.sonda or "NO VERIFICADO",
        }


@dataclass(frozen=True)
class Dim:
    name: str
    label: str
    sql: str
    kind: str                          # categorical | temporal | code | text
    vocabulario: str | None = None     # para mp_codes_search
    nota: str = ""


@dataclass(frozen=True)
class Measure:
    name: str
    label: str
    sql: str
    fns: tuple[str, ...]
    unidad: str                        # CLP | conteo | dias | unidad_medida | ratio
    caveats: tuple[Caveat, ...] = ()
    nota: str = ""


@dataclass(frozen=True)
class Attr:
    """Campo de solo despliegue: sirve en mp_search, no en group_by ni measures."""
    name: str
    label: str
    sql: str


@dataclass(frozen=True)
class Entity:
    name: str
    label: str
    table: str
    grano: str                         # que es UNA fila. Critico para no doble-contar.
    procedencia: str                   # ocds-bulk | oc-csv-procesado | api-live | derivado
    dims: dict[str, Dim] = field(default_factory=dict)
    measures: dict[str, Measure] = field(default_factory=dict)
    attrs: dict[str, Attr] = field(default_factory=dict)
    caveats: tuple[Caveat, ...] = ()

    def campo(self, name: str) -> Dim | Measure | Attr | None:
        return self.dims.get(name) or self.measures.get(name) or self.attrs.get(name)


def _d(name, label, sql, kind="categorical", vocabulario=None, nota=""):
    return Dim(name, label, sql, kind, vocabulario, nota)


def _m(name, label, sql, fns=FNS_NUMERICAS, unidad="conteo", caveats=(), nota=""):
    return Measure(name, label, sql, fns, unidad, caveats, nota)


def _idx(items):
    return {i.name: i for i in items}


# --------------------------------------------------------------------------
# Caveats compartidos
# --------------------------------------------------------------------------

CAV_UNSPSC_V7 = Caveat(
    "Los codigos son UNSPSC version 7, obsoleta respecto de las versiones "
    "vigentes. Cualquier cruce con CPV (UE) o NAICS/PSC (EEUU) requiere una "
    "tabla de equivalencia mantenida por nosotros.",
    sonda="P-08", severidad="media",
)
CAV_UNSPSC_DIGITADO = Caveat(
    "El codigo lo digita el organismo comprador. MEDIDO 2026-07-31 sobre 980 "
    "items de una muestra de 200 licitaciones abiertas: 0 % sin codigo, 100 % con "
    "8 digitos, solo 1,02 % usando el generico de clase. La calidad de "
    "codificacion es ALTA y el supuesto contrario quedo refutado en la muestra. "
    "Sigue valiendo combinar con texto, pero como refinamiento, no como parche a "
    "un dato roto. Pendiente: la muestra es del stock abierto de hoy, no del "
    "historico.",
    sonda="P-08", severidad="baja",
)
CAV_MONEDA = Caveat(
    "Los montos vienen en CLP, CLF (UF), USD, UTM o EUR. La conversion a CLP "
    "depende de la tabla tipo_cambio a la fecha del documento. Si falta el "
    "tipo de cambio, el valor en CLP queda NULL y la fila se cuenta en "
    "coverage.excluidas_sin_conversion; NO se imputa.",
    sonda="P-11", severidad="alta",
)
CAV_OC_PROCESADO = Caveat(
    "Las transacciones atipicas NO vienen removidas: es lo contrario de lo que "
    "decia esta salvedad. [MEDIDO 2026-08-19] la descarga masiva trae TODAS las "
    "ordenes, incluidas las que ChileCompra excluye de sus cifras oficiales por "
    "error de monto o de tipo de moneda -- y sin filtrarlas, la I. Municipalidad "
    "de Rio Bueno aparecia tercera en el gasto nacional por una orden de arriendo "
    "de vehiculos cargada como 5.294.933 UF. Este almacen carga las dos listas de "
    "exclusion que ChileCompra publica (14.443 codigos) y deja `total_clp` en NULL "
    "en esas filas, marcadas con `excluida_por_fuente`. Asi que sumar total_clp ya "
    "es correcto; lo que NO se puede es sumar `total`, que conserva el monto "
    "original en su moneda. La version anterior de esta salvedad tambien mandaba a "
    "usar adjudicacion_item 'derivada de OCDS': hoy viene del bulk lic-da.",
    sonda="P-10", severidad="alta",
)
CAV_OC_LATENCIA = Caveat(
    "Rezago de UN DIA, no de 50. [MEDIDO 2026-08-19] la descarga masiva se "
    "regenera todos los dias entre las 12:00 y las 14:00 y el dato mas reciente "
    "del almacen es de ayer. Corrige la version anterior de esta salvedad, que "
    "hablaba de 'hasta ~50 dias' porque se escribio cuando la unica via conocida "
    "era el reporte MENSUAL (publicado el dia 20 del mes siguiente). Ese reporte "
    "existe y sigue teniendo ese rezago, pero no es lo que carga este almacen. "
    "Para el dia EN CURSO igual hace falta la API: mp_ordenes_vivo.",
    sonda="P-14", severidad="baja",
)
CAV_MONTO_ESTIMADO = Caveat(
    "MontoEstimado solo es publico si VisibilidadMonto=1, y Estimacion=3 "
    "significa 'monto no es posible de estimar'. Agregar montos estimados sin "
    "filtrar por visibilidad subestima el total de forma silenciosa: la medida "
    "reporta n_con_monto y n_sin_monto.",
    sonda="P-12", severidad="alta",
)
CAV_ADJ_MULTIPROVEEDOR = Caveat(
    "El registro OCP documenta que hay adjudicaciones con multiples proveedores "
    "asociadas a un unico contrato, lo que impide atribuir valor por proveedor "
    "en esos casos. Afecta la exactitud de monto_adjudicado por proveedor.",
    sonda="P-07", severidad="media",
)


# --------------------------------------------------------------------------
# Dimensiones reutilizadas
# --------------------------------------------------------------------------

def _dims_unspsc(prefijo: str = "") -> list[Dim]:
    p = prefijo
    return [
        _d("unspsc_commodity", "UNSPSC commodity (8 digitos)", f"{p}unspsc_commodity",
           kind="code", vocabulario="unspsc_commodity"),
        _d("unspsc_clase", "UNSPSC clase (6 digitos)", f"{p}unspsc_clase",
           kind="code", vocabulario="unspsc_clase"),
        _d("unspsc_familia", "UNSPSC familia (4 digitos)", f"{p}unspsc_familia",
           kind="code", vocabulario="unspsc_familia",
           nota="Nivel recomendado para matching de mercado."),
        _d("unspsc_segmento", "UNSPSC segmento (2 digitos)", f"{p}unspsc_segmento",
           kind="code", vocabulario="unspsc_segmento"),
    ]


def _dims_comprador(p: str = "") -> list[Dim]:
    return [
        _d("organismo", "Organismo comprador", f"{p}organismo_nombre",
           vocabulario="organismo"),
        _d("organismo_codigo", "Codigo del organismo", f"{p}organismo_codigo", kind="code"),
        _d("region_comprador", "Region de la unidad compradora", f"{p}region_comprador",
           vocabulario="region"),
        _d("comuna_comprador", "Comuna de la unidad compradora", f"{p}comuna_comprador",
           vocabulario="comuna"),
    ]


def _dims_proveedor(p: str = "") -> list[Dim]:
    return [
        _d("rut_proveedor", "RUT del proveedor", f"{p}rut_proveedor", kind="code",
           vocabulario="proveedor"),
        _d("nombre_proveedor", "Nombre del proveedor", f"{p}nombre_proveedor"),
    ]


# --------------------------------------------------------------------------
# Entidades
# --------------------------------------------------------------------------

LICITACION = Entity(
    name="licitacion",
    label="Licitacion (cabecera)",
    table="licitacion",
    grano="Una fila = una licitacion.",
    procedencia="ocds-bulk + api-live",
    dims=_idx([
        _d("codigo", "Codigo externo de la licitacion", "codigo", kind="code"),
        _d("estado", "Estado", "estado",
           nota="publicada, cerrada, desierta, adjudicada, revocada, suspendida"),
        _d("tipo", "Tipo por tramo UTM", "tipo",
           nota="L1<100 UTM, LE 100-1000, LP 1000-2000, LQ 2000-5000, LR>=5000, "
                "LS servicios personales; E2/CO/B2/H2/I2 son los privados"),
        _d("moneda", "Moneda declarada", "moneda"),
        _d("es_obra", "Es licitacion de obra", "es_obra"),
        _d("toma_razon", "Requiere toma de razon de Contraloria", "toma_razon"),
        _d("subcontratacion", "Permite subcontratacion", "subcontratacion"),
        _d("extension_plazo", "Extension automatica art. 25", "extension_plazo"),
        _d("visibilidad_monto", "Monto estimado publico", "visibilidad_monto"),
        _d("fecha_publicacion", "Fecha de publicacion", "fecha_publicacion", kind="temporal"),
        _d("fecha_cierre", "Fecha de cierre de ofertas", "fecha_cierre", kind="temporal"),
        _d("fecha_adjudicacion", "Fecha de adjudicacion", "fecha_adjudicacion", kind="temporal"),
        *_dims_comprador(),
    ]),
    measures=_idx([
        _m("n_licitaciones", "Cantidad de licitaciones", "codigo", FNS_CONTEO, "conteo"),
        _m("monto_estimado_clp", "Monto estimado en CLP", "monto_estimado_clp",
           FNS_NUMERICAS, "CLP", caveats=(CAV_MONTO_ESTIMADO, CAV_MONEDA)),
        _m("dias_publicacion_cierre", "Dias entre publicacion y cierre",
           "date_diff('day', fecha_publicacion, fecha_cierre)", FNS_NUMERICAS, "dias"),
        _m("dias_cierre_adjudicacion", "Dias entre cierre y adjudicacion",
           "date_diff('day', fecha_cierre, fecha_adjudicacion)", FNS_NUMERICAS, "dias"),
        _m("n_oferentes_adjudicados", "Proveedores adjudicados",
           "n_oferentes", FNS_NUMERICAS, "conteo"),
        _m("cantidad_reclamos", "Reclamos recibidos", "cantidad_reclamos",
           FNS_NUMERICAS, "conteo"),
    ]),
    attrs=_idx([
        Attr("nombre", "Nombre de la licitacion", "nombre"),
        Attr("descripcion", "Descripcion / objeto de la contratacion", "descripcion"),
        Attr("url_ficha", "URL de la ficha en mercadopublico.cl", "url_ficha"),
        Attr("url_acta", "URL del acta de adjudicacion", "url_acta"),
    ]),
    caveats=(
        Caveat("El endpoint de listado de la API solo devuelve CodigoExterno, Nombre, "
               "CodigoEstado y FechaCierre. Todo el resto de esta entidad proviene del "
               "detalle (?codigo=) o del bulk OCDS.", sonda="P-01", severidad="alta"),
    ),
)

LICITACION_ITEM = Entity(
    name="licitacion_item",
    label="Linea de licitacion (item solicitado)",
    table="licitacion_item",
    grano="Una fila = una linea de una licitacion. OJO: sumar montos de cabecera "
          "sobre esta entidad los multiplica por el numero de lineas.",
    procedencia="ocds-bulk + api-live",
    dims=_idx([
        _d("codigo_licitacion", "Codigo de la licitacion", "codigo_licitacion", kind="code"),
        _d("unidad_medida", "Unidad de medida", "unidad_medida"),
        _d("estado_licitacion", "Estado de la licitacion", "estado"),
        _d("fecha_publicacion", "Fecha de publicacion", "fecha_publicacion", kind="temporal"),
        _d("fecha_cierre", "Fecha de cierre", "fecha_cierre", kind="temporal"),
        *_dims_unspsc(),
        *_dims_comprador(),
    ]),
    measures=_idx([
        _m("n_items", "Cantidad de lineas", "item_id", FNS_CONTEO, "conteo"),
        _m("n_licitaciones", "Licitaciones distintas", "codigo_licitacion",
           ("count_distinct",), "conteo"),
        _m("cantidad", "Cantidad solicitada", "cantidad", FNS_NUMERICAS, "unidad_medida",
           caveats=(Caveat("La cantidad solo es comparable dentro de una misma "
                           "unidad_medida. Agregar cantidades de unidades distintas "
                           "produce un numero sin significado: agrupar siempre por "
                           "unidad_medida.", sonda=None, severidad="alta"),)),
    ]),
    attrs=_idx([
        Attr("nombre_producto", "Nombre del producto o servicio", "nombre_producto"),
        Attr("descripcion", "Descripcion del item", "descripcion"),
        Attr("categoria_texto", "Ruta de categoria UNSPSC en texto", "categoria_texto"),
    ]),
    caveats=(CAV_UNSPSC_V7, CAV_UNSPSC_DIGITADO),
)

ADJUDICACION_ITEM = Entity(
    name="adjudicacion_item",
    label="Linea adjudicada (fuente de precios)",
    table="adjudicacion_item",
    grano="Una fila = una linea adjudicada a un proveedor.",
    procedencia="ocds-bulk",
    dims=_idx([
        _d("codigo_licitacion", "Codigo de la licitacion", "codigo_licitacion", kind="code"),
        _d("unidad_medida", "Unidad de medida", "unidad_medida"),
        _d("moneda", "Moneda original", "moneda"),
        _d("fecha_adjudicacion", "Fecha de adjudicacion", "fecha_adjudicacion", kind="temporal"),
        _d("tipo_licitacion", "Tipo por tramo UTM", "tipo"),
        *_dims_unspsc(),
        *_dims_proveedor(),
        *_dims_comprador(),
    ]),
    measures=_idx([
        _m("n_adjudicaciones", "Lineas adjudicadas", "adjudicacion_id", FNS_CONTEO, "conteo"),
        _m("precio_unitario_clp", "Precio unitario adjudicado en CLP",
           "precio_unitario_clp", FNS_NUMERICAS, "CLP",
           caveats=(CAV_MONEDA,),
           nota="Medida central del benchmark. Comparable solo dentro de un mismo "
                "unspsc_commodity + unidad_medida."),
        _m("cantidad_adjudicada", "Cantidad adjudicada", "cantidad_adjudicada",
           FNS_NUMERICAS, "unidad_medida"),
        _m("monto_linea_clp", "Monto de la linea en CLP",
           "precio_unitario_clp * cantidad_adjudicada", FNS_NUMERICAS, "CLP",
           caveats=(CAV_MONEDA, CAV_ADJ_MULTIPROVEEDOR)),
        _m("n_proveedores", "Proveedores distintos", "rut_proveedor",
           ("count_distinct",), "conteo"),
    ]),
    attrs=_idx([
        Attr("nombre_producto", "Nombre del producto o servicio", "nombre_producto"),
    ]),
    caveats=(CAV_UNSPSC_V7, CAV_UNSPSC_DIGITADO, CAV_MONEDA, CAV_ADJ_MULTIPROVEEDOR),
)

ORDEN_COMPRA = Entity(
    name="orden_compra",
    label="Orden de compra (cabecera)",
    table="orden_compra",
    grano="Una fila = una orden de compra.",
    procedencia="oc-csv-procesado",
    dims=_idx([
        _d("excluida_por_fuente", "Excluida de las cifras oficiales", "excluida_por_fuente",
           nota="true = ChileCompra la excluye por error de monto o de moneda"),
        _d("codigo", "Codigo de la OC", "codigo", kind="code"),
        _d("codigo_licitacion", "Licitacion asociada", "codigo_licitacion", kind="code",
           nota="Vacio en compras sin licitacion (Convenio Marco, Compra Agil, trato directo)"),
        _d("estado", "Estado de la OC", "estado"),
        _d("tipo", "Tipo de OC", "tipo", nota="SE = sin emision automatica, CM = convenio marco"),
        _d("moneda", "Moneda", "moneda"),
        _d("forma_pago", "Forma de pago", "forma_pago"),
        _d("tipo_despacho", "Tipo de despacho", "tipo_despacho"),
        _d("fecha_envio", "Fecha de envio al proveedor", "fecha_envio", kind="temporal"),
        _d("fecha_aceptacion", "Fecha de aceptacion", "fecha_aceptacion", kind="temporal"),
        *_dims_proveedor(),
        *_dims_comprador(),
    ]),
    measures=_idx([
        _m("n_ordenes", "Cantidad de ordenes", "codigo", FNS_CONTEO, "conteo"),
        _m("total_clp", "Total de la OC en CLP", "total_clp", FNS_NUMERICAS, "CLP",
           caveats=(Caveat(
            "`total_clp` YA EXCLUYE las ordenes que ChileCompra saca de sus cifras "
            "oficiales por error de monto o de tipo de moneda: en esas filas es NULL "
            "y `excluida_por_fuente` es true. No es cosmetico. [MEDIDO 2026-08-19] "
            "158 ordenes de 1,7 millones -- el 0,009% -- distorsionaban el ranking "
            "de gasto entero: la I. Municipalidad de Rio Bueno aparecia tercera con "
            "$1.053 mil millones por un arriendo de vehiculos cargado como 5.294.933 "
            "UF (el monto en pesos dentro de una orden denominada en UF); su cifra "
            "real es $11,4 mil millones. El Ministerio de Obras Publicas caia de "
            "$2.134 a $464 mil millones. Si necesitas el universo crudo, filtra por "
            "`excluida_por_fuente` y usa `total`, que conserva el monto original en "
            "su moneda -- pero NO lo sumes entre monedas.",
            sonda=None, severidad="alta"),
        CAV_OC_PROCESADO, CAV_MONEDA)),
        _m("total_neto_clp", "Total neto en CLP", "total_neto_clp", FNS_NUMERICAS, "CLP",
           caveats=(CAV_OC_PROCESADO, CAV_MONEDA)),
        _m("promedio_calificacion", "Calificacion promedio del proveedor",
           "promedio_calificacion", FNS_NUMERICAS, "ratio"),
    ]),
    attrs=_idx([
        Attr("nombre", "Nombre de la OC", "nombre"),
        Attr("descripcion", "Descripcion", "descripcion"),
    ]),
    caveats=(CAV_OC_PROCESADO, CAV_OC_LATENCIA),
)

ORDEN_COMPRA_ITEM = Entity(
    name="orden_compra_item",
    label="Linea de orden de compra",
    table="orden_compra_item",
    grano="Una fila = una linea de una OC.",
    procedencia="oc-csv-procesado",
    dims=_idx([
        _d("excluida_por_fuente", "Excluida de las cifras oficiales", "excluida_por_fuente",
           nota="true = ChileCompra la excluye por error de monto o de moneda"),
        _d("codigo_oc", "Codigo de la OC", "codigo_oc", kind="code"),
        _d("moneda", "Moneda", "moneda"),
        _d("fecha_envio", "Fecha de envio", "fecha_envio", kind="temporal"),
        *_dims_unspsc(),
        *_dims_proveedor(),
        *_dims_comprador(),
    ]),
    measures=_idx([
        _m("n_items", "Cantidad de lineas", "item_id", FNS_CONTEO, "conteo"),
        _m("precio_neto_clp", "Precio unitario neto en CLP", "precio_neto_clp",
           FNS_NUMERICAS, "CLP", caveats=(Caveat(
            "`total_clp` YA EXCLUYE las ordenes que ChileCompra saca de sus cifras "
            "oficiales por error de monto o de tipo de moneda: en esas filas es NULL "
            "y `excluida_por_fuente` es true. No es cosmetico. [MEDIDO 2026-08-19] "
            "158 ordenes de 1,7 millones -- el 0,009% -- distorsionaban el ranking "
            "de gasto entero: la I. Municipalidad de Rio Bueno aparecia tercera con "
            "$1.053 mil millones por un arriendo de vehiculos cargado como 5.294.933 "
            "UF (el monto en pesos dentro de una orden denominada en UF); su cifra "
            "real es $11,4 mil millones. El Ministerio de Obras Publicas caia de "
            "$2.134 a $464 mil millones. Si necesitas el universo crudo, filtra por "
            "`excluida_por_fuente` y usa `total`, que conserva el monto original en "
            "su moneda -- pero NO lo sumes entre monedas.",
            sonda=None, severidad="alta"),
        CAV_OC_PROCESADO, CAV_MONEDA)),
        _m("cantidad", "Cantidad", "cantidad", FNS_NUMERICAS, "unidad_medida"),
        _m("total_clp", "Total de la linea en CLP", "total_clp", FNS_NUMERICAS, "CLP",
           caveats=(CAV_OC_PROCESADO, CAV_MONEDA)),
    ]),
    attrs=_idx([
        Attr("especificacion_comprador", "Especificacion del comprador",
             "especificacion_comprador"),
    ]),
    caveats=(CAV_OC_PROCESADO, CAV_OC_LATENCIA, CAV_UNSPSC_V7, CAV_UNSPSC_DIGITADO),
)

ORGANISMO = Entity(
    name="organismo",
    label="Organismo comprador",
    table="organismo",
    grano="Una fila = un organismo comprador.",
    procedencia="derivado",
    dims=_idx([
        _d("codigo", "Codigo del organismo", "codigo", kind="code"),
        _d("nombre", "Nombre", "nombre", vocabulario="organismo"),
        _d("region", "Region", "region", vocabulario="region"),
        _d("comuna", "Comuna", "comuna", vocabulario="comuna"),
        _d("actividad", "Actividad", "actividad"),
    ]),
    measures=_idx([
        _m("n_organismos", "Cantidad de organismos", "codigo", FNS_CONTEO, "conteo"),
    ]),
)

PROVEEDOR = Entity(
    name="proveedor",
    label="Proveedor del Estado",
    table="proveedor",
    grano="Una fila = un proveedor (RUT).",
    procedencia="derivado",
    dims=_idx([
        _d("rut", "RUT", "rut", kind="code", vocabulario="proveedor"),
        _d("nombre", "Razon social", "nombre"),
        _d("region", "Region", "region", vocabulario="region"),
        _d("comuna", "Comuna", "comuna", vocabulario="comuna"),
    ]),
    measures=_idx([
        _m("n_proveedores", "Cantidad de proveedores", "rut", FNS_CONTEO, "conteo"),
    ]),
    caveats=(
        Caveat("El tamano de la empresa (micro/pequena/mediana/grande) NO viene en "
               "estas fuentes. Cualquier segmentacion por tamano requiere cruzar con "
               "otra fuente (p.ej. SII) que hoy no esta integrada.",
               sonda=None, severidad="media"),
    ),
)

UNSPSC = Entity(
    name="unspsc",
    label="Vocabulario UNSPSC v7",
    table="unspsc",
    grano="Una fila = un codigo UNSPSC en cualquier nivel.",
    procedencia="derivado",
    dims=_idx([
        _d("codigo", "Codigo", "codigo", kind="code"),
        _d("nivel", "Nivel", "nivel", nota="segmento | familia | clase | commodity"),
        _d("nombre", "Nombre del codigo", "nombre"),
        _d("segmento", "Segmento padre", "segmento", kind="code"),
        _d("familia", "Familia padre", "familia", kind="code"),
        _d("clase", "Clase padre", "clase", kind="code"),
    ]),
    measures=_idx([
        _m("n_codigos", "Cantidad de codigos", "codigo", FNS_CONTEO, "conteo"),
    ]),
    caveats=(CAV_UNSPSC_V7,),
)


OFERTA = Entity(
    name="oferta",
    label="Oferta de un proveedor a una linea de licitacion",
    table="oferta",
    grano="Una fila = una oferta de un proveedor a una linea de una licitacion. "
          "Incluye las que PERDIERON.",
    procedencia="bulk-lic (datos abiertos de ChileCompra, sin ticket)",
    dims=_idx([
        _d("codigo_licitacion", "Licitacion", "codigo_licitacion", kind="code"),
        _d("correlativo_item", "Linea", "correlativo_item", kind="code"),
        _d("unspsc_commodity", "Commodity UNSPSC", "unspsc_commodity", kind="code",
           vocabulario="unspsc_commodity"),
        _d("nombre_linea", "Nombre de la linea", "nombre_linea"),
        _d("rut_proveedor", "RUT del oferente", "rut_proveedor", kind="code",
           vocabulario="proveedor"),
        _d("nombre_proveedor", "Oferente", "nombre_proveedor"),
        _d("seleccionada", "Gano esta linea", "seleccionada",
           nota="true = su oferta fue la seleccionada para esa linea"),
        _d("estado_oferta", "Estado de la oferta", "estado_oferta",
           nota="Aceptada | Rechazada"),
        _d("moneda", "Moneda", "moneda",
           nota="normalizada a CLP/USD/CLF/UTM/EUR; NULL si la fuente decia "
                "'Moneda revisar'"),
        _d("n_oferentes", "Numero de oferentes del proceso", "n_oferentes"),
        _d("criterios", "Criterios de evaluacion", "criterios"),
        _d("organismo_codigo", "Organismo", "organismo_codigo", kind="code",
           vocabulario="organismo"),
        _d("organismo_nombre", "Nombre del organismo", "organismo_nombre"),
        _d("fecha_adjudicacion", "Fecha de adjudicacion", "fecha_adjudicacion",
           kind="temporal",
           nota="PUEDE SER ESTIMADA FUTURA: para ordenar hechos usa fecha_envio_oferta"),
        _d("fecha_envio_oferta", "Fecha de envio de la oferta", "fecha_envio_oferta",
           kind="temporal", nota="la fecha del hecho: la oferta se envio"),
    ]),
    measures=_idx([
        _m("n_ofertas", "Cantidad de ofertas", "oferta_id", FNS_CONTEO, "conteo"),
        _m("n_oferentes_distintos", "Oferentes distintos", "rut_proveedor",
           ("count_distinct",), "conteo"),
        _m("precio_unitario_clp", "Precio unitario ofertado en CLP",
           "precio_unitario_clp", FNS_NUMERICAS, "monto"),
        _m("total_ofertado", "Valor total ofertado", "total_ofertado", FNS_NUMERICAS,
           "monto"),
        _m("monto_adjudicado", "Monto adjudicado de la linea", "monto_adjudicado",
           FNS_NUMERICAS, "monto"),
        _m("cantidad_adjudicada", "Cantidad adjudicada", "cantidad_adjudicada",
           FNS_NUMERICAS, "cantidad"),
    ]),
    caveats=(
        Caveat("Es la unica entidad con las ofertas de los PERDEDORES. [MEDIDO "
               "2026-08-19] sobre un mes: 119.281 ofertas, de las cuales 111.045 no "
               "fueron seleccionadas, con precio unitario y RUT al 100%. Esto NO "
               "viene de la API v1, que no expone ofertas: viene de los datos "
               "abiertos, con un dia de desfase.",
               sonda=None, severidad="baja"),
        Caveat("NO calcules dispersion de precios agrupando por UNSPSC. [MEDIDO 2026-08-19] un "
               "mismo commodity mezcla hasta 19 unidades de medida distintas, con "
               "ratios de x13.450.000 entre minimo y maximo: el 'precio unitario' de "
               "un servicio global y el de un metro lineal no son comparables. La "
               "comparacion valida es DENTRO de la misma linea de la misma "
               "licitacion, donde unidad y objeto son identicos por construccion; "
               "ahi el sobreprecio mediano del perdedor sobre el ganador fue 20,7%.",
               sonda=None, severidad="alta"),
        Caveat("`criterios` tiene cobertura real del 89%, no del 100%: [MEDIDO 2026-08-19] 799 "
               "de 7.273 licitaciones traen 'NA' literal en la fuente y quedan como "
               "NULL. Y solo el 54,4% menciona 'Precio' entre sus criterios.",
               sonda=None, severidad="media"),
        Caveat("`precio_unitario` es NULL cuando la fuente traia menos de 2 pesos: "
               "[MEDIDO 2026-08-19] 4.973 de 119.281 ofertas venian con 0 o 1 peso. No se "
               "corrigen ni se imputan, se descartan y quedan marcadas en "
               "`precio_sospechoso`. Cualquier promedio excluye esas filas.",
               sonda=None, severidad="media"),
        Caveat("`precio_unitario_clp` usa la paridad del MES, no del dia, y la serie "
               "de ChileCompra va un mes atras: [MEDIDO 2026-08-19] para los meses posteriores "
               "al ultimo publicado se arrastra la ultima paridad disponible. Para "
               "montos grandes en meses volatiles eso introduce error.",
               sonda=None, severidad="media"),
        Caveat("`fecha_adjudicacion` puede ser una fecha ESTIMADA FUTURA: [MEDIDO 2026-08-19] "
               "hay filas con adjudicacion en meses que todavia no ocurrieron. Para "
               "ordenar hechos usa `fecha_envio_oferta`, que si ocurrio.",
               sonda=None, severidad="alta"),
    ),
)


ENTIDADES: dict[str, Entity] = {
    e.name: e for e in [
        LICITACION, LICITACION_ITEM, ADJUDICACION_ITEM, OFERTA,
        ORDEN_COMPRA, ORDEN_COMPRA_ITEM,
        ORGANISMO, PROVEEDOR, UNSPSC,
    ]
}

VOCABULARIOS = (
    "unspsc_commodity", "unspsc_clase", "unspsc_familia", "unspsc_segmento",
    "organismo", "proveedor", "region", "comuna",
)


def describe(entidad: str | None = None, incluir_caveats: bool = True) -> dict:
    """Payload de mp_schema_describe."""
    nombres = [entidad] if entidad else list(ENTIDADES)
    salida = {}
    for n in nombres:
        e = ENTIDADES[n]
        salida[n] = {
            "label": e.label,
            "grano": e.grano,
            "procedencia": e.procedencia,
            "dimensiones": {
                d.name: {"label": d.label, "tipo": d.kind,
                         **({"vocabulario": d.vocabulario} if d.vocabulario else {}),
                         **({"nota": d.nota} if d.nota else {})}
                for d in e.dims.values()
            },
            "medidas": {
                m.name: {"label": m.label, "funciones": list(m.fns), "unidad": m.unidad,
                         **({"nota": m.nota} if m.nota else {}),
                         **({"limitaciones": [c.as_dict() for c in m.caveats]}
                            if (m.caveats and incluir_caveats) else {})}
                for m in e.measures.values()
            },
            "atributos": {a.name: a.label for a in e.attrs.values()},
        }
        if incluir_caveats and e.caveats:
            salida[n]["limitaciones"] = [c.as_dict() for c in e.caveats]
    return {
        "entidades": salida,
        "operadores_filtro": list(OPS),
        "granos_temporales": list(GRANOS_TEMPORALES),
        "vocabularios": list(VOCABULARIOS),
    }
