-- Messy 40-table ERP database used by integration tests: cryptic names,
-- no comments and no declared foreign keys. Ground truth joins: erp_relationships.yaml.
CREATE TABLE ctry_ref (ctry_cd char(2) PRIMARY KEY, ctry_nm text NOT NULL, rgn text);
CREATE TABLE cat_ref (cat_cd varchar(4) PRIMARY KEY, cat_desc text);
CREATE TABLE chnl_ref (chnl_cd varchar(3) PRIMARY KEY, chnl_nm text);
CREATE TABLE carr_ref (carr_cd varchar(4) PRIMARY KEY, carr_nm text);
CREATE TABLE dept_ref (dept_cd varchar(4) PRIMARY KEY, dept_nm text);
CREATE TABLE rsn_ref (rsn_cd smallint PRIMARY KEY, rsn_txt text);

CREATE TABLE cust_mstr (
    cust_id  integer PRIMARY KEY,
    cust_nm  text,
    eml_addr text,
    ph_no    varchar(20),
    ctry_cd  char(2),
    crt_dt   date,
    sts_flg  char(1)
);

CREATE TABLE cust_addr (
    addr_id integer PRIMARY KEY,
    cust_id integer,
    ln1     text,
    cty     text,
    pst_cd  varchar(10),
    ctry_cd char(2),
    is_dflt char(1)
);

CREATE TABLE prd_mstr (
    prd_id   integer PRIMARY KEY,
    prd_nm   text,
    cat_cd   varchar(4),
    unit_prc numeric(10, 2),
    actv_ind boolean
);

CREATE TABLE sku_xref (
    prd_id  integer,
    src_sys text,
    ext_sku text,
    PRIMARY KEY (prd_id, src_sys)
);

CREATE TABLE str_loc (
    loc_id  integer PRIMARY KEY,
    loc_nm  text,
    cty     text,
    ctry_cd char(2),
    opn_dt  date
);

CREATE TABLE inv_bal (
    loc_id     integer,
    prd_id     integer,
    on_hnd_qty integer,
    upd_ts     timestamptz,
    PRIMARY KEY (loc_id, prd_id)
);

CREATE TABLE emp (
    emp_id     integer PRIMARY KEY,
    emp_nm     text,
    mgr_emp_id integer,
    dept_cd    varchar(4),
    loc_id     integer,
    hire_dt    date,
    sal_amt    numeric(12, 2)
);

CREATE TABLE ord_hdr (
    ord_no        bigint PRIMARY KEY,
    cust_ref      integer,
    ord_dt        varchar(8),
    sts_cd        char(1),
    chnl_cd       varchar(3),
    loc_id        integer,
    tot_amt_cents bigint
);

CREATE TABLE ord_dtl (
    ord_no bigint,
    ln_no  smallint,
    prd_id integer,
    qty    integer,
    prc    numeric(10, 2),
    PRIMARY KEY (ord_no, ln_no)
);

CREATE TABLE pmt_txn (
    txn_id   uuid PRIMARY KEY,
    ord_no   bigint,
    pmt_mthd varchar(4),
    amt      numeric(12, 2),
    txn_ts   timestamptz,
    gw_resp  jsonb
);

CREATE TABLE shp_trk (
    trk_id  integer PRIMARY KEY,
    ord_no  bigint,
    carr_cd varchar(4),
    shp_dt  date,
    dlv_dt  date,
    trk_url text
);

CREATE TABLE promo (
    promo_cd varchar(10) PRIMARY KEY,
    disc_pct numeric(5, 2),
    strt_dt  date,
    end_dt   date
);

CREATE TABLE ord_promo (
    ord_no   bigint,
    promo_cd varchar(10),
    PRIMARY KEY (ord_no, promo_cd)
);

CREATE TABLE rtn_req (
    rtn_id   integer PRIMARY KEY,
    ord_no   bigint,
    ln_no    smallint,
    rsn_cd   smallint,
    rtn_dt   date,
    rfnd_amt numeric(10, 2)
);

CREATE TABLE sup_mstr (
    sup_id    integer PRIMARY KEY,
    sup_nm    text,
    ctry_cd   char(2),
    cntct_eml text
);

CREATE TABLE po_hdr (
    po_no   integer PRIMARY KEY,
    sup_id  integer,
    po_dt   date,
    sts     text,
    appr_by integer
);

CREATE TABLE po_dtl (
    po_no     integer,
    ln        smallint,
    prd_id    integer,
    qty       integer,
    unit_cost numeric(10, 2),
    PRIMARY KEY (po_no, ln)
);

CREATE TABLE wh_xfer (
    xfer_id  integer PRIMARY KEY,
    from_loc integer,
    to_loc   integer,
    prd_id   integer,
    qty      integer,
    xfer_dt  date
);

CREATE TABLE loy_acct (
    loy_id  integer PRIMARY KEY,
    cust_id integer UNIQUE,
    tier_cd varchar(3),
    pts_bal integer
);

CREATE TABLE loy_txn (
    id     bigint PRIMARY KEY,
    loy_id integer,
    pts    integer,
    txn_dt date,
    src    text
);

CREATE TABLE web_sess (
    sess_id   uuid PRIMARY KEY,
    cust_ref  integer,
    strt_ts   timestamptz,
    dev_typ   text,
    src_chnl  text
);

CREATE TABLE pg_evt (
    evt_id  bigint PRIMARY KEY,
    sess_id uuid,
    evt_typ text,
    pg_url  text,
    evt_ts  timestamptz
);

CREATE TABLE tkt (
    tkt_id   integer PRIMARY KEY,
    cust_id  integer,
    opn_ts   timestamptz,
    cls_ts   timestamptz,
    pri      smallint,
    sts      text,
    asgn_emp integer
);

CREATE TABLE tkt_msg (
    msg_id   integer PRIMARY KEY,
    tkt_id   integer,
    auth_typ char(1),
    body     text,
    ts       timestamptz
);

CREATE TABLE mkt_cmp (
    cmp_id  integer PRIMARY KEY,
    cmp_nm  text,
    chnl_cd varchar(3),
    bdgt    numeric(12, 2),
    strt_dt date
);

CREATE TABLE cmp_resp (
    cmp_id  integer,
    cust_id integer,
    resp_dt date,
    cnv_flg char(1),
    PRIMARY KEY (cmp_id, cust_id)
);

CREATE TABLE fx_rt (
    from_ccy char(3),
    to_ccy   char(3),
    rt_dt    date,
    rt       numeric(12, 6),
    PRIMARY KEY (from_ccy, to_ccy, rt_dt)
);

CREATE TABLE gl_acct (acct_no varchar(10) PRIMARY KEY, acct_nm text, acct_typ text);

CREATE TABLE gl_jrnl (
    jrnl_id bigint PRIMARY KEY,
    acct_no varchar(10),
    amt     numeric(14, 2),
    dr_cr   char(1),
    pst_dt  date,
    ref_doc text
);

CREATE TABLE aud_log (
    id      bigint PRIMARY KEY,
    tbl_nm  text,
    op      char(1),
    chg_by  text,
    chg_ts  timestamptz,
    old_val jsonb
);

CREATE TABLE etl_run (
    run_id  integer PRIMARY KEY,
    job_nm  text,
    strt_ts timestamptz,
    end_ts  timestamptz,
    rc      smallint
);

CREATE TABLE cfg_param (k text PRIMARY KEY, v text);

CREATE TABLE stg_imp_2023 (raw_cust text, raw_amt text, raw_dt text, ld_ts timestamptz);

CREATE TABLE tmp_bkp_cust (
    cust_id integer,
    cust_nm text,
    eml_addr text,
    ctry_cd char(2),
    crt_dt date
);

SELECT setseed(0.17);

INSERT INTO ctry_ref VALUES
    ('IN', 'India', 'APAC'), ('US', 'United States', 'AMER'), ('GB', 'United Kingdom', 'EMEA'),
    ('DE', 'Germany', 'EMEA'), ('CA', 'Canada', 'AMER'), ('AE', 'United Arab Emirates', 'EMEA'),
    ('SG', 'Singapore', 'APAC'), ('AU', 'Australia', 'APAC');

INSERT INTO cat_ref VALUES
    ('ELEC', 'Electronics'), ('BOOK', 'Books'), ('APRL', 'Apparel'),
    ('HOME', 'Home and kitchen'), ('SPRT', 'Sports'), ('TOYS', 'Toys');

INSERT INTO chnl_ref VALUES
    ('WEB', 'Website'), ('APP', 'Mobile app'), ('POS', 'In store'), ('TEL', 'Telephone');

INSERT INTO carr_ref VALUES
    ('DHL', 'DHL Express'), ('FDX', 'FedEx'), ('UPS', 'UPS'), ('BLD', 'Bluedart');

INSERT INTO dept_ref VALUES
    ('SALE', 'Sales'), ('SUPP', 'Customer support'), ('WHSE', 'Warehouse'),
    ('FIN', 'Finance'), ('MKT', 'Marketing'), ('IT', 'Technology');

INSERT INTO rsn_ref VALUES
    (1, 'Damaged on arrival'), (2, 'Wrong item'), (3, 'Size or fit'),
    (4, 'Changed mind'), (5, 'Late delivery');

INSERT INTO cust_mstr
SELECT g,
       'Cust ' || g,
       'c' || g || '@mail.test',
       '+1-555-' || lpad(g::text, 4, '0'),
       (ARRAY['IN', 'US', 'GB', 'DE', 'CA', 'AE', 'SG', 'AU'])[1 + floor(random() * 8)::int],
       date '2021-01-01' + floor(random() * 1000)::int,
       (ARRAY['A', 'A', 'A', 'A', 'I', 'S'])[1 + floor(random() * 6)::int]
FROM generate_series(1, 500) AS g;

INSERT INTO cust_addr
SELECT g,
       c.cust_id,
       g || ' Market Street',
       (ARRAY['Pune', 'Austin', 'Leeds', 'Berlin', 'Toronto', 'Dubai'])[1 + floor(random() * 6)::int],
       lpad((10000 + g)::text, 6, '0'),
       c.ctry_cd,
       CASE WHEN g <= 500 THEN 'Y' ELSE 'N' END
FROM generate_series(1, 700) AS g
JOIN cust_mstr c ON c.cust_id = 1 + ((g * 7919) % 500);

INSERT INTO prd_mstr
SELECT g,
       'Item ' || g,
       (ARRAY['ELEC', 'BOOK', 'APRL', 'HOME', 'SPRT', 'TOYS'])[1 + floor(random() * 6)::int],
       round((3 + random() * 600)::numeric, 2),
       random() > 0.1
FROM generate_series(1, 120) AS g;

INSERT INTO sku_xref
SELECT g, 'AMZ', 'AMZ-' || left(md5('amz' || g), 8) FROM generate_series(1, 120) AS g
UNION ALL
SELECT g, 'SHPFY', 'SH-' || left(md5('sh' || g), 8) FROM generate_series(1, 80) AS g;

INSERT INTO str_loc
SELECT g,
       'Store ' || lpad(g::text, 2, '0'),
       (ARRAY['Pune', 'Austin', 'Leeds', 'Berlin', 'Toronto', 'Dubai'])[1 + (g % 6)],
       (ARRAY['IN', 'US', 'GB', 'DE', 'CA', 'AE'])[1 + (g % 6)],
       date '2018-01-01' + g * 90
FROM generate_series(1, 12) AS g;

INSERT INTO inv_bal
SELECT l, p, floor(random() * 400)::int, now() - random() * interval '20 days'
FROM generate_series(1, 12) AS l
CROSS JOIN generate_series(1, 120) AS p
WHERE random() < 0.6;

INSERT INTO emp
SELECT g,
       'Employee ' || g,
       CASE WHEN g = 1 THEN NULL WHEN g <= 8 THEN 1 ELSE 2 + (g % 7) END,
       (ARRAY['SALE', 'SUPP', 'WHSE', 'FIN', 'MKT', 'IT'])[1 + floor(random() * 6)::int],
       1 + floor(random() * 12)::int,
       date '2015-01-01' + floor(random() * 3000)::int,
       round((30000 + random() * 90000)::numeric, 2)
FROM generate_series(1, 80) AS g;

INSERT INTO ord_hdr (ord_no, cust_ref, ord_dt, sts_cd, chnl_cd, loc_id)
SELECT 100000 + g,
       1 + floor(random() * 500)::int,
       to_char(date '2024-01-01' + floor(random() * 690)::int, 'YYYYMMDD'),
       (ARRAY['O', 'P', 'P', 'S', 'S', 'S', 'X'])[1 + floor(random() * 7)::int],
       s.chnl,
       CASE WHEN s.chnl = 'POS' THEN 1 + floor(random() * 12)::int END
FROM (
    SELECT g, (ARRAY['WEB', 'WEB', 'APP', 'APP', 'POS', 'TEL'])[1 + floor(random() * 6)::int] AS chnl
    FROM generate_series(1, 6000) AS g
) AS s;

INSERT INTO ord_dtl
SELECT line.ord_no, line.ln_no, p.prd_id, line.qty, p.unit_prc
FROM (
    SELECT h.ord_no,
           n AS ln_no,
           1 + floor(random() * 120)::int AS prd_id,
           1 + floor(random() * 5)::int AS qty
    FROM ord_hdr h
    CROSS JOIN LATERAL generate_series(1, 1 + (h.ord_no % 4)::int) AS n
) AS line
JOIN prd_mstr p ON p.prd_id = line.prd_id;

UPDATE ord_hdr h
SET tot_amt_cents = t.cents
FROM (
    SELECT ord_no, round(sum(qty * prc) * 100)::bigint AS cents FROM ord_dtl GROUP BY ord_no
) AS t
WHERE t.ord_no = h.ord_no;

INSERT INTO pmt_txn
SELECT md5(h.ord_no::text || 'pay')::uuid,
       h.ord_no,
       (ARRAY['CC', 'CC', 'CC', 'PP', 'COD', 'GC'])[1 + floor(random() * 6)::int],
       h.tot_amt_cents / 100.0,
       to_date(h.ord_dt, 'YYYYMMDD') + random() * interval '6 hours',
       jsonb_build_object('code', '00', 'auth', left(md5(h.ord_no::text), 6), 'avs', 'Y')
FROM ord_hdr h
WHERE h.sts_cd IN ('P', 'S');

INSERT INTO shp_trk
SELECT row_number() OVER (ORDER BY h.ord_no),
       h.ord_no,
       (ARRAY['DHL', 'FDX', 'UPS', 'BLD'])[1 + floor(random() * 4)::int],
       s.shp_dt,
       CASE WHEN random() < 0.9 THEN s.shp_dt + 2 + floor(random() * 5)::int END,
       'https://track.example.test/' || h.ord_no
FROM ord_hdr h
CROSS JOIN LATERAL (
    SELECT to_date(h.ord_dt, 'YYYYMMDD') + 1 + floor(random() * 3)::int AS shp_dt
) AS s
WHERE h.sts_cd = 'S';

INSERT INTO promo VALUES
    ('WELCOME10', 10, '2024-01-01', '2025-12-31'), ('SUMMER15', 15, '2024-05-01', '2024-08-31'),
    ('SUMMER25', 25, '2025-05-01', '2025-08-31'), ('DIWALI20', 20, '2024-10-15', '2024-11-15'),
    ('BFRI30', 30, '2024-11-25', '2024-12-02'), ('BFRI35', 35, '2025-11-24', '2025-12-01'),
    ('FREESHIP', 0, '2024-01-01', '2025-12-31'), ('APP5', 5, '2024-03-01', '2025-12-31'),
    ('LOYAL12', 12, '2024-01-01', '2025-12-31'), ('NEWYR10', 10, '2025-01-01', '2025-01-07');

INSERT INTO ord_promo
SELECT ord_no, (ARRAY['WELCOME10', 'SUMMER15', 'SUMMER25', 'DIWALI20', 'BFRI30',
                      'BFRI35', 'FREESHIP', 'APP5', 'LOYAL12', 'NEWYR10'])[1 + floor(random() * 10)::int]
FROM ord_hdr
WHERE random() < 0.2;

INSERT INTO rtn_req
SELECT row_number() OVER (ORDER BY d.ord_no, d.ln_no),
       d.ord_no,
       d.ln_no,
       1 + floor(random() * 5)::smallint,
       to_date(h.ord_dt, 'YYYYMMDD') + 7 + floor(random() * 20)::int,
       d.qty * d.prc
FROM ord_dtl d
JOIN ord_hdr h ON h.ord_no = d.ord_no
WHERE h.sts_cd = 'S' AND random() < 0.05;

INSERT INTO sup_mstr
SELECT g,
       'Supplier ' || g,
       (ARRAY['IN', 'US', 'GB', 'DE', 'CA', 'AE', 'SG', 'AU'])[1 + floor(random() * 8)::int],
       'orders@supplier' || g || '.test'
FROM generate_series(1, 25) AS g;

INSERT INTO po_hdr
SELECT g,
       1 + floor(random() * 25)::int,
       date '2024-01-01' + floor(random() * 680)::int,
       (ARRAY['OPEN', 'RCVD', 'RCVD', 'RCVD', 'CNCL'])[1 + floor(random() * 5)::int],
       1 + floor(random() * 80)::int
FROM generate_series(1, 300) AS g;

INSERT INTO po_dtl
SELECT h.po_no, n, 1 + floor(random() * 120)::int, 10 + floor(random() * 200)::int,
       round((2 + random() * 300)::numeric, 2)
FROM po_hdr h
CROSS JOIN LATERAL generate_series(1, 1 + (h.po_no % 3)) AS n;

INSERT INTO wh_xfer
SELECT g, l.loc, 1 + (l.loc % 12), 1 + floor(random() * 120)::int,
       1 + floor(random() * 50)::int, date '2024-01-01' + floor(random() * 680)::int
FROM generate_series(1, 300) AS g
CROSS JOIN LATERAL (SELECT 1 + floor(random() * 12)::int AS loc) AS l;

INSERT INTO loy_acct
SELECT g, g, (ARRAY['BRZ', 'BRZ', 'SLV', 'GLD', 'PLT'])[1 + floor(random() * 5)::int],
       floor(random() * 20000)::int
FROM generate_series(1, 300) AS g;

INSERT INTO loy_txn
SELECT g, 1 + floor(random() * 300)::int, (floor(random() * 900) - 200)::int,
       date '2024-01-01' + floor(random() * 680)::int,
       (ARRAY['PURCHASE', 'PURCHASE', 'REDEEM', 'BONUS', 'EXPIRE'])[1 + floor(random() * 5)::int]
FROM generate_series(1, 2000) AS g;

INSERT INTO web_sess
SELECT md5('sess' || g)::uuid,
       CASE WHEN random() < 0.6 THEN 1 + floor(random() * 500)::int END,
       timestamptz '2024-01-01' + random() * interval '680 days',
       (ARRAY['desktop', 'mobile', 'mobile', 'tablet'])[1 + floor(random() * 4)::int],
       (ARRAY['organic', 'paid_search', 'email', 'social', 'direct'])[1 + floor(random() * 5)::int]
FROM generate_series(1, 3000) AS g;

INSERT INTO pg_evt
SELECT g,
       md5('sess' || (1 + (g * 31) % 3000))::uuid,
       (ARRAY['view', 'view', 'view', 'add_to_cart', 'checkout', 'search'])[1 + floor(random() * 6)::int],
       (ARRAY['/', '/search', '/p/item', '/cart', '/checkout'])[1 + floor(random() * 5)::int],
       timestamptz '2024-01-01' + random() * interval '680 days'
FROM generate_series(1, 15000) AS g;

INSERT INTO tkt
SELECT g,
       1 + floor(random() * 500)::int,
       o.opened,
       CASE WHEN random() < 0.85 THEN o.opened + random() * interval '5 days' END,
       1 + floor(random() * 4)::smallint,
       (ARRAY['NEW', 'OPEN', 'CLOSED', 'CLOSED', 'CLOSED'])[1 + floor(random() * 5)::int],
       1 + floor(random() * 80)::int
FROM generate_series(1, 600) AS g
CROSS JOIN LATERAL (
    SELECT timestamptz '2024-01-01' + random() * interval '680 days' AS opened
) AS o;

INSERT INTO tkt_msg
SELECT g, 1 + floor(random() * 600)::int, (ARRAY['C', 'A'])[1 + floor(random() * 2)::int],
       'Message body ' || g, timestamptz '2024-01-01' + random() * interval '680 days'
FROM generate_series(1, 1500) AS g;

INSERT INTO mkt_cmp
SELECT g, 'Campaign ' || g, (ARRAY['WEB', 'APP', 'POS', 'TEL'])[1 + floor(random() * 4)::int],
       round((1000 + random() * 50000)::numeric, 2), date '2024-01-01' + g * 40
FROM generate_series(1, 15) AS g;

INSERT INTO cmp_resp
SELECT c, cust, date '2024-01-01' + c * 40 + floor(random() * 30)::int,
       CASE WHEN random() < 0.15 THEN 'Y' ELSE 'N' END
FROM generate_series(1, 15) AS c
CROSS JOIN generate_series(1, 500) AS cust
WHERE random() < 0.2;

INSERT INTO fx_rt
SELECT p.from_ccy, p.to_ccy, date '2025-01-01' + d, round((p.base * (0.97 + random() * 0.06))::numeric, 6)
FROM (VALUES ('USD', 'INR', 83.2), ('USD', 'EUR', 0.92), ('USD', 'GBP', 0.79)) AS p(from_ccy, to_ccy, base)
CROSS JOIN generate_series(0, 89) AS d;

INSERT INTO gl_acct VALUES
    ('1000', 'Cash', 'ASSET'), ('1100', 'Accounts receivable', 'ASSET'), ('1200', 'Inventory', 'ASSET'),
    ('2000', 'Accounts payable', 'LIABILITY'), ('2100', 'Sales tax payable', 'LIABILITY'),
    ('3000', 'Retained earnings', 'EQUITY'), ('4000', 'Product revenue', 'REVENUE'),
    ('4100', 'Shipping revenue', 'REVENUE'), ('4900', 'Refunds', 'REVENUE'),
    ('5000', 'Cost of goods sold', 'EXPENSE'), ('6000', 'Salaries', 'EXPENSE'),
    ('6100', 'Rent', 'EXPENSE'), ('6200', 'Marketing', 'EXPENSE'), ('6300', 'Software', 'EXPENSE'),
    ('6400', 'Payment fees', 'EXPENSE'), ('6500', 'Shipping costs', 'EXPENSE'),
    ('7000', 'Interest income', 'REVENUE'), ('7100', 'FX gain loss', 'EXPENSE'),
    ('8000', 'Income tax', 'EXPENSE'), ('9999', 'Suspense', 'ASSET');

INSERT INTO gl_jrnl
SELECT g,
       (ARRAY['1000', '1100', '1200', '2000', '4000', '4100', '4900', '5000', '6000', '6200'])[1 + floor(random() * 10)::int],
       round((10 + random() * 5000)::numeric, 2),
       (ARRAY['D', 'C'])[1 + floor(random() * 2)::int],
       date '2024-01-01' + floor(random() * 680)::int,
       'DOC-' || (200000 + g)
FROM generate_series(1, 3000) AS g;

INSERT INTO aud_log
SELECT g,
       (ARRAY['cust_mstr', 'prd_mstr', 'ord_hdr', 'emp'])[1 + floor(random() * 4)::int],
       (ARRAY['I', 'U', 'U', 'D'])[1 + floor(random() * 4)::int],
       (ARRAY['etl_svc', 'admin', 'jdoe', 'asmith'])[1 + floor(random() * 4)::int],
       timestamptz '2024-01-01' + random() * interval '680 days',
       jsonb_build_object('row', g)
FROM generate_series(1, 1000) AS g;

INSERT INTO etl_run
SELECT g,
       (ARRAY['load_orders', 'load_customers', 'refresh_inventory', 'gl_close'])[1 + floor(random() * 4)::int],
       s.started,
       s.started + random() * interval '40 minutes',
       (ARRAY[0, 0, 0, 0, 1, 2])[1 + floor(random() * 6)::int]
FROM generate_series(1, 200) AS g
CROSS JOIN LATERAL (
    SELECT timestamptz '2024-01-01' + random() * interval '680 days' AS started
) AS s;

INSERT INTO cfg_param VALUES
    ('base_ccy', 'USD'), ('fiscal_year_start', '04-01'), ('tax_rate_pct', '18'),
    ('loyalty_pts_per_usd', '10'), ('return_window_days', '30'), ('low_stock_qty', '15'),
    ('default_carrier', 'DHL'), ('support_sla_hours', '24'), ('cents_per_unit', '100'),
    ('etl_owner', 'data-eng');

INSERT INTO stg_imp_2023
SELECT 'CUST#' || (1 + floor(random() * 900)::int),
       to_char(random() * 900, 'FM999990.00'),
       to_char(date '2023-01-01' + floor(random() * 365)::int, 'DD/MM/YYYY'),
       timestamptz '2023-12-31' + random() * interval '2 days'
FROM generate_series(1, 300);

INSERT INTO tmp_bkp_cust
SELECT cust_id, cust_nm, eml_addr, ctry_cd, crt_dt FROM cust_mstr WHERE cust_id <= 200;

ANALYZE;
