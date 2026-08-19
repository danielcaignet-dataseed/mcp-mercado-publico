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

    con.execute("DELETE FROM orden_compra WHERE _procedencia = ?",
                [f"bulk-oc:{periodo}"])
    con.execute("DELETE FROM orden_compra_item WHERE _procedencia = ?",
                [f"bulk-oc:{periodo}"])

    con.execute(f"""
        INSERT OR REPLACE INTO orden_compra
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
               ?, ?
        FROM cruda WHERE "Codigo" IS NOT NULL GROUP BY "Codigo"
    """, [f"bulk-oc:{periodo}", ts])
    n_oc = con.execute("SELECT count(*) FROM orden_compra WHERE _procedencia = ?",
                       [f"bulk-oc:{periodo}"]).fetchone()[0]

    # El precio sospechoso se guarda como NULL, no como 0: un 0 se promedia y
    # miente; un NULL se excluye y se cuenta.
    con.execute(f"""
        INSERT OR REPLACE INTO orden_compra_item
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
               TRY_CAST("FechaEnvio" AS DATE), ?, ?
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

    con.execute("DELETE FROM oferta WHERE _procedencia = ?", [proc])
    con.execute("DELETE FROM adjudicacion_item WHERE _procedencia = ?", [proc])

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
    con.execute("DROP TABLE cruda")
    return {"filas_csv": filas, "oferta": n_of, "ofertas_perdedoras": perdedoras,
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
