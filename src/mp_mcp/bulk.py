"""Cargadores de los datos abiertos de ChileCompra (bulk, sin ticket ni cuota).

POR QUE EXISTE ESTE MODULO
--------------------------
Hasta el 2026-08-19 el proyecto asumia que la API v1 era la unica fuente y que la
cuota de 10.000 hits/dia era la restriccion de diseno. Eso resulto falso para todo
lo historico, y la diferencia es enorme:

| Necesidad                  | Por API              | Por bulk |
|----------------------------|----------------------|----------|
| 1 ano de adjudicaciones    | ~95.000 hits         | 0 hits   |
| ordenes de compra de 1 dia | 11.681 hits (117%)   | 0 hits   |
| ordenes de compra de 1 ano | ~4,3 M hits (427 d)  | 0 hits   |
| ofertas de los perdedores  | IMPOSIBLE            | 0 hits   |
| criterios de evaluacion    | IMPOSIBLE            | 0 hits   |
| serie de tipo de cambio    | IMPOSIBLE            | 0 hits   |

La API sigue siendo la unica via para el DIA EN CURSO: el bulk tiene un dia de
desfase. No se reemplaza, se complementa.

DOS SONDAS DEL REPO QUEDAN REFUTADAS por esta fuente:
  - P-09 (VERIFICADO): "el diccionario NO incluye criterios de evaluacion,
    ponderaciones, ni las ofertas de los perdedores". Si los incluye: 100% de
    cobertura en `CriteriosEvaluacion` y `MontoUnitarioOferta` por oferente.
  - P-13 (VERIFICADO): "ninguna fuente de ChileCompra entrega la serie de UF, UTM
    ni tipo de cambio". Si la entrega: oc-da/ParidadMoneda.csv, 2007-01 a la fecha.

EL ATAJO, DECLARADO
-------------------
El patron de URL esta documentado en la pagina de descargas de ChileCompra. Los
dos valores de `TipoReporte` -- `oc-da` y `lic-da` -- NO: salieron del bundle
JavaScript de su SPA. [Probable] que sean estables (el historico llega a 2015),
pero es un contrato implicito. Por eso todo aca FALLA RUIDOSO: si el ZIP no
aparece o le faltan columnas, se levanta excepcion. Nunca se deja una tabla vacia
en silencio, que es el modo de fallo que este proyecto no acepta.

ENCODING, MEDIDO
----------------
[MEDIDO 2026-08-19] sobre oc-da/2026-8.zip, 411.777.058 bytes:
  - NO es UTF-8 valido;
  - NO es cp1252 puro: 71 bytes indefinidos (0x8d x42, 0x81 x28, 0x9d x1);
  - los acentos (0xC0-0xFF) son identicos en cp1252 y latin-1;
  - hay 98.740 bytes en 0x80-0x9F, de los cuales 58.319 son comillas
    tipograficas que latin-1 convertiria en caracteres de control.
=> cp1252 con errors="replace": 71 caracteres perdidos en 411 MB, contra 58.319
   corrompidos si se usara latin-1. Es la cuarta vez que el encoding muerde a este
   proyecto (ver _decodificar en api.py).
"""

from __future__ import annotations

import os
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import httpx

BLOB = "https://transparenciachc.blob.core.windows.net"
CONTENEDOR_OC = "oc-da"
CONTENEDOR_LIC = "lic-da"
URL_PARIDAD = f"{BLOB}/{CONTENEDOR_OC}/ParidadMoneda.csv"
URL_OC_ERRONEAS = f"{BLOB}/{CONTENEDOR_OC}/hist_OC_erroneas.csv"
URL_OC_MONEDA = f"{BLOB}/{CONTENEDOR_OC}/hist_moneda_H_vs_I.csv"

# Un precio unitario de 0 o 1 peso no es un precio: es ruido de la fuente.
# [MEDIDO 2026-08-19] sin este corte, la dispersion de una linea UNSPSC daba
# ratios de 3,1e10 -- mostrar eso a un cliente cuesta la credibilidad entera.
PRECIO_MINIMO_CREIBLE = 2.0

_NL = b"\n"

# [MEDIDO 2026-08-19] las dos fuentes usan vocabularios DISTINTOS para la moneda y
# por eso la conversion a CLP daba 0 filas:
#   lic-da  -> "Peso Chileno" (117.773), "Dolar" (566), "Unidad de Fomento" (436),
#              "Moneda revisar" (9)
#   oc-da   -> "CLP" (243.937), "USD" (788), "CLF" (676), "UTM" (32), "EUR" (5)
#   paridad -> CLF, CLP, EUR, USD, UTM
# "Moneda revisar" es la propia ChileCompra diciendo que el dato esta mal; se deja
# sin normalizar a proposito para que no se convierta ni se agregue.
MONEDA_CANONICA = {
    "peso chileno": "CLP", "clp": "CLP", "pesos chilenos": "CLP",
    "dolar": "USD", "dolar americano": "USD", "usd": "USD",
    "unidad de fomento": "CLF", "clf": "CLF", "uf": "CLF",
    "unidad tributaria mensual": "UTM", "utm": "UTM",
    "euro": "EUR", "eur": "EUR",
}


def _sql_moneda(col: str) -> str:
    """SQL que normaliza el nombre de moneda al codigo de la serie de paridades."""
    casos = " ".join(
        "WHEN lower(trim(%s)) = '%s' THEN '%s'" % (col, k, v)
        for k, v in MONEDA_CANONICA.items())
    return "CASE %s ELSE NULL END" % casos


class ErrorBulk(RuntimeError):
    pass


# ---------------------------------------------------------------- descarga

def url_mes(contenedor: str, anio: int, mes: int) -> str:
    """El mes va SIN cero a la izquierda. [MEDIDO] `2026-07.zip` da 404 y
    `2026-7.zip` da 200."""
    return f"{BLOB}/{contenedor}/{anio}-{mes}.zip"


def descargar(url: str, destino: Path, timeout: float = 900.0) -> int:
    """Descarga en streaming. Levanta si no es 200 o si el cuerpo es sospechoso."""
    destino.parent.mkdir(parents=True, exist_ok=True)
    tmp = destino.with_suffix(destino.suffix + ".parcial")
    n = 0
    with httpx.stream("GET", url, timeout=timeout, follow_redirects=True) as r:
        if r.status_code != 200:
            raise ErrorBulk(
                f"{url} devolvio HTTP {r.status_code}. Si es 404, puede que el mes "
                f"no exista todavia (el bulk tiene un dia de desfase) o que "
                f"ChileCompra haya renombrado el contenedor: los tokens 'oc-da' y "
                f"'lic-da' NO estan documentados, salieron del bundle de su SPA.")
        with open(tmp, "wb") as f:
            for ch in r.iter_bytes(1 << 22):
                f.write(ch)
                n += len(ch)
    if n < 100_000:
        tmp.unlink(missing_ok=True)
        raise ErrorBulk(
            f"{url} devolvio solo {n} bytes. Los ZIP mensuales pesan entre 2 y "
            f"150 MB; esto es la cascara HTML de su SPA o un error, no datos.")
    tmp.replace(destino)
    return n


def transcodificar(zip_path: Path, csv_path: Path) -> tuple[str, int]:
    """ZIP (cp1252) -> CSV en UTF-8. Corta en salto de linea para no partir un
    caracter multibyte entre bloques."""
    with zipfile.ZipFile(zip_path) as z:
        entradas = z.infolist()
        if not entradas:
            raise ErrorBulk(f"{zip_path} esta vacio")
        nombre = entradas[0].filename
        with z.open(nombre) as src, open(csv_path, "wb") as dst:
            resto = b""
            while True:
                ch = src.read(1 << 22)
                if not ch:
                    break
                buf = resto + ch
                corte = buf.rfind(_NL) + 1
                if corte == 0:
                    resto = buf
                    continue
                dst.write(buf[:corte].decode("cp1252", errors="replace").encode("utf-8"))
                resto = buf[corte:]
            if resto:
                dst.write(resto.decode("cp1252", errors="replace").encode("utf-8"))
    return nombre, csv_path.stat().st_size


def cargar_excluidas(con) -> dict:
    """Las dos listas de exclusion que publica ChileCompra.

    [MEDIDO 2026-08-19] 4.190 codigos por error de monto y 12.163 por moneda de
    encabezado distinta de la del item, cubriendo 2006-2026. Sin esto, un solo
    error de tipeo de un municipio -- 5,29 millones de UF por un arriendo de
    vehiculos -- mete $212.200 millones falsos en cualquier ranking de gasto.
    """
    import csv as _csv
    import io as _io
    con.execute("""CREATE TABLE IF NOT EXISTS oc_excluida (
        codigo VARCHAR PRIMARY KEY, motivo VARCHAR, _fuente VARCHAR)""")
    con.execute("DELETE FROM oc_excluida")
    vistos: dict[str, set] = {}
    for url, motivo, col in ((URL_OC_ERRONEAS, "monto", 1),
                             (URL_OC_MONEDA, "moneda_encabezado_vs_item", 1)):
        raw = httpx.get(url, timeout=300.0, follow_redirects=True)
        if raw.status_code != 200:
            raise ErrorBulk(f"{url} devolvio HTTP {raw.status_code}: sin la lista de "
                            f"exclusion los agregados de monto quedan contaminados, "
                            f"asi que NO se sigue en silencio.")
        texto = raw.content.decode("cp1252", errors="replace")
        for fila in _csv.reader(_io.StringIO(texto), delimiter=";"):
            if len(fila) <= col or not fila[col] or fila[col] in ("numoc", "porCode"):
                continue
            cod = fila[col].strip().strip('"')
            if cod and cod != "NA":
                vistos.setdefault(cod, set()).add(motivo)
    filas = [(c, "ambos" if len(m) > 1 else next(iter(m)), "bulk-exclusiones")
             for c, m in vistos.items()]
    if not filas:
        raise ErrorBulk("las listas de exclusion vinieron vacias: no se sigue")
    con.executemany("INSERT OR REPLACE INTO oc_excluida VALUES (?, ?, ?)", filas)
    por_motivo = dict(con.execute(
        "SELECT motivo, count(*) FROM oc_excluida GROUP BY 1").fetchall())
    return {"codigos": len(filas), "por_motivo": por_motivo}


def marcar_excluidas(con) -> dict:
    """Anula los montos en CLP de las OC excluidas. El original se conserva.

    Es el DEFAULT y no una advertencia a proposito: el ranking de gasto salio mal
    porque nada lo detuvo. Un caveat que el agente puede no leer no es un control.
    """
    for tabla in ("orden_compra", "orden_compra_item"):
        con.execute(f"ALTER TABLE {tabla} ADD COLUMN IF NOT EXISTS "
                    f"excluida_por_fuente BOOLEAN")
    con.execute("""UPDATE orden_compra SET excluida_por_fuente = TRUE,
                   total_clp = NULL, total_neto_clp = NULL, impuestos_clp = NULL
                   WHERE codigo IN (SELECT codigo FROM oc_excluida)""")
    con.execute("""UPDATE orden_compra_item SET excluida_por_fuente = TRUE,
                   precio_neto_clp = NULL, total_clp = NULL
                   WHERE codigo_oc IN (SELECT codigo FROM oc_excluida)""")
    con.execute("""UPDATE orden_compra SET excluida_por_fuente = FALSE
                   WHERE excluida_por_fuente IS NULL""")
    con.execute("""UPDATE orden_compra_item SET excluida_por_fuente = FALSE
                   WHERE excluida_por_fuente IS NULL""")
    oc = con.execute("SELECT count(*) FROM orden_compra "
                     "WHERE excluida_por_fuente").fetchone()[0]
    it = con.execute("SELECT count(*) FROM orden_compra_item "
                     "WHERE excluida_por_fuente").fetchone()[0]
    monto = con.execute("SELECT round(sum(total)/1e9, 1) FROM orden_compra "
                        "WHERE excluida_por_fuente").fetchone()[0]
    return {"orden_compra_marcadas": oc, "items_marcados": it,
            "monto_original_excluido_mil_mill": monto}


# ------------------------------------------------------------ carga comun

def _exigir_columnas(con, tabla: str, requeridas: list[str], fuente: str) -> None:
    hay = {r[1] for r in con.execute(f"PRAGMA table_info({tabla})").fetchall()}
    faltan = [c for c in requeridas if c not in hay]
    if faltan:
        raise ErrorBulk(
            f"{fuente} cambio de formato: faltan {len(faltan)} columnas "
            f"{faltan[:8]}{'...' if len(faltan) > 8 else ''}. NO se carga nada: "
            f"antes que dejar la tabla a medias, esto se arregla mirando el archivo. "
            f"Columnas presentes: {sorted(hay)[:12]}...")


def _leer_csv(con, csv_path: Path, tabla: str = "cruda") -> int:
    con.execute(f"DROP TABLE IF EXISTS {tabla}")
    con.execute(
        f"""CREATE TABLE {tabla} AS SELECT * FROM read_csv(?, delim=';',
            header=true, quote='"', all_varchar=true, ignore_errors=true,
            strict_mode=false, sample_size=-1)""", [str(csv_path)])
    return con.execute(f"SELECT count(*) FROM {tabla}").fetchone()[0]


_NUM = "TRY_CAST(replace(replace({c}, '.', ''), ',', '.') AS DOUBLE)"


def _num(col: str) -> str:
    """Numero chileno: punto de miles, coma decimal."""
    return _NUM.format(c=f'"{col}"')


def _bool(col: str) -> str:
    """Booleano de la fuente: "1" es true, "0" es false, cualquier otra cosa NULL.

    No se asume que 0/1 sea exhaustivo: [MEDIDO 2026-08-19] la columna `Obras`
    trae tambien "2", que no esta documentado. Un valor desconocido entra como
    NULL en vez de colapsar a false, porque false es una afirmacion.
    """
    return (f'CASE trim("{col}") WHEN \'1\' THEN TRUE '
            f'WHEN \'0\' THEN FALSE ELSE NULL END')


# ------------------------------------------------------- ordenes de compra

COLS_OC = ["Codigo", "Nombre", "codigoEstado", "Estado", "FechaEnvio",
           "MontoTotalOC", "TipoMonedaOC", "MontoTotalOC_PesosChilenos",
           "TotalNetoOC", "Impuestos", "CodigoOrganismoPublico",
           "OrganismoPublico", "RegionUnidadCompra", "CiudadUnidadCompra",
           "CodigoProveedor", "NombreProveedor", "RutSucursal",
           "CodigoLicitacion", "IDItem", "codigoProductoONU", "cantidad",
           "precioNeto", "totalLineaNeto", "EsCompraAgil", "FechaAceptacion",
           "EspecificacionComprador", "UnidadMedida", "monedaItem",
           "ActividadProveedor", "ComunaProveedor", "RegionProveedor",
           "ActividadComprador", "FormaPago", "TipoDespacho",
           "PromedioCalificacion", "Descripcion/Obervaciones"]


def cargar_oc(con, csv_path: Path, periodo: str) -> dict:
    """Llena orden_compra, orden_compra_item, proveedor y organismo."""
    filas = _leer_csv(con, csv_path)
    _exigir_columnas(con, "cruda", COLS_OC, f"oc-da {periodo}")
    ts = datetime.now(timezone.utc)

    # TRANSACCION: si el INSERT falla, el DELETE se revierte. Sin esto, el
    # 2026-08-20 se borraron 240.619 ordenes de compra que no se pudieron recargar.
    con.execute("BEGIN TRANSACTION")
    try:
        con.execute("DELETE FROM orden_compra WHERE _procedencia = ?",
                    [f"bulk-oc:{periodo}"])
        con.execute("DELETE FROM orden_compra_item WHERE _procedencia = ?",
                    [f"bulk-oc:{periodo}"])

        con.execute(f"""
        INSERT OR REPLACE INTO orden_compra (codigo, nombre, descripcion, codigo_licitacion, estado, tipo, moneda, forma_pago, tipo_despacho, total, total_clp, total_neto_clp, impuestos_clp, promedio_calificacion, rut_proveedor, nombre_proveedor, organismo_codigo, organismo_nombre, region_comprador, comuna_comprador, fecha_envio, fecha_aceptacion, excluida_por_fuente, _procedencia, _ingerido_en)
        SELECT "Codigo", any_value("Nombre"), any_value("Descripcion/Obervaciones"),
               nullif(any_value("CodigoLicitacion"), ''), any_value("Estado"),
               any_value("CodigoAbreviadoTipoOC"), any_value("TipoMonedaOC"),
               any_value("FormaPago"), any_value("TipoDespacho"),
               any_value({_num('MontoTotalOC')}),
               any_value({_num('MontoTotalOC_PesosChilenos')}),
               any_value({_num('TotalNetoOC')}), any_value({_num('Impuestos')}),
               any_value({_num('PromedioCalificacion')}),
               nullif(any_value("RutSucursal"), ''), any_value("NombreProveedor"),
               any_value("CodigoOrganismoPublico"), any_value("OrganismoPublico"),
               any_value("RegionUnidadCompra"), any_value("CiudadUnidadCompra"),
               any_value(TRY_CAST("FechaEnvio" AS DATE)),
               any_value(TRY_CAST("FechaAceptacion" AS DATE)),
               FALSE, ?, ?
        FROM cruda WHERE "Codigo" IS NOT NULL GROUP BY "Codigo"
        """, [f"bulk-oc:{periodo}", ts])
        n_oc = con.execute("SELECT count(*) FROM orden_compra WHERE _procedencia = ?",
                           [f"bulk-oc:{periodo}"]).fetchone()[0]

    # El precio sospechoso se guarda como NULL, no como 0: un 0 se promedia y
    # miente; un NULL se excluye y se cuenta.
        con.execute(f"""
        INSERT OR REPLACE INTO orden_compra_item (item_id, codigo_oc, correlativo, unspsc_commodity, unspsc_clase, unspsc_familia, unspsc_segmento, especificacion_comprador, unidad_medida, cantidad, moneda, precio_neto, precio_neto_clp, total_clp, rut_proveedor, nombre_proveedor, organismo_codigo, organismo_nombre, region_comprador, comuna_comprador, fecha_envio, excluida_por_fuente, _procedencia, _ingerido_en)
        SELECT "IDItem", "Codigo", TRY_CAST("IDItem" AS INTEGER),
               "codigoProductoONU", NULL, NULL, NULL,
               "EspecificacionComprador", "UnidadMedida", {_num('cantidad')},
               {_sql_moneda(chr(34) + "monedaItem" + chr(34))},
               CASE WHEN {_num('precioNeto')} >= {PRECIO_MINIMO_CREIBLE}
                    THEN {_num('precioNeto')} END,
               NULL,
               {_num('totalLineaNeto')},
               nullif("RutSucursal", ''), "NombreProveedor",
               "CodigoOrganismoPublico", "OrganismoPublico",
               "RegionUnidadCompra", "CiudadUnidadCompra",
               TRY_CAST("FechaEnvio" AS DATE), FALSE, ?, ?
        FROM cruda WHERE "IDItem" IS NOT NULL
        """, [f"bulk-oc:{periodo}", ts])
        n_item = con.execute(
            "SELECT count(*) FROM orden_compra_item WHERE _procedencia = ?",
            [f"bulk-oc:{periodo}"]).fetchone()[0]

        con.execute("""
        INSERT OR REPLACE INTO proveedor
        SELECT nullif("RutSucursal", ''), any_value("NombreProveedor"),
               any_value("RegionProveedor"), any_value("ComunaProveedor"),
               any_value("ActividadProveedor")
        FROM cruda WHERE nullif("RutSucursal", '') IS NOT NULL
        GROUP BY nullif("RutSucursal", '')""")
        con.execute("""
        INSERT OR REPLACE INTO organismo
        SELECT "CodigoOrganismoPublico", any_value("OrganismoPublico"),
               any_value("RegionUnidadCompra"), any_value("CiudadUnidadCompra"),
               any_value("ActividadComprador")
        FROM cruda WHERE "CodigoOrganismoPublico" IS NOT NULL
        GROUP BY "CodigoOrganismoPublico" """)

        sospechosos = con.execute(
            f"SELECT count(*) FROM cruda WHERE {_num('precioNeto')} < "
            f"{PRECIO_MINIMO_CREIBLE}").fetchone()[0]
        con.execute("COMMIT")
    except Exception:
        # El rollback es el punto de todo esto: deja los datos del periodo como
        # estaban en vez de borrados a medias.
        con.execute("ROLLBACK")
        raise
    con.execute("DROP TABLE cruda")
    return {"filas_csv": filas, "orden_compra": n_oc, "orden_compra_item": n_item,
            "precios_descartados": sospechosos}


# ------------------------------------------------- licitaciones y ofertas

COLS_LIC = ["CodigoExterno", "Nombre", "CodigoEstado", "Estado", "CodigoOrganismo",
            "NombreOrganismo", "FechaAdjudicacion", "NumeroOferentes",
            "CriteriosEvaluacion", "Codigoitem", "CodigoProductoONU",
            "Nombre linea Adquisicion", "RutProveedor", "NombreProveedor",
            "RazonSocialProveedor", "Estado Oferta", "Oferta seleccionada",
            "Cantidad Ofertada", "Moneda de la Oferta", "MontoUnitarioOferta",
            "Valor Total Ofertado", "CantidadAdjudicada", "MontoLineaAdjudica",
            "FechaEnvioOferta", "RegionUnidad", "ComunaUnidad", "FechaPublicacion",
            "FechaCierre", "MontoEstimado", "CodigoMoneda", "Cantidad",
            "UnidadMedida", "Tipo", "sector"]


def cargar_lic(con, csv_path: Path, periodo: str) -> dict:
    """Llena oferta y adjudicacion_item. Es la carga que da la capacidad que la
    API no puede dar: el precio al que ofertaron los que PERDIERON."""
    filas = _leer_csv(con, csv_path)
    _exigir_columnas(con, "cruda", COLS_LIC, f"lic-da {periodo}")
    ts = datetime.now(timezone.utc)
    proc = f"bulk-lic:{periodo}"

    # Mismo riesgo que en cargar_oc: el DELETE sin transaccion borra y deja el
    # hueco si el INSERT falla.
    con.execute("BEGIN TRANSACTION")
    _en_transaccion = True
    for _t in ("oferta", "adjudicacion_item", "licitacion", "licitacion_item"):
        con.execute(f"DELETE FROM {_t} WHERE _procedencia = ?", [proc])

    con.execute(f"""
        INSERT OR REPLACE INTO oferta SELECT
            -- [MEDIDO] la clave licitacion|item|rut colisionaba en 497 filas de
            -- 119.281: un mismo proveedor puede ofertar VARIAS lineas para el mismo
            -- item (hasta 5 casos vistos). Eran ofertas reales que INSERT OR REPLACE
            -- se comia en silencio. El correlativo las conserva.
            "CodigoExterno" || '|' || coalesce("Codigoitem",'') || '|' ||
                coalesce("RutProveedor",'') || '|' ||
                CAST(row_number() OVER (PARTITION BY "CodigoExterno", "Codigoitem",
                     "RutProveedor" ORDER BY "Nombre de la Oferta") AS VARCHAR)
                AS oferta_id,
            "CodigoExterno", "Codigoitem", "CodigoProductoONU",
            "Nombre linea Adquisicion",
            nullif("RutProveedor",''), "NombreProveedor", "RazonSocialProveedor",
            "Estado Oferta",
            "Oferta seleccionada" = 'Seleccionada',
            {_num('Cantidad Ofertada')}, {_sql_moneda(chr(34) + "Moneda de la Oferta" + chr(34))},
            CASE WHEN {_num('MontoUnitarioOferta')} >= {PRECIO_MINIMO_CREIBLE}
                 THEN {_num('MontoUnitarioOferta')} END,
            NULL,
            {_num('Valor Total Ofertado')}, {_num('CantidadAdjudicada')},
            {_num('MontoLineaAdjudica')},
            TRY_CAST("FechaEnvioOferta" AS TIMESTAMP),
            TRY_CAST("NumeroOferentes" AS INTEGER),
            -- [MEDIDO] 799 de 7.273 licitaciones traen "NA" literal, no vacio.
            -- Sin esto la cobertura de criterios parece 100% y es 89%.
            CASE WHEN upper(trim(coalesce("CriteriosEvaluacion",''))) IN ('', 'NA', 'N/A')
                 THEN NULL ELSE "CriteriosEvaluacion" END,
            "CodigoOrganismo", "NombreOrganismo",
            TRY_CAST("FechaAdjudicacion" AS DATE),
            {_num('MontoUnitarioOferta')} < {PRECIO_MINIMO_CREIBLE},
            ?, ?
        FROM cruda WHERE "CodigoExterno" IS NOT NULL
    """, [proc, ts])
    n_of = con.execute("SELECT count(*) FROM oferta WHERE _procedencia = ?",
                       [proc]).fetchone()[0]

    # adjudicacion_item: solo las lineas SELECCIONADAS con cantidad adjudicada.
    con.execute(f"""
        INSERT OR REPLACE INTO adjudicacion_item SELECT
            "CodigoExterno" || '|' || coalesce("Codigoitem",'') AS adjudicacion_id,
            "CodigoExterno", TRY_CAST("Codigoitem" AS INTEGER),
            "CodigoProductoONU", NULL, NULL, NULL,
            "Nombre linea Adquisicion", "UnidadMedida",
            {_sql_moneda(chr(34) + "Moneda de la Oferta" + chr(34))},
            CASE WHEN {_num('MontoUnitarioOferta')} >= {PRECIO_MINIMO_CREIBLE}
                 THEN {_num('MontoUnitarioOferta')} END,
            NULL,
            {_num('CantidadAdjudicada')},
            nullif("RutProveedor",''), "NombreProveedor", "Tipo",
            "CodigoOrganismo", "NombreOrganismo", "RegionUnidad", "ComunaUnidad",
            TRY_CAST("FechaAdjudicacion" AS DATE),
            FALSE, ?, ?
        FROM cruda
        WHERE "Oferta seleccionada" = 'Seleccionada'
          AND {_num('CantidadAdjudicada')} > 0
          AND "CodigoExterno" IS NOT NULL
    """, [proc, ts])
    n_adj = con.execute(
        "SELECT count(*) FROM adjudicacion_item WHERE _procedencia = ?",
        [proc]).fetchone()[0]

    # licitacion: una fila por proceso. El archivo viene a grano de oferta-linea,
    # asi que se colapsa por CodigoExterno.
    #
    # `cantidad_reclamos` queda NULL a proposito: [MEDIDO 2026-08-19] la columna
    # CantidadReclamos trae valores como 972, 507 y 11.788 repetidos cientos de
    # veces -- son identificadores, no cantidades de reclamos. Cargar un numero
    # que no es lo que dice su nombre es peor que no cargarlo.
    con.execute(f"""
        INSERT OR REPLACE INTO licitacion
        SELECT "CodigoExterno", any_value("Nombre"), any_value("Descripcion"),
               any_value("Estado"), any_value("Tipo"),
               any_value({_sql_moneda('"Moneda Adquisicion"')}),
               any_value({_bool("Obras")}), any_value({_bool("TomaRazon")}),
               any_value({_bool("SubContratacion")}),
               any_value({_bool("ExtensionPlazo")}),
               any_value({_bool("VisibilidadMonto")}),
               any_value(TRY_CAST("Estimacion" AS INTEGER)),
               any_value({_num('MontoEstimado')}), NULL,
               any_value(TRY_CAST("NumeroOferentes" AS INTEGER)),
               NULL,
               any_value("CodigoOrganismo"), any_value("NombreOrganismo"),
               any_value("RegionUnidad"), any_value("ComunaUnidad"),
               any_value(TRY_CAST("FechaPublicacion" AS DATE)),
               any_value(TRY_CAST("FechaCierre" AS DATE)),
               any_value(TRY_CAST("FechaAdjudicacion" AS DATE)),
               any_value("Link"), NULL, ?, ?
        FROM cruda WHERE "CodigoExterno" IS NOT NULL
        GROUP BY "CodigoExterno"
    """, [proc, ts])
    n_lic = con.execute("SELECT count(*) FROM licitacion WHERE _procedencia = ?",
                        [proc]).fetchone()[0]

    con.execute("""
        INSERT OR REPLACE INTO licitacion_item
        SELECT "CodigoExterno" || '#' || coalesce("Codigoitem",''),
               "CodigoExterno", TRY_CAST("Correlativo" AS INTEGER),
               any_value("CodigoProductoONU"), NULL, NULL, NULL,
               any_value("Rubro3"), any_value("Nombre producto genrico"),
               any_value("Descripcion linea Adquisicion"),
               any_value("UnidadMedida"),
               any_value(TRY_CAST(replace(replace("Cantidad",'.',''),',','.') AS DOUBLE)),
               any_value("Estado"), any_value("CodigoOrganismo"),
               any_value("NombreOrganismo"), any_value("RegionUnidad"),
               any_value("ComunaUnidad"),
               any_value(TRY_CAST("FechaPublicacion" AS DATE)),
               any_value(TRY_CAST("FechaCierre" AS DATE)), ?, ?
        FROM cruda WHERE "CodigoExterno" IS NOT NULL
        GROUP BY "CodigoExterno", "Codigoitem", TRY_CAST("Correlativo" AS INTEGER)
    """, [proc, ts])
    n_item = con.execute(
        "SELECT count(*) FROM licitacion_item WHERE _procedencia = ?",
        [proc]).fetchone()[0]

    con.execute("""
        INSERT OR REPLACE INTO proveedor
        SELECT nullif("RutProveedor",''), any_value("NombreProveedor"),
               NULL, NULL, NULL
        FROM cruda WHERE nullif("RutProveedor",'') IS NOT NULL
          AND nullif("RutProveedor",'') NOT IN (SELECT rut FROM proveedor)
        GROUP BY nullif("RutProveedor",'')""")

    perdedoras = con.execute(
        "SELECT count(*) FROM oferta WHERE _procedencia = ? AND NOT seleccionada",
        [proc]).fetchone()[0]
    con.execute("COMMIT")
    con.execute("DROP TABLE cruda")
    return {"filas_csv": filas, "licitacion": n_lic, "licitacion_item": n_item,
            "oferta": n_of, "ofertas_perdedoras": perdedoras,
            "adjudicacion_item": n_adj}


# ------------------------------------------------------------ paridades

def cargar_paridad(con, csv_path: Path) -> dict:
    """Llena tipo_cambio desde ParidadMoneda.csv.

    LIMITACION que hay que declarar al agente: la serie es MENSUAL, no diaria.
    Convertir el monto de un dia concreto usa la paridad de su mes. Para montos
    grandes en meses volatiles eso introduce error, y por eso _fuente lo dice.
    """
    filas = _leer_csv(con, csv_path, "par")
    _exigir_columnas(con, "par", ["YEAR", "MONTH", "MONEDA", "VMCLP"],
                     "ParidadMoneda.csv")
    con.execute("DELETE FROM tipo_cambio WHERE _fuente LIKE 'bulk-paridad%'")
    con.execute(f"""
        INSERT OR REPLACE INTO tipo_cambio
        SELECT "MONEDA",
               make_date(TRY_CAST("YEAR" AS INTEGER), TRY_CAST("MONTH" AS INTEGER), 1),
               {_num('VMCLP')},
               'bulk-paridad-mensual'
        FROM par
        WHERE "MONEDA" IS NOT NULL AND {_num('VMCLP')} > 0
          AND TRY_CAST("YEAR" AS INTEGER) IS NOT NULL""")
    n = con.execute("SELECT count(*) FROM tipo_cambio").fetchone()[0]
    monedas = [r[0] for r in con.execute(
        "SELECT DISTINCT moneda FROM tipo_cambio ORDER BY 1").fetchall()]
    rango = con.execute("SELECT min(fecha), max(fecha) FROM tipo_cambio").fetchone()
    con.execute("DROP TABLE par")
    return {"filas_csv": filas, "tipo_cambio": n, "monedas": monedas,
            "desde": str(rango[0]), "hasta": str(rango[1])}


# ------------------------------------------------------ conversion a CLP

def convertir_a_clp(con) -> dict:
    """Rellena las columnas *_clp con la paridad del mes, arrastrando la ultima.

    Resuelve P-11 ("los montos vienen en CLP, CLF, USD, UTM y EUR y sin serie de
    tipo de cambio no son agregables entre si"): con esto lo son.

    DOS COSAS QUE HAY QUE DECLARAR, las dos medidas:

    1. La serie es MENSUAL y va un mes atras: [MEDIDO 2026-08-19] llega a 2026-07
       mientras los datos ya son de agosto. Exigir el mes exacto dejaba 0 filas
       convertidas. Se arrastra la ultima paridad disponible y se cuenta cuantas
       filas usaron arrastre, para que el agente pueda decirlo.
    2. `fecha_adjudicacion` NO sirve como fecha del hecho: [MEDIDO] hay filas con
       adjudicacion en 2026-09, 10 y 11 -- son fechas ESTIMADAS futuras. Para la
       oferta se usa `fecha_envio_oferta`, que es un hecho ocurrido.
    """
    tope = con.execute("SELECT max(fecha) FROM tipo_cambio").fetchone()[0]
    out = {"paridad_hasta": str(tope), "por_tabla": {}}
    for tabla, col_orig, col_clp, col_fecha in (
        ("oferta", "precio_unitario", "precio_unitario_clp",
         "coalesce(fecha_envio_oferta, fecha_adjudicacion)"),
        ("adjudicacion_item", "precio_unitario", "precio_unitario_clp",
         "fecha_adjudicacion"),
        ("orden_compra_item", "precio_neto", "precio_neto_clp", "fecha_envio"),
        ("licitacion", "monto_estimado", "monto_estimado_clp", "fecha_publicacion"),
    ):
        con.execute(f"""
            UPDATE {tabla} SET {col_clp} = {col_orig} * tc.a_clp
            FROM tipo_cambio tc
            WHERE tc.moneda = {tabla}.moneda
              AND tc.fecha = least(date_trunc('month', CAST({col_fecha} AS DATE)),
                                   DATE '{tope}')
              AND {tabla}.{col_orig} IS NOT NULL
              AND {tabla}.{col_clp} IS NULL""")
        conv = con.execute(
            f"SELECT count(*) FROM {tabla} WHERE {col_clp} IS NOT NULL").fetchone()[0]
        pend = con.execute(
            f"SELECT count(*) FROM {tabla} WHERE {col_orig} IS NOT NULL "
            f"AND {col_clp} IS NULL").fetchone()[0]
        arrastre = con.execute(
            f"SELECT count(*) FROM {tabla} WHERE {col_clp} IS NOT NULL AND "
            f"date_trunc('month', CAST({col_fecha} AS DATE)) > DATE '{tope}'"
        ).fetchone()[0]
        sin_moneda = con.execute(
            f"SELECT count(*) FROM {tabla} WHERE moneda IS NULL "
            f"AND {col_orig} IS NOT NULL").fetchone()[0]
        out["por_tabla"][tabla] = {
            "convertidas": conv, "sin_convertir": pend,
            "con_paridad_arrastrada": arrastre,
            "moneda_no_reconocida": sin_moneda}
    return out
