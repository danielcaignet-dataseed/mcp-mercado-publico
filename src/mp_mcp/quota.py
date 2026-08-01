"""Gobernador de cuota.

Es el UNICO componente con permiso de tocar api.mercadopublico.cl (SPEC-001 §3).
Lleva libro mayor de cada hit y se niega antes de pasarse, en vez de descubrirlo
cuando ChileCompra corta el acceso.

Reserva: una fraccion de la cuota queda apartada para consultas on-demand de
agentes, para que una ingesta larga no deje a Demeter sin frescura.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .config import QUOTA_DOCUMENTADA_DIA
from .store import Store

RESERVA_ON_DEMAND = 1_000       # hits que la ingesta no puede consumir


class CuotaAgotada(RuntimeError):
    pass


class Cuota:
    def __init__(self, store: Store, limite: int = QUOTA_DOCUMENTADA_DIA):
        self.store = store
        self.limite = limite

    def usados_24h(self) -> int:
        """Requests que consumen cuota.

        Los 429 se excluyen: `[Adivinando]` una peticion rechazada por el limite
        de tasa no deberia descontar de los 10.000, pero no esta medido (P-03).
        Si resultara que si descuentan, este contador subestima el consumo, y
        por eso `estado()` reporta los throttled aparte y bien visibles.
        """
        desde = datetime.now(timezone.utc) - timedelta(hours=24)
        r = self.store.one(
            "SELECT count(*) FROM cuota_hit WHERE ts >= ? AND status <> 429", [desde])
        return int(r[0]) if r else 0

    def throttled_24h(self) -> int:
        desde = datetime.now(timezone.utc) - timedelta(hours=24)
        r = self.store.one(
            "SELECT count(*) FROM cuota_hit WHERE ts >= ? AND status = 429", [desde])
        return int(r[0]) if r else 0

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

    def registrar(self, endpoint: str, motivo: str, status: int) -> None:
        if self.store.read_only:
            return
        self.store.con.execute(
            "INSERT INTO cuota_hit VALUES (?, ?, ?, ?)",
            [datetime.now(timezone.utc), endpoint, motivo, status],
        )

    def estado(self) -> dict:
        usados = self.usados_24h()
        thr = self.throttled_24h()
        return {
            "limite_documentado_dia": self.limite,
            "usados_ultimas_24h": usados,
            "throttled_429_ultimas_24h": thr,
            "disponibles_on_demand": max(0, self.limite - usados),
            "disponibles_ingesta": max(0, self.limite - RESERVA_ON_DEMAND - usados),
            "nota": "Hay DOS limites. El diario de 10.000 es [DOC]. Ademas existe un "
                    "limite de tasa de corto plazo NO documentado, medido el "
                    "2026-07-31: ver sonda P-21. Los 429 no cuentan aca (supuesto sin "
                    "medir) y por eso se reportan aparte.",
        }
