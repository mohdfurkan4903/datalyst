# Datalyst

An open-source AI data analyst. Ask a question in plain English; Datalyst writes the query, checks that it is read-only, runs it against your database and shows the result.

Bring your own model: OpenAI, Anthropic, Google Gemini, Ollama, LM Studio, vLLM or any OpenAI-compatible server.

```console
$ datalyst ask "Top 5 customers by amount spent on paid or shipped orders"

SELECT c.name, SUM(oi.quantity * oi.unit_price) AS total_spent
FROM public.customers c
JOIN public.orders o ON c.id = o.customer_id
JOIN public.order_items oi ON o.id = oi.order_id
WHERE o.status IN ('paid', 'shipped')
GROUP BY c.id, c.name
ORDER BY total_spent DESC
LIMIT 5
┏━━━━━━━━━━━━━━┳━━━━━━━━━━━━━┓
┃ name         ┃ total_spent ┃
┡━━━━━━━━━━━━━━╇━━━━━━━━━━━━━┩
│ Customer 180 │ 18608.66    │
│ Customer 99  │ 13701.19    │
│ Customer 70  │ 13625.80    │
│ Customer 135 │ 13482.13    │
│ Customer 168 │ 13096.69    │
└──────────────┴─────────────┘
5 row(s) in 10 ms
1 attempt(s), 37020 ms
```

## Features

- **Text to SQL** with a self-repair loop: if a query fails validation or execution, the error goes back to the model for a corrected attempt.
- **Read-only by design**, enforced in three independent layers (see [Safety](#safety)).
- **Bring your own LLM**: switch providers with environment variables, no code changes.
- **Schema-aware prompts**: tables, columns, keys, table and column comments, and common values of category-like columns are read from the database and given to the model.
- **Large databases**: the schema is cached on disk, and for big databases only the relevant tables are sent to the model (see [Large databases](#large-databases)).
- **Clear refusals**: write requests and questions the data cannot answer are declined instead of guessed.
- **CLI** for asking questions, running SQL through the guardrails and inspecting the schema.

Supported data sources: PostgreSQL. More are on the [roadmap](#roadmap).

## Quick start

Requirements: Python 3.12+, [uv](https://docs.astral.sh/uv/) and Docker.

```bash
git clone https://github.com/mohdfurkan4903/datalyst.git
```

```bash
cd datalyst && uv sync
```

Start the metastore (where Datalyst keeps what it knows about your databases) and the sample shop database:

```bash
docker compose up -d
```

Configure Datalyst:

```bash
cp .env.example .env
```

```bash
uv run datalyst secret-key
```

Put that key in `DATALYST_SECRET_KEY` in `.env` (it encrypts stored connection details), then set your LLM provider, model and key (see [LLM providers](#llm-providers)).

Create the metastore tables:

```bash
uv run datalyst migrate
```

Register the sample database with its read-only user. You are prompted for the connection URL, so the password stays out of your shell history:

```bash
uv run datalyst source add shop
```

The sample URL is `postgresql://datalyst_ro:datalyst_ro@localhost:5433/shop`.

Compose also starts `erp`, a deliberately messy sample: 40 legacy-style tables with cryptic names, no comments and no declared foreign keys, like many real company databases. Register it the same way with `postgresql://wh_ro:wh_ro@localhost:5435/erp`. The true relationships between its tables are listed in [`docker/warehouse/relationships.yaml`](docker/warehouse/relationships.yaml) for testing.

Ask something:

```bash
uv run datalyst ask "How many orders are there per status?"
```

## CLI

| Command | What it does |
|---|---|
| `datalyst ask "<question>"` | The agent writes and runs a query. Add `--no-show-sql` to hide the SQL. |
| `datalyst query "<sql>"` | Runs your own SQL through the same guardrails, without the LLM. |
| `datalyst schema` | Shows the tables, columns and keys the agent can see. `--refresh` re-reads them from the database; `--search "<question>"` shows only the tables retrieval would pick. |
| `datalyst source add/list/sync/remove` | Manage registered databases. `add` tests the connection and reads the schema. |
| `datalyst migrate` | Creates or upgrades the metastore tables. |
| `datalyst secret-key` | Prints a new encryption key for `DATALYST_SECRET_KEY`. |
| `datalyst worker` | Runs the background sync worker until stopped (SIGINT/SIGTERM); `--once` processes queued jobs and exits. |
| `datalyst source status <name>` | Shows sync state, queued jobs, running workers and recent schema changes. |
| `datalyst source sync <name> --background` | Queues a schema check for the worker instead of running it now. |
| `datalyst source relationships <name>` | Lists declared and inferred joins with confidence and evidence; `--inferred` shows only inferred ones. |
| `datalyst source profile <name>` | Queues profiling of every table for the worker. |
| `datalyst source describe <name>` | Queues LLM descriptions for tables whose evidence changed; `--force` redoes all. |
| `datalyst usage` | LLM calls and tokens used per purpose and model (`--source`, `--days`). |
| `datalyst glossary add/list/remove` | Business terms the agent follows, e.g. what "revenue" means, with an optional SQL fragment. |
| `datalyst api` | Runs the HTTP API (`--host`, `--port`, `--workers`). |
| `datalyst user create/list/password` | Manage sign-in accounts; `create --admin` makes an admin. |
| `datalyst group create/add/grant/list` | Groups, members and which sources and tables they can query. |
| `datalyst examples add/list/remove` | Verified question and SQL pairs; `add` checks and runs the SQL first. Similar questions reuse them as examples. |

`ask`, `query` and `schema` take `--source <name>`; it can be left out when only one data source is registered.

## Users, permissions and the API

Datalyst runs as an HTTP API for the web UI and other clients:

```bash
uv run datalyst api --host 0.0.0.0 --port 8000
```

Interactive API docs are served at `/docs`. Create the first admin from the command line (you are prompted for the password, at least 10 characters):

```bash
uv run datalyst user create you@company.com --name "Your Name" --admin
```

- **Accounts.** Email and password, hashed with Argon2. Sign-in returns a signed token (default lifetime 8 hours). Changing a password, changing a role or deactivating a user ends their existing sessions.
- **Roles.** Admins manage data sources, users, groups, descriptions, joins, the glossary and examples. Analysts ask questions, run read-only SQL and give feedback.
- **Access.** Analysts see only the data sources granted to their groups, optionally limited to table patterns such as `public.ord_*`. Hidden tables are removed before anything reaches the LLM: retrieval, value matches, examples and the query check all use the user's visible tables, so a question cannot reach them and SQL that names them is rejected.

```bash
uv run datalyst group create sales
uv run datalyst group add sales analyst@company.com
uv run datalyst group grant sales erp --tables "public.ord_*" --tables "public.cust_mstr"
```

- **History and feedback.** Every question is recorded with its SQL, tables, status, row count and timing. Users rate answers; admins promote good answers to verified examples.
- **Health.** `/health` for liveness and `/ready` for readiness (metastore reachable and migrated).

Main endpoints: `POST /api/auth/login`, `GET /api/sources`, `POST /api/sources/{id}/ask`, `POST /api/sources/{id}/query`, `GET /api/sources/{id}/tables`, `GET /api/history`, plus admin endpoints for sources, curation (`PUT .../tables/{table}/description`, `PATCH .../relationships/{id}`), glossary, examples, users, groups and usage.

## Configuration

All settings are environment variables prefixed with `DATALYST_`, read from the environment or a `.env` file.

| Variable | Default | Description |
|---|---|---|
| `DATALYST_METASTORE_URL` | required | Postgres where Datalyst stores sources and catalogs, e.g. `postgresql://datalyst:pass@host:5432/datalyst` |
| `DATALYST_SECRET_KEY` | required to add sources | Key that encrypts stored connection URLs; create with `datalyst secret-key`. Keep it safe: losing it means re-entering every connection |
| `DATALYST_METASTORE_POOL_MIN_SIZE` / `DATALYST_METASTORE_POOL_MAX_SIZE` | `1` / `10` | Metastore connection pool size |
| `DATALYST_AUTH_TOKEN_MINUTES` | `480` | How long a sign-in lasts |
| `DATALYST_API_CORS_ORIGINS` | `[]` | JSON list of browser origins allowed to call the API |
| `DATALYST_QUERY_TIMEOUT_SECONDS` | `30` | Statement timeout per query |
| `DATALYST_MAX_ROWS` | `1000` | Maximum rows returned; larger results are marked truncated |
| `DATALYST_POOL_MIN_SIZE` / `DATALYST_POOL_MAX_SIZE` | `1` / `5` | Connection pool size |
| `DATALYST_SCHEMA_SAMPLE_VALUES` | `true` | Include common values of category-like text columns |
| `DATALYST_SYNC_BATCH_SIZE` | `200` | Changed tables re-read per sync job |
| `DATALYST_PROFILE_SAMPLE_ROWS` | `500` | Rows sampled per table when profiling |
| `DATALYST_PROFILE_MAX_AGE_HOURS` | `24` | How often tables are re-profiled to pick up data changes |
| `DATALYST_WORKER_POLL_INTERVAL_SECONDS` | `2` | How often an idle worker looks for jobs |
| `DATALYST_WORKER_LEASE_SECONDS` | `900` | After this long, a running job whose worker stopped is handed to another worker |
| `DATALYST_RETRIEVAL_SEARCH_ABOVE_TABLES` | `30` | Send the whole schema up to this many tables; search above it |
| `DATALYST_RETRIEVAL_TOP_K` | `10` | Best-matching tables taken from the search |
| `DATALYST_RETRIEVAL_MAX_TABLES` | `20` | Most tables sent to the model, including join tables |
| `DATALYST_RETRIEVAL_REWRITE_QUESTION` | `true` | Let the model rewrite the question into search terms first |
| `DATALYST_RETRIEVAL_CANDIDATES` | `30` | Candidate tables gathered before selection |
| `DATALYST_RETRIEVAL_SELECT_ABOVE` | `12` | Let the LLM choose tables when there are more candidates than this |
| `DATALYST_LLM_PROVIDER` | `openai` | See the table below |
| `DATALYST_LLM_MODEL` | `gpt-4.1-mini` | Model name for the provider |
| `DATALYST_LLM_API_KEY` | empty | API key, if the provider needs one |
| `DATALYST_LLM_BASE_URL` | empty | Custom endpoint for local or self-hosted servers |
| `DATALYST_LLM_TEMPERATURE` | `0` | Sampling temperature |
| `DATALYST_LLM_TIMEOUT_SECONDS` | `60` | Timeout per LLM call |
| `DATALYST_LLM_MAX_RETRIES` | `2` | Retries on transient LLM errors |
| `DATALYST_AGENT_MAX_ATTEMPTS` | `3` | Generate-and-repair attempts per question |
| `DATALYST_DESCRIBE_ENABLED` | `true` | Let the worker write AI descriptions |
| `DATALYST_DESCRIBE_SAMPLE_ROWS` | `5` | Sample rows shown to the LLM when a source allows rows |
| `DATALYST_DESCRIBE_LLM_PROVIDER` / `_MODEL` / `_API_KEY` / `_BASE_URL` | main LLM | Separate, usually cheaper model for background descriptions |
| `DATALYST_EMBEDDING_PROVIDER` / `_MODEL` / `_API_KEY` / `_BASE_URL` | off | Embedding model, e.g. `google_genai` + `gemini-embedding-001`, `openai` + `text-embedding-3-small`, `ollama`, or `lmstudio` |
| `DATALYST_EMBED_COLUMNS` | `true` | Embed columns as well as tables |

## LLM providers

| Provider | `DATALYST_LLM_PROVIDER` | Extra install | Notes |
|---|---|---|---|
| OpenAI | `openai` | none | Set `DATALYST_LLM_API_KEY` |
| Anthropic | `anthropic` | `uv sync --extra anthropic` | Set `DATALYST_LLM_API_KEY` |
| Google Gemini | `google_genai` | `uv sync --extra google` | Set `DATALYST_LLM_API_KEY` |
| Groq | `groq` | none | Set `DATALYST_LLM_API_KEY`; e.g. `openai/gpt-oss-20b` |
| Ollama | `ollama` | `uv sync --extra ollama` | Runs locally, no key |
| LM Studio | `lmstudio` | none | Defaults to `http://localhost:1234/v1` |
| vLLM | `vllm` | none | Defaults to `http://localhost:8000/v1` |

Any other OpenAI-compatible server works with `openai` plus `DATALYST_LLM_BASE_URL`.

Example for a local model in LM Studio:

```dotenv
DATALYST_LLM_PROVIDER=lmstudio
DATALYST_LLM_MODEL=qwen/qwen3.5-9b
DATALYST_LLM_BASE_URL=http://localhost:1234/v1
DATALYST_LLM_TIMEOUT_SECONDS=180
```

## Large databases

Datalyst is built for databases with hundreds or thousands of tables.

- **Schema catalog.** Each data source's schema is read once and stored in the metastore, shared by every user and every Datalyst process. Questions read it from there, not from the database's system catalog. Each process keeps an in-memory copy and reloads it only when the catalog version changes.
- **Staying in sync.** The sync worker (`datalyst worker`) checks every source on its own interval (5 minutes by default, set per source). A check is one query that hashes each table's columns, types, keys and comments; only tables whose hash changed are re-read. Added, altered (with the exact column changes) and removed tables are logged, and descriptions of changed tables and columns are marked stale for review. Run one or several workers: jobs are claimed with `FOR UPDATE SKIP LOCKED`, failed jobs retry with backoff, and jobs left by a stopped worker are picked up again. Check `datalyst source status <name>` to see it working.
- **Profiling.** After a table is synced, and again daily, the worker profiles it from Postgres statistics and a small sample (`TABLESAMPLE` on big tables): null rate, distinct count, value range, examples, and a detected meaning such as email, URL, phone, UUID, country or currency code, yes/no flag, category, money, money in cents, percent, quantity, JSON (with its keys), or a date stored as text with its format (for example `YYYYMMDD`). This works with no comments at all. Values of low-cardinality columns are indexed with trigram search, so questions can be matched to real values.
- **Relationship inference.** Many databases declare no foreign keys. Datalyst proposes joins from column naming (`cust_ref` to `cust_mstr.cust_id`, `from_loc` to `str_loc.loc_id`, `mgr_emp_id` to `emp.emp_id`, `appr_by` to an employee table) and confirms each one by checking that sampled values really exist in the parent column. Staging, temporary and backup tables are ignored. Each join gets a confidence; those at 0.8 or above are used when writing queries. Declared keys are never overridden, and a join an admin rejects stays rejected. On the bundled `erp` sample (no declared keys), it recovers all 42 true joins with no false ones; real schemas will vary, so review them with `datalyst source relationships <name>`.
- **AI descriptions.** For tables without good documentation, the worker asks the LLM to describe each table and column from the evidence above: what a row is, units and encodings, the likely meaning of codes (for example "S likely shipped"), synonyms business users would say, and example questions. Precedence is always human-written text, then database comments, then AI text; AI never overwrites what a person wrote, and descriptions affected by a schema change are marked stale. A table is only described again when its evidence changes, so repeated runs cost nothing. What may be sent is set per source with `--llm-data`: `rows` (statistics, common values and 5 sample rows), `stats` (no rows) or `names` (structure only, no data). Background descriptions can use a cheaper model than questions (`DATALYST_DESCRIBE_LLM_*`), and every call is logged (`datalyst usage`).
- **Embeddings.** With an embedding provider configured, tables and columns are embedded (pgvector, in the metastore) for meaning-based search; only changed objects are re-embedded, and rate limits pause and resume the work instead of failing it.
- **Finding the right tables.** For large schemas, candidate tables come from several signals fused by rank: keyword search over names, descriptions and synonyms; meaning search over table and column embeddings; tables whose values match words in the question (for example "electronics" matching `cat_ref.cat_desc = 'Electronics'`); tables used by similar verified examples; and tables named in matching glossary terms. Staging, temporary and backup tables are left out unless the question names them. When there are many candidates, the LLM picks the ones it needs, and the join graph (declared and inferred keys) adds the tables that connect them.
- **What the model sees.** Each table with its description, each column with its meaning, codes, units, text-date format and date range, the matching values, the business terms and the verified examples.
- **Checking before running.** Besides the safety checks, every table and column in a generated query is checked against the catalog. An unknown name is sent back to the model with a "did you mean" suggestion before anything runs on the database.
- **Self-healing.** If a query fails because a table or column no longer exists, Datalyst re-syncs the schema on the spot, shows the model the current definition and retries once.
- **Descriptions and values.** Table and column comments (`COMMENT ON ...`) and the common values of low-cardinality text columns, taken from Postgres statistics, help the model choose the right columns and filter values. Add comments to your own tables to improve accuracy. Common values are real data: they are sent to your LLM provider and stored in the catalog, so set `DATALYST_SCHEMA_SAMPLE_VALUES=false` if that is not acceptable.
- **Table retrieval.** Up to `DATALYST_RETRIEVAL_SEARCH_ABOVE_TABLES` tables, the whole schema is sent. Above that, a BM25 keyword search over table names, column names, comments and values picks the best matches, and the foreign-key graph adds the tables needed to join them, up to `DATALYST_RETRIEVAL_MAX_TABLES`.
- **Question rewriting.** For large schemas, the model first rewrites the question into a clear question plus search terms and synonyms, so "clients who bought gadgets" still finds `customers` and `products`. The query is still written from the original question.

Check what retrieval picks for a question:

```bash
uv run datalyst schema --search "which customers bought electronics"
```

## Safety

Datalyst never needs write access, and it does not trust the model to behave. Every query passes three independent layers:

1. **Query validation.** The SQL is parsed into a syntax tree with [sqlglot](https://github.com/tobymao/sqlglot). Only a single `SELECT` (or `UNION`/`INTERSECT`/`EXCEPT`) statement is allowed. Any write, DDL, `COPY`, `SET`, locking, transaction control or dangerous function (`pg_sleep`, `pg_read_file`, `dblink`, `set_config` and others) is rejected before it reaches the database.
2. **Read-only sessions.** Every pooled connection is opened read-only, each query runs with a statement timeout, and results are capped at `DATALYST_MAX_ROWS`.
3. **Read-only database role.** Connect with a role that only has `SELECT`. The sample setup creates one in [`docker/postgres/init/03_readonly_role.sql`](docker/postgres/init/03_readonly_role.sql); use it as a template for your own database:

```sql
CREATE ROLE datalyst_ro LOGIN PASSWORD '<choose-a-password>';
GRANT CONNECT ON DATABASE your_db TO datalyst_ro;
GRANT USAGE ON SCHEMA public TO datalyst_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO datalyst_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO datalyst_ro;
ALTER ROLE datalyst_ro SET default_transaction_read_only = on;
```

If one layer has a gap, the next one still blocks the write.

## How it works

```
question ──► AnalystService ──► SchemaCatalog (cached schema)
                   │
                   ▼
             AnalystAgent (LangGraph)
                   │
     large schema? ├── yes ──► rewrite ──┐
                   └── no ───────────────┤
                                         ▼
                                     retrieve
                                         │
                     many candidates? ── yes ──► select (LLM)
                                         │                │
                    ┌──────────────► generate ◄───────────┘
                    │                    │ └──► refused ──► answer
                    ├──── error ──── validate (safety + catalog check)
                    │                    │
                    ├──── error ──── execute ──► result ──► answer
                    │                    │ table or column gone
                    └───────────────── heal (re-sync, retry once)
```

- **rewrite** (large schemas only) turns the question into a clear question and search terms.
- **retrieve** gathers candidate tables and context (value matches, business terms, examples): all tables for small schemas, the relevant ones for large schemas.
- **select** lets the LLM choose among many candidates.
- **generate** sends the schema and question to the model and extracts the SQL.
- **validate** runs the safety checks.
- **execute** runs the query on a read-only connection.
- On a validation or execution error, the error is sent back to the model, up to `DATALYST_AGENT_MAX_ATTEMPTS` times.

### Project layout

```
src/datalyst/
├── core/          models, interfaces and exceptions (no external dependencies)
├── connectors/    data source implementations and the connector registry
│   └── postgres/  connection pool, schema introspection, query validator
├── catalog/       schema cache, table search and retrieval
├── llm/           chat model factory for all providers
├── agent/         LangGraph agent, prompts and response parsing
├── services/      analyst, questions, auth and the per-source registry
├── api/           FastAPI app, routers, auth dependencies, error mapping
├── security/      password hashing, tokens, permissions, secret encryption
├── bootstrap.py   wires the pieces together from settings
├── config.py      settings
└── cli.py         command-line interface
```

New data sources plug in by implementing the `DataSource` interface and registering a URL scheme with `@register_connector`.

## Development

```bash
uv sync --all-extras
```

```bash
uv run pytest -m "not integration"
```

The integration tests need the Docker Postgres running:

```bash
docker compose up -d && uv run pytest
```

Lint and format:

```bash
uv run ruff check . && uv run ruff format .
```

## Roadmap

- Evaluations: a golden dataset of questions with reference queries, scored on execution accuracy and safety
- Observability: OpenTelemetry tracing of every question, LLM call and query
- REST API (FastAPI) and a web UI (Streamlit) with charts
- Conversation memory and multiple sessions
- Authentication and multi-user support
- More data sources: CSV and Parquet upload, MongoDB, Cassandra and others
- MCP server

## Contributing

Issues and pull requests are welcome. Please run the tests and ruff before opening a pull request.
