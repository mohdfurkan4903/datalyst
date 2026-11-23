from datetime import timedelta

from datalyst.core.models import Column, Table, TableFingerprint, TableKind, TableRef
from datalyst.core.schema_diff import chunked, diff_columns, diff_fingerprints
from datalyst.metastore.repositories.jobs import backoff


def ref(name):
    return TableRef(schema_name="public", name=name)


def fingerprint(name, value):
    return TableFingerprint(table=ref(name), kind=TableKind.TABLE, fingerprint=value)


def test_diff_fingerprints():
    diff = diff_fingerprints(
        [fingerprint("a", "1"), fingerprint("b", "2"), fingerprint("new", "3")],
        {ref("a"): "1", ref("b"): "old", ref("gone"): "4", ref("never"): None},
    )

    assert diff.new == [ref("new")]
    assert diff.changed == [ref("b")]
    assert diff.unchanged == [ref("a")]
    assert diff.removed == [ref("gone"), ref("never")]
    assert diff.to_sync == [ref("new"), ref("b")]


def test_table_without_stored_fingerprint_counts_as_changed():
    diff = diff_fingerprints([fingerprint("a", "1")], {ref("a"): None})

    assert diff.changed == [ref("a")]


def table(*columns):
    return Table(
        schema_name="public",
        name="t",
        columns=[Column(name=n, data_type=d) for n, d in columns],
    )


def test_diff_columns():
    before = {"id": "integer", "name": "text", "legacy": "text"}
    after = table(("id", "bigint"), ("name", "text"), ("email", "text"))

    assert diff_columns(before, after) == {
        "added": ["email"],
        "removed": ["legacy"],
        "retyped": {"id": ["integer", "bigint"]},
    }
    assert diff_columns({"id": "integer"}, table(("id", "integer"))) == {}


def test_chunked():
    refs = [ref(str(i)) for i in range(5)]

    assert chunked(refs, 2) == [refs[0:2], refs[2:4], refs[4:5]]
    assert chunked([], 2) == []


def test_backoff_grows_and_is_capped():
    assert backoff(1) == timedelta(seconds=10)
    assert backoff(2) == timedelta(seconds=20)
    assert backoff(4) == timedelta(seconds=80)
    assert backoff(30) == timedelta(hours=1)
