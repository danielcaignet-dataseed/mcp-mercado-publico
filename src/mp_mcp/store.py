"""Acceso al almacen DuckDB."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from .config import Config

_SCHEMA = Path(__file__).resolve().parents[2] / "sql" / "schema.sql"


class Store:
    """Dos bases, por la restriccion de un escritor por archivo en DuckDB.

    - `mp.duckdb`      hechos + ledgers. Lo escribe la ingesta y las sondas.
    - `catalogo.duckdb` consultas guardadas. Lo escribe el servidor MCP.

    En `read_only=True` (el modo del servidor MCP en Demeter) los hechos se
    abren sin lock de escritura, asi la ingesta puede correr en paralelo.
    """

    def __init__(self, cfg: Config, read_only: bool = False):
        self.cfg = cfg
        self.read_only = read_only
        nueva = not Path(cfg.db_path).exists()
        if read_only and nueva:
            raise RuntimeError(
                f"No existe {cfg.db_path}. El servidor MCP no crea el almacen: "
                f"corre primero la ingesta (mp-ingest) con permiso de escritura.")
        self.con = duckdb.connect(str(cfg.db_path), read_only=read_only)
        if not read_only:
            self.con.execute(_SCHEMA.read_text(encoding="utf-8"))
        self._cat = None          # conexion perezosa, ver la propiedad cat
        self._firma = self._stat()

    @property
    def cat(self):
        """Conexion al catalogo, abierta al primer uso.

        No es un ATTACH: un ATTACH hereda el modo de acceso de la conexion
        principal, y con los hechos en solo-lectura `CREATE TABLE catalogo.*`
        falla con "attached in read-only mode". Lo descubrio el primer arranque
        real en modo produccion, que es el modo que el smoke test no ejercitaba.

        Perezosa porque la mayoria de las consultas no tocan el catalogo: abrir
        dos bases en el constructor era gratis en fallos y no en beneficios.
        """
        if self._cat is None:
            self._cat = duckdb.connect(str(self.cfg.catalog_path), read_only=False)
            self._cat.execute("""
                CREATE TABLE IF NOT EXISTS consulta_guardada (
                    query_id VARCHAR PRIMARY KEY, nombre VARCHAR, descripcion VARCHAR,
                    modo VARCHAR, consulta JSON, etiquetas VARCHAR[],
                    creada_en TIMESTAMP, ultima_corrida TIMESTAMP)""")
        return self._cat

    def _stat(self):
        try:
            s = Path(self.cfg.db_path).stat()
            return (s.st_ino, s.st_mtime_ns, s.st_size)
        except OSError:
            return None

    def refrescar(self) -> bool:
        """Reabre si la ingesta publico un snapshot nuevo.

        [MEDIDO] 2026-07-31: DuckDB NO permite abrir en solo-lectura mientras otro
        proceso tiene el archivo tomado. Por eso la ingesta no escribe sobre la
        base servida: construye `mp.next.duckdb` aparte y hace un rename atomico.
        En Linux el rename sobre un archivo abierto no rompe al lector -- conserva
        el inodo viejo hasta que reabre -- y esto detecta el cambio y reabre.
        Sin este metodo, el MCP seguiria sirviendo el snapshot anterior para
        siempre.
        """
        if not self.read_only:
            return False
        actual = self._stat()
        if actual == self._firma or actual is None:
            return False
        try:
            self.con.close()
        except Exception:                                     # noqa: BLE001
            pass
        self.con = duckdb.connect(str(self.cfg.db_path), read_only=True)
        self._firma = actual
        return True

    # -- consultas ---------------------------------------------------------

    def rows(self, sql: str, params: list | None = None) -> tuple[list[str], list[list]]:
        self.refrescar()
        cur = self.con.execute(sql, params or [])
        cols = [d[0] for d in cur.description]
        return cols, [list(r) for r in cur.fetchall()]

    def one(self, sql: str, params: list | None = None):
        self.refrescar()
        r = self.con.execute(sql, params or []).fetchone()
        return r

    def tabla_vacia(self, tabla: str) -> bool:
        try:
            return self.one(f"SELECT count(*) FROM {tabla}")[0] == 0
        except duckdb.Error:
            return True

    # -- estado del almacen -----------------------------------------------

    def as_of(self) -> dict:
        """Frescura por fuente. Va en meta de cada respuesta: sin esto el agente
        afirma como actual un dato de hace meses."""
        out = {}
        for tabla, campo in (
            ("licitacion", "fecha_publicacion"),
            ("adjudicacion_item", "fecha_adjudicacion"),
            ("orden_compra", "fecha_envio"),
        ):
            try:
                r = self.one(
                    f"SELECT max({campo}), count(*), max(_ingerido_en) FROM {tabla}")
                out[tabla] = {
                    "dato_mas_reciente": str(r[0]) if r and r[0] else None,
                    "filas": r[1] if r else 0,
                    "ultima_ingesta": str(r[2]) if r and r[2] else None,
                }
            except duckdb.Error:
                out[tabla] = {"dato_mas_reciente": None, "filas": 0, "ultima_ingesta": None}
        return out

    def log_ingesta(self, fuente: str, detalle: str, filas: int, ok: bool) -> None:
        if self.read_only:
            return
        self.con.execute(
            "INSERT INTO ingesta_log VALUES (?, ?, ?, ?, ?)",
            [datetime.now(timezone.utc), fuente, detalle, filas, ok],
        )

    # -- consultas guardadas ----------------------------------------------

    def guardar_consulta(self, query_id: str, nombre: str, descripcion: str,
                         modo: str, consulta: dict, etiquetas: list[str]) -> None:
        self.cat.execute(
            """INSERT OR REPLACE INTO consulta_guardada
               (query_id, nombre, descripcion, modo, consulta, etiquetas, creada_en,
                ultima_corrida)
               VALUES (?, ?, ?, ?, ?, ?, ?, NULL)""",
            [query_id, nombre, descripcion, modo,
             json.dumps(consulta, ensure_ascii=False), etiquetas,
             datetime.now(timezone.utc)],
        )

    def leer_consulta(self, ref: str) -> dict | None:
        r = self.cat.execute(
            """SELECT query_id, nombre, descripcion, modo, consulta
               FROM consulta_guardada WHERE query_id = ? OR nombre = ? LIMIT 1""",
            [ref, ref],
        ).fetchone()
        if not r:
            return None
        return {"query_id": r[0], "nombre": r[1], "descripcion": r[2],
                "modo": r[3], "consulta": json.loads(r[4])}

    def listar_consultas(self, etiqueta: str | None = None) -> list[dict]:
        def _q(sql, p=None):
            cur = self.cat.execute(sql, p or [])
            return [d[0] for d in cur.description], [list(r) for r in cur.fetchall()]
        if etiqueta:
            cols, rows = _q(
                """SELECT query_id, nombre, descripcion, modo, etiquetas
                   FROM consulta_guardada WHERE list_contains(etiquetas, ?)
                   ORDER BY nombre""", [etiqueta])
        else:
            cols, rows = _q(
                """SELECT query_id, nombre, descripcion, modo, etiquetas
                   FROM consulta_guardada ORDER BY nombre""")
        return [dict(zip(cols, r)) for r in rows]

    def marcar_corrida(self, query_id: str) -> None:
        self.cat.execute(
            "UPDATE consulta_guardada SET ultima_corrida = ? WHERE query_id = ?",
            [datetime.now(timezone.utc), query_id])

    # -- volcado de listados en vivo ---------------------------------------

    _DDL_VOLCADO = """
        CREATE TABLE IF NOT EXISTS listado_vivo (
            capturado_en TIMESTAMP, tipo VARCHAR, filtros VARCHAR,
            codigo VARCHAR, nombre VARCHAR, codigo_estado INTEGER,
            fecha_cierre VARCHAR)"""

    def volcar_listado(self, tipo: str, filtros: str, filas: list) -> dict:
        """Guarda un listado en vivo COMPLETO en el catalogo, no en los hechos.

        Va al catalogo porque es la unica base que el servidor MCP abre en
        escritura -- los hechos estan en solo-lectura para no quitarle el lock a
        la ingesta -- y porque `mp-ingest publicar` los reemplaza con un rename,
        asi que lo que viviera ahi se borraria en la siguiente publicacion. Es el
        mismo razonamiento que el del libro mayor de cuota (ver quota.py).

        Reemplaza el volcado anterior de la MISMA consulta: es una foto de esa
        pregunta, no una bitacora que crece sin techo.
        """
        con = self.cat
        con.execute(self._DDL_VOLCADO)
        con.execute("DELETE FROM listado_vivo WHERE tipo = ? AND filtros = ?",
                    [tipo, filtros])
        ts = datetime.now(timezone.utc)
        # executemany, no un execute por fila. [MEDIDO 2026-08-19] con 11.681 filas
        # el bucle de a una dejo un WAL de 3,8 MB para una tabla de ~500 KB.
        con.executemany(
            "INSERT INTO listado_vivo VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(ts, tipo, filtros,
              f.get("CodigoExterno") or f.get("Codigo"),
              f.get("Nombre"),
              f.get("CodigoEstado"),
              str(f.get("FechaCierre")) if f.get("FechaCierre") else None)
             for f in filas])
        # CHECKPOINT explicito: el servidor MCP tiene la conexion abierta de forma
        # indefinida, asi que DuckDB nunca cierra por su cuenta y el WAL crece en
        # cada volcado sin volver a bajar. El dato ya estaba durable en el WAL; lo
        # que esto evita es que el archivo lateral quede mas grande que la base.
        try:
            con.execute("CHECKPOINT")
        except Exception:                                     # noqa: BLE001
            pass                                              # el dato ya esta en el WAL
        return {"tabla": "listado_vivo", "tipo": tipo, "filtros": filtros,
                "filas": len(filas), "capturado_en": ts.isoformat()}

    def leer_volcado(self, tipo: str, filtros=None, texto=None, codigo_estado=None,
                     limite: int = 50, offset: int = 0) -> dict:
        """Pagina y filtra UN volcado. NO consume cuota: es local.

        Acotar a un solo `filtros` no es un detalle de comodidad. [MEDIDO
        2026-08-19] con dos volcados de `ordenes` conviviendo -- uno de
        {"fecha":"28072026"} con 11.681 filas y otro de
        {"estado":"todos","fecha":"19082026"} con 62 -- una lectura por `tipo`
        devolvia 11.743 y los conteos por estado dejaban de calzar con los de la
        API (7.775 contra 7.774). El agente habria informado un total que mezcla
        dos preguntas distintas sin saberlo.

        Sin `filtros`, se lee el volcado MAS RECIENTE de ese tipo, y la respuesta
        dice cual leyo y que otros habia.
        """
        con = self.cat
        con.execute(self._DDL_VOLCADO)

        if filtros is None:
            r = con.execute(
                "SELECT filtros FROM listado_vivo WHERE tipo = ? "
                "ORDER BY capturado_en DESC LIMIT 1", [tipo]).fetchone()
            if r is None:
                return {"coinciden": 0, "devueltas": 0, "filas": [],
                        "leyendo_volcado": None, "filas_en_el_volcado": 0,
                        "otros_volcados": [],
                        "nota": ("No hay ningun volcado de tipo '%s'. Primero llama "
                                 "la tool en vivo con volcar=true." % tipo)}
            filtros = r[0]

        where, params = ["tipo = ?", "filtros = ?"], [tipo, filtros]
        if texto:
            where.append("lower(nombre) LIKE ?")
            params.append("%" + str(texto).lower() + "%")
        if codigo_estado is not None:
            where.append("codigo_estado = ?")
            params.append(int(codigo_estado))
        w = " AND ".join(where)
        total = con.execute(
            "SELECT count(*) FROM listado_vivo WHERE " + w, params).fetchone()[0]
        cur = con.execute(
            "SELECT codigo, nombre, codigo_estado, fecha_cierre FROM listado_vivo "
            "WHERE " + w + " ORDER BY codigo LIMIT ? OFFSET ?",
            params + [int(limite), int(offset)])
        cols = [d[0] for d in cur.description]
        filas = [dict(zip(cols, r)) for r in cur.fetchall()]
        capt = con.execute(
            "SELECT max(capturado_en), count(*) FROM listado_vivo "
            "WHERE tipo = ? AND filtros = ?", [tipo, filtros]).fetchone()
        otros = [x for x in self.volcados()
                 if x["tipo"] == tipo and x["filtros"] != filtros]
        return {"coinciden": total, "devueltas": len(filas), "filas": filas,
                "leyendo_volcado": {"tipo": tipo, "filtros": filtros,
                                    "capturado_en": str(capt[0]) if capt and capt[0]
                                    else None},
                "volcado_capturado_en": str(capt[0]) if capt and capt[0] else None,
                "filas_en_el_volcado": capt[1] if capt else 0,
                "otros_volcados": otros}

    def volcados(self) -> list:
        """Que volcados hay, para no adivinar el `tipo`."""
        con = self.cat
        con.execute(self._DDL_VOLCADO)
        cur = con.execute(
            "SELECT tipo, filtros, max(capturado_en) AS capturado_en, "
            "count(*) AS filas FROM listado_vivo GROUP BY 1, 2 ORDER BY 3 DESC")
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    def close(self) -> None:
        self.con.close()
        if self._cat is not None:
            self._cat.close()
            self._cat = None
