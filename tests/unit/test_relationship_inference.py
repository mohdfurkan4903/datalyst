import pytest

from datalyst.core.models import (
    Column,
    ColumnProfile,
    ColumnRef,
    Containment,
    DataSchema,
    ForeignKey,
    Table,
    TableKind,
)
from datalyst.enrichment.relationships import (
    candidates,
    infer_relationships,
    is_noise_table,
    name_score,
)


def table(name, *columns, pk=None, kind=TableKind.TABLE, unique=(), fks=()):
    return Table(
        schema_name="public",
        name=name,
        kind=kind,
        columns=[
            Column(
                name=c,
                data_type=t,
                profile=ColumnProfile(unique=True) if c in unique else None,
            )
            for c, t in columns
        ],
        primary_key=[pk] if pk else [],
        foreign_keys=[
            ForeignKey(column=c, references_table=r, references_column="id")
            for c, r in fks
        ],
    )


def col(t, name):
    return next(c for c in t.columns if c.name == name)


CUST = table("cust_mstr", ("cust_id", "integer"), ("cust_nm", "text"), pk="cust_id")
ADDR = table("cust_addr", ("addr_id", "integer"), ("cust_id", "integer"), pk="addr_id")
EMP = table(
    "emp",
    ("emp_id", "integer"),
    ("mgr_emp_id", "integer"),
    ("dept_cd", "varchar(4)"),
    pk="emp_id",
)
LOC = table("str_loc", ("loc_id", "integer"), pk="loc_id")
CUSTOMERS = table("customers", ("id", "integer"), pk="id")
ORDERS = table(
    "ord_hdr",
    ("ord_no", "bigint"),
    ("cust_ref", "integer"),
    ("customer_id", "integer"),
    pk="ord_no",
)
XFER = table("wh_xfer", ("xfer_id", "integer"), ("from_loc", "integer"), pk="xfer_id")
PO = table("po_hdr", ("po_no", "integer"), ("appr_by", "integer"), pk="po_no")
LINES = table("ord_dtl", ("qty", "integer"), ("ord_no", "bigint"))


@pytest.mark.parametrize(
    ("child_table", "child", "parent_table", "parent", "score"),
    [
        (ADDR, "cust_id", CUST, "cust_id", 1.0),
        (ORDERS, "cust_ref", CUST, "cust_id", 0.8),
        (ORDERS, "customer_id", CUSTOMERS, "id", 0.9),
        (XFER, "from_loc", LOC, "loc_id", 0.8),
        (EMP, "mgr_emp_id", EMP, "emp_id", 0.8),
        (PO, "appr_by", EMP, "emp_id", 0.6),
        (CUST, "cust_id", ADDR, "addr_id", 0.0),
        (LINES, "qty", CUST, "cust_id", 0.0),
        (CUST, "cust_id", CUST, "cust_id", 0.0),
    ],
)
def test_name_score(child_table, child, parent_table, parent, score):
    found, _ = name_score(
        child_table, col(child_table, child), parent_table, col(parent_table, parent)
    )
    assert found == score


def test_generic_id_needs_the_whole_table_name():
    loyalty_txn = table("loy_txn", ("id", "bigint"), pk="id")
    account = table("loy_acct", ("loy_id", "integer"), pk="loy_id")

    score, _ = name_score(
        account, col(account, "loy_id"), loyalty_txn, col(loyalty_txn, "id")
    )
    assert score == 0.0


def test_noise_tables():
    assert is_noise_table(table("tmp_bkp_cust"))
    assert is_noise_table(table("stg_imp_2023"))
    assert is_noise_table(table("orders_backup"))
    assert not is_noise_table(table("stage_progress"))
    assert not is_noise_table(table("customers"))


def found_pairs(schema):
    return {
        (c.child.qualified_name, c.parent.qualified_name) for c in candidates(schema)
    }


def test_candidates_skip_noise_views_declared_and_incompatible_types():
    backup = table("tmp_bkp_cust", ("cust_id", "integer"))
    view = table("cust_v", ("cust_id", "integer"), kind=TableKind.VIEW)
    texty = table("notes", ("cust_id", "text"))
    declared = table(
        "cust_note", ("cust_id", "integer"), fks=[("cust_id", "public.cust_mstr")]
    )
    pairs = found_pairs(DataSchema(tables=[CUST, ADDR, backup, view, texty, declared]))

    assert pairs == {("public.cust_addr.cust_id", "public.cust_mstr.cust_id")}


def test_primary_key_is_not_a_child_of_a_merely_unique_column():
    loyalty = table(
        "loy_acct",
        ("loy_id", "integer"),
        ("cust_id", "integer"),
        pk="loy_id",
        unique={"cust_id"},
    )
    pairs = found_pairs(DataSchema(tables=[CUST, loyalty]))

    assert ("public.cust_mstr.cust_id", "public.loy_acct.cust_id") not in pairs
    assert ("public.loy_acct.cust_id", "public.cust_mstr.cust_id") in pairs


def containment_from(ratios):
    def check(child: ColumnRef, parent: ColumnRef) -> Containment:
        ratio = ratios.get((child.qualified_name, parent.qualified_name), 0.0)
        return Containment(sampled=100, matched=round(ratio * 100))

    return check


def test_infer_keeps_contained_candidates_with_confidence():
    schema = DataSchema(tables=[CUST, ADDR, ORDERS, LINES])
    check = containment_from(
        {
            ("public.cust_addr.cust_id", "public.cust_mstr.cust_id"): 1.0,
            ("public.ord_hdr.cust_ref", "public.cust_mstr.cust_id"): 1.0,
            ("public.ord_dtl.ord_no", "public.ord_hdr.ord_no"): 0.5,
        }
    )

    found = {
        (r.child.qualified_name, r.parent.qualified_name): r
        for r in infer_relationships(schema, check)
    }

    assert set(found) == {
        ("public.cust_addr.cust_id", "public.cust_mstr.cust_id"),
        ("public.ord_hdr.cust_ref", "public.cust_mstr.cust_id"),
    }
    assert (
        found[("public.cust_addr.cust_id", "public.cust_mstr.cust_id")].confidence
        == 1.0
    )
    exact = found[("public.ord_hdr.cust_ref", "public.cust_mstr.cust_id")]
    assert exact.confidence == 0.9
    assert exact.evidence["reason"] == "name tokens match"
    assert exact.evidence["sampled"] == 100


def test_best_parent_wins_when_several_match():
    customers2 = table("cust_ref_tbl", ("cust_id", "integer"), pk="cust_id")
    schema = DataSchema(tables=[CUST, customers2, ADDR])
    check = containment_from(
        {
            ("public.cust_addr.cust_id", "public.cust_mstr.cust_id"): 1.0,
            ("public.cust_addr.cust_id", "public.cust_ref_tbl.cust_id"): 0.99,
        }
    )

    [relationship] = infer_relationships(schema, check)
    assert relationship.parent.table.name == "cust_mstr"


def test_nothing_sampled_means_no_relationship():
    def empty(child, parent):
        return Containment(sampled=0, matched=0)

    assert infer_relationships(DataSchema(tables=[CUST, ADDR]), empty) == []
