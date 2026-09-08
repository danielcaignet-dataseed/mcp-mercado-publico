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


def _dv_modulo11(cuerpo: str) -> str:
    """Digito verificador de un RUT chileno. Determinista, no es una adivinanza."""
    suma, factor = 0, 2
    for ch in reversed(cuerpo):
        suma += int(ch) * factor
        factor = 2 if factor == 7 else factor + 1
    resto = 11 - (suma % 11)
    return {11: "0", 10: "K"}.get(resto, str(resto))


def _puntear(cuerpo: str) -> str:
    grupos = []
    while len(cuerpo) > 3:
        grupos.insert(0, cuerpo[-3:])
        cuerpo = cuerpo[:-3]
    grupos.insert(0, cuerpo)
    return ".".join(grupos)


def normalizar_rut(rut: str) -> str:
    """A NN.NNN.NNN-D, el unico formato que acepta BuscarProveedor (MEDIDO).

    `96756540` es ambiguo: puede ser el cuerpo sin digito verificador, o
    `9675654-0`. Se desempata con modulo 11, que es un checksum verificable y no
    una suposicion: para 9675654 el DV es 2, no 0, asi que la lectura correcta es
    el cuerpo completo y el DV que corresponde es 7 -> 96.756.540-7.

    Si el DV que trae la entrada NO calza con el cuerpo, se respeta tal cual y no
    se corrige. Un digito mal tipeado es dato del usuario, no ruido nuestro:
    arreglarlo en silencio consultaria por una empresa distinta de la que pidio.
    `dv_calza()` permite avisarlo.
    """
    s = "".join(ch for ch in str(rut) if ch.isalnum()).upper()
    if len(s) < 2:
        return str(rut).strip()

    # a) cuerpo + DV correcto
    if s[:-1].isdigit() and _dv_modulo11(s[:-1]) == s[-1]:
        return _puntear(s[:-1]) + "-" + s[-1]
    # b) todo digitos y el ultimo no sirve como DV -> era el cuerpo entero
    if s.isdigit():
        return _puntear(s) + "-" + _dv_modulo11(s)
    # c) DV que no calza: se manda lo que dio el usuario, solo formateado
    if s[:-1].isdigit():
        return _puntear(s[:-1]) + "-" + s[-1]
    return str(rut).strip()


def dv_calza(rut: str) -> bool | None:
    """True/False si se puede evaluar el digito verificador; None si no aplica."""
    s = "".join(ch for ch in str(rut) if ch.isalnum()).upper()
    if len(s) < 2 or not s[:-1].isdigit():
        return None
    if s.isdigit() and _dv_modulo11(s[:-1]) != s[-1]:
        return None            # se leyo como cuerpo completo: no habia DV que juzgar
    return _dv_modulo11(s[:-1]) == s[-1]


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

    # -- endpoints: licitaciones -------------------------------------------

    def licitaciones_listado(self, fecha: str | None = None, estado: str | None = None,
                             codigo_organismo: str | None = None,
                             codigo_proveedor: str | None = None,
                             motivo="ingesta") -> dict:
        """Listado con los cuatro filtros documentados, combinables.

        `[MEDIDO 2026-08-12]` el item del listado trae exactamente 4 campos:
        CodigoExterno, Nombre, CodigoEstado, FechaCierre. Para monto, comprador,
        items o adjudicacion hace falta el detalle (`licitacion()`), un hit por
        codigo.

        Sin ningun filtro el endpoint responde sobre el **dia corriente**. Por eso
        `estado=adjudicada` a secas puede dar Cantidad=0: no es que no existan
        adjudicadas, es que hoy no se adjudico nada.

        `[MEDIDO]` fecha=28072026 -> 700; +estado=adjudicada -> 259.
        """
        p: dict[str, str] = {}
        if fecha:
            p["fecha"] = fecha
        if estado:
            p["estado"] = estado
        if codigo_organismo:
            p["CodigoOrganismo"] = codigo_organismo
        if codigo_proveedor:
            p["CodigoProveedor"] = codigo_proveedor
        return self._get("publico/licitaciones.json", p, motivo)

    def licitaciones_por_estado(self, estado: str = "activas", motivo="ingesta") -> dict:
        """Listado. Devuelve solo 4 campos por licitacion (verificado por P-01)."""
        return self.licitaciones_listado(estado=estado, motivo=motivo)

    def licitaciones_por_fecha(self, fecha_ddmmaaaa: str, estado: str | None = None,
                               motivo="ingesta") -> dict:
        """Listado de un dia. `estado` es combinable con `fecha`."""
        return self.licitaciones_listado(fecha=fecha_ddmmaaaa, estado=estado, motivo=motivo)

    def licitaciones_por_organismo(self, codigo_organismo: str,
                                   motivo="ingesta") -> dict:
        """`[MEDIDO]` CodigoOrganismo=111870 (Div. Logistica del Ejercito) -> 2."""
        return self.licitaciones_listado(codigo_organismo=codigo_organismo, motivo=motivo)

    def licitaciones_por_proveedor(self, codigo_proveedor: str,
                                   motivo="ingesta") -> dict:
        """Licitaciones de un proveedor. Es el CODIGO de empresa, no el RUT:
        se obtiene con `buscar_proveedor()`."""
        return self.licitaciones_listado(codigo_proveedor=codigo_proveedor, motivo=motivo)

    def licitacion(self, codigo: str, motivo="on-demand") -> dict:
        """Detalle. Unica fuente de Items/UNSPSC, monto, comprador y fechas."""
        return self._get("publico/licitaciones.json", {"codigo": codigo}, motivo)

    # -- endpoints: ordenes de compra --------------------------------------

    def ordenes_listado(self, fecha: str | None = None, estado: str | None = None,
                        codigo_organismo: str | None = None,
                        codigo_proveedor: str | None = None,
                        motivo="ingesta") -> dict:
        """Listado de OC con los cuatro filtros documentados.

        `[MEDIDO 2026-08-12]` el item trae **solo 3 campos**: Codigo, Nombre,
        CodigoEstado. Ni fecha, ni proveedor, ni monto. Para eso, detalle por
        codigo con `orden_compra()`.

        `[MEDIDO]` y esto importa para el consumidor: **no hay paginacion**.
        fecha=28072026 devolvio Cantidad=12265 y las 12.265 en el mismo cuerpo.
        Quien llame esto tiene que truncar.
        """
        p: dict[str, str] = {}
        if fecha:
            p["fecha"] = fecha
        if estado:
            p["estado"] = estado
        if codigo_organismo:
            p["CodigoOrganismo"] = codigo_organismo
        if codigo_proveedor:
            p["CodigoProveedor"] = codigo_proveedor
        return self._get("publico/ordenesdecompra.json", p, motivo)

    def ordenes_por_fecha(self, fecha_ddmmaaaa: str, motivo="ingesta") -> dict:
        return self.ordenes_listado(fecha=fecha_ddmmaaaa, motivo=motivo)

    def ordenes_por_estado(self, estado: str, motivo="on-demand") -> dict:
        """`[MEDIDO 2026-08-12]` enviadaproveedor 2.879 · aceptada 4.364 ·
        cancelada 116 · recepcionconforme 4.857 · todos 12.379."""
        return self.ordenes_listado(estado=estado, motivo=motivo)

    def ordenes_por_proveedor(self, codigo_proveedor: str, motivo="on-demand") -> dict:
        """`[MEDIDO]` CodigoProveedor=47740 (B BRAUN MEDICAL SPA) -> 52 OC.
        Es el codigo de empresa, no el RUT: usar `buscar_proveedor()` antes."""
        return self.ordenes_listado(codigo_proveedor=codigo_proveedor, motivo=motivo)

    def ordenes_por_organismo(self, codigo_organismo: str, motivo="on-demand") -> dict:
        """`[MEDIDO]` CodigoOrganismo=111870 -> 310 OC."""
        return self.ordenes_listado(codigo_organismo=codigo_organismo, motivo=motivo)

    def orden_compra(self, codigo: str, motivo="on-demand") -> dict:
        """Detalle de una OC: totales, items, proveedor y fechas de la orden."""
        return self._get("publico/ordenesdecompra.json", {"codigo": codigo}, motivo)

    # -- catalogo de empresas ----------------------------------------------

    def buscar_proveedor(self, rut: str, motivo="on-demand") -> dict:
        """RUT -> codigo y nombre de empresa proveedora.

        El RUT se normaliza a NN.NNN.NNN-D porque **es el unico formato que este
        endpoint acepta**. `[MEDIDO 2026-08-12]` con el RUT de B BRAUN MEDICAL SPA:

            96.756.540-7  -> CodigoEmpresa 47740
            96756540-7    -> Codigo 10200 "No hay resultados de empresas."
            96756540      -> idem
            967565407     -> idem

        Sin normalizar, el formato natural que escribe cualquiera devuelve vacio
        y el agente concluye que la empresa no existe.
        """
        return self._get("Publico/Empresas/BuscarProveedor",
                         {"rutempresaproveedor": normalizar_rut(rut)}, motivo)

    def buscar_comprador(self, motivo="on-demand") -> dict:
        """Catalogo completo de organismos compradores. Un solo hit."""
        return self._get("Publico/Empresas/BuscarComprador", {}, motivo)

    def compradores(self, motivo="ingesta") -> dict:
        """Alias historico que usa la sonda P-05."""
        return self.buscar_comprador(motivo=motivo)

    def close(self) -> None:
        self._cli.close()
