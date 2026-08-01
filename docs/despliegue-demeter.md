# Despliegue en Demeter (VPS Hostinger)

Estado: **diseño verificado en lo medible desde local, sin ejecutar en el VPS.**
Lo que está `[SIN VERIFICAR]` aquí no debe darse por bueno hasta correr la sonda
correspondiente.

---

## 1. La restricción que ordena la topología

`[MEDIDO 2026-07-31]` **DuckDB no permite abrir un archivo en solo-lectura mientras
otro proceso lo tiene tomado.** Probado con dos procesos reales: el lector falla con
`IO Error ... utilizado por otro proceso`.

Consecuencia directa: el servidor MCP y la ingesta **no pueden compartir el archivo**.
Un MCP persistente bloquearía la ingesta para siempre.

Solución: **intercambio de snapshot**.

```
  ingesta  →  escribe /data/mp/mp.duckdb.next     (su propio archivo)
           →  mp-ingest publicar
           →  rename atómico sobre /data/mp/mp.duckdb
  MCP      →  siguiente consulta detecta el cambio de inodo y reabre
```

En Linux el `rename` sobre un archivo abierto no rompe al lector: conserva el inodo
viejo hasta que reabre. `Store.refrescar()` compara `(st_ino, st_mtime_ns, st_size)`
en cada consulta y reabre si cambió.

`[SIN VERIFICAR]` Esa semántica de `rename` no se puede probar en Windows. Es la
primera sonda a correr en el VPS.

Guardas de `mp-ingest publicar`: no publica un archivo de menos de 1 KB, no publica
un snapshot con 0 licitaciones, y exige que origen y destino estén en el mismo
filesystem (si no, `rename` no es atómico).

---

## 2. Topología

```
  ┌───────────────────────────┐      ┌────────────────────────────────┐
  │ contenedor mp-ingesta     │      │ contenedor hermes-agent        │
  │  timer 22:00–07:00        │      │  Demeter                       │
  │  ENTRYPOINT mp-ingest     │      │   └─ mp-mcp (hijo stdio)       │
  │                           │      │                                │
  │  HTTPS_PROXY → Agent Vault│      │  SIN proxy, SIN ticket         │
  │  vault: mp-ingesta        │      │  vault: demeter-vault          │
  │  ÚNICO con acceso a la API│      │  base en SOLO LECTURA          │
  └──────────┬────────────────┘      └──────────────┬─────────────────┘
             │ volumen mp-data (rw)                 │ mp-data (:ro)
             └──────────────────┬───────────────────┘
                          mp.duckdb
                                                    │ mp-catalogo (rw)
                                                    └── catalogo.duckdb
```

Dos volúmenes, no uno:

| Volumen | Contenido | mp-ingesta | hermes-agent |
|---|---|---|---|
| `mp-data` | `mp.duckdb` — hechos | **rw** | **ro** |
| `mp-catalogo` | `catalogo.duckdb` — consultas guardadas | — | **rw** |

**El `:ro` lo hace cumplir el kernel, no una convención.** Si alguien pone
`MP_MCP_ESCRITURA=1` por error, el montaje lo frena igual.

---

## 3. Por qué dos contenedores y no un cron dentro de Demeter

Porque Demeter tiene shell. Si el proxy de la API está en su contenedor —aunque sea
solo en el entorno de un cron— el agente puede leerlo y usarlo. Eso no es aislamiento,
es un acuerdo de caballeros.

Con dos contenedores, `api.mercadopublico.cl` está declarado **solo** en
`mercadopublico-ingesta-vault`. Demeter no lo tiene concedido, así que el proxy le
responde 403 aunque descubra la URL. Es la invariante 3 de PROC-001: un agente, un
vault.

Costo: un contenedor más. Es el precio de que el techo de 10.000 hits/día no dependa
de que el agente se porte bien.

---

## 4. Agent Vault

Ver `docs/services.example.yaml` para la declaración del servicio.

```bash
agent-vault vault service set -f services.yaml --vault mercadopublico-ingesta-vault
```

`[SIN VERIFICAR]` La sintaxis para **crear el vault**, **cargar la credencial** y
**crear la identidad del agente de ingesta** no está confirmada contra esta versión
de Agent Vault. Correr primero:

```bash
agent-vault vault --help
agent-vault vault credential --help
agent-vault agent --help
```

Lo que hay que lograr, en orden:

1. Vault `mercadopublico-ingesta-vault`.
2. Credencial **static** con clave `MERCADOPUBLICO_TICKET` = el ticket. **Se carga sin
   pasar por el chat de ningún agente ni por la línea de comandos** (queda en el
   historial de la shell: fue la quinta fuga del proyecto).
3. Aplicar `services.yaml`.
4. Identidad `mp-ingesta` con grant `mercadopublico-ingesta-vault:proxy`.
5. Verificar que `agent list` muestra **exactamente un** grant para esa identidad.

Env del contenedor de ingesta:

```
MP_USAR_AGENT_VAULT=1
MP_HOME=/data/mp
MP_DB=/data/mp/mp.duckdb.next
MP_MIN_INTERVALO=1.5
HTTPS_PROXY=http://<token-de-mp-ingesta>:mercadopublico-ingesta-vault@172.16.1.1:15322
SSL_CERT_FILE=<ruta de la CA de Agent Vault>
```

El nombre del vault va **en el campo password de la URL del proxy**, no en una
variable suelta. Ignorar eso costó dos sesiones de diagnóstico (D-012).

---

## 5. Pipeline nocturno

```bash
mp-ingest live-listado --estado activas \
  && mp-ingest derivar \
  && mp-ingest convertir \
  && mp-ingest publicar
```

`[MEDIDO]` Costos reales:

| | Hits | Duración a 1,5 s/request |
|---|---|---|
| Carga inicial (stock abierto, 4.350) | 43,5 % de cuota | **109 min** |
| Ingesta diaria (flujo, ~1.119) | 11,2 % de cuota | **28 min** |

ChileCompra recomienda concentrar carga alta entre 22:00 y 07:00. El intervalo de
1,5 s no es opcional: por debajo aparece un límite de tasa no documentado (sonda
P-21), con 25–50 % de rechazos 429.

---

## 6. Registro en Hermes

Bloque a agregar bajo `mcp_servers:` en `config.yaml`:

```yaml
  mercado_publico:
    command: /opt/mp-mcp/.venv/bin/mp-mcp
    env:
      MP_HOME: /data/mp
      MP_DB: /data/mp/mp.duckdb
      MP_CATALOGO: /data/mp-catalogo/catalogo.duckdb
```

Sin `MP_USAR_AGENT_VAULT`, sin `HTTPS_PROXY`, sin `MP_TICKET`. El servidor MCP no
habla con la API: solo lee un archivo.

---

## 7. Verificación

Antes de dar el despliegue por bueno:

| # | Qué | Cómo | Sonda |
|---|---|---|---|
| 1 | El ticket no está en el proceso | `/proc/<pid>/environ` del contenedor de ingesta, con la máscara de PROC-001 §8 | V-08 |
| 2 | La substitution funciona | Un `mp-ingest live-listado --solo-listado` devuelve `Cantidad` > 0 | V-08 |
| 3 | Demeter NO alcanza la API | Desde su contenedor, `curl https://api.mercadopublico.cl/...` debe dar **403** del proxy | — |
| 4 | El swap no rompe al lector | Consultar por el MCP mientras corre `mp-ingest publicar` | nueva |
| 5 | El montaje `:ro` se cumple | Intentar escribir el volumen desde el contenedor de Demeter | — |

El punto 3 es el que importa. Si Demeter puede llamar la API, el aislamiento no
existe por más que el diagrama diga otra cosa.

---

## 8. Lo que falta antes de mostrarlo a un cliente

- El almacén tiene **200 licitaciones de muestra**, no el universo. `meta.as_of` lo
  declara en cada respuesta, pero conviene correr la carga completa primero.
- Los cargadores de bulk (OCDS y CSV de órdenes de compra) están sin implementar a
  propósito, esperando `--inspect` sobre un archivo real.
- `CAPABILITIES.md`: 14 de 27 afirmaciones medidas. Las 13 restantes están
  bloqueadas por el bulk, por Agent Vault o por una corrida de agente.
