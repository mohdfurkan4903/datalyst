from fastapi import APIRouter

from datalyst.api.deps import Admin, CurrentUser, Services
from datalyst.api.schemas import (
    DescriptionUpdate,
    RelationshipCreate,
    RelationshipUpdate,
)
from datalyst.core.models import ColumnRef, Table, TableRef

router = APIRouter(prefix="/api/sources/{source_id}", tags=["catalog"])


@router.get("/tables")
def list_tables(source_id: int, user: CurrentUser, app: Services) -> list[Table]:
    return app.questions.schema(user, source_id).tables


@router.get("/tables/{table}")
def get_table(source_id: int, table: str, user: CurrentUser, app: Services) -> dict:
    user.require_source(source_id)
    return app.metastore.catalog.curation(source_id, _ref(table))


@router.put("/tables/{table}/description", status_code=204)
def describe_table(
    source_id: int, table: str, body: DescriptionUpdate, _: Admin, app: Services
) -> None:
    app.metastore.catalog.set_description(source_id, _ref(table), body.description)


@router.put("/tables/{table}/columns/{column}/description", status_code=204)
def describe_column(
    source_id: int,
    table: str,
    column: str,
    body: DescriptionUpdate,
    _: Admin,
    app: Services,
) -> None:
    app.metastore.catalog.set_description(
        source_id, _ref(table), body.description, column
    )


@router.get("/stale")
def stale_descriptions(source_id: int, _: Admin, app: Services) -> list[dict]:
    return app.metastore.catalog.stale_descriptions(source_id)


@router.get("/relationships")
def list_relationships(source_id: int, user: CurrentUser, app: Services) -> list[dict]:
    user.require_source(source_id)
    return app.metastore.catalog.relationships(source_id)


@router.patch("/relationships/{relationship_id}", status_code=204)
def update_relationship(
    source_id: int,
    relationship_id: int,
    body: RelationshipUpdate,
    _: Admin,
    app: Services,
) -> None:
    app.metastore.catalog.set_relationship_status(
        source_id, relationship_id, body.status
    )


@router.post("/relationships", status_code=201)
def add_relationship(
    source_id: int, body: RelationshipCreate, _: Admin, app: Services
) -> dict:
    app.metastore.catalog.add_relationship(
        source_id, _column(body.child), _column(body.parent)
    )
    return {"child": body.child, "parent": body.parent, "origin": "human"}


def _ref(name: str) -> TableRef:
    if "." not in name:
        name = f"public.{name}"
    return TableRef.parse(name)


def _column(name: str) -> ColumnRef:
    table, _, column = name.rpartition(".")
    return ColumnRef(table=_ref(table), column=column)
