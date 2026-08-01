"""Acceso al almacen DuckDB."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from .config import Config

_SCHEMA = Path(__file__).resolve().parents[2] / "sql" / "schema.sql"


CAT = "catalogo"          # nombre del schema adjunto


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
        self.con.execute(f"ATTACH IF NOT EXISTS '{cfg.catalog_path.as_posix()}' AS {CAT}")
        self._catalogo()
        self._firma = self._stat()

    def _catalogo(self) -> None:
        self.con.execute(
            f"ATTACH IF NOT EXISTS '{self.cfg.catalog_path.as_posix()}' AS {CAT}")
        self.con.execute(f"""
            CREATE TABLE IF NOT EXISTS {CAT}.consulta_guardada (
                query_id VARCHAR PRIMARY KEY, nombre VARCHAR, descripcion VARCHAR,
                modo VARCHAR, consulta JSON, etiquetas VARCHAR[],
                creada_en TIMESTAMP, ultima_corrida TIMESTAMP)""")

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
        self._catalogo()
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
        self.con.execute(
            """INSERT OR REPLACE INTO catalogo.consulta_guardada
               (query_id, nombre, descripcion, modo, consulta, etiquetas, creada_en,
                ultima_corrida)
               VALUES (?, ?, ?, ?, ?, ?, ?, NULL)""",
            [query_id, nombre, descripcion, modo,
             json.dumps(consulta, ensure_ascii=False), etiquetas,
             datetime.now(timezone.utc)],
        )

    def leer_consulta(self, ref: str) -> dict | None:
        r = self.one(
            """SELECT query_id, nombre, descripcion, modo, consulta
               FROM catalogo.consulta_guardada WHERE query_id = ? OR nombre = ? LIMIT 1""",
            [ref, ref],
        )
        if not r:
            return None
        return {"query_id": r[0], "nombre": r[1], "descripcion": r[2],
                "modo": r[3], "consulta": json.loads(r[4])}

    def listar_consultas(self, etiqueta: str | None = None) -> list[dict]:
        if etiqueta:
            cols, rows = self.rows(
                """SELECT query_id, nombre, descripcion, modo, etiquetas
                   FROM catalogo.consulta_guardada WHERE list_contains(etiquetas, ?)
                   ORDER BY nombre""", [etiqueta])
        else:
            cols, rows = self.rows(
                """SELECT query_id, nombre, descripcion, modo, etiquetas
                   FROM catalogo.consulta_guardada ORDER BY nombre""")
        return [dict(zip(cols, r)) for r in rows]

    def marcar_corrida(self, query_id: str) -> None:
        self.con.execute(
            "UPDATE catalogo.consulta_guardada SET ultima_corrida = ? WHERE query_id = ?",
            [datetime.now(timezone.utc), query_id])

    def close(self) -> None:
        self.con.close()
