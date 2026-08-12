"""Gobernador de cuota.

Es el UNICO componente con permiso de tocar api.mercadopublico.cl (SPEC-001 §3).
Lleva libro mayor de cada hit y se niega antes de pasarse, en vez de descubrirlo
cuando ChileCompra corta el acceso.

Reserva: una fraccion de la cuota queda apartada para consultas on-demand de
agentes, para que una ingesta larga no deje a Demeter sin frescura.

POR QUE EL LIBRO MAYOR NO VIVE EN DUCKDB (2026-08-12)
-----------------------------------------------------
Vivio en la tabla `cuota_hit` de `mp.duckdb` hasta hoy, y ahi el contador mentia
por dos razones, las dos medidas:

1. El servidor MCP abre los hechos en SOLO LECTURA, asi que `registrar()` hacia
   `return` en silencio. Todo hit de una tool viva se perdia: el agente veia
   0 usados despues de gastar cuota real.
2. `mp-ingest publicar` reemplaza `mp.duckdb` por `mp.next.duckdb` con un rename
   atomico. El archivo nuevo se construye desde cero, asi que **cada publicacion
   borraba el libro mayor**. [MEDIDO 2026-08-12] la tabla tenia 4.374 filas,
   todas de una sola corrida (06/08 04:16-07:27) y ninguna anterior.

Arreglarlo abriendo los hechos en escritura no sirve: DuckDB admite un escritor
por archivo, asi que el servidor le quitaria el lock a la ingesta, y ademas el
rename seguiria borrando la historia. Poner el libro mayor en `catalogo.duckdb`
tampoco: el servidor se queda con ese lock y entonces la ingesta no puede leerlo,
que es justo el proceso que necesita el contador para frenar a tiempo.

Por eso el libro mayor es un JSONL de solo-append, un archivo por dia UTC:
sin lock, escribible por los dos procesos a la vez, y sobrevive al rename porque
no esta dentro del snapshot. La tabla `cuota_hit` se sigue escribiendo cuando el
almacen es escribible, porque las sondas P-03 y P-21 la consultan, pero ya no es
la fuente del contador.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import QUOTA_DOCUMENTADA_DIA
from .store import Store

RESERVA_ON_DEMAND = 1_000       # hits que la ingesta no puede consumir

# El conteo se recalcula a lo sumo cada TTL segundos. Sin esto, `exigir()` releeria
# el JSONL entero en cada hit: una ingesta de 4.000 hits se volvia cuadratica.
# Los hits propios se suman al vuelo, asi que dentro del proceso el numero es
# exacto; el TTL solo demora en cuanto se ve lo que gasto el OTRO proceso.
_TTL_CACHE_S = 5.0

RETENCION_DIAS = 7              # archivos de dias mas viejos se pueden borrar


class CuotaAgotada(RuntimeError):
    pass


class Cuota:
    def __init__(self, store: Store, limite: int = QUOTA_DOCUMENTADA_DIA):
        self.store = store
        self.limite = limite
        self.dir_ledger = Path(
            os.environ.get("MP_CUOTA_LEDGER", Path(store.cfg.data_dir) / "cuota"))
        # Diagnostico que `estado()` publica: si el libro mayor no se puede
        # escribir, el numero es basura y el agente tiene que enterarse.
        self.escrituras_ok = 0
        self.escrituras_fallidas = 0
        self.ultimo_fallo: str | None = None
        self._cache: tuple[float, int, int] | None = None

    # -- el libro mayor ----------------------------------------------------

    def _archivo(self, ts: datetime) -> Path:
        return self.dir_ledger / f"cuota-{ts:%Y%m%d}.jsonl"

    def _leer_ventana(self, desde: datetime) -> tuple[int, int]:
        """(usados, throttled) desde `desde`, leyendo hoy y ayer en UTC.

        Una linea ilegible se cuenta como usada: si hubo un hit a medio escribir,
        el hit igual ocurrio. Preferimos sobrestimar el consumo antes que dejar
        pasar una llamada de mas.
        """
        usados = thr = 0
        ahora = datetime.now(timezone.utc)
        for dia in (desde, ahora):
            f = self._archivo(dia)
            if not f.exists():
                continue
            try:
                texto = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for linea in texto.splitlines():
                if not linea.strip():
                    continue
                try:
                    r = json.loads(linea)
                    ts = datetime.fromisoformat(r["ts"])
                    if ts < desde:
                        continue
                    if int(r.get("status", 0)) == 429:
                        thr += 1
                    else:
                        usados += 1
                except (ValueError, KeyError, TypeError):
                    usados += 1
            if self._archivo(desde) == self._archivo(ahora):
                break
        return usados, thr

    def _conteo(self) -> tuple[int, int]:
        import time
        ahora = time.monotonic()
        if self._cache and ahora - self._cache[0] < _TTL_CACHE_S:
            return self._cache[1], self._cache[2]
        desde = datetime.now(timezone.utc) - timedelta(hours=24)
        usados, thr = self._leer_ventana(desde)
        self._cache = (ahora, usados, thr)
        return usados, thr

    # -- lectura -----------------------------------------------------------

    def usados_24h(self) -> int:
        """Requests que consumen cuota.

        Los 429 se excluyen: `[Adivinando]` una peticion rechazada por el limite
        de tasa no deberia descontar de los 10.000, pero no esta medido (P-03).
        Si resultara que si descuentan, este contador subestima el consumo, y
        por eso `estado()` reporta los throttled aparte y bien visibles.
        """
        return self._conteo()[0]

    def throttled_24h(self) -> int:
        return self._conteo()[1]

    def historico_en_almacen(self) -> int:
        """Filas de `cuota_hit` en el snapshot servido.

        NO entra en el contador: es historia de la ingesta que publico este
        snapshot y se borra en la siguiente publicacion. Se expone solo para que
        se vea de donde sale cada numero.
        """
        try:
            r = self.store.one("SELECT count(*) FROM cuota_hit")
            return int(r[0]) if r else 0
        except Exception:                                     # noqa: BLE001
            return 0

    def disponibles(self, motivo: str) -> int:
        techo = self.limite - (RESERVA_ON_DEMAND if motivo == "ingesta" else 0)
        return max(0, techo - self.usados_24h())

    def exigir(self, n: int, motivo: str) -> None:
        libres = self.disponibles(motivo)
        if n > libres:
            reserva = (f" (la ingesta respeta una reserva de {RESERVA_ON_DEMAND} hits "
                       f"para consultas on-demand)" if motivo == "ingesta" else "")
            raise CuotaAgotada(
                f"Necesitas {n} hits y quedan {libres} de {self.limite} en la ventana "
                f"de 24 h{reserva}. Opciones: (a) esperar, (b) reducir el rango de "
                f"fechas, (c) responder desde el almacen sin frescura del dia. "
                f"El limite lo fija ChileCompra y no es modificable."
            )

    # -- escritura ---------------------------------------------------------

    def registrar(self, endpoint: str, motivo: str, status: int) -> None:
        """Anota un hit. NO propaga errores.

        Un fallo del libro mayor no puede tumbar una consulta que ya se hizo y ya
        gasto cuota. Pero tampoco se traga: queda contado en `escrituras_fallidas`
        y `estado()` lo publica como limitacion severa.
        """
        ts = datetime.now(timezone.utc)
        linea = json.dumps(
            {"ts": ts.isoformat(), "endpoint": str(endpoint)[:300],
             "motivo": str(motivo)[:80], "status": int(status)},
            ensure_ascii=False)
        try:
            self.dir_ledger.mkdir(parents=True, exist_ok=True)
            # Una escritura sola, en modo append y por debajo de PIPE_BUF: dos
            # procesos escribiendo a la vez no se entrelazan las lineas.
            with open(self._archivo(ts), "a", encoding="utf-8") as fh:
                fh.write(linea + "\n")
            self.escrituras_ok += 1
        except OSError as exc:
            self.escrituras_fallidas += 1
            self.ultimo_fallo = f"{type(exc).__name__}: {exc}"

        # El conteo propio se mantiene exacto sin releer el archivo.
        if self._cache:
            t, u, thr = self._cache
            self._cache = (t, u, thr + 1) if status == 429 else (t, u + 1, thr)

        # La tabla sigue existiendo para las sondas P-03 y P-21, que la
        # consultan. No es la fuente del contador.
        if not self.store.read_only:
            try:
                self.store.con.execute(
                    "INSERT INTO cuota_hit VALUES (?, ?, ?, ?)",
                    [ts, endpoint, motivo, status])
            except Exception:                                 # noqa: BLE001
                pass

    def purgar(self) -> int:
        """Borra archivos de dias fuera de la retencion. Devuelve cuantos borro.

        Seguro sin lock: a un archivo de un dia ya cerrado nadie le escribe mas.
        """
        limite = (datetime.now(timezone.utc) - timedelta(days=RETENCION_DIAS)).date()
        n = 0
        if not self.dir_ledger.exists():
            return 0
        for f in self.dir_ledger.glob("cuota-*.jsonl"):
            try:
                d = datetime.strptime(f.stem[6:], "%Y%m%d").date()
            except ValueError:
                continue
            if d < limite:
                try:
                    f.unlink()
                    n += 1
                except OSError:
                    pass
        return n

    # -- reporte -----------------------------------------------------------

    def salud(self) -> dict:
        """Si el libro mayor no se puede escribir, el contador no vale nada.

        Se comprueba de verdad -- se toca el archivo del dia -- en vez de asumir
        que anda. La comprobacion vale porque el modo de acceso del almacen ya
        engano una vez a este mismo contador.
        """
        escribible, detalle = True, None
        try:
            self.dir_ledger.mkdir(parents=True, exist_ok=True)
            with open(self._archivo(datetime.now(timezone.utc)), "a",
                      encoding="utf-8"):
                pass
        except OSError as exc:
            escribible, detalle = False, f"{type(exc).__name__}: {exc}"
        if self.escrituras_fallidas:
            escribible = False
            detalle = detalle or self.ultimo_fallo
        return {
            "ledger": str(self.dir_ledger),
            "escribible": escribible,
            "hits_anotados_en_esta_sesion": self.escrituras_ok,
            "hits_que_no_se_pudieron_anotar": self.escrituras_fallidas,
            "detalle_del_fallo": detalle,
        }

    def estado(self) -> dict:
        usados = self.usados_24h()
        thr = self.throttled_24h()
        salud = self.salud()
        est = {
            "limite_documentado_dia": self.limite,
            "usados_ultimas_24h": usados,
            "throttled_429_ultimas_24h": thr,
            "disponibles_on_demand": max(0, self.limite - usados),
            "disponibles_ingesta": max(0, self.limite - RESERVA_ON_DEMAND - usados),
            "contador_confiable": salud["escribible"],
            "libro_mayor": salud,
            "hits_historicos_en_el_snapshot": self.historico_en_almacen(),
            "nota": "Hay DOS limites. El diario de 10.000 es [DOC]. Ademas existe un "
                    "limite de tasa de corto plazo NO documentado, medido el "
                    "2026-07-31: ver sonda P-21. Los 429 no cuentan aca (supuesto sin "
                    "medir) y por eso se reportan aparte. "
                    "`hits_historicos_en_el_snapshot` NO entra en el contador: es la "
                    "corrida de ingesta que publico este snapshot y se borra en la "
                    "siguiente publicacion.",
        }
        if not salud["escribible"]:
            est["ADVERTENCIA"] = (
                "El libro mayor no se puede escribir, asi que `usados_ultimas_24h` "
                "esta por debajo del consumo real y puede ser 0 aunque se haya "
                "gastado cuota. NO uses este numero como presupuesto: "
                f"{salud['detalle_del_fallo']}")
        return est
