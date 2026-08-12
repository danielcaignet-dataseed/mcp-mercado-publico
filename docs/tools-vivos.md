# Consultas en vivo y el índice de esquema

Estado: **desplegado y verificado en producción el 2026-08-12** (`mp-mcp:0.3.1`).
Todo lo marcado `[MEDIDO]` viene de un comando contra la API real con su salida.

---

## 1. Por qué

El MCP exponía 8 herramientas y **todas leían el snapshot local**. Un agente solo
sabía lo que hubiera en el último corte: si el usuario preguntaba por una
licitación publicada después, la respuesta era "no la tengo".

Ahora hay 13. Las cinco nuevas salen a la red.

---

## 2. El cambio de postura, explícito

Hasta este commit, `docs/despliegue-demeter.md` declaraba como control de
seguridad que *"el MCP no expone ninguna herramienta que haga HTTP — no hay
passthrough, solo lectura de DuckDB"*.

**Eso dejó de ser cierto.** Se toma a conciencia y se acota así:

- El cliente HTTP se crea **perezosamente**, al primer uso de una tool viva. Un
  despliegue sin proxy ni ticket sigue sirviendo el snapshot sin romperse; solo
  fallan esas tools, con un error legible.
- Todo hit pasa por `Cuota`, que aplica el techo diario de 10.000. El libro
  mayor que sostiene ese techo estaba roto y se arregló: ver §7.
- **El ticket sigue viviendo solo en Agent Vault.** El proceso emite
  `__mp_ticket__` y el proxy lo sustituye; nunca lo conoce.

La nota equivalente está en `server.py`, junto al código, no solo acá.

---

## 3. Las herramientas nuevas

| Tool | Costo | Para qué |
|---|---|---|
| `mp_licitacion_vivo(codigo)` | 1 hit | Detalle de cualquier licitación del país, esté o no en el snapshot |
| `mp_oc_vivo(codigo)` | 1 hit | Orden de compra: totales, ítems, proveedor. Única vía mientras `orden_compra` esté vacía |
| `mp_proveedor_resolver(rut)` | 1 hit | RUT → código y nombre de empresa |
| `mp_comprador_catalogo()` | 1 hit | Catálogo completo de organismos. `[MEDIDO]` 899 |
| `mp_cuota_estado()` | 0 | Cuánto queda del techo diario |

`[MEDIDO 2026-08-12]` `mp_licitacion_vivo("948355-24-LE26")` devuelve la
licitación con su adjudicación: 8 oferentes, proveedor ganador y monto unitario
adjudicado. **Precio y ganador por licitación, sin ingesta masiva.**

### Distinguir "no existe" de "no pude preguntar"

ChileCompra devuelve sus errores **con HTTP 2xx**. `[MEDIDO]` un ticket ausente
o inválido responde HTTP 203 con cuerpo
`{"Codigo":203,"Mensaje":"Ticket no valido."}` y **sin clave `Listado`**.

La primera versión leyó esa ausencia como "no encontrada" y devolvió
`encontrada:false` sobre una licitación que sí existe. Un agente le habría dicho
al usuario que algo no existe cuando en realidad la consulta nunca se autenticó.

`_error_chilecompra()` detecta ese sobre y devuelve `{error, nota}`. Verificado
en los dos sentidos:

| Consulta | Respuesta |
|---|---|
| Código real | `encontrada: true` + detalle |
| Código inventado (`0000-0-XX99`) | `encontrada: false`, `cantidad: 0` |
| Sin proxy del vault | `error` + `"NO es que la licitacion no exista: la consulta fallo."` |

---

## 4. El índice de esquema

`mp_schema_describe()` sin argumentos devuelve el **índice**: entidades con su
grano, estado de carga y **limitaciones completas**, sin el detalle columna por
columna. El detalle se pide con `entity='<nombre>'`.

`[MEDIDO]` payload 24.426 → 7.997 bytes, **67 % menos**, en algo que se reenvía
en cada iteración del bucle agéntico.

**Las limitaciones no se tocan.** Son la fuente de honestidad del agente —
recortarlas sería cambiar contexto por confiabilidad. Lo que se omite son
nombres de campo que no hacen falta hasta elegir la entidad.

---

## 5. Filtros de la API que no se usaban

`licitaciones_por_fecha` ahora acepta `estado`, combinable.

`[MEDIDO 2026-08-12]`:

| Consulta | Cantidad |
|---|---|
| `fecha=28072026` | 700 |
| `fecha=28072026&estado=adjudicada` | **259** |
| `ordenesdecompra.json?fecha=28072026` | 12.383 |

**Esto explica por qué `adjudicacion_item` estaba vacía**, y no era falta de
código: el cargador existe en `_upsert_licitacion` y funciona
(`test_adjudicacion.py`). La ingesta corrió con `--estado activas`, y una
licitación activa no tiene bloque de adjudicación. Sin `fecha`, el endpoint
responde sobre el día corriente, y por eso `--estado adjudicada` a secas daba 0.

Para poblarla: `live-listado --fecha ddmmaaaa --estado adjudicada`, ~260 hits por
día de historia.

Se agregaron además `licitaciones_por_organismo`, `licitaciones_por_proveedor`,
`orden_compra`, `buscar_proveedor` y `buscar_comprador`.

---

## 6. Despliegue

El contenedor necesita el proxy del vault, que antes no tenía:

```yaml
HTTP_PROXY:  http://${MP_AGENT_TOKEN}:mercadopublico-ingesta-vault@172.16.1.1:15322
HTTPS_PROXY: http://${MP_AGENT_TOKEN}:mercadopublico-ingesta-vault@172.16.1.1:15322
NO_PROXY: localhost,127.0.0.1
SSL_CERT_FILE: /etc/ssl/certs/agent-vault-ca.pem
REQUESTS_CA_BUNDLE: /etc/ssl/certs/agent-vault-ca.pem
```

```bash
MP_AGENT_TOKEN=$(cat /root/.mp-agent-token) docker compose up -d
```

El token del **agente** (`av_agt_*`) no puede ser un placeholder: es la llave que
abre el vault, no una credencial de API. No se puede guardar la llave de la caja
fuerte dentro de la caja fuerte. Se inyecta desde el archivo que ya existe, en el
momento del `up`, para no crear copias nuevas en disco. Queda visible en
`docker inspect`, igual que el de Demeter.

**El ticket de ChileCompra sigue solo en el vault.**

---

## 7. El contador de cuota mentía, y por dos motivos

`mp_cuota_estado` reportaba **0 usados después de gastar cuota real**. Al
revisarlo aparecieron dos causas independientes, las dos medidas:

1. **El servidor abre los hechos en solo-lectura**, así que `Cuota.registrar()`
   hacía `return` en silencio. Todo hit de una tool viva se perdía.
2. **`mp-ingest publicar` borraba el libro mayor.** El comando reemplaza
   `mp.duckdb` por `mp.next.duckdb` con un rename atómico, y ese archivo se
   construye desde cero. `[MEDIDO 2026-08-12]` la tabla `cuota_hit` en producción
   tenía 4.374 filas, **todas de una sola corrida** (06/08 04:16–07:27) y ninguna
   anterior. El resto de la historia se había ido en publicaciones previas.

La segunda es la que importa: afecta también al lado de la **ingesta**, que sí
escribe. Después de cada publicación su contador arranca en cero, así que
`exigir()` deja pasar más hits de los que quedan y el techo de 10.000 se puede
superar sin que nada avise.

### Por qué el arreglo no fue abrir la base en escritura

Ni eso ni mover el libro mayor a `catalogo.duckdb` sirven:

| Intento | Por qué no |
|---|---|
| Abrir `mp.duckdb` en escritura desde el servidor | DuckDB admite **un escritor por archivo**: el servidor le quita el lock a la ingesta. Y el rename sigue borrando la historia |
| Libro mayor en `catalogo.duckdb` | El servidor se queda con ese lock, y entonces la ingesta —el proceso que más necesita el contador— no puede leerlo |

El libro mayor es ahora un **JSONL de sólo-append, un archivo por día UTC**, en
`$MP_HOME/data/cuota/` (configurable con `MP_CUOTA_LEDGER`). Sin lock, escribible
por los dos procesos a la vez, y fuera del snapshot, así que el rename no lo toca.
Cada línea se escribe con un solo `write` en modo append y por debajo de
`PIPE_BUF`: dos procesos concurrentes no se entrelazan.

La tabla `cuota_hit` se sigue escribiendo cuando el almacén es escribible, porque
las sondas **P-03 y P-21** la consultan, pero ya no es la fuente del contador.
El test verifica que eso **no** produce doble conteo.

### Y si el libro mayor no se puede escribir, se dice

Es lo que faltaba antes: el modo de acceso engañó al contador una vez y nadie se
enteró. Ahora `estado()` comprueba de verdad que el archivo del día sea
escribible y publica `contador_confiable`, `hits_anotados_en_esta_sesion` y
`hits_que_no_se_pudieron_anotar`. Si falla, `mp_cuota_estado` antepone una
limitación de **severidad alta** con el texto de la advertencia, de modo que el
agente lo lee en la respuesta y no sólo en este repo. `registrar()` nunca propaga
el error: la consulta ya se hizo y ya gastó cuota, romperla después no la
devuelve.

`hits_historicos_en_el_snapshot` se expone aparte y **no** entra en el contador,
porque se borra en la siguiente publicación.

`test_cuota_persiste.py` cubre los seis casos: persistencia con el almacén en
solo-lectura, supervivencia al reinicio del proceso, supervivencia a `publicar`,
ausencia de doble conteo, la declaración del fallo, y la purga por retención
(7 días) sin tocar la ventana viva.

---

## 8. Pendientes conocidos

- Los cargadores de **OCDS bulk** y **CSV de órdenes de compra** siguen sin
  implementar, a propósito: requieren `--inspect` sobre un archivo real. Adivinar
  nombres de campo es lo que este proyecto no hace.
- La rama `feat/tools-vivos-e-indice-esquema` **no pudo empujarse**: el PAT
  fine-grained de `contacto101` no tiene este repositorio en su selección.
  `contacto101` es colaborador con permiso de escritura —la API lo confirma—
  pero el token no lo alcanza. Se resuelve agregando el repo al PAT.
