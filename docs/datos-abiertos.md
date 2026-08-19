# Datos abiertos de ChileCompra: la fuente que el proyecto no estaba usando

Estado: **cargado y publicado en producción el 2026-08-19** (`mp-mcp:0.4.1`).
Todo lo marcado `[MEDIDO]` viene de un comando contra el archivo real con su salida.

---

## 1. La premisa que era falsa

`SPEC-001` está construido sobre una idea: la API v1 es la única fuente y la cuota
de 10.000 hits/día es la restricción de diseño. Eso es cierto para el día en curso
y **falso para todo lo histórico**:

| Necesidad | Por API | Por bulk |
|---|---|---|
| 1 año de adjudicaciones | ~95.000 hits (9,5 días de cuota) | **0 hits** |
| Órdenes de compra de 1 día | 11.681 hits (**117% de la cuota diaria**) | **0 hits** |
| Órdenes de compra de 1 año | ~4,3 M hits (**427 días de cuota**) | **0 hits** |
| Ofertas de los perdedores | **imposible** | **0 hits** |
| Criterios de evaluación | **imposible** | **0 hits** |
| Serie de tipo de cambio | **imposible** | **0 hits** |

La API sigue siendo la **única** vía para el día en curso: el bulk tiene un día de
desfase. No se reemplaza, se complementa. Y las dos fuentes no son redundantes:
`[MEDIDO]` `lic-da` de agosto trae **1.266** licitaciones —las que ya recibieron
ofertas, o sea cerradas— contra las **4.393** que la API lista como activas.

## 2. Dos sondas del repo quedan refutadas

- **P-09** (estaba VERIFICADO): *"el diccionario de datos NO incluye criterios de
  evaluación, ponderaciones, ni las ofertas de los perdedores"*. Sí los incluye:
  `CriteriosEvaluacion`, `MontoUnitarioOferta`, `Oferta seleccionada`,
  `NumeroOferentes`. `[MEDIDO]` cobertura del 100% en precio y RUT del oferente;
  89% real en criterios (799 de 7.273 licitaciones traen `"NA"` literal).
- **P-13** (estaba VERIFICADO): *"ninguna fuente de ChileCompra entrega la serie de
  UF, UTM ni tipo de cambio"*. Sí la entrega: `oc-da/ParidadMoneda.csv`, 1.175
  paridades mensuales de CLF, CLP, EUR, USD y UTM desde 2007-01. Con eso, P-11
  ("los montos no son agregables entre monedas") también se resuelve.

## 3. Las URLs, y el atajo declarado

```
https://transparenciachc.blob.core.windows.net/oc-da/{año}-{mes}.zip     ordenes de compra
https://transparenciachc.blob.core.windows.net/lic-da/{año}-{mes}.zip    licitaciones + ofertas
https://transparenciachc.blob.core.windows.net/oc-da/ParidadMoneda.csv   tipo de cambio
```

El **patrón** está documentado en la página de descargas de ChileCompra: la propia
UI te da la URL para copiar. Los dos valores de `TipoReporte` —`oc-da` y `lic-da`—
**no**: salieron de `main.c002bf6b.js`, el bundle de su SPA, donde figuran como
`ux=[{name:"Órdenes de compra",value:"oc-da"},{name:"Licitaciones",value:"lic-da"}]`.

`[Probable]` que sean estables: el histórico llega a 2015 y la página los usa en
producción. Pero es un contrato implícito, no publicado. **Por eso el cargador falla
ruidoso**: si el ZIP no aparece, si pesa menos de 100 KB, o si le falta una columna
requerida, levanta excepción con el detalle. Nunca deja una tabla vacía en silencio.

`[MEDIDO]` el mes va **sin cero a la izquierda**: `2026-7.zip` da 200 y
`2026-07.zip` da 404.

## 4. El encoding, que ya había morfido a este proyecto

`[MEDIDO 2026-08-19]` sobre `oc-da/2026-8.zip`, 411.777.058 bytes:

- **no** es UTF-8 válido;
- **no** es cp1252 puro: 71 bytes indefinidos (`0x8d` ×42, `0x81` ×28, `0x9d` ×1);
- los acentos (`0xC0`–`0xFF`) son idénticos en cp1252 y latin-1;
- hay 98.740 bytes en `0x80`–`0x9F`, de los cuales **58.319 son comillas
  tipográficas** que latin-1 convertiría en caracteres de control.

Se lee con **`cp1252` y `errors="replace"`**: pierde 71 caracteres en 411 MB, contra
58.319 corrompidos si se usara latin-1. Es la cuarta vez que el encoding muerde a
este proyecto (ver `_decodificar` en `api.py`). DuckDB además **rechaza** el archivo
si se le pasa `encoding='latin-1'`, así que se transcodifica a UTF-8 al extraer,
cortando en salto de línea para no partir un carácter multibyte entre bloques.

`[MEDIDO]` `strict_mode=false` es obligatorio: el CSV de 2026-3 tiene filas que no
cumplen el estándar y `ignore_errors=true` por sí solo no alcanza — el parser aborta.

## 5. Lo que quedó cargado

`[MEDIDO 2026-08-19]` 12 meses en producción, **2,31 GB** de un techo de 10 GB:

| Tabla | Filas |
|---|---|
| `orden_compra_item` | 4.836.812 |
| `orden_compra` | 1.721.976 |
| `oferta` | 1.471.590 |
| `adjudicacion_item` | 263.782 |
| `proveedor` | 81.354 |
| `organismo` | 1.196 |
| `tipo_cambio` | 1.175 |

Conversión a CLP: 1.440.376 ofertas y 4.819.481 ítems de OC. Las 366 y 40 que no se
convierten son las que ChileCompra marca `"Moneda revisar"` — su propio sistema
declarando que el dato está mal.

Costo por mes: 223,8 MB tipado. 36 meses son 7,87 GB.

## 6. Latencia

Era el riesgo declarado antes de publicar: un agente que tarda 30 s no sirve.
`[MEDIDO]` por el protocolo MCP, sobre 4,8 millones de filas y 2 vCPU:

| Consulta | Segundos |
|---|---|
| Esquema (índice) | 0,01 |
| Búsqueda de ofertas de un proveedor | 0,01 |
| Licitaciones con un solo oferente | 0,01 |
| Agregado por organismo | 0,05 |
| Monto de OC por organismo | 0,03 |
| Serie mensual de competencia | **0,10** |

## 7. Lo que se puede prometer y lo que no

**Sí, y es lo que diferencia al producto.** Las cuatro preguntas no se responden
navegando mercadopublico.cl, porque no hay pantalla que las agregue:

1. **"¿A qué precio me ganaron?"** `[MEDIDO]` en un mes hay **21.241 líneas
   comparables** perdedor-contra-ganador, con **sobreprecio mediano del 20,7%**.
2. **"¿Con qué criterios me evaluaron cuando gano y cuando pierdo?"** Texto real,
   cobertura 89%.
3. **Competencia medida.** `[MEDIDO]` **1.657 de 7.273** licitaciones de julio
   tuvieron **un solo oferente**: el 22,8%.
4. **Lo adjudicado contra lo pagado.** El 99,7% de las OC traen `CodigoLicitacion`.

**No.** La **dispersión de precios por UNSPSC no es una métrica válida**, y no es
arreglable con un filtro: `[MEDIDO]` un mismo commodity mezcla hasta **19 unidades
de medida distintas**, con ratios de x13.450.000 entre mínimo y máximo. Comparar el
"precio unitario" de un servicio global contra un metro lineal no significa nada.
Está declarado como limitación de severidad alta en la entidad `oferta`.

Lo que **sí** es válido es comparar dentro de la **misma línea de la misma
licitación**, donde unidad y objeto son idénticos por construcción. Es exactamente
la pregunta 1, y es la buena.

**Y el dato no es un foso.** Es CC0, cualquiera baja los mismos ZIP. Lo defendible
son los joins ya resueltos, la capa que impide que el agente afirme más de lo que el
payload sostiene, y el acceso conversacional. Ninguna de las tres es el dato.

## 8. Trampas medidas al cargar

- **`CantidadReclamos` no es una cantidad de reclamos.** `[MEDIDO]` trae valores
  como 972, 507 y 11.788 repetidos cientos de veces: son identificadores. Se deja
  `NULL` a propósito. Cargar un número que no es lo que dice su nombre es peor que
  no cargarlo.
- **`fecha_adjudicacion` puede ser una fecha estimada futura.** `[MEDIDO]` hay
  filas con adjudicación en 2026-09, 10 y 11. Para ordenar hechos, `fecha_envio_oferta`.
- **Precios de 0 y 1 peso.** `[MEDIDO]` 4.973 de 119.281 ofertas. Se descartan a
  `NULL` y quedan marcadas en `precio_sospechoso`; no se imputan.
- **La PK obvia pierde datos.** `licitacion|item|rut` colisionaba en 497 de 119.281
  filas: un mismo proveedor puede ofertar varias líneas del mismo ítem. Con
  correlativo, 119.281 de 119.281.
- **Los booleanos no son sólo 0/1.** `Obras` trae también `"2"`. Un valor
  desconocido entra como `NULL`, no como `false`: `false` es una afirmación.
- **Un archivo malo mataba la corrida.** El CSV de 2026-3 abortó la primera carga de
  12 meses y perdió el trabajo de los cinco meses anteriores. Ahora cada mes y cada
  fuente se atrapan por separado.

## 9. El cron

```
CRON_TZ=America/Santiago
0 7,19 * * * /usr/local/bin/mp-sync.sh
```

`CRON_TZ` y no horas UTC porque **Chile pasa a UTC-3 en septiembre** y un cron fijo
se correría una hora solo. Las 07:00 y 19:00 quedan fuera de la ventana 12:00–14:00
en que ChileCompra regenera los archivos.

El wrapper recorta el log a 2 MB antes de cada corrida. Corre `--meses 2`: el mes en
curso se rearma a diario en la fuente y el anterior recibe correcciones; los otros
diez ya están y no se vuelven a bajar.

Cada corrida parte de una **copia** de la base servida, no de cero —construir de
cero perdería los meses cargados—, y **si al terminar queda alguna tabla vacía no
publica**: se conserva el almacén anterior y el comando sale con error.
