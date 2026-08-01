# CAPABILITIES.md — potencialidades y limitaciones del MCP Mercado Publico

> **ARCHIVO GENERADO.** No editar a mano: se sobrescribe con `mp-probe report`. La fuente de las afirmaciones es `probes/probes.yaml`; la de las mediciones, la tabla `probe_result`.

Generado: 2026-08-01T01:30:29+00:00  
Afirmaciones declaradas: **27** (8 potencialidades, 19 limitaciones)  
Verificadas con medicion: **14/27**

## Como leer esto

**La convencion es contraintuitiva y hay que leerla dos veces:** para una LIMITACION, `VERIFICADO` significa que **la limitacion existe y esta confirmada** — es mala noticia comprobada, no buena. Para una POTENCIALIDAD, `VERIFICADO` es buena noticia comprobada.

| Sello | Potencialidad (`V-*`) | Limitacion (`P-*`) | Uso comercial |
|---|---|---|---|
| `VERIFICADO` | la capacidad existe | **la limitacion existe** | Se puede afirmar, citando la medicion |
| `REFUTADO` | la capacidad NO existe | la limitacion no aplica | Revisar el diseno: una de las dos suposiciones estaba mal |
| `NO CONCLUYENTE` | sonda corrida sin datos suficientes | idem | No afirmar |
| `NO VERIFICADO` | nunca se corrio | idem | **Prohibido en material comercial** |

## Tabla resumen

| ID | Tipo | Sello | Afirmacion (resumen) |
|---|---|---|---|
| V-01 | pot | VERIFICADO | El detalle de licitacion (?codigo=) devuelve Items/Listado con CodigoProducto y CodigoCategoria en UNSPSC, lo ... |
| V-02 | pot | VERIFICADO | Las licitaciones adjudicadas traen, por linea, RutProveedor, CantidadAdjudicada y MontoUnitario, lo que permit... |
| V-03 | pot | NO CONCLUYENTE | El bulk OCDS es descargable sin ticket y con licencia CC0, cubriendo el historico de procesos de contratacion.... |
| V-04 | pot | NO CONCLUYENTE | El CSV mensual de ordenes de compra es descargable sin ticket, lo que da el gasto efectivo sin consumir cuota.... |
| V-05 | pot | NO CONCLUYENTE | Existe dataset propio de Compra Agil, canal que la documentacion de la API no menciona.... |
| V-06 | pot | NO CONCLUYENTE | Las OC traen CodigoLicitacion, lo que permite unir lo licitado con lo efectivamente ejecutado.... |
| V-07 | pot | VERIFICADO | Los endpoints BuscarProveedor y BuscarComprador responden y permiten armar fichas de entidad.... |
| V-08 | pot | NO VERIFICADO | Agent Vault sustituye el ticket en el query string mediante substitutions con in:[query], de modo que el proce... |
| P-01 | lim | VERIFICADO | El endpoint de listado (?fecha= o ?estado=) devuelve solo CodigoExterno, Nombre, CodigoEstado y FechaCierre. S... |
| P-02 | lim | VERIFICADO | El conjunto de campos del detalle puede diferir del diccionario de datos publicado.... |
| P-03 | lim | VERIFICADO | La cuota es de 10.000 hits/dia por ticket y no es modificable. El comportamiento exacto al alcanzarla no esta ... |
| P-04 | lim | NO VERIFICADO | El ticket es unico por persona, validado contra nombre, RUT y email reales, y ChileCompra puede limitarlo o su... |
| P-07 | lim | NO CONCLUYENTE | Hay una contradiccion sin resolver en la cobertura del bloque implementation del OCDS chileno: el registro OCP... |
| P-08 | lim | REFUTADO | Los codigos UNSPSC son version 7 y los digita el comprador: hay items sin codigo, con codigo de longitud irreg... |
| P-09 | lim | VERIFICADO | El diccionario de datos de licitaciones NO incluye criterios de evaluacion, ponderaciones, ni las ofertas de l... |
| P-10 | lim | NO VERIFICADO | El CSV mensual de ordenes de compra esta procesado por ChileCompra: convertido a pesos y con transacciones ati... |
| P-11 | lim | VERIFICADO | Los montos vienen en CLP, CLF (UF), USD, UTM y EUR, y sin serie de tipo de cambio no son agregables entre si.... |
| P-12 | lim | VERIFICADO | MontoEstimado solo es publico si VisibilidadMonto=1, y Estimacion=3 significa que el organismo declara que el ... |
| P-13 | lim | VERIFICADO | Ninguna de las fuentes de ChileCompra entrega la serie de UF, UTM ni tipo de cambio. Hay que integrar una fuen... |
| P-14 | lim | NO VERIFICADO | El CSV de OC se publica a mas tardar el dia 20 del mes siguiente: rezago de hasta ~50 dias.... |
| P-15 | lim | VERIFICADO | No esta verificado hasta que fecha hacia atras responde el parametro fecha=ddmmaaaa de la API.... |
| P-16 | lim | VERIFICADO | El presupuesto de cuota se dimensiono con una estimacion inferida de ~1.150 licitaciones nuevas por dia habil,... |
| P-17 | lim | NO CONCLUYENTE | El volumen diario de ordenes de compra no esta medido.... |
| P-18 | lim | VERIFICADO | El diccionario de licitaciones no incluye campos de garantia de seriedad de la oferta ni de fiel cumplimiento.... |
| P-19 | lim | NO VERIFICADO | La API no permite escritura: no existe endpoint para presentar una oferta.... |
| P-21 | lim | VERIFICADO | Ademas de la cuota diaria de 10.000, ChileCompra aplica un LIMITE DE TASA DE CORTO PLAZO que no documenta en n... |
| P-20 | lim | NO VERIFICADO | No esta medido con que frecuencia un agente construye un mp_aggregate valido al primer intento. Es la metrica ... |

## Que NO se puede prometer

Lista derivada automaticamente de las limitaciones con `bloquea_venta_de`. Es la lista de humo prohibido.

- **P-01** (**limitacion confirmada por medicion**): 'Filtrado por rubro en tiempo real sobre la API' sin almacen intermedio.
- **P-03** (**limitacion confirmada por medicion**): Cualquier promesa de 'consultas ilimitadas'.
- **P-04** (sin medir): Un modelo donde cada cliente use su propio ticket sin tramitarlo el mismo.
- **P-07** (sonda corrida, sin datos suficientes): Analisis de ejecucion contractual historica hasta que se mida.
- **P-08** (medida y NO confirmada: revisar): 'Clasificacion exacta por rubro' sin margen de error declarado.
- **P-09** (**limitacion confirmada por medicion**): 'Win rate', 'probabilidad de adjudicacion' y 'simulador de puntaje' como funciones basadas en datos estructurados.
- **P-10** (sin medir): Deteccion de anomalias y sobreprecios sobre datos de OC.
- **P-11** (**limitacion confirmada por medicion**): Totales de mercado en CLP mientras la cobertura de conversion no este medida y declarada.
- **P-12** (**limitacion confirmada por medicion**): 'Tamano del mercado' como cifra unica sin declarar cobertura.
- **P-14** (sin medir): 'Datos de gasto en tiempo real'.
- **P-18** (**limitacion confirmada por medicion**): 'Alerta automatica de garantias exigidas' como funcion de datos.
- **P-19** (sin medir): 'Postulacion automatica' o 'el agente presenta la oferta'.
- **P-21** (**limitacion confirmada por medicion**): 'Consulta en vivo contra la API' como respuesta a una pregunta de usuario: cualquier producto que dispare varias llamadas por pregunta se auto-throttlea.

## Potencialidades

### V-01 — VERIFICADO

El detalle de licitacion (?codigo=) devuelve Items/Listado con CodigoProducto y CodigoCategoria en UNSPSC, lo que permite segmentar por mercado.

- **Fuente declarada:** [DOC] Diccionario de Datos - Licitaciones, campos 87-89
- **Metodo:** Pedir el detalle de una licitacion real y verificar que existan las claves Items.Listado[].CodigoProducto y CodigoCategoria con valor no vacio.
- **Habilita:** Segmentacion por rubro, perfiles de comprador, matching de oportunidades.
- **Medido 2026-07-31 21:06:25.292950:**

```json
{
  "codigo": "1000813-12-LE26",
  "n_items": 1,
  "n_con_unspsc": 1,
  "muestra": {
    "CodigoProducto": 31231313,
    "CodigoCategoria": "31231300",
    "Categoria": "Artículos de fabricación y producción / Materia prima en planchas, barras o tubos / Tubos y cañerías",
    "NombreProducto": "Tubería de plástico",
    "UnidadMedida": "Unidad"
  }
}
```
- **Evidencia:** 1/1 items con CodigoProducto.

### V-02 — VERIFICADO

Las licitaciones adjudicadas traen, por linea, RutProveedor, CantidadAdjudicada y MontoUnitario, lo que permite construir benchmark de precios reales.

- **Fuente declarada:** [DOC] Diccionario de Datos - Licitaciones, campos 94-97
- **Metodo:** Pedir el detalle de una licitacion en estado adjudicada y verificar Items.Listado[].Adjudicacion.MontoUnitario no nulo.
- **Habilita:** mp_aggregate con p10/p50/p90 sobre precio_unitario_clp. Es la funcion de mayor valor del producto.
- **Medido 2026-07-31 21:03:37.383709:**

```json
{
  "codigo": "4079-30-LR26",
  "n_items": 1,
  "n_con_precio": 1,
  "muestra": {
    "RutProveedor": "76.483.457-7",
    "NombreProveedor": "SOLUCIONES LOGÍSTICAS EXPRESS SOLEX SPA",
    "Cantidad": 29.0,
    "MontoUnitario": 19850000.0
  }
}
```
- **Evidencia:** 1/1 lineas con MontoUnitario adjudicado.

### V-03 — NO CONCLUYENTE

El bulk OCDS es descargable sin ticket y con licencia CC0, cubriendo el historico de procesos de contratacion.

- **Fuente declarada:** [DOC] OCP Data Registry, publicacion 144; CC0 1.0
- **Metodo:** HEAD/GET a datos-abiertos.chilecompra.cl/descargas/procesos-ocds y registrar codigo HTTP, tamano y ultima modificacion.
- **Habilita:** Historia completa sin gastar cuota. Sin esto el backfill toma 490 dias.
- **Medido 2026-07-31 21:03:39.595424:**

```json
{
  "url": "https://datos-abiertos.chilecompra.cl/descargas/procesos-ocds",
  "status": 200,
  "content_type": "text/html",
  "bytes": 1051,
  "sha256_16": "87fa1f7fa0aef421",
  "last_modified": "Fri, 29 May 2026 20:06:16 GMT",
  "control_negativo": {
    "url": "https://datos-abiertos.chilecompra.cl/ruta-que-no-existe-control-negativo-mp",
    "status": 200,
    "bytes": 1051,
    "sha256_16": "87fa1f7fa0aef421"
  },
  "enlaces_a_datos_encontrados": []
}
```
- **Evidencia:** El control negativo devuelve EXACTAMENTE el mismo cuerpo: el portal es una SPA que responde 200 a cualquier ruta. El 200 no prueba que el dataset exista. Hay que renderizar el portal o encontrar el endpoint real de descarga.

### V-04 — NO CONCLUYENTE

El CSV mensual de ordenes de compra es descargable sin ticket, lo que da el gasto efectivo sin consumir cuota.

- **Fuente declarada:** [DOC] Centro de ayuda Mercado Publico KA-01967
- **Metodo:** GET al listado de descargas y verificar disponibilidad del ultimo mes cerrado; registrar columnas y filas.
- **Habilita:** Gasto real por organismo y proveedor. Es la fuente de las OC.
- **Medido 2026-07-31 21:03:41.847492:**

```json
{
  "url": "https://datos-abiertos.chilecompra.cl/descargas",
  "status": 200,
  "content_type": "text/html",
  "bytes": 1051,
  "sha256_16": "87fa1f7fa0aef421",
  "last_modified": "Fri, 29 May 2026 20:06:16 GMT",
  "control_negativo": {
    "url": "https://datos-abiertos.chilecompra.cl/ruta-que-no-existe-control-negativo-mp",
    "status": 200,
    "bytes": 1051,
    "sha256_16": "87fa1f7fa0aef421"
  },
  "enlaces_a_datos_encontrados": []
}
```
- **Evidencia:** El control negativo devuelve EXACTAMENTE el mismo cuerpo: el portal es una SPA que responde 200 a cualquier ruta. El 200 no prueba que el dataset exista. Hay que renderizar el portal o encontrar el endpoint real de descarga.

### V-05 — NO CONCLUYENTE

Existe dataset propio de Compra Agil, canal que la documentacion de la API no menciona.

- **Fuente declarada:** [DOC] datos-abiertos.chilecompra.cl/descargas/compra-agil
- **Metodo:** GET al recurso y registrar disponibilidad y columnas.
- **Habilita:** Cobertura del canal de compras menores. Ignorarlo deja un hueco de mercado sin explicacion para el cliente.
- **Medido 2026-07-31 21:03:43.793180:**

```json
{
  "url": "https://datos-abiertos.chilecompra.cl/descargas/compra-agil",
  "status": 200,
  "content_type": "text/html",
  "bytes": 1051,
  "sha256_16": "87fa1f7fa0aef421",
  "last_modified": "Fri, 29 May 2026 20:06:16 GMT",
  "control_negativo": {
    "url": "https://datos-abiertos.chilecompra.cl/ruta-que-no-existe-control-negativo-mp",
    "status": 200,
    "bytes": 1051,
    "sha256_16": "87fa1f7fa0aef421"
  },
  "enlaces_a_datos_encontrados": []
}
```
- **Evidencia:** El control negativo devuelve EXACTAMENTE el mismo cuerpo: el portal es una SPA que responde 200 a cualquier ruta. El 200 no prueba que el dataset exista. Hay que renderizar el portal o encontrar el endpoint real de descarga.

### V-06 — NO CONCLUYENTE

Las OC traen CodigoLicitacion, lo que permite unir lo licitado con lo efectivamente ejecutado.

- **Fuente declarada:** [DOC] Diccionario de Datos - Ordenes de Compra, campo 7
- **Metodo:** Contar en el almacen el porcentaje de OC con codigo_licitacion no vacio que hace match con una fila de licitacion.
- **Habilita:** Ratio ejecutado/adjudicado, deteccion de contratos que no se ejecutaron, seguimiento post-adjudicacion.
- **Medido 2026-07-31 21:26:43.574969:**

```json
{
  "tabla": "orden_compra",
  "filas": 0
}
```
- **Evidencia:** 'orden_compra' esta vacia: corre la ingesta antes de esta sonda.

### V-07 — VERIFICADO

Los endpoints BuscarProveedor y BuscarComprador responden y permiten armar fichas de entidad.

- **Fuente declarada:** [DOC] chilecompra.cl/api
- **Metodo:** Llamar ambos endpoints y registrar las claves devueltas.
- **Habilita:** Fichas de organismo y proveedor sin depender del bulk.
- **Medido 2026-07-31 21:03:44.520132:**

```json
{
  "BuscarComprador": {
    "ok": true,
    "claves": [
      "Cantidad",
      "FechaCreacion",
      "listaEmpresas"
    ]
  }
}
```

### V-08 — NO VERIFICADO

Agent Vault sustituye el ticket en el query string mediante substitutions con in:[query], de modo que el proceso del MCP nunca conoce la credencial.

- **Fuente declarada:** [DOC] docs.agent-vault.dev/learn/services.md
- **Metodo:** Configurar el servicio con auth passthrough + substitution, pedir con ticket=__mp_ticket__ y verificar HTTP 200. Verificar ademas que el ticket no aparece en /proc/<pid>/environ del proceso del MCP.
- **Habilita:** Brokering del ticket sin que el agente ni el MCP lo vean.
- **Nota:** La combinacion passthrough + substitutions NO esta ejemplificada en la documentacion. Si falla, el plan B es auth-type custom con un header inerte y la substitution declarada aparte.
- **Medido:** nunca. Corre `mp-probe run`.

## Limitaciones

### P-01 — VERIFICADO · severidad alta

El endpoint de listado (?fecha= o ?estado=) devuelve solo CodigoExterno, Nombre, CodigoEstado y FechaCierre. Sin items, sin UNSPSC, sin monto, sin comprador.

- **Fuente declarada:** [DOC] chilecompra.cl/api y ejemplos oficiales
- **Metodo:** Llamar licitaciones.json?estado=activas y volcar el conjunto exacto de claves de un elemento del Listado.
- **Impacto:** Segmentar por mercado exige una llamada de detalle por licitacion.
- **PROHIBIDO PROMETER:** 'Filtrado por rubro en tiempo real sobre la API' sin almacen intermedio.
- **Medido 2026-07-31 21:03:47.523808:**

```json
{
  "claves_reales": [
    "CodigoEstado",
    "CodigoExterno",
    "FechaCierre",
    "Nombre"
  ],
  "cantidad_listada": 4349,
  "claves_esperadas": [
    "CodigoEstado",
    "CodigoExterno",
    "FechaCierre",
    "Nombre"
  ]
}
```
- **Evidencia:** Un elemento del Listado trae 4 claves.

### P-03 — VERIFICADO · severidad alta

La cuota es de 10.000 hits/dia por ticket y no es modificable. El comportamiento exacto al alcanzarla no esta documentado.

- **Fuente declarada:** [DOC] chilecompra.cl/api
- **Metodo:** Leer el libro mayor de cuota_hit y registrar el maximo observado en 24 h junto al codigo HTTP recibido en el rechazo, si ocurrio. NO se agota la cuota deliberadamente.
- **Impacto:** El techo es duro y compartido por todos los clientes del producto.
- **PROHIBIDO PROMETER:** Cualquier promesa de 'consultas ilimitadas'.
- **Medido 2026-07-31 21:03:52.567991:**

```json
{
  "cuota": {
    "limite_documentado_dia": 10000,
    "usados_ultimas_24h": 68,
    "throttled_429_ultimas_24h": 225,
    "disponibles_on_demand": 9932,
    "disponibles_ingesta": 8932,
    "nota": "Hay DOS limites. El diario de 10.000 es [DOC]. Ademas existe un limite de tasa de corto plazo NO documentado, medido el 2026-07-31: ver sonda P-21. Los 429 no cuentan aca (supuesto sin medir) y por eso se reportan aparte."
  },
  "status_observados": {
    "200": 62,
    "203": 6,
    "429": 225
  }
}
```
- **Evidencia:** Veredicto UNKNOWN mientras no se observe un rechazo real. No se agota la cuota a proposito.

### P-04 — NO VERIFICADO · severidad alta

El ticket es unico por persona, validado contra nombre, RUT y email reales, y ChileCompra puede limitarlo o suspenderlo.

- **Fuente declarada:** [DOC] chilecompra.cl/api, condiciones de uso
- **Metodo:** Verificacion documental. No hay medicion posible sin arriesgar el acceso.
- **Impacto:** Punto unico de falla del producto. Debe estar a nombre de la empresa, no de una persona.
- **NO AFIRMAR HASTA MEDIR:** Un modelo donde cada cliente use su propio ticket sin tramitarlo el mismo.
- **Medido:** nunca. Corre `mp-probe run`.

### P-07 — NO CONCLUYENTE · severidad alta

Hay una contradiccion sin resolver en la cobertura del bloque implementation del OCDS chileno: el registro OCP reporta 74.931 contratos frente a 4,9 M adjudicaciones, mientras Development Gateway afirma cobertura alta.

- **Fuente declarada:** [DOC] contradictorio entre dos fuentes
- **Metodo:** Parsear N lineas del JSONL y contar cuantas traen contracts[], contracts[].implementation y transacciones tipo OC.
- **Impacto:** Si el bloque implementation esta vacio, las OC historicas solo existen en el CSV procesado, con su definicion de outlier ajena.
- **NO AFIRMAR HASTA MEDIR:** Analisis de ejecucion contractual historica hasta que se mida.
- **Medido 2026-07-31 21:03:52.590832:**

```json
{
  "MP_OCDS_FILE": null
}
```
- **Evidencia:** Define MP_OCDS_FILE apuntando a un procesos-ocds .jsonl(.gz) descargado. Sin el archivo la contradiccion no se puede resolver.

### P-08 — REFUTADO · severidad alta

Los codigos UNSPSC son version 7 y los digita el comprador: hay items sin codigo, con codigo de longitud irregular, y concentracion artificial en el commodity generico de la clase.

- **Fuente declarada:** [DOC] version; [INFERIDO] calidad
- **Metodo:** Sobre licitacion_item medir porcentaje sin codigo, distribucion de longitud del codigo, y los 20 commodities mas frecuentes. Se CONFIRMA solo si mas del 5 % no tiene codigo o mas del 20 % usa el generico terminado en 00.
- **Impacto:** El matching por codigo solo es incompleto. Hay que combinar con texto.
- **NO AFIRMAR HASTA MEDIR:** 'Clasificacion exacta por rubro' sin margen de error declarado.
- **Medido 2026-07-31 21:28:34.256198:**

```json
{
  "umbral_para_confirmar": "sin_codigo > 5% o generico_00 > 20%",
  "n_items": 980,
  "pct_sin_codigo": 0.0,
  "distribucion_largo": {
    "8": 980
  },
  "pct_commodity_generico_termina_en_00": 1.02,
  "top20_commodities": [
    {
      "codigo": "42242003",
      "ejemplo": "Dispositivos de sujeción protésica o accesorios",
      "n": 60,
      "pct": 6.12
    },
    {
      "codigo": "85122103",
      "ejemplo": "Servicios de rehabilitación de drogas o abuso de sustancias",
      "n": 59,
      "pct": 6.02
    },
    {
      "codigo": "41116009",
      "ejemplo": "Reactivos de analizadores histológicos",
      "n": 43,
      "pct": 4.39
    },
    {
      "codigo": "42293603",
      "ejemplo": "Sondas quirúrgicas",
      "n": 30,
      "pct": 3.06
    },
    {
      "codigo": "80111715",
      "ejemplo": "Profesionales",
      "n": 25,
      "pct": 2.55
    },
    {
      "codigo": "42271903",
      "ejemplo": "Tubos endotraquiales",
      "n": 21,
      "pct": 2.14
    },
    {
      "codigo": "80141607",
      "ejemplo": "Producción de eventos",
      "n": 19,
      "pct": 1.94
    },
    {
      "codigo": "72131702",
      "ejemplo": "Construcción de obras civiles",
      "n": 16,
      "pct": 1.63
    },
    {
      "codigo": "42161601",
      "ejemplo": "Kits o sets de administración de hemodiálisis o accesorios",
      "n": 13,
      "pct": 1.33
    },
    {
      "codigo": "78111501",
      "ejemplo": "Servicios de helicóptero",
      "n": 12,
      "pct": 1.22
    },
    {
      "codigo": "42142504",
      "ejemplo": "Agujas de biopsia",
      "n": 12,
      "pct": 1.22
    },
    {
      "codigo": "42271709",
      "ejemplo": "Cánula médico nasal",
      "n": 11,
      "pct": 1.12
    },
    {
      "codigo": "42311505",
      "ejemplo": "Vendas o vendajes para uso general",
      "n": 10,
      "pct": 1.02
    },
    {
      "codigo": "80111707",
      "ejemplo": "Personal técnico",
      "n": 10,
      "pct": 1.02
    },
    {
      "codigo": "42294944",
      "ejemplo": "Kits de accesorios para endoscopía",
      "n": 10,
      "pct": 1.02
    },
    {
      "codigo": "42311514",
      "ejemplo": "Vendajes germicidas",
      "n": 9,
      "pct": 0.92
    },
    {
      "codigo": "42142609",
      "ejemplo": "Jeringas médicas con aguja",
      "n": 9,
      "pct": 0.92
    },
    {
      "codigo": "56101504",
      "ejemplo": "Sillas",
      "n": 9,
      "pct": 0.92
    },
    {
      "codigo": "10110100",
      "ejemplo": "Licitación Públ
```
- **Evidencia:** Concentracion alta en pocos commodities o mucho '...00' indica clasificacion perezosa del comprador.

### P-09 — VERIFICADO · severidad alta

El diccionario de datos de licitaciones NO incluye criterios de evaluacion, ponderaciones, ni las ofertas de los proveedores no adjudicados. Solo entrega UrlActa como PDF.

- **Fuente declarada:** [DOC] ausencia en el diccionario de 97 campos
- **Metodo:** Buscar en las claves reales del detalle cualquier campo cuyo nombre contenga criterio, evaluacion, ponderacion, oferta o puntaje.
- **Impacto:** No se puede calcular tasa de exito real de un proveedor (participo vs gano), porque no hay registro estructurado de participacion. Solo se puede saber quien gano. Tampoco se puede simular puntaje sin parsear PDFs de actas.
- **PROHIBIDO PROMETER:** 'Win rate', 'probabilidad de adjudicacion' y 'simulador de puntaje' como funciones basadas en datos estructurados.
- **Medido 2026-07-31 21:06:37.279686:**

```json
{
  "campos_de_criterio_o_ponderacion": [],
  "campos_de_oferta_no_adjudicada": [],
  "descartados_por_ruido": [
    "EstadoPublicidadOfertas",
    "Fechas.FechaTiempoEvaluacion",
    "UnidadTiempoEvaluacion"
  ],
  "n_campos_revisados": 89
}
```
- **Evidencia:** La limitacion se confirma cuando no hay criterios/ponderaciones NI listado de ofertas de participantes no adjudicados.

### P-10 — NO VERIFICADO · severidad alta

El CSV mensual de ordenes de compra esta procesado por ChileCompra: convertido a pesos y con transacciones atipicas removidas.

- **Fuente declarada:** [DOC] datosabiertos.chilecompra.cl/Data/SobreDatosAbiertos
- **Metodo:** Comparar el total del CSV de un mes contra la suma de OC del mismo mes obtenidas por API, y registrar la diferencia.
- **Impacto:** Se hereda una definicion de outlier ajena. No usar para benchmark de precios.
- **NO AFIRMAR HASTA MEDIR:** Deteccion de anomalias y sobreprecios sobre datos de OC.
- **Medido:** nunca. Corre `mp-probe run`.

### P-11 — VERIFICADO · severidad alta

Los montos vienen en CLP, CLF (UF), USD, UTM y EUR, y sin serie de tipo de cambio no son agregables entre si.

- **Fuente declarada:** [DOC] Diccionario, seccion 3.2
- **Metodo:** Distribucion de la dimension moneda por conteo y por monto, y porcentaje de filas con tipo de cambio disponible a su fecha.
- **Impacto:** Define el tamano real del problema de conversion. Las filas sin conversion quedan NULL y se reportan en meta.cobertura.
- **PROHIBIDO PROMETER:** Totales de mercado en CLP mientras la cobertura de conversion no este medida y declarada.
- **Medido 2026-07-31 21:28:34.304422:**

```json
{
  "total_licitaciones": 200,
  "por_moneda": [
    {
      "moneda": "CLP",
      "n": 194,
      "pct": 97.0,
      "sin_conversion_clp": 90
    },
    {
      "moneda": "USD",
      "n": 3,
      "pct": 1.5,
      "sin_conversion_clp": 3
    },
    {
      "moneda": "CLF",
      "n": 3,
      "pct": 1.5,
      "sin_conversion_clp": 3
    }
  ]
}
```
- **Evidencia:** Cuanto mas peso tengan CLF/UTM/USD/EUR, mas critica es la tabla tipo_cambio.

### P-12 — VERIFICADO · severidad alta

MontoEstimado solo es publico si VisibilidadMonto=1, y Estimacion=3 significa que el organismo declara que el monto no es estimable.

- **Fuente declarada:** [DOC] Diccionario, campos 55-58 y seccion 3.3
- **Metodo:** Porcentaje de licitaciones con visibilidad_monto verdadero y con monto_estimado no nulo, por tipo de licitacion.
- **Impacto:** Agregar montos estimados sin filtrar subestima el total de forma silenciosa.
- **PROHIBIDO PROMETER:** 'Tamano del mercado' como cifra unica sin declarar cobertura.
- **Medido 2026-07-31 21:26:43.785419:**

```json
{
  "por_tipo": [
    {
      "tipo": "LE",
      "n": 84,
      "con_monto_visible": 48,
      "con_monto": 48,
      "no_estimable": 0,
      "pct_con_monto": 57.14
    },
    {
      "tipo": "LP",
      "n": 55,
      "con_monto_visible": 31,
      "con_monto": 31,
      "no_estimable": 0,
      "pct_con_monto": 56.36
    },
    {
      "tipo": "LR",
      "n": 27,
      "con_monto_visible": 16,
      "con_monto": 16,
      "no_estimable": 0,
      "pct_con_monto": 59.26
    },
    {
      "tipo": "L1",
      "n": 15,
      "con_monto_visible": 11,
      "con_monto": 11,
      "no_estimable": 0,
      "pct_con_monto": 73.33
    },
    {
      "tipo": "O1",
      "n": 10,
      "con_monto_visible": 0,
      "con_monto": 0,
      "no_estimable": 0,
      "pct_con_monto": 0.0
    },
    {
      "tipo": "CO",
      "n": 5,
      "con_monto_visible": 0,
      "con_monto": 0,
      "no_estimable": 0,
      "pct_con_monto": 0.0
    },
    {
      "tipo": "LS",
      "n": 2,
      "con_monto_visible": 1,
      "con_monto": 0,
      "no_estimable": 0,
      "pct_con_monto": 0.0
    },
    {
      "tipo": "B2",
      "n": 1,
      "con_monto_visible": 0,
      "con_monto": 0,
      "no_estimable": 0,
      "pct_con_monto": 0.0
    },
    {
      "tipo": "E2",
      "n": 1,
      "con_monto_visible": 1,
      "con_monto": 1,
      "no_estimable": 0,
      "pct_con_monto": 100.0
    }
  ]
}
```
- **Evidencia:** El pct_con_monto es la cobertura real de cualquier cifra de 'tamano de mercado'.

### P-13 — VERIFICADO · severidad alta

Ninguna de las fuentes de ChileCompra entrega la serie de UF, UTM ni tipo de cambio. Hay que integrar una fuente externa.

- **Fuente declarada:** [INFERIDO] por ausencia
- **Metodo:** Contar filas en tipo_cambio por moneda y rango de fechas cubierto.
- **Impacto:** Sin esta tabla, toda medida en CLP sobre documentos en UF, USD, UTM o EUR queda NULL.
- **Medido 2026-07-31 21:26:43.828517:**

```json
{
  "limitacion_confirmada": true,
  "mitigado": false,
  "series_cargadas": [],
  "filas_en_moneda_no_CLP_afectadas": 6
}
```
- **Evidencia:** SIN MITIGAR: no hay serie cargada, toda medida CLP sobre documentos en otra moneda queda NULL. Integrar una fuente externa (SII/BCCh).

### P-16 — VERIFICADO · severidad alta

El presupuesto de cuota se dimensiono con una estimacion inferida de ~1.150 licitaciones nuevas por dia habil, y confundiendo STOCK con FLUJO: estado=activas devuelve las licitaciones abiertas en este momento, no las publicadas hoy.

- **Fuente declarada:** [INFERIDO], refutado parcialmente por la primera medicion
- **Metodo:** Comparar el conteo de estado=activas (stock) contra fecha=ddmmaaaa de tres dias habiles (flujo), y calcular el ratio. Registrar el costo en hits de la carga inicial y de la ingesta diaria por separado.
- **Impacto:** La carga inicial cuesta un hit por licitacion abierta y es un costo unico; la ingesta diaria cuesta un hit por licitacion nueva. Dimensionar con el numero equivocado sobreestima el costo recurrente o revienta la cuota el primer dia.
- **Nota:** Primera medicion 2026-07-31: estado=activas devolvio 4.313, contra los ~1.150 inferidos. Eso es 43 % de la cuota diaria en una sola pasada, y obliga a separar los dos numeros. El flujo sigue sin medir.
- **Medido 2026-07-31 21:04:17.125862:**

```json
{
  "stock_activas": 4350,
  "flujo_por_dia_habil": {
    "2026-07-31": 1198,
    "2026-07-30": 1046,
    "2026-07-29": 1112
  },
  "flujo_promedio": 1118.7,
  "estimacion_previa_inferida_flujo": 1150,
  "interpretacion": "stock/flujo = 3.9x. Consistente con que 'activas' sea un STOCK de licitaciones abiertas.",
  "costo_carga_inicial_hits": 4350,
  "costo_ingesta_diaria_hits": 1118.7,
  "pct_cuota_carga_inicial": 43.5,
  "pct_cuota_diaria": 11.2
}
```
- **Evidencia:** El presupuesto de cuota se dimensiona con el FLUJO, no con el stock. El stock es un costo unico de carga inicial.

### P-19 — NO VERIFICADO · severidad alta

La API no permite escritura: no existe endpoint para presentar una oferta.

- **Fuente declarada:** [DOC] chilecompra.cl/api, solo endpoints de lectura
- **Metodo:** Verificacion documental de la lista de endpoints publicados.
- **Impacto:** La postulacion en mercadopublico.cl es manual y humana.
- **NO AFIRMAR HASTA MEDIR:** 'Postulacion automatica' o 'el agente presenta la oferta'.
- **Medido:** nunca. Corre `mp-probe run`.

### P-21 — VERIFICADO · severidad alta

Ademas de la cuota diaria de 10.000, ChileCompra aplica un LIMITE DE TASA DE CORTO PLAZO que no documenta en ninguna parte. Sin marcapasos entre requests, la API devuelve 429 en cadena.

- **Fuente declarada:** [MEDIDO] 2026-07-31, no aparece en ninguna documentacion
- **Metodo:** Repetir el mismo request a intervalos crecientes (0 / 0,5 / 1,0 / 1,5 / 2,5 s) y buscar el intervalo minimo con cero rechazos. Cuesta ~15 hits.
- **Impacto:** La carga inicial del libro abierto (~4.348 licitaciones) no es una pasada rapida: a un request por segundo son mas de 70 minutos. El cliente necesita marcapasos, backoff exponencial y respeto de Retry-After, o la ingesta no avanza y el ticket queda marcado por uso inconsistente.
- **Nota:** Primera evidencia: en una corrida sin pausas, 216 de 255 requests devolvieron 429. Los exitosos quedaron espaciados 0,7-1,0 s. Ademas aparecieron respuestas HTTP 203 (Non-Authoritative Information) con cuerpo valido, que el cliente trataba como error: corregido para aceptar todo 2xx.
- **PROHIBIDO PROMETER:** 'Consulta en vivo contra la API' como respuesta a una pregunta de usuario: cualquier producto que dispare varias llamadas por pregunta se auto-throttlea.
- **Medido 2026-07-31 21:05:04.226281:**

```json
{
  "metodo": "4 requests seguidos por tramo, cliente sin reintento; conteo tomado del ledger cuota_hit",
  "por_intervalo": {
    "0.1s": {
      "requests": 4,
      "por_status": {
        "200": 3,
        "429": 1
      },
      "rechazos_429": 1
    },
    "0.3s": {
      "requests": 4,
      "por_status": {
        "200": 2,
        "429": 2
      },
      "rechazos_429": 2
    },
    "0.5s": {
      "requests": 4,
      "por_status": {
        "200": 3,
        "429": 1
      },
      "rechazos_429": 1
    },
    "0.75s": {
      "requests": 4,
      "por_status": {
        "200": 3,
        "429": 1
      },
      "rechazos_429": 1
    },
    "1.0s": {
      "requests": 4,
      "por_status": {
        "200": 3,
        "429": 1
      },
      "rechazos_429": 1
    },
    "1.5s": {
      "requests": 4,
      "por_status": {
        "200": 4
      },
      "rechazos_429": 0
    }
  },
  "intervalo_minimo_sin_rechazo_s": 1.5,
  "valor_configurado_en_cliente_s": 1.5,
  "requests_por_minuto_sostenibles": 40.0,
  "carga_inicial_4348_licitaciones_minutos": 109
}
```
- **Evidencia:** El limite de tasa NO esta documentado por ChileCompra. Es un segundo techo, independiente de la cuota diaria de 10.000. Si ningun tramo da cero rechazos, el limite es mas lento que 1,5 s por request.

### P-02 — VERIFICADO · severidad media

El conjunto de campos del detalle puede diferir del diccionario de datos publicado.

- **Fuente declarada:** [INFERIDO]
- **Metodo:** Comparar las claves reales del detalle contra la lista de 97 campos del diccionario y registrar faltantes y extras.
- **Impacto:** Cualquier campo que el diccionario promete y la API no entrega es una funcion prometida que no existe.
- **Nota:** MEDIDO 2026-07-31: el detalle trae 89 rutas de campo. Ademas aparecio el tipo de licitacion "O1" en 10 de 200 licitaciones (5 %), que NO figura en la seccion 3.1 del diccionario de datos (L1, LE, LP, LQ, LR, LS, E2, CO, B2, H2, I2). Un producto que mapee tipos contra la tabla documentada deja ese 5 % fuera en silencio.
- **Medido 2026-07-31 21:06:32.148767:**

```json
{
  "n_campos_reales": 89,
  "campos": [
    "Adjudicacion",
    "CantidadReclamos",
    "CodigoBIP",
    "CodigoEstado",
    "CodigoExterno",
    "CodigoTipo",
    "Comprador.CargoUsuario",
    "Comprador.CodigoOrganismo",
    "Comprador.CodigoUnidad",
    "Comprador.CodigoUsuario",
    "Comprador.ComunaUnidad",
    "Comprador.DireccionUnidad",
    "Comprador.NombreOrganismo",
    "Comprador.NombreUnidad",
    "Comprador.NombreUsuario",
    "Comprador.RegionUnidad",
    "Comprador.RutUnidad",
    "Comprador.RutUsuario",
    "Contrato",
    "Descripcion",
    "DiasCierreLicitacion",
    "DireccionEntrega",
    "DireccionVisita",
    "EmailResponsableContrato",
    "EmailResponsablePago",
    "EsBaseTipo",
    "EsRenovable",
    "Estado",
    "EstadoEtapas",
    "EstadoPublicidadOfertas",
    "Estimacion",
    "Etapas",
    "ExtensionPlazo",
    "FechaCierre",
    "Fechas.FechaActoAperturaEconomica",
    "Fechas.FechaActoAperturaTecnica",
    "Fechas.FechaAdjudicacion",
    "Fechas.FechaCierre",
    "Fechas.FechaCreacion",
    "Fechas.FechaEntregaAntecedentes",
    "Fechas.FechaEstimadaAdjudicacion",
    "Fechas.FechaEstimadaFirma",
    "Fechas.FechaFinal",
    "Fechas.FechaInicio",
    "Fechas.FechaPubRespuestas",
    "Fechas.FechaPublicacion",
    "Fechas.FechaSoporteFisico",
    "Fechas.FechaTiempoEvaluacion",
    "Fechas.FechaVisitaTerreno",
    "Fechas.FechasUsuario",
    "FonoResponsableContrato",
    "FuenteFinanciamiento",
    "Informada",
    "Items.Cantidad",
    "Items.Listado.0.Adjudicacion",
    "Items.Listado.0.Cantidad",
    "Items.Listado.0.Categoria",
    "Items.Listado.0.CodigoCategoria",
    "Items.Listado.0.CodigoProducto",
    "Items.Listado.0.Correlativo",
    "Items.Listado.0.Descripcion",
    "Items.Listado.0.NombreProducto",
    "Items.Listado.0.UnidadMedida",
    "JustificacionMontoEstimado",
    "JustificacionPublicidad",
    "Modalidad",
    "Moneda",
    "MontoEstimado",
    "Nombre",
    "NombreResponsableContrato",
    "NombreResponsablePago",
    "Obras",
    "ObservacionContract",
    "PeriodoTiempoRenovacion",
    "ProhibicionContratacion",
    "SubContratacion",
    "Tiempo",
    "TiempoDuracionContrato",
    "Tipo",
    "TipoConvocatoria",
    "TipoDuracionContrato",
    "TipoPago",
    "TomaRazon",
    "UnidadTiempo",
    "UnidadTiempoContratoLicitacion",
    "UnidadTiempoDuracionContrato",
    "UnidadTiempoEvaluacion",
    "ValorTiempoRenovacion",
    "VisibilidadMonto"
  ]
}
```
- **Evidencia:** Detalle de 1000813-12-LE26: 89 rutas de campo.

### P-14 — NO VERIFICADO · severidad media

El CSV de OC se publica a mas tardar el dia 20 del mes siguiente: rezago de hasta ~50 dias.

- **Fuente declarada:** [DOC] Centro de ayuda KA-01967
- **Metodo:** Registrar la fecha de ultima modificacion del recurso frente al mes que cubre, durante tres meses consecutivos.
- **Impacto:** No sirve para preguntas sobre la semana en curso. Para eso se usa la reserva on-demand de la cuota, dirigida.
- **NO AFIRMAR HASTA MEDIR:** 'Datos de gasto en tiempo real'.
- **Medido:** nunca. Corre `mp-probe run`.

### P-15 — VERIFICADO · severidad media

No esta verificado hasta que fecha hacia atras responde el parametro fecha=ddmmaaaa de la API.

- **Fuente declarada:** [INFERIDO]
- **Metodo:** Consultar fechas de hace 1 mes, 1 ano y 5 anos y registrar si devuelven datos o vacio.
- **Impacto:** Define si el delta puede recuperarse tras una caida larga de ingesta.
- **Medido 2026-07-31 21:04:06.136034:**

```json
{
  "hace_1_mes": {
    "fecha": "2026-07-02",
    "cantidad": 588
  },
  "hace_1_ano": {
    "fecha": "2025-08-01",
    "cantidad": 461
  },
  "hace_5_anos": {
    "fecha": "2021-08-02",
    "cantidad": 506
  }
}
```

### P-17 — NO CONCLUYENTE · severidad media

El volumen diario de ordenes de compra no esta medido.

- **Fuente declarada:** [INFERIDO]
- **Metodo:** Contar filas del CSV de un mes cerrado y dividir por dias habiles.
- **Impacto:** Confirma o refuta que las OC no caben por API y deben venir del bulk.
- **Medido 2026-07-31 21:26:43.885256:**

```json
{
  "tabla": "orden_compra",
  "filas": 0
}
```
- **Evidencia:** 'orden_compra' esta vacia: corre la ingesta antes de esta sonda.

### P-18 — VERIFICADO · severidad media

El diccionario de licitaciones no incluye campos de garantia de seriedad de la oferta ni de fiel cumplimiento.

- **Fuente declarada:** [DOC] ausencia en el diccionario de 97 campos
- **Metodo:** Buscar en las claves reales del detalle cualquier campo cuyo nombre contenga garantia, boleta o caucion.
- **Impacto:** La bandera 'requiere garantia' no se puede derivar de datos estructurados; hay que leer las bases en PDF. Cualquier alerta de garantia es parseo de documento, no consulta.
- **PROHIBIDO PROMETER:** 'Alerta automatica de garantias exigidas' como funcion de datos.
- **Medido 2026-07-31 21:04:22.204014:**

```json
{
  "campos_relacionados_hallados": []
}
```
- **Evidencia:** La limitacion se confirma cuando no hay campos de garantia.

### P-20 — NO VERIFICADO · severidad media

No esta medido con que frecuencia un agente construye un mp_aggregate valido al primer intento. Es la metrica que decide si la gramatica generica sirve.

- **Fuente declarada:** [INFERIDO]
- **Metodo:** Correr las 20 preguntas reales del set de evaluacion y registrar intentos por pregunta, llamadas totales y tokens.
- **Impacto:** Si hacen falta 3 intentos por consulta, la gramatica esta mal disenada y hay que mover mas preguntas a la biblioteca de consultas guardadas.
- **Medido:** nunca. Corre `mp-probe run`.
