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
