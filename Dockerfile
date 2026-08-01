# Contenedor de INGESTA. Es el unico proceso con acceso a api.mercadopublico.cl.
#
# El servidor MCP NO usa esta imagen: corre como hijo stdio de Hermes, con el
# mismo paquete instalado pero sin proxy, sin ticket y con la base en solo
# lectura. Separarlos es lo que impide que el agente llame a la API directo y
# agote los 10.000 hits del dia.

FROM python:3.12-slim

RUN useradd --create-home --uid 10010 mp
WORKDIR /app

COPY pyproject.toml README.md ./
COPY src/ ./src/
COPY sql/ ./sql/
COPY probes/ ./probes/
COPY seeds/ ./seeds/

RUN pip install --no-cache-dir -e . && chown -R mp:mp /app

USER mp

# MP_DB apunta al snapshot en construccion, NO a la base que sirve el MCP.
# DuckDB no permite dos procesos sobre el mismo archivo (medido 2026-07-31),
# asi que la ingesta construye aparte y `mp-ingest publicar` hace el rename
# atomico.
ENV MP_HOME=/data/mp \
    MP_DB=/data/mp/mp.duckdb.next \
    MP_USAR_AGENT_VAULT=1 \
    MP_MIN_INTERVALO=1.5

VOLUME ["/data/mp"]

# Sin CMD por defecto a proposito: este contenedor no debe quedar corriendo.
# Lo dispara el timer nocturno con el pipeline completo.
ENTRYPOINT ["mp-ingest"]
CMD ["estado"]
