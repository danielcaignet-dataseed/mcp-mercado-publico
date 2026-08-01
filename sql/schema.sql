-- Esquema del almacen. DuckDB.
-- Las tablas de negocio reflejan 1:1 los nombres declarados en semantic.py.
-- Si agregas una columna aca, agregala tambien alli: el registro semantico es
-- el contrato, esto es solo el almacenamiento.

CREATE TABLE IF NOT EXISTS licitacion (
    codigo              VARCHAR PRIMARY KEY,
    nombre              VARCHAR,
    descripcion         VARCHAR,
    estado              VARCHAR,
    tipo                VARCHAR,        -- L1 LE LP LQ LR LS E2 CO B2 H2 I2
    moneda              VARCHAR,
    es_obra             BOOLEAN,
    toma_razon          BOOLEAN,
    subcontratacion     BOOLEAN,
    extension_plazo     BOOLEAN,
    visibilidad_monto   BOOLEAN,
    estimacion          INTEGER,        -- 1 presupuesto, 2 referencial, 3 no estimable
    monto_estimado      DOUBLE,         -- en la moneda original
    monto_estimado_clp  DOUBLE,         -- NULL si falta tipo de cambio: no se imputa
    n_oferentes         INTEGER,
    cantidad_reclamos   INTEGER,
    organismo_codigo    VARCHAR,
    organismo_nombre    VARCHAR,
    region_comprador    VARCHAR,
    comuna_comprador    VARCHAR,
    fecha_publicacion   DATE,
    fecha_cierre        DATE,
    fecha_adjudicacion  DATE,
    url_ficha           VARCHAR,
    url_acta            VARCHAR,
    _procedencia        VARCHAR,        -- ocds-bulk | api-live
    _ingerido_en        TIMESTAMP
);

CREATE TABLE IF NOT EXISTS licitacion_item (
    item_id             VARCHAR PRIMARY KEY,   -- codigo_licitacion || '#' || correlativo
    codigo_licitacion   VARCHAR,
    correlativo         INTEGER,
    unspsc_commodity    VARCHAR,
    unspsc_clase        VARCHAR,
    unspsc_familia      VARCHAR,
    unspsc_segmento     VARCHAR,
    categoria_texto     VARCHAR,
    nombre_producto     VARCHAR,
    descripcion         VARCHAR,
    unidad_medida       VARCHAR,
    cantidad            DOUBLE,
    -- desnormalizado desde licitacion para evitar joins en cada consulta
    estado              VARCHAR,
    organismo_codigo    VARCHAR,
    organismo_nombre    VARCHAR,
    region_comprador    VARCHAR,
    comuna_comprador    VARCHAR,
    fecha_publicacion   DATE,
    fecha_cierre        DATE,
    _procedencia        VARCHAR,
    _ingerido_en        TIMESTAMP
);

CREATE TABLE IF NOT EXISTS adjudicacion_item (
    adjudicacion_id     VARCHAR PRIMARY KEY,
    codigo_licitacion   VARCHAR,
    correlativo         INTEGER,
    unspsc_commodity    VARCHAR,
    unspsc_clase        VARCHAR,
    unspsc_familia      VARCHAR,
    unspsc_segmento     VARCHAR,
    nombre_producto     VARCHAR,
    unidad_medida       VARCHAR,
    moneda              VARCHAR,
    precio_unitario     DOUBLE,
    precio_unitario_clp DOUBLE,        -- NULL si falta tipo de cambio
    cantidad_adjudicada DOUBLE,
    rut_proveedor       VARCHAR,
    nombre_proveedor    VARCHAR,
    tipo                VARCHAR,
    organismo_codigo    VARCHAR,
    organismo_nombre    VARCHAR,
    region_comprador    VARCHAR,
    comuna_comprador    VARCHAR,
    fecha_adjudicacion  DATE,
    -- true si la adjudicacion tiene varios proveedores y no se pudo atribuir
    -- valor por proveedor (caveat documentado por el registro OCP)
    atribucion_ambigua  BOOLEAN DEFAULT FALSE,
    _procedencia        VARCHAR,
    _ingerido_en        TIMESTAMP
);

CREATE TABLE IF NOT EXISTS orden_compra (
    codigo                VARCHAR PRIMARY KEY,
    nombre                VARCHAR,
    descripcion           VARCHAR,
    codigo_licitacion     VARCHAR,
    estado                VARCHAR,
    tipo                  VARCHAR,      -- SE | CM
    moneda                VARCHAR,
    forma_pago            VARCHAR,
    tipo_despacho         VARCHAR,
    total                 DOUBLE,
    total_clp             DOUBLE,
    total_neto_clp        DOUBLE,
    impuestos_clp         DOUBLE,
    promedio_calificacion DOUBLE,
    rut_proveedor         VARCHAR,
    nombre_proveedor      VARCHAR,
    organismo_codigo      VARCHAR,
    organismo_nombre      VARCHAR,
    region_comprador      VARCHAR,
    comuna_comprador      VARCHAR,
    fecha_envio           DATE,
    fecha_aceptacion      DATE,
    _procedencia          VARCHAR,      -- oc-csv-procesado | api-live
    _ingerido_en          TIMESTAMP
);

CREATE TABLE IF NOT EXISTS orden_compra_item (
    item_id                  VARCHAR PRIMARY KEY,
    codigo_oc                VARCHAR,
    correlativo              INTEGER,
    unspsc_commodity         VARCHAR,
    unspsc_clase             VARCHAR,
    unspsc_familia           VARCHAR,
    unspsc_segmento          VARCHAR,
    especificacion_comprador VARCHAR,
    unidad_medida            VARCHAR,
    cantidad                 DOUBLE,
    moneda                   VARCHAR,
    precio_neto              DOUBLE,
    precio_neto_clp          DOUBLE,
    total_clp                DOUBLE,
    rut_proveedor            VARCHAR,
    nombre_proveedor         VARCHAR,
    organismo_codigo         VARCHAR,
    organismo_nombre         VARCHAR,
    region_comprador         VARCHAR,
    comuna_comprador         VARCHAR,
    fecha_envio              DATE,
    _procedencia             VARCHAR,
    _ingerido_en             TIMESTAMP
);

CREATE TABLE IF NOT EXISTS organismo (
    codigo    VARCHAR PRIMARY KEY,
    nombre    VARCHAR,
    region    VARCHAR,
    comuna    VARCHAR,
    actividad VARCHAR
);

CREATE TABLE IF NOT EXISTS proveedor (
    rut       VARCHAR PRIMARY KEY,
    nombre    VARCHAR,
    region    VARCHAR,
    comuna    VARCHAR,
    actividad VARCHAR
);

-- Vocabulario UNSPSC v7. Se puebla desde los datos observados (nombre segun
-- el texto que trae ChileCompra) mas la tabla oficial si se consigue.
CREATE TABLE IF NOT EXISTS unspsc (
    codigo   VARCHAR PRIMARY KEY,
    nivel    VARCHAR,        -- segmento | familia | clase | commodity
    nombre   VARCHAR,
    segmento VARCHAR,
    familia  VARCHAR,
    clase    VARCHAR,
    _origen  VARCHAR         -- 'observado' | 'oficial'
);

-- Conversion a CLP. Sin fila para (moneda, fecha) el monto en CLP queda NULL.
-- No se interpola ni se arrastra el ultimo valor: eso inventaria datos.
CREATE TABLE IF NOT EXISTS tipo_cambio (
    moneda  VARCHAR,
    fecha   DATE,
    a_clp   DOUBLE,
    _fuente VARCHAR,
    PRIMARY KEY (moneda, fecha)
);

-- Consultas guardadas: la primitiva de dashboard.
CREATE TABLE IF NOT EXISTS consulta_guardada (
    query_id     VARCHAR PRIMARY KEY,
    nombre       VARCHAR,
    descripcion  VARCHAR,
    modo         VARCHAR,          -- search | aggregate
    consulta     JSON,
    etiquetas    VARCHAR[],
    creada_en    TIMESTAMP,
    ultima_corrida TIMESTAMP
);

-- Gobernador de cuota: libro mayor de cada hit a la API.
CREATE TABLE IF NOT EXISTS cuota_hit (
    ts       TIMESTAMP,
    endpoint VARCHAR,
    motivo   VARCHAR,
    status   INTEGER
);

CREATE TABLE IF NOT EXISTS ingesta_log (
    ts        TIMESTAMP,
    fuente    VARCHAR,
    detalle   VARCHAR,
    filas     BIGINT,
    ok        BOOLEAN
);

-- Resultado de las sondas. CAPABILITIES.md se genera desde aca.
CREATE TABLE IF NOT EXISTS probe_result (
    probe_id     VARCHAR,
    ts           TIMESTAMP,
    afirmacion   VARCHAR,
    metodo       VARCHAR,
    veredicto    VARCHAR,      -- PASS | FAIL | UNKNOWN
    medido       JSON,
    evidencia    VARCHAR,
    PRIMARY KEY (probe_id, ts)
);
