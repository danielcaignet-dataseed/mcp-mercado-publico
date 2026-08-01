# MCP Mercado Público (Chile)

Acceso a datos de compras públicas chilenas para agentes. **Agnóstico de rubro y
de producto**: expone entidades, dimensiones y medidas, no verticales. Los perfiles
de mercado, dossiers de postulación, watchlists y dashboards son capa de producto
y viven fuera de este repositorio.

Diseño y decisiones: `SPEC-001-mcp-mercado-publico.md` y `decisiones.md` en el workspace interno de DataSeed (Agent Factory). No se versionan aquí: este repositorio es el código, no el registro interno del proyecto.
Potencialidades y limitaciones **verificadas**: [`CAPABILITIES.md`](CAPABILITIES.md)

---

## La propiedad que define el proyecto

`CAPABILITIES.md` es un **archivo generado**, no redactado. Las afirmaciones se
declaran en `probes/probes.yaml`, las mediciones se guardan en la tabla
`probe_result`, y el documento se produce cruzando ambas. Una afirmación sin
corrida de sonda aparece como `NO VERIFICADO` y el propio documento la marca como
prohibida en material comercial.

No es disciplina: es estructura. No hay forma de que una promesa sin evidencia
llegue a un cliente sin que el documento lo diga.

## Instalación

```bash
python -m venv .venv && .venv/Scripts/activate
pip install -e .
```

## Puesta en marcha

```bash
mp-ingest estado                          # frescura, cuota y configuración
mp-ingest seed-consultas                  # biblioteca de consultas canónicas
mp-probe status                           # qué sondas se pueden correr hoy
```

Con ticket (o con Agent Vault brokeando el ticket):

```bash
mp-ingest live-listado --estado activas   # listado del día + detalle de cada una
mp-ingest derivar                         # puebla unspsc, organismo, proveedor
mp-probe run                              # mide y regenera CAPABILITIES.md
```

Bulk — **inspeccionar antes de mapear**:

```bash
mp-ingest ocds --file procesos-2025.jsonl.gz --inspect
mp-ingest oc-csv --file oc-2026-06.csv --inspect
```

Los cargadores de bulk están deliberadamente sin implementar hasta que se
inspeccione un archivo real. No conozco las rutas exactas del JSONL de ChileCompra
ni los nombres de columna del CSV; escribir ese mapeo a ciegas sería inventar
nombres de campo.

## Credenciales

**Nunca `$env:MP_TICKET = "..."`.** La asignación por línea de comandos queda en el
historial de la shell y en cualquier transcript que se copie. Así ocurrió la quinta
fuga de credencial del proyecto, el 2026-07-31. Usar:

```bash
mp-ingest set-ticket     # lee sin eco, guarda en $MP_HOME/ticket
```

Dos modos. El segundo es el de producción.

| Modo | Cómo | El proceso ve el ticket |
|---|---|---|
| Local | `mp-ingest set-ticket` → archivo `$MP_HOME/ticket` | sí (texto plano en disco: paliativo de desarrollo) |
| Agent Vault | `MP_USAR_AGENT_VAULT=1` | **no** — emite `__mp_ticket__` y el proxy lo sustituye |

En Windows `os.chmod` no restringe ACLs. Para restringir de verdad el archivo:

```bash
icacls "%USERPROFILE%\.mp-mcp\ticket" /inheritance:r /grant:r "%USERNAME%:R"
```

`services.yaml` del vault de ingesta:

```yaml
services:
  - host: api.mercadopublico.cl
    auth: { type: passthrough }
    substitutions:
      - key: MERCADOPUBLICO_TICKET
        placeholder: __mp_ticket__
        in: [query]
```

Aplicar con `agent-vault vault service set -f services.yaml --vault mercadopublico-ingesta-vault`.

**`api.mercadopublico.cl` no va en `demeter-vault`.** Un bucle del agente agotaría
los 10.000 hits del día. Demeter llega a los datos por este MCP, nunca por la API.

Todo lo que sale del proceso pasa por `config.scrub()`: URLs con credenciales,
`?ticket=`, tokens del proyecto y asignaciones `TOKEN=`. httpx incluye la URL en
sus excepciones, y la URL lleva el ticket — es la fuga que ocurrió en D-012.

## Las 8 herramientas

| Tool | Qué hace |
|---|---|
| `mp_schema_describe` | Entidades, dimensiones, medidas **y sus limitaciones**. Llamarla primero |
| `mp_codes_search` | Lenguaje natural → códigos UNSPSC / organismo / proveedor / región, con volumen |
| `mp_search` | Nivel fila |
| `mp_aggregate` | Nivel agregado con `grain` temporal. El motor de gráficos |
| `mp_get` | Una entidad completa con lo relacionado unido en una llamada |
| `mp_query_save` | Persiste una consulta canónica |
| `mp_query_run` | Re-ejecuta una guardada, con `sobrescribir` |
| `mp_query_list` | Lista la biblioteca |

Deliberadamente **no** existe passthrough a la API ni SQL libre: un agente con paso
libre quema la cuota y vuelca cientos de KB al contexto.

### Cómo se compone lo que antes eran herramientas

Un dashboard es un conjunto de consultas guardadas. Lo que en un diseño ingenuo
serían tools dedicadas, acá son entradas de `seeds/consultas.yaml`:

| Pregunta | Consulta guardada |
|---|---|
| Benchmark de precio | `benchmark_precio_por_commodity` |
| Perfil de comprador | `perfil_comprador_que_compra`, `..._a_quien_adjudica`, `..._estacionalidad`, `..._plazos` |
| Perfil de competidor | `perfil_competidor` |
| Oportunidades del rubro | `oportunidades_abiertas_por_familia`, `cierres_proximos` |

Eso resuelve la tensión entre flexibilidad y efectividad: **forma de tarea donde
ya conocemos la pregunta, gramática genérica donde no.**

## El payload como mecanismo anti-humo

Cada respuesta lleva:

- `meta.procedencia` — `ocds-bulk` / `oc-csv-procesado` / `api-live`
- `meta.as_of` — dato más reciente y última ingesta, por tabla
- `meta.cobertura` — filas consideradas y **excluidas por falta de tipo de cambio**,
  con el porcentaje. Un p50 sobre 40 % de cobertura se ve como lo que es
- `meta.limitaciones` — los caveats de la entidad y de las medidas usadas, cada uno
  con `verificado_por`
- `meta.cuota` — hits usados y disponibles
- `meta.atribucion` — exigencia de los términos de uso de ChileCompra
- `chart_hint` — encoding sugerido. **El MCP no renderiza**: eso es de presentación
- `consulta` + `query_id` — la consulta canónica, lista para guardar

Un agente no puede afirmar más de lo que el payload sostiene.

## Tres límites que conviene saber antes de leer el código

1. **No hay escritura.** No existe endpoint para presentar una oferta. La
   postulación en mercadopublico.cl es manual.
2. **No hay win rate.** No hay registro estructurado de participación, solo de
   adjudicación. Se puede saber quién ganó, no contra cuántos.
3. **No hay campos de garantía ni criterios de evaluación.** Están en las bases en
   PDF. Cualquier alerta sobre eso es parseo de documento, no consulta.

Las tres están declaradas como sondas (P-19, P-09, P-18) y aparecen en
`CAPABILITIES.md` con su sello.

## Estructura

```
src/mp_mcp/
  config.py     env, ticket, scrub()
  semantic.py   registro de entidades/dimensiones/medidas + caveats  ← el contrato
  query.py      consulta canónica, validación, SQL, chart_hint, cobertura
  store.py      DuckDB
  quota.py      gobernador de cuota
  api.py        cliente de api.mercadopublico.cl con errores enmascarados
  tools.py      las 8 herramientas
  server.py     entrypoint MCP stdio
  cli.py        ingesta
  probe.py      arnés de verificación → CAPABILITIES.md
sql/schema.sql
probes/probes.yaml    las afirmaciones
seeds/consultas.yaml  la biblioteca
```

Si agregas una columna a `sql/schema.sql`, agrégala también a `semantic.py`. El
registro semántico es el contrato; el SQL es solo almacenamiento.
