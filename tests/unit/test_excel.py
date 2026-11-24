from datetime import date, datetime, time

import pytest
from openpyxl import Workbook

from datalyst.connectors.files import FilesDataSource, FileStore
from datalyst.core.exceptions import ConfigurationError


@pytest.fixture
def store(tmp_path):
    return FileStore.create(tmp_path / "files")


def save(tmp_path, build, name="book.xlsx"):
    workbook = Workbook()
    build(workbook)
    path = tmp_path / name
    workbook.save(path)
    return path


def read(store, query):
    with FilesDataSource(store.url, store.directory.parent) as source:
        return source.execute(query)


def types(store, table):
    with FilesDataSource(store.url, store.directory.parent) as source:
        return {
            c.name: c.data_type
            for t in source.describe_tables()
            if t.name == table
            for c in t.columns
        }


def one_sheet(workbook):
    sheet = workbook.active
    sheet.title = "Orders"
    sheet.append(["Order ID", "Customer", "Order Date", "Total ($)", "Paid?", "Zip"])
    sheet.append([1, "Ann", date(2024, 1, 5), 10.5, True, "02134"])
    sheet.append([2, "Bob", datetime(2024, 2, 11), 20, False, "10001"])
    sheet.append([3, None, datetime(2024, 3, 1), 7.25, True, "94105"])


def test_a_workbook_with_one_sheet_is_named_after_the_file(tmp_path, store):
    imported = store.add(save(tmp_path, one_sheet), "Sales 2024.xlsx")

    assert [(t.name, t.rows) for t in imported] == [("sales_2024", 3)]
    assert imported[0].columns == [
        "order_id",
        "customer",
        "order_date",
        "total",
        "paid",
        "zip",
    ]


def test_types_and_values_are_kept(tmp_path, store):
    store.add(save(tmp_path, one_sheet), "orders.xlsx")

    assert types(store, "orders") == {
        "order_id": "BIGINT",
        "customer": "VARCHAR",
        "order_date": "DATE",
        "total": "DOUBLE",
        "paid": "BOOLEAN",
        "zip": "VARCHAR",
    }
    rows = read(store, "SELECT * FROM orders ORDER BY order_id").rows
    assert rows[0] == [1, "Ann", date(2024, 1, 5), 10.5, True, "02134"]
    assert rows[2][1] is None
    assert read(store, "SELECT SUM(total) FROM orders").rows == [[37.75]]


def test_several_sheets_become_tables_named_after_the_sheets(tmp_path, store):
    def build(workbook):
        customers = workbook.active
        customers.title = "Customers"
        customers.append(["id", "name"])
        customers.append([1, "Ann"])
        orders = workbook.create_sheet("Q1 Orders")
        orders.append(["id", "customer_id", "placed"])
        orders.append([10, 1, datetime(2024, 1, 5, 13, 30)])

    imported = store.add(save(tmp_path, build), "book.xlsx")

    assert [t.name for t in imported] == ["customers", "q1_orders"]
    assert types(store, "q1_orders")["placed"] == "TIMESTAMP"
    assert read(
        store,
        "SELECT c.name FROM customers c JOIN q1_orders o ON o.customer_id = c.id",
    ).rows == [["Ann"]]


def test_title_rows_above_the_table_are_skipped(tmp_path, store):
    def build(workbook):
        sheet = workbook.active
        sheet.append(["Quarterly report"])
        sheet.append([])
        sheet.append(["Region", "Units", "Revenue"])
        sheet.append(["North", 5, 100.0])
        sheet.append(["South", 7, 140.5])

    imported = store.add(save(tmp_path, build), "report.xlsx")

    assert imported[0].columns == ["region", "units", "revenue"]
    assert imported[0].rows == 2


def test_hidden_and_empty_sheets_are_left_out(tmp_path, store):
    def build(workbook):
        data = workbook.active
        data.title = "Data"
        data.append(["a", "b"])
        data.append([1, 2])
        workbook.create_sheet("Blank")
        secret = workbook.create_sheet("Secret")
        secret.append(["x"])
        secret.append([1])
        secret.sheet_state = "hidden"
        notes = workbook.create_sheet("Notes")
        notes.append(["just a header"])

    imported = store.add(save(tmp_path, build), "book.xlsx")

    assert [t.name for t in imported] == ["book"]


def test_messy_cells_are_cleaned(tmp_path, store):
    def build(workbook):
        sheet = workbook.active
        sheet.append(["Name", "", "Score", "Score", "Mixed"])
        sheet.append(["  Ann  ", None, 1, 2, 5])
        sheet.append([None, None, None, None, None])
        sheet.append(["Bob", "extra", "#DIV/0!", 4, "n/a"])
        sheet.append(["Cy", None, 3, 5, 7.0])

    imported = store.add(save(tmp_path, build), "messy.xlsx")

    assert imported[0].columns == ["name", "column_2", "score", "score_2", "mixed"]
    assert imported[0].rows == 3
    assert read(store, "SELECT name FROM messy ORDER BY name").rows == [
        ["Ann"],
        ["Bob"],
        ["Cy"],
    ]
    assert read(store, "SELECT score FROM messy ORDER BY name").rows == [
        [1],
        [None],
        [3],
    ]
    # A column mixing numbers and words stays text, without ".0" noise.
    assert read(store, "SELECT mixed FROM messy ORDER BY name").rows == [
        ["5"],
        ["n/a"],
        ["7"],
    ]


def test_times_and_text_with_commas_and_newlines(tmp_path, store):
    def build(workbook):
        sheet = workbook.active
        sheet.append(["note", "seen_at"])
        sheet.append(['said "hi", then left\nsecond line', time(9, 30)])
        sheet.append(["plain", time(17, 0)])

    store.add(save(tmp_path, build), "notes.xlsx")

    rows = read(store, "SELECT note, seen_at FROM notes ORDER BY seen_at").rows
    assert rows == [
        ['said "hi", then left\nsecond line', time(9, 30)],
        ["plain", time(17, 0)],
    ]


def test_many_rows_import_quickly(tmp_path, store):
    def build(workbook):
        sheet = workbook.active
        sheet.append(["id", "name", "amount", "day"])
        for i in range(20000):
            sheet.append([i, f"name {i}", i * 0.5, date(2024, 1, 1 + i % 28)])

    imported = store.add(save(tmp_path, build), "big.xlsx")

    assert imported[0].rows == 20000
    assert read(store, "SELECT MAX(amount) FROM big").rows == [[9999.5]]


def test_formulas_without_a_saved_value_are_empty_not_errors(tmp_path, store):
    def build(workbook):
        sheet = workbook.active
        sheet.append(["a", "b"])
        sheet.append([1, "=A2*2"])
        sheet.append([2, 5])

    store.add(save(tmp_path, build), "formulas.xlsx")

    assert read(store, "SELECT b FROM formulas ORDER BY a").rows == [[None], [5]]


def test_replacing_a_workbook_replaces_its_tables(tmp_path, store):
    store.add(save(tmp_path, one_sheet), "orders.xlsx")

    again = store.add(save(tmp_path, one_sheet), "orders.xlsx")

    assert again[0].replaced is True
    assert [f.table for f in store.files()] == ["orders"]


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"not a zip at all", "Could not read"),
        (b"PK\x03\x04broken", "Could not read"),
    ],
)
def test_damaged_workbooks_are_rejected(tmp_path, store, content, message):
    path = tmp_path / "bad.xlsx"
    path.write_bytes(content)

    with pytest.raises(ConfigurationError, match=message):
        store.add(path, "bad.xlsx")
    assert store.files() == []


def test_empty_workbooks_are_rejected(tmp_path, store):
    path = save(tmp_path, lambda workbook: None, "empty.xlsx")

    with pytest.raises(ConfigurationError, match="no data rows"):
        store.add(path, "empty.xlsx")
    assert store.files() == []
