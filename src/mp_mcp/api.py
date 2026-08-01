"""Cliente de api.mercadopublico.cl.

Dos reglas duras:

1. Todo error pasa por scrub() antes de propagarse. httpx incluye la URL en el
   texto de sus excepciones, y la URL lleva el ticket. Sin esto, el ticket
   termina en el log del agente. Es exactamente la fuga que ocurrio en D-012.
2. Ningun request sale sin pasar por el gobernador de cuota.
"""

from __future__ import annotations

import json as _json
import os
import time
from typing import Any

import httpx

from .config import API_BASE, Config, scrub
from .quota import Cuota

# [MEDIDO] 2026-07-31 (sonda P-21): ademas de la cuota diaria de 10.000,
# ChileCompra aplica un limite de tasa de corto plazo que NO documenta.
#   0,1 / 0,3 / 0,5 / 0,75 s -> 1 de cada 4 requests rechazado
#   1,0 s                    -> 2 de cada 4 rechazados
#   1,5 s                    -> 0 de 4
# La no-monotonia sugiere un bucket con rafaga permitida, no un espaciado fijo,
# asi que 1,5 s es un valor operativo seguro, no un umbral exacto. 40 req/min.
MIN_INTERVALO_S = float(os.environ.get("MP_MIN_INTERVALO", "1.5"))
MAX_REINTENTOS = int(os.environ.get("MP_MAX_REINTENTOS", "4"))
BACKOFF_BASE_S = 2.0


class ErrorAPI(RuntimeError):
    pass


def _decodificar(raw: bytes) -> str:
    """[MEDIDO] 2026-07-31: la API devuelve texto que httpx decodifica mal.

    Sonda V-01 trajo "Art�culos", "Tuber�a de pl�stico": el
    caracter de reemplazo U+FFFD. El cuerpo no es UTF-8 valido, es cp1252/latin-1.
    Sin esto, todo nombre de producto, categoria y organismo entra corrupto al
    almacen, y el matching por texto -- que es lo que compensa la mala calidad de
    los codigos UNSPSC -- deja de funcionar sobre cualquier palabra con tilde o n.
    """
    for enc in ("utf-8", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


class ClienteAPI:
    """Cliente con marcapasos y reintento.

    Sin marcapasos la API devuelve 429 en cadena y la ingesta no avanza. El
    intervalo minimo se adapta hacia arriba cuando aparecen 429 y no vuelve a
    bajar dentro de la misma corrida: preferimos ir lento a que ChileCompra
    marque el ticket por inconsistencia de uso.
    """

    def __init__(self, cfg: Config, cuota: Cuota, timeout: float = 30.0):
        self.cfg = cfg
        self.cuota = cuota
        self._cli = httpx.Client(timeout=timeout, follow_redirects=False)
        self._intervalo = MIN_INTERVALO_S
        self._ultimo = 0.0
        self.throttled = 0
        # Por instancia, para que una sonda pueda medir la tasa CRUDA poniendolo
        # en 0. Con reintento activo, el backoff tapa los 429 y cualquier medicion
        # del limite de tasa da un falso negativo.
        self.max_reintentos = MAX_REINTENTOS
        self.adaptar_intervalo = True

    def _marcapasos(self) -> None:
        espera = self._intervalo - (time.monotonic() - self._ultimo)
        if espera > 0:
            time.sleep(espera)

    def _get(self, path: str, params: dict[str, Any], motivo: str) -> dict:
        self.cuota.exigir(1, motivo)
        url = f"{API_BASE}/{path}"
        # El ticket va como parametro de query string, no como header. En modo
        # Agent Vault esto es el placeholder y el proxy lo sustituye.
        params = {**params, "ticket": self.cfg.ticket()}
        ultimo_status = 0

        for intento in range(self.max_reintentos + 1):
            self._marcapasos()
            status = 0
            try:
                r = self._cli.get(url, params=params)
                status = r.status_code
            except httpx.HTTPError as exc:
                self.cuota.registrar(path, motivo, 0)
                raise ErrorAPI(scrub(f"Fallo de transporte contra {path}: {exc}")) from None
            finally:
                self._ultimo = time.monotonic()

            self.cuota.registrar(path, motivo, status)
            ultimo_status = status

            if status == 429:
                self.throttled += 1
                # El intervalo sube y se queda arriba durante la corrida.
                if self.adaptar_intervalo:
                    self._intervalo = min(self._intervalo * 1.5, 10.0)
                if intento >= self.max_reintentos:
                    break
                ra = r.headers.get("retry-after")
                espera = float(ra) if (ra or "").strip().isdigit() else (
                    BACKOFF_BASE_S * (2 ** intento))
                time.sleep(min(espera, 30.0))
                continue

            if 200 <= status < 300:
                # 203 (Non-Authoritative Information) aparece en la practica y
                # trae cuerpo valido: aceptar todo 2xx, no solo 200.
                texto = _decodificar(r.content)
                try:
                    return _json.loads(texto)
                except ValueError:
                    raise ErrorAPI(scrub(
                        f"{path} respondio HTTP {status} con cuerpo no-JSON. "
                        f"Primeros 200 caracteres: {texto[:200]}"))

            raise ErrorAPI(scrub(
                f"{path} devolvio HTTP {status}. "
                + ("Ticket invalido o suspendido." if status in (401, 403) else "")
                + (f" Cuerpo: {r.text[:300]}" if status >= 500 else "")))

        raise ErrorAPI(
            f"{path} devolvio HTTP {ultimo_status} tras {self.max_reintentos} reintentos "
            f"con backoff (intervalo actual {self._intervalo:.1f}s). Es el limite de "
            f"tasa de corto plazo de ChileCompra, no la cuota diaria: subir "
            f"MP_MIN_INTERVALO o correr en la ventana 22:00-07:00 que ellos "
            f"recomiendan. Ver sonda P-21.")

    # -- endpoints ---------------------------------------------------------

    def licitaciones_por_estado(self, estado: str = "activas", motivo="ingesta") -> dict:
        """Listado. Devuelve solo 4 campos por licitacion (verificado por P-01)."""
        return self._get("publico/licitaciones.json", {"estado": estado}, motivo)

    def licitaciones_por_fecha(self, fecha_ddmmaaaa: str, motivo="ingesta") -> dict:
        return self._get("publico/licitaciones.json", {"fecha": fecha_ddmmaaaa}, motivo)

    def licitacion(self, codigo: str, motivo="on-demand") -> dict:
        """Detalle. Unica fuente de Items/UNSPSC, monto, comprador y fechas."""
        return self._get("publico/licitaciones.json", {"codigo": codigo}, motivo)

    def ordenes_por_fecha(self, fecha_ddmmaaaa: str, motivo="ingesta") -> dict:
        return self._get("publico/ordenesdecompra.json", {"fecha": fecha_ddmmaaaa}, motivo)

    def orden(self, codigo: str, motivo="on-demand") -> dict:
        return self._get("publico/ordenesdecompra.json", {"codigo": codigo}, motivo)

    def proveedor(self, rut: str, motivo="on-demand") -> dict:
        return self._get("Publico/Empresas/BuscarProveedor",
                         {"rutempresaproveedor": rut}, motivo)

    def compradores(self, motivo="ingesta") -> dict:
        return self._get("Publico/Empresas/BuscarComprador", {}, motivo)

    def close(self) -> None:
        self._cli.close()
