from pathlib import Path

import pytest
import yaml

from datalyst.core.models import ColumnRef, TableRef

pytestmark = pytest.mark.integration

GROUND_TRUTH = Path(__file__).parents[2] / "docker" / "warehouse" / "relationships.yaml"


def column(text: str) -> ColumnRef:
    table, name = text.strip().rsplit(".", 1)
    return ColumnRef(table=TableRef(schema_name="public", name=table), column=name)


def edges(kind: str) -> list[tuple[ColumnRef, ColumnRef]]:
    data = yaml.safe_load(GROUND_TRUTH.read_text())
    return [tuple(map(column, edge.split("->"))) for edge in data[kind]]


def test_warehouse_is_undocumented(warehouse):
    schema = warehouse.get_schema()

    assert len(schema.tables) == 40
    assert not any(t.description for t in schema.tables)
    assert not any(t.foreign_keys for t in schema.tables)
    assert all(
        t.primary_key for t in schema.tables if not t.name.startswith(("stg_", "tmp_"))
    )


def test_every_true_relationship_holds_in_the_data(warehouse):
    for child, parent in edges("relationships"):
        result = warehouse.containment(child, parent)
        assert result.sampled > 0, child.qualified_name
        assert result.ratio == 1.0, f"{child.qualified_name} -> {parent.qualified_name}"


def test_traps_also_pass_containment(warehouse):
    for child, parent in edges("traps"):
        assert warehouse.containment(child, parent).ratio == 1.0
