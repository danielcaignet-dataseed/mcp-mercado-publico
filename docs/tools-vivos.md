# Consultas en vivo y el índice de esquema

Estado: **desplegado y verificado en producción el 2026-08-12** (`mp-mcp:0.3.5`).
Todo lo marcado `[MEDIDO]` viene de un comando contra la API real con su salida.

---

## 1. Por qué

El MCP exponía 8 herramientas y **todas leían el snapshot local**. Un agente solo
sabía lo que hubiera en el último corte: si el usuario preguntaba por una
licitación publicada después, la respuesta era "no la tengo".

Ahora hay 16. Las siete que salen a la red cubren la superficie completa
de la API v1 menos Compra Ágil: ver §3.

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
| `mp_licitaciones_vivo(...)` | 1 hit | **Busca** licitaciones por fecha, estado, organismo y/o proveedor |
| `mp_ordenes_vivo(...)` | 1 hit | **Busca** órdenes de compra por los mismos cuatro filtros |
| `mp_licitacion_vivo(codigo)` | 1 hit | Detalle de cualquier licitación del país, esté o no en el snapshot |
| `mp_oc_vivo(codigo)` | 1 hit | Orden de compra: totales, ítems, proveedor. Única vía mientras `orden_compra` esté vacía |
| `mp_proveedor_resolver(rut)` | 1 hit | RUT → código y nombre de empresa |
| `mp_comprador_catalogo()` | 1 hit | Catálogo completo de organismos. `[MEDIDO]` 899 |
| `mp_volcado_leer(...)` | **0** | Pagina y filtra un listado ya volcado. No sale a la red |
| `mp_cuota_estado()` | 0 | Cuánto queda del techo diario |

### La superficie está completa

`[MEDIDO 2026-08-12]` La documentación oficial expone tres APIs —Licitaciones,
Órdenes de Compra, Compra Ágil— más dos servicios de catálogo. Excluyendo Compra
Ágil, son **13 combinaciones endpoint+parámetro, y las 13 están implementadas y
alcanzables desde una tool**:

| | `codigo` | `fecha` | `estado` | `CodigoOrganismo` | `CodigoProveedor` |
|---|:--:|:--:|:--:|:--:|:--:|
| `licitaciones.json` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `ordenesdecompra.json` | ✅ | ✅ | ✅ | ✅ | ✅ |

Más `BuscarProveedor?rut` y `BuscarComprador`. `fecha` y `estado` son combinables
(medido). `test_superficie_api.py` interpone `_get` y compara el **query string
exacto** de las 13: un `codigoOrganismo` en minúscula no falla, devuelve el día
corriente completo y el agente cree que filtró. Eso sólo se detecta así.

**Dos tools, no nueve.** Las nueve combinaciones de listado viven en
`mp_licitaciones_vivo` y `mp_ordenes_vivo` con cuatro parámetros opcionales cada
una. Nueve tools separadas habrían sumado ~10 KB al `tools/list` que este mismo
trabajo recortó un 67%, y no dejarían combinar filtros.

**Valores verificados uno por uno** —los rechazos de la primera sonda eran el
límite de tasa de P-21, no valores inválidos: mi sonda no respetaba el marcapasos
de 1,5 s—:

- licitaciones: `activas` 4.533 · `publicada` 190 · `cerrada` 64 · `adjudicada`
  159 · `desierta` 46 · `revocada` 8 · `suspendida` 1
- órdenes: `enviadaproveedor` 2.879 · `aceptada` 4.364 · `cancelada` 116 ·
  `recepcionconforme` 4.857 · `todos` 12.379

### El truncado no es cosmético, y no corta lo que se analiza

`[MEDIDO 2026-08-12]` `ordenesdecompra.json?fecha=28072026` devolvió `Cantidad=12265`
**y las 12.265 en el mismo cuerpo: la API no pagina.** Son **1,47 MB ≈ 384.000
tokens** en una sola respuesta de tool. No hay contexto que lo aguante.

La objeción obvia es *"¿y entonces cómo se analiza algo truncado?"*. La respuesta
son dos cosas distintas:

**1. Los agregados no se truncan.** `cantidad_total` viene del `Cantidad` de la
API, y `conteo_por_codigo_estado` se calcula sobre **todas** las filas que llegaron,
no sobre las que se devuelven. `[MEDIDO en vivo 2026-08-19, por MCP]`:

```
cantidad_total=11681  devueltas=25  conteo_calculado_sobre=11681
por codigo_estado: {'12': 7774, '6': 3478, '9': 223, '5': 19, '4': 187}  suma=11681
bytes de la respuesta: 6187
```

25 filas a la vista y los conteos cubren las 11.681. Para *"cuántas hay"*,
*"cuántas por estado"*, *"cuántas de este proveedor"* no falta nada.

**2. Para las filas hay `volcar=true`.** Guarda el listado completo en el almacén
y devuelve sólo el conteo:

```
volcado: filas=11681 tabla=listado_vivo error=None
bytes de la respuesta: 6756   (mandar las 11.681 crudas: ~1.500.000)
```

Después se pagina y filtra con `mp_volcado_leer`, **sin gastar cuota**
`[MEDIDO en vivo 2026-08-19]`:

| Consulta local | Coinciden | Contra la API |
|---|---|---|
| todas | 11.681 | 11.681 ✅ |
| `texto='combustible'` | 53 | — |
| `texto='medicamento'` | 101 | — |
| `texto='servicio'` | 1.212 | — |
| `codigo_estado=12` | 7.774 | 7.774 ✅ |
| `codigo_estado=6` | 3.478 | 3.478 ✅ |
| `codigo_estado=9` | 223 | 223 ✅ |

**Siete lecturas, 0 hits de cuota** (contador 2 → 2). Un volcado cuesta 1 hit y
después se recorre gratis cuantas veces haga falta.

El volcado va a `catalogo.duckdb`, **no** a los hechos: es la única base que el
servidor abre en escritura —los hechos están en solo-lectura para no quitarle el
lock a la ingesta— y además `publicar` reemplaza los hechos con un rename, así que
lo que viviera ahí se borraría en la siguiente publicación. Es el mismo
razonamiento que el del libro mayor de cuota (§7). Re-volcar la misma consulta
**reemplaza**: es una foto de esa pregunta, no una bitácora que crece sin techo.

Cuando truncan, las tools agregan una limitación de **severidad alta** que ofrece
`volcar=true` como la salida buena, aclara que las devueltas son las primeras en el
orden de ChileCompra y **no** una muestra representativa, y que **el recorte es de
la tool, no de ChileCompra** — Demeter se lo atribuyó a ChileCompra en la primera
validación y hubo que decirlo en el payload.

Lo que sigue faltando **no es el truncado**: el listado de la API trae `Codigo`,
`Nombre` y `CodigoEstado`, **cero campos de monto**. Con las 11.681 filas completas
no se calcula un peso. Para plata hace falta el detalle, 1 hit por código.

Sin ningún filtro las tools rechazan la llamada **sin gastar cuota**, porque sin
filtro la API responde el día corriente entero.

### Dos bugs que sólo aparecieron al medir

**El RUT.** `BuscarProveedor` acepta un solo formato:

| RUT enviado | Respuesta |
|---|---|
| `96.756.540-7` | `CodigoEmpresa 47740`, B BRAUN MEDICAL SPA |
| `96756540-7` | `Codigo 10200` "No hay resultados de empresas." |
| `96756540` | idem |
| `967565407` | idem |

`mp_proveedor_resolver` pasaba el RUT sin tocar, así que el formato natural que
escribe cualquiera daba vacío. Ahora se normaliza. `96756540` es ambiguo —cuerpo
sin dígito verificador, o `9675654-0`— y se desempata con **módulo 11**, que es un
checksum verificable: para `9675654` el DV es 2, no 0, así que la lectura correcta
es el cuerpo completo con DV 7. Si el DV que trae la entrada **no** calza, se
respeta tal cual y se avisa: corregirlo en silencio consultaría por otra empresa.

**Y el error inverso al de §7.** `Codigo=10200` es una consulta exitosa con cero
filas, y `_error_chilecompra` la trataba como fallo: el agente recibía
`"error"` y la nota *"NO es que el RUT no exista: la consulta falló"* cuando la
consulta había funcionado y el RUT existía. El mismo error del ticket inválido,
espejado. Además `BuscarProveedor` responde en `listaEmpresas`, no en `Listado`,
así que sus respuestas válidas caían al camino correcto por casualidad —sólo
porque no traían `Mensaje`—; ahora `listaEmpresas` es un sobre de éxito explícito.

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

`licitaciones_por_fecha` ahora acepta `estado`, combinable. La superficie completa
quedo cubierta despues: ver §3.

`[MEDIDO 2026-08-12]`:

| Consulta | Cantidad |
|---|---|
| `fecha=28072026` | 700 |
| `fecha=28072026&estado=adjudicada` | **259** |
| `ordenesdecompra.json?fecha=28072026` | 12.383 |

`[MEDIDO en vivo 2026-08-12, por MCP]` las 9 combinaciones nuevas, con `limite=3`:
`licitaciones?fecha` 693 · `?estado=adjudicada` 162 · `?fecha&estado` 259 ·
`?CodigoOrganismo` 2 · `?CodigoProveedor` 0 (filtro aceptado, cero filas) ·
`ordenesdecompra?fecha` 12.263 · `?estado=aceptada` 4.480 · `?CodigoProveedor` 54 ·
`?CodigoOrganismo` 325. El `tools/list` pasó de 14.388 a 18.395 bytes por dos
tools; nueve habrían costado unos 10 KB más.

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
