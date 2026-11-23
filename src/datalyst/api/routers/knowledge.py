from datetime import timedelta

from fastapi import APIRouter

from datalyst.api.deps import Admin, CurrentUser, Services
from datalyst.api.schemas import ExampleCreate, TermUpdate
from datalyst.catalog.sqlrefs import tables_in_sql
from datalyst.core.exceptions import ConfigurationError
from datalyst.core.models import GlossaryTerm, VerifiedQuery
from datalyst.sync import embed_source_request

router = APIRouter(prefix="/api/sources/{source_id}", tags=["knowledge"])


@router.get("/glossary")
def list_terms(source_id: int, user: CurrentUser, app: Services) -> list[GlossaryTerm]:
    user.require_source(source_id)
    return app.metastore.knowledge.glossary(source_id)


@router.put("/glossary/{term}")
def save_term(
    source_id: int, term: str, body: TermUpdate, admin: Admin, app: Services
) -> GlossaryTerm:
    app.metastore.sources.get(source_id)
    return app.metastore.knowledge.save_term(
        source_id,
        GlossaryTerm(
            term=term,
            definition=body.definition,
            sql_hint=body.sql_hint,
            created_by=admin.user.email,
        ),
    )


@router.delete("/glossary/{term}", status_code=204)
def delete_term(source_id: int, term: str, _: Admin, app: Services) -> None:
    app.metastore.knowledge.delete_term(source_id, term)


@router.get("/examples")
def list_examples(
    source_id: int, user: CurrentUser, app: Services
) -> list[VerifiedQuery]:
    user.require_source(source_id)
    visible = {t.qualified_name for t in app.questions.schema(user, source_id).tables}
    return [
        e
        for e in app.metastore.knowledge.examples(source_id)
        if set(e.tables) <= visible
    ]


@router.post("/examples", status_code=201)
def add_example(
    source_id: int, body: ExampleCreate, admin: Admin, app: Services
) -> VerifiedQuery:
    return save_example(app, admin.user.email, source_id, body.question, body.sql)


@router.delete("/examples/{example_id}", status_code=204)
def delete_example(source_id: int, example_id: int, _: Admin, app: Services) -> None:
    app.metastore.knowledge.delete_example(source_id, example_id)


def save_example(
    app, author: str, source_id: int, question: str, sql: str
) -> VerifiedQuery:
    if not question.strip() or not sql.strip():
        raise ConfigurationError("An example needs a question and SQL.")
    source = app.metastore.sources.get(source_id)
    analyst = app.registry.get(source)
    analyst.run_sql(sql)
    schema = analyst.schema()
    saved = app.metastore.knowledge.save_example(
        source_id,
        VerifiedQuery(
            question=question,
            sql=sql.strip().rstrip(";"),
            tables=tables_in_sql(sql, schema),
            created_by=author,
        ),
    )
    app.metastore.jobs.enqueue(embed_source_request(source_id, timedelta(0)))
    return saved
