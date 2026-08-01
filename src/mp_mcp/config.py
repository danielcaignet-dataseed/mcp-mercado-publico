"""Configuracion y manejo de credenciales.

Regla no negociable (ver decisiones.md D-012, cuarta fuga del proyecto):
el ticket NUNCA se escribe a un log, a un mensaje de error, ni al contexto
de un agente. Toda cadena que salga de este proceso pasa por scrub().
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

# Placeholder que Agent Vault sustituye en el query string.
# Ver SPEC-001 §7: substitutions con in:[query].
TICKET_PLACEHOLDER = "__mp_ticket__"

API_BASE = "https://api.mercadopublico.cl/servicios/v1"

# Cuota documentada por ChileCompra: 10.000 hits/dia por ticket, no modificable.
# Es [DOC], no [MEDIDO]. La sonda P-03 mide el comportamiento real en el limite.
QUOTA_DOCUMENTADA_DIA = 10_000

# Ventana que ChileCompra recomienda para carga alta.
VENTANA_CARGA_ALTA = (22, 7)

_SCRUB_PATTERNS = [
    # Credenciales embebidas en URL: scheme://user:pass@host
    (re.compile(r"://[^@/\s]*@"), "://***@"),
    # El parametro ticket, en cualquier orden de query string.
    (re.compile(r"([?&]ticket=)[^&\s\"']+", re.IGNORECASE), r"\1***"),
    # Tokens conocidos del proyecto.
    (re.compile(r"(av_agt_|ghp_|github_pat_|sk-|Bearer )[A-Za-z0-9_\-]{6,}"), r"\1***"),
    # Asignaciones tipo VAR=valor.
    (re.compile(r"\b(TICKET|TOKEN|SECRET|KEY|PASSWORD)=[^\s&\"']+", re.IGNORECASE), r"\1=***"),
]


def scrub(text: object) -> str:
    """Enmascara credenciales. Se aplica a TODO lo que sale del proceso."""
    s = str(text)
    for pattern, repl in _SCRUB_PATTERNS:
        s = pattern.sub(repl, s)
    return s


class TicketNoConfigurado(RuntimeError):
    pass


def ruta_ticket(raiz: Path) -> Path:
    """Archivo donde vive el ticket en desarrollo local."""
    return Path(os.environ.get("MP_TICKET_FILE", raiz / "ticket"))


def _leer_ticket(raiz: Path) -> str | None:
    """Prioridad: archivo > variable de entorno.

    El archivo existe porque `$env:MP_TICKET = "..."` deja el secreto en la linea
    de comandos, y por lo tanto en el historial de la shell y en cualquier
    transcript que se copie. Fue la quinta fuga del proyecto (2026-07-31).
    Usar `mp-ingest set-ticket`, que lee sin eco.

    Sigue siendo texto plano en disco: es un paliativo de desarrollo. En
    produccion no hay ticket en el proceso, lo sustituye Agent Vault.
    """
    f = ruta_ticket(raiz)
    if f.exists():
        v = f.read_text(encoding="utf-8").strip()
        if v:
            return v
    return os.environ.get("MP_TICKET") or None


@dataclass(frozen=True)
class Config:
    db_path: Path
    # Base separada para lo que ESCRIBE el servidor MCP (consultas guardadas).
    # DuckDB admite un solo proceso escritor por archivo: si el MCP abriera
    # mp.duckdb en escritura, la ingesta nunca podria correr mientras el MCP
    # este vivo. El MCP abre los hechos en solo-lectura y escribe solo aca.
    catalog_path: Path
    data_dir: Path
    # Si es True, el proceso NO conoce el ticket: emite el placeholder y
    # Agent Vault lo sustituye en el query string. Es el modo de produccion.
    usar_agent_vault: bool
    _ticket: str | None

    @classmethod
    def from_env(cls) -> "Config":
        raiz = Path(os.environ.get("MP_HOME", Path.home() / ".mp-mcp")).expanduser()
        raiz.mkdir(parents=True, exist_ok=True)
        (raiz / "data").mkdir(exist_ok=True)
        usar_av = os.environ.get("MP_USAR_AGENT_VAULT", "").lower() in ("1", "true", "yes")
        return cls(
            db_path=Path(os.environ.get("MP_DB", raiz / "mp.duckdb")),
            catalog_path=Path(os.environ.get("MP_CATALOGO", raiz / "catalogo.duckdb")),
            data_dir=raiz / "data",
            usar_agent_vault=usar_av,
            _ticket=None if usar_av else _leer_ticket(raiz),
        )

    def ticket(self) -> str:
        """Devuelve el ticket o el placeholder de Agent Vault.

        En modo Agent Vault el proceso jamas ve el valor real: emite
        `__mp_ticket__` y el proxy lo reemplaza. Es el mismo patron que
        `__agent_vault_google_oauth__` en PROC-001 §4.
        """
        if self.usar_agent_vault:
            return TICKET_PLACEHOLDER
        if not self._ticket:
            raise TicketNoConfigurado(
                "No hay ticket. Opciones: (a) exportar MP_TICKET, o "
                "(b) MP_USAR_AGENT_VAULT=1 y declarar la substitution "
                f"con placeholder {TICKET_PLACEHOLDER} e in:[query]. "
                "Sin ticket el MCP sigue sirviendo todo lo cargado desde bulk; "
                "solo pierde frescura del dia."
            )
        return self._ticket

    def tiene_ticket(self) -> bool:
        return self.usar_agent_vault or bool(self._ticket)
