"""Los cruces que el agente estaba haciendo a mano.

POR QUE EXISTE ESTE MODULO
--------------------------
[MEDIDO 2026-08-19] Le pedimos a Demeter "a que precio me ganaron" y tardo 19
minutos. La consulta al MCP cuesta 0,11 s. Los 19 minutos se fueron en armar el
cruce a mano: 26 archivos lote_NN.json para paginar, ~20 consultas mas en lotes,
dos scripts Python que se escribio sola, un errores.log propio, y 23.981 lineas de
TSV ensambladas para llegar a un joined.tsv de 2.058.

No es el modelo: es una tool que falta. Ningun dashboard "en segundos" existe
encima de un agente que tarda 19 minutos en armar el dato.

Cada funcion de aca devuelve la MISMA envoltura que mp_search y mp_aggregate
--data, columnas, chart_hint, meta con procedencia, as_of y limitaciones-- para
que la capa de presentacion no tenga que adivinar el formato en cada pregunta.
El MCP no renderiza; declara como se deberia renderizar.
"""

from __future__ import annotations

from .semantic import ENTIDADES

ATRIBUCION = "Fuente: ChileCompra - Mercado Publico (https://www.mercadopublico.cl)"

# La unica comparacion de precios que es valida. [MEDIDO 2026-08-19] agrupar por
# UNSPSC mezcla hasta 19 unidades de medida y da ratios de x13.450.000; dentro de
# la misma linea de la misma licitacion, unidad y objeto son identicos por
# construccion.
_CAV_MISMA_LINEA = {
    "limitacion": (
        "La comparacion es DENTRO de la misma linea de la misma licitacion, que es "
        "la unica valida: ahi unidad de medida y objeto son identicos por "
        "construccion. NO extrapoles a 'precio de mercado' del producto: [MEDIDO "
        "2026-08-19] agrupar por UNSPSC mezcla hasta 19 unidades de medida "
        "distintas y produce ratios de x13.450.000."),
    "severidad": "alta", "verificado_por": "MEDIDO 2026-08-19"}

_CAV_PRECIO_NULO = {
    "limitacion": (
        "Solo entran las lineas donde AMBAS ofertas tienen precio unitario "
        "creible en CLP. Se excluyen las que la fuente traia con menos de 2 pesos "
        "([MEDIDO] 4.973 de 119.281) y las que no se pudieron convertir de moneda. "
        "`lineas_sin_comparar` dice cuantas quedaron afuera: si es alto respecto de "
        "`lineas_comparadas`, el resultado es de una parte y hay que decirlo."),
    "severidad": "media", "verificado_por": "MEDIDO 2026-08-19"}

_CAV_VENTANA = {
    "limitacion": (
        "El almacen cubre los ultimos 12 meses de datos abiertos mas el dia en "
        "curso por API. Una licitacion anterior a la ventana no aparece, y eso NO "
        "significa que no exista. `meta.as_of` dice hasta donde llega el dato."),
    "severidad": "media", "verificado_por": "MEDIDO 2026-08-19"}


def _envolver(store, titulo: str, cols: list[str], rows: list,
              chart: dict, limitaciones: list[dict], resumen: dict | None = None,
              nota: str | None = None) -> dict:
    """La misma forma que mp_search y mp_aggregate. Sin esto, cada tool nueva
    obliga a la capa de presentacion a aprender un formato mas."""
    out = {
        "titulo": titulo,
        "data": [dict(zip(cols, r)) for r in rows],
        "columnas": cols,
        "chart_hint": chart,
        "meta": {
            "procedencia": "bulk-lic + bulk-oc (datos abiertos de ChileCompra)",
            "as_of": store.as_of(),
            "filas": len(rows),
            "limitaciones": limitaciones,
            "atribucion": ATRIBUCION,
        },
    }
    if resumen is not None:
        out["resumen"] = resumen
    if nota:
        out["nota"] = nota
    return out


def _barra(eje_x: str, medida: str, label: str, unidad: str, formato: str) -> dict:
    return {"tipo": "barra", "eje_x": eje_x, "serie": None,
            "dimensiones": [eje_x],
            "medidas": [{"columna": medida, "label": label, "unidad": unidad,
                         "formato": formato}]}


def _tabla(cols: list[str]) -> dict:
    return {"tipo": "tabla", "dimensiones": cols, "medidas": []}


def _rango(desde, hasta) -> tuple[str, list]:
    cond, par = "", []
    if desde:
        cond += " AND o.fecha_envio_oferta >= ?"
        par.append(desde)
    if hasta:
        cond += " AND o.fecha_envio_oferta <= ?"
        par.append(hasta)
    return cond, par



# --------------------------------------------------------------- resolutor

# Palabras que una sigla nunca usa.
_VACIAS = {"DE", "DEL", "LA", "EL", "LOS", "LAS", "Y", "E", "EN", "A"}


def _sigla_por_prefijos(aguja: str, nombre: str) -> int | None:
    """Descompone `aguja` en PREFIJOS de palabras de `nombre`, en orden.

    Devuelve con cuantas palabras se logro, o None. Menos palabras = prefijos mas
    largos = calce mas limpio, y asi se ordenan los candidatos.

    Con retroceso, no codicioso: [MEDIDO] tomar siempre el prefijo mas largo
    agarraba FON de FONDO y despues no podia colocar la A de FONASA. La particion
    correcta usa prefijos cortos -- FO|NA|SA -- y solo se encuentra probando todas
    las longitudes.
    """
    palabras = [w for w in nombre.split() if w and w not in _VACIAS]
    if not palabras or not aguja:
        return None
    memo: dict[tuple[int, int], int | None] = {}

    def rec(i: int, j: int) -> int | None:
        """Palabras minimas para consumir aguja[i:] usando palabras[j:]."""
        if i == len(aguja):
            return 0
        if j >= len(palabras):
            return None
        if (i, j) in memo:
            return memo[(i, j)]
        mejor = rec(i, j + 1)                 # saltear esta palabra
        w = palabras[j]
        k = 0
        while k < len(w) and i + k < len(aguja) and w[k] == aguja[i + k]:
            k += 1
            r = rec(i + k, j + 1)             # tomar un prefijo de largo k
            if r is not None and (mejor is None or r + 1 < mejor):
                mejor = r + 1
        memo[(i, j)] = mejor
        return mejor

    return rec(0, 0)


def _subsecuencia(aguja: str, pajar: str) -> bool:
    """Las letras de `aguja` aparecen EN ORDEN dentro de `pajar`.

    Se conserva como red de ultimo recurso, pero NO decide sola: es demasiado
    laxa (ver el caso FONASA en el docstring de este modulo).
    """
    it = iter(pajar)
    return all(ch in it for ch in aguja)


def _solo_letras(t: str) -> str:
    return "".join(c for c in str(t).upper() if c.isalpha())


def resolver_organismo(store, texto: str, tope: int = 6) -> dict:
    """De lo que escribio el usuario al organismo del almacen.

    Devuelve {resuelto, candidatos, modo}. `modo` dice COMO se resolvio, para que
    el agente pueda declararlo en vez de presentar una coincidencia difusa como si
    fuera exacta.
    """
    t = str(texto or "").strip()
    if not t:
        return {"resuelto": None, "candidatos": [], "modo": "vacio"}

    # Las DOS fuentes: un organismo puede licitar sin tener ordenes de compra
    # en la ventana, y al reves. Mirar solo orden_compra lo hacia irresoluble.
    _, filas = store.rows(
        "SELECT organismo_codigo, organismo_nombre, sum(n) AS n FROM ("
        "  SELECT organismo_codigo, organismo_nombre, count(*) AS n"
        "  FROM orden_compra WHERE organismo_nombre IS NOT NULL GROUP BY 1, 2"
        "  UNION ALL"
        "  SELECT organismo_codigo, organismo_nombre, count(*) AS n"
        "  FROM oferta WHERE organismo_nombre IS NOT NULL GROUP BY 1, 2"
        ") GROUP BY 1, 2 ORDER BY n DESC")
    orgs = [(c, n, k) for c, n, k in filas]

    exacto = [o for o in orgs if o[0] == t or o[1].upper() == t.upper()]
    if exacto:
        return {"resuelto": {"codigo": exacto[0][0], "nombre": exacto[0][1]},
                "candidatos": [], "modo": "exacto"}

    sub = [o for o in orgs if t.upper() in o[1].upper()]
    if len(sub) == 1:
        return {"resuelto": {"codigo": sub[0][0], "nombre": sub[0][1]},
                "candidatos": [], "modo": "subcadena"}
    if sub:
        return {"resuelto": None, "modo": "subcadena_ambigua",
                "candidatos": [{"codigo": c, "nombre": n, "ordenes": k}
                               for c, n, k in sub[:tope]]}

    # Sigla por prefijos de palabra: CENtral ABASTecimiento.
    aguja = _solo_letras(t)
    if len(aguja) >= 3:
        pref = []
        for c, nom, k in orgs:
            u = _sigla_por_prefijos(aguja, nom.upper())
            if u is not None:
                pref.append((u, -k, c, nom, k))
        if pref:
            # Menos palabras usadas primero (prefijos mas largos = calce mas
            # limpio); a igualdad, el organismo con mas ordenes.
            pref.sort()
            mejores = [x for x in pref if x[0] == pref[0][0]]
            elegido = pref[0]
            if len(mejores) == 1:
                return {"resuelto": {"codigo": elegido[2], "nombre": elegido[3]},
                        "candidatos": [], "modo": "sigla"}
            return {"resuelto": {"codigo": elegido[2], "nombre": elegido[3]},
                    "modo": "sigla_ambigua",
                    "candidatos": [{"codigo": c, "nombre": n, "ordenes": k}
                                   for _, _, c, n, k in pref[:tope]]}

        # Ultimo recurso: subsecuencia suelta. NUNCA resuelve sola -- solo
        # propone -- porque es la que confundia FONASA.
        sig = [o for o in orgs if _subsecuencia(aguja, _solo_letras(o[1]))]
        if sig:
            return {"resuelto": None, "modo": "sigla_dudosa",
                    "candidatos": [{"codigo": c, "nombre": n, "ordenes": k}
                                   for c, n, k in sig[:tope]]}
    return {"resuelto": None, "candidatos": [], "modo": "sin_coincidencia"}


_MODO_NOTA = {
    "sigla": ("Se interpreto '%s' como una SIGLA y se resolvio a '%s'. La sigla no "
              "figura en el nombre legal; la coincidencia es por subsecuencia de "
              "letras. Si no es el organismo que buscabas, pedilo por su codigo."),
    "sigla_ambigua": ("Se interpreto '%s' como sigla y hay VARIOS organismos con "
                      "el mismo calce. Se uso '%s' por ser el de mas ordenes; mirá "
                      "`resumen.candidatos` antes de afirmar que es ese."),
    "subcadena": ("'%s' se resolvio a '%s' por coincidencia de texto."),
}


# ==========================================================================

def mp_precio_perdido(store, proveedor: str, desde=None, hasta=None,
                      limite: int = 25) -> dict:
    """A que precio gano el competidor en las lineas que este proveedor perdio.

    Es la pregunta que un gerente comercial paga por responder y que hoy nadie en
    su empresa puede contestar: no hay pantalla en mercadopublico.cl que la agregue.
    """
    n = max(1, min(int(limite or 25), 200))
    cond, par = _rango(desde, hasta)
    sql = f"""
        WITH mias AS (
            SELECT o.codigo_licitacion, o.correlativo_item, o.nombre_linea,
                   o.organismo_nombre, o.precio_unitario_clp AS mi_precio,
                   o.cantidad_ofertada, o.fecha_envio_oferta, o.n_oferentes
            FROM oferta o
            WHERE (o.rut_proveedor = ? OR upper(o.nombre_proveedor) LIKE upper(?))
              AND NOT o.seleccionada {cond}
        ),
        ganadoras AS (
            SELECT codigo_licitacion, correlativo_item,
                   min(precio_unitario_clp) AS precio_ganador,
                   arg_min(nombre_proveedor, precio_unitario_clp) AS ganador
            FROM oferta
            WHERE seleccionada AND precio_unitario_clp IS NOT NULL
            GROUP BY 1, 2
        )
        SELECT m.codigo_licitacion, m.organismo_nombre, m.nombre_linea,
               round(m.mi_precio)                              AS mi_precio_clp,
               round(g.precio_ganador)                         AS precio_ganador_clp,
               g.ganador,
               round(100.0 * (m.mi_precio - g.precio_ganador)
                     / nullif(g.precio_ganador, 0), 1)         AS sobreprecio_pct,
               round((m.mi_precio - g.precio_ganador)
                     * coalesce(m.cantidad_ofertada, 1))       AS diferencia_clp,
               m.n_oferentes, CAST(m.fecha_envio_oferta AS DATE) AS fecha
        FROM mias m JOIN ganadoras g
          ON g.codigo_licitacion = m.codigo_licitacion
         AND g.correlativo_item = m.correlativo_item
        WHERE m.mi_precio IS NOT NULL AND g.precio_ganador IS NOT NULL
          AND m.mi_precio > g.precio_ganador
        ORDER BY diferencia_clp DESC NULLS LAST
        LIMIT {n}"""
    cols, rows = store.rows(sql, [proveedor, "%" + str(proveedor) + "%"] + par)

    res = store.one(f"""
        WITH mias AS (
            SELECT o.codigo_licitacion, o.correlativo_item,
                   o.precio_unitario_clp AS mi_precio
            FROM oferta o
            WHERE (o.rut_proveedor = ? OR upper(o.nombre_proveedor) LIKE upper(?))
              AND NOT o.seleccionada {cond}
        ),
        ganadoras AS (
            SELECT codigo_licitacion, correlativo_item,
                   min(precio_unitario_clp) AS precio_ganador
            FROM oferta WHERE seleccionada AND precio_unitario_clp IS NOT NULL
            GROUP BY 1, 2
        )
        SELECT count(*) FILTER (WHERE m.mi_precio IS NOT NULL
                                  AND g.precio_ganador IS NOT NULL),
               count(*) FILTER (WHERE m.mi_precio IS NULL
                                   OR g.precio_ganador IS NULL),
               round(median(100.0 * (m.mi_precio - g.precio_ganador)
                            / nullif(g.precio_ganador, 0)), 1)
        FROM mias m LEFT JOIN ganadoras g
          ON g.codigo_licitacion = m.codigo_licitacion
         AND g.correlativo_item = m.correlativo_item""",
                    [proveedor, "%" + str(proveedor) + "%"] + par)

    resumen = {"lineas_comparadas": res[0] if res else 0,
               "lineas_sin_comparar": res[1] if res else 0,
               "sobreprecio_mediano_pct": res[2] if res else None}
    nota = None
    if not rows:
        nota = ("Cero lineas comparables. Puede ser que el proveedor no aparezca "
                "en la ventana del almacen, que su RUT o nombre no coincida, o que "
                "las lineas que perdio no tengan precio de ganador cargado. NO "
                "significa que nunca haya perdido: probá con mp_huella para ver si "
                "el proveedor existe en el corpus.")
    return _envolver(
        store, f"Lineas perdidas por {proveedor}: mi precio contra el del ganador",
        cols, rows, _tabla(cols),
        [_CAV_MISMA_LINEA, _CAV_PRECIO_NULO, _CAV_VENTANA],
        resumen=resumen, nota=nota)


def mp_sin_competencia(store, proveedor=None, unspsc=None, organismo=None,
                       desde=None, hasta=None, limite: int = 25) -> dict:
    """Licitaciones donde hubo UN SOLO oferente: donde se entra sin pelear precio.

    Con `proveedor`, acota a su huella real de mercado -- los UNSPSC donde efectiva-
    mente oferta -- y saca aquellas donde el unico oferente fue el mismo.
    """
    n = max(1, min(int(limite or 25), 200))
    cond, par = "", []
    if desde:
        cond += " AND o.fecha_envio_oferta >= ?"
        par.append(desde)
    if hasta:
        cond += " AND o.fecha_envio_oferta <= ?"
        par.append(hasta)
    if unspsc:
        cond += " AND o.unspsc_commodity = ?"
        par.append(unspsc)
    if organismo:
        cond += " AND (o.organismo_codigo = ? OR upper(o.organismo_nombre) LIKE upper(?))"
        par += [organismo, "%" + str(organismo) + "%"]
    if proveedor:
        cond += """ AND o.unspsc_commodity IN (
                      SELECT DISTINCT unspsc_commodity FROM oferta
                      WHERE (rut_proveedor = ? OR upper(nombre_proveedor) LIKE upper(?))
                        AND unspsc_commodity IS NOT NULL)
                    AND o.codigo_licitacion NOT IN (
                      SELECT DISTINCT codigo_licitacion FROM oferta
                      WHERE rut_proveedor = ? OR upper(nombre_proveedor) LIKE upper(?))"""
        par += [proveedor, "%" + str(proveedor) + "%"] * 2

    cols, rows = store.rows(f"""
        SELECT o.codigo_licitacion, o.organismo_nombre,
               any_value(o.nombre_linea)                       AS ejemplo_linea,
               count(DISTINCT o.correlativo_item)              AS lineas,
               round(sum(o.monto_adjudicado))                  AS monto_adjudicado_clp,
               any_value(o.unspsc_commodity)                   AS unspsc,
               CAST(max(o.fecha_envio_oferta) AS DATE)         AS fecha
        FROM oferta o
        WHERE o.n_oferentes = 1 {cond}
        GROUP BY o.codigo_licitacion, o.organismo_nombre
        ORDER BY monto_adjudicado_clp DESC NULLS LAST
        LIMIT {n}""", par)

    tot = store.one(f"SELECT count(DISTINCT o.codigo_licitacion) FROM oferta o "
                    f"WHERE o.n_oferentes = 1 {cond}", par)
    resumen = {"licitaciones_con_un_solo_oferente": tot[0] if tot else 0,
               "mostradas": len(rows)}
    lims = [_CAV_VENTANA, {
        "limitacion": (
            "`n_oferentes` es del PROCESO, no de la linea. Y el conteo sale de las "
            "ofertas registradas: una licitacion declarada desierta sin ofertas "
            "cargadas no aparece aca. 'Un solo oferente' no garantiza adjudicacion: "
            "el organismo puede declararla desierta igual."),
        "severidad": "media", "verificado_por": "MEDIDO 2026-08-19"}]
    if proveedor:
        lims.append({
            "limitacion": (
                f"Acotado a la huella de '{proveedor}': los UNSPSC donde EL ya "
                f"oferto en la ventana. Es una definicion operativa de 'su rubro', "
                f"no la clasificacion oficial de su giro. Se excluyen las "
                f"licitaciones donde el unico oferente fue el mismo."),
            "severidad": "media", "verificado_por": "definido por esta consulta"})
    return _envolver(store, "Licitaciones con un solo oferente", cols, rows,
                     _tabla(cols), lims, resumen=resumen)


def mp_huella(store, proveedor: str, limite: int = 20) -> dict:
    """Donde compite un proveedor: sus UNSPSC, con volumen y tasa de exito."""
    n = max(1, min(int(limite or 20), 200))
    cols, rows = store.rows(f"""
        SELECT o.unspsc_commodity                              AS unspsc,
               any_value(o.nombre_linea)                       AS ejemplo_linea,
               count(*)                                        AS ofertas,
               count(*) FILTER (WHERE o.seleccionada)          AS ganadas,
               round(100.0 * count(*) FILTER (WHERE o.seleccionada)
                     / nullif(count(*), 0), 1)                 AS tasa_exito_pct,
               round(sum(o.monto_adjudicado)
                     FILTER (WHERE o.seleccionada))            AS adjudicado_clp,
               count(DISTINCT o.organismo_codigo)              AS organismos
        FROM oferta o
        WHERE (o.rut_proveedor = ? OR upper(o.nombre_proveedor) LIKE upper(?))
          AND o.unspsc_commodity IS NOT NULL
        GROUP BY o.unspsc_commodity
        ORDER BY ofertas DESC
        LIMIT {n}""", [proveedor, "%" + str(proveedor) + "%"])

    res = store.one("""
        SELECT count(*), count(*) FILTER (WHERE seleccionada),
               count(DISTINCT codigo_licitacion),
               count(DISTINCT unspsc_commodity), min(CAST(fecha_envio_oferta AS DATE)),
               max(CAST(fecha_envio_oferta AS DATE))
        FROM oferta
        WHERE rut_proveedor = ? OR upper(nombre_proveedor) LIKE upper(?)""",
                    [proveedor, "%" + str(proveedor) + "%"])
    resumen = {"ofertas_totales": res[0], "ganadas": res[1],
               "tasa_exito_pct": round(100.0 * res[1] / res[0], 1) if res[0] else None,
               "licitaciones": res[2], "codigos_unspsc": res[3],
               "primera_oferta": str(res[4]) if res[4] else None,
               "ultima_oferta": str(res[5]) if res[5] else None}
    nota = None if res[0] else (
        f"'{proveedor}' no aparece en el corpus. Probá con el RUT en formato "
        f"NN.NNN.NNN-D, o con una parte del nombre; y recorda que la ventana son "
        f"los ultimos 12 meses.")
    return _envolver(
        store, f"Huella de mercado de {proveedor}", cols, rows,
        _barra("unspsc", "ofertas", "Ofertas presentadas", "conteo", "entero"),
        [_CAV_VENTANA, {
            "limitacion": (
                "`tasa_exito_pct` es sobre LINEAS ofertadas, no sobre licitaciones "
                "ganadas: un proveedor puede ganar 3 de 10 lineas de una misma "
                "licitacion. Y el match por nombre es por subcadena: si dos "
                "empresas comparten prefijo, se mezclan. Preferí el RUT."),
            "severidad": "media", "verificado_por": "definido por esta consulta"}],
        resumen=resumen, nota=nota)


def mp_comprador_perfil(store, organismo: str, limite: int = 15) -> dict:
    """Que compra un organismo, a quien y por cuanto."""
    n = max(1, min(int(limite or 15), 200))
    r = resolver_organismo(store, organismo)
    if r["resuelto"] is None:
        return _envolver(
            store, f"Comprador '{organismo}': no se pudo resolver", [], [],
            _tabla([]), [_CAV_VENTANA],
            resumen={"modo_de_resolucion": r["modo"],
                     "candidatos": r.get("candidatos") or []},
            nota=("No se encontro un organismo que calce con '%s'. Si es una sigla, "
                  "probá con parte del nombre legal; mp_comprador_catalogo trae el "
                  "listado completo. `resumen.candidatos` trae lo mas parecido si "
                  "hubo algo." % organismo))
    codigo = r["resuelto"]["codigo"]
    nombre_real = r["resuelto"]["nombre"]
    par = [codigo, nombre_real]
    cols, rows = store.rows(f"""
        SELECT oc.nombre_proveedor                             AS proveedor,
               count(DISTINCT oc.codigo)                       AS ordenes,
               round(sum(oc.total_clp))                        AS monto_clp,
               round(100.0 * sum(oc.total_clp)
                     / nullif(sum(sum(oc.total_clp)) OVER (), 0), 1) AS share_pct,
               CAST(max(oc.fecha_envio) AS DATE)               AS ultima_compra
        FROM orden_compra oc
        WHERE (oc.organismo_codigo = ? OR oc.organismo_nombre = ?)
          AND oc.total_clp IS NOT NULL
        GROUP BY oc.nombre_proveedor
        ORDER BY monto_clp DESC NULLS LAST
        LIMIT {n}""", par)

    res = store.one("""
        SELECT count(DISTINCT codigo), round(sum(total_clp)),
               count(DISTINCT nombre_proveedor),
               count(*) FILTER (WHERE excluida_por_fuente),
               CAST(min(fecha_envio) AS DATE), CAST(max(fecha_envio) AS DATE)
        FROM orden_compra
        WHERE organismo_codigo = ? OR organismo_nombre = ?""", par)
    resumen = {"ordenes": res[0], "monto_total_clp": res[1],
               "proveedores_distintos": res[2],
               "ordenes_excluidas_por_la_fuente": res[3],
               "desde": str(res[4]) if res[4] else None,
               "hasta": str(res[5]) if res[5] else None}
    nota = None if res[0] else (
        f"'{organismo}' no aparece como comprador en el corpus. Probá con "
        f"mp_comprador_catalogo para encontrar el nombre o el codigo exacto.")
    if r["modo"] in _MODO_NOTA and nota is None:
        nota = _MODO_NOTA[r["modo"]] % (organismo, nombre_real)
        if r.get("candidatos"):
            resumen["candidatos"] = r["candidatos"]
    resumen["organismo_resuelto"] = nombre_real
    resumen["modo_de_resolucion"] = r["modo"]
    return _envolver(
        store, f"Proveedores de {nombre_real}, por monto", cols, rows,
        _barra("proveedor", "monto_clp", "Monto en CLP", "CLP", "moneda_clp"),
        [_CAV_VENTANA, {
            "limitacion": (
                "El monto EXCLUYE las ordenes que ChileCompra saca de sus cifras "
                "oficiales por error de monto o de moneda; `resumen."
                "ordenes_excluidas_por_la_fuente` dice cuantas fueron. Sin esa "
                "exclusion, [MEDIDO 2026-08-19] una sola orden mal cargada ponia a "
                "la I. Municipalidad de Rio Bueno tercera en el gasto nacional."),
            "severidad": "alta", "verificado_por": "MEDIDO 2026-08-19"}],
        resumen=resumen, nota=nota)
