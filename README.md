# Datalyst

An open-source AI data analyst your team runs on its own servers. People ask questions in plain English in a web app; Datalyst finds the right tables, writes the SQL, checks that it is read-only, runs it and shows the answer as a table and chart.

Bring your own model: OpenAI, Anthropic, Google Gemini, Groq, Ollama, LM Studio, vLLM or any OpenAI-compatible server.

## Features

- **Web app for the whole team**: ask questions, see charts, keep history, rate answers.
- **Set up in the browser**: the first visit creates the admin; the AI model, API keys, databases and users are all configured in the UI.
- **Understands messy databases**: profiles columns, infers joins that were never declared, and writes descriptions with the LLM, so cryptic legacy schemas work without manual documentation.
- **Stays in sync**: a background worker notices schema changes within minutes and re-reads only what changed.
- **Read-only by design**, enforced in three independent layers (see [Safety](#safety)).
- **Simple access control**: admins manage everything; each database is open to all users or only to the users an admin picks.
- **Scales out**: stateless API and worker processes share one Postgres metastore.

Supported data sources: PostgreSQL, MySQL 8, and uploaded CSV and Excel files. More are on the [roadmap](#roadmap).

## Install

Requirements: Docker with Compose v2.

```bash
git clone https://github.com/mohdfurkan4903/datalyst.git && cd datalyst
```

```bash
cp deploy/.env.example deploy/.env
```

Set `METASTORE_PASSWORD` in `deploy/.env` to any long random value.

Start everything (web UI, API, background worker, a Postgres metastore and an HTTPS proxy):

```bash
docker compose -f deploy/compose.yaml up -d --build
```

Open `http://localhost` and:

1. **Create the admin account.** The setup page appears only while no users exist.
2. **Choose the AI model** on the Settings page: provider, model and API key, then **Test connection**.
3. **Add a database** under Data sources, using a read-only login. The worker reads its structure, profiles it and links its tables within a minute or two.
4. **Invite your team** under Users. Every database is open to all users unless you limit it to picked users on its Data sources entry.

See [docs/deployment.md](docs/deployment.md) for HTTPS on your domain, connecting databases safely, a managed Postgres, scaling, upgrades, backups, monitoring and a security checklist.

## Configuration

Datalyst follows the approach Apache Airflow takes: almost everything is configured at runtime and kept in the metastore, and environment variables are only needed to start.

**Needed to start** (environment variables or a `.env` file):

| Variable | Description |
|---|---|
| `DATALYST_METASTORE_URL` | Postgres where Datalyst keeps everything it knows. Set for you by `deploy/compose.yaml`. |

That is the only one. The key that signs sign-ins is created in the metastore on first start.

**Everything else** is on the **Settings** page (admins only): **Model** and **Embeddings** up front, and the tuning sections (Descriptions, Answers, Retrieval, Queries, Worker, Sign-in) under **Advanced**. Changes reach every API and worker process within a few seconds, with no restart. API keys are never shown again after saving.

Connection URLs and API keys are stored as plain text in the metastore, so protect the metastore and its backups like any database that holds credentials (see [docs/deployment.md](docs/deployment.md)).

**Pinning an option.** Any option can be fixed with an environment variable named `DATALYST__<SECTION>__<KEY>`, for example `DATALYST__LLM__MODEL=gpt-4.1-mini`. A pinned option wins over the stored value and is locked in the UI; this suits teams that manage configuration in code or a secret manager. Precedence: environment variable, then Settings page, then default.

**Seeing the effective configuration.** Like `airflow config list`:

- In the UI: **Settings → Advanced → Download config** (every option, its value and where it comes from) or **Download .env** (only changed options, as `DATALYST__*` variables).
- From the command line:

```bash
datalyst config list --include-sources
```

```bash
datalyst config get-value llm model
```

Secrets are always shown as `< hidden >`. `datalyst config options` lists every option with its environment variable and description.

## LLM providers

| Provider | Setting `llm.provider` | Notes |
|---|---|---|
| OpenAI | `openai` | API key needed |
| Anthropic | `anthropic` | API key needed |
| Google Gemini | `google_genai` | API key needed |
| Groq | `groq` | API key needed; e.g. `openai/gpt-oss-20b` |
| Ollama | `ollama` | Runs locally, no key |
| LM Studio | `lmstudio` | Defaults to `http://localhost:1234/v1` |
| vLLM | `vllm` | Defaults to `http://localhost:8000/v1` |

Any other OpenAI-compatible server works with `openai` plus a base URL. Background descriptions can use a separate, cheaper model (**Descriptions** section), and meaning-based search needs an embedding model (**Embeddings** section: `openai` + `text-embedding-3-small`, `google_genai` + `gemini-embedding-001`, or `ollama`). Use a self-hosted model if no data may leave your network.

## Using Datalyst

Everyone sees:

- **Ask:** chat with a data source; each answer shows the result table, an automatic chart (line for time series, bar for categories, a single number as a metric), the SQL, and 👍/👎 feedback. Results download as CSV. Admins can save a good answer as a verified example that similar questions reuse.
- **History:** past questions with their status and SQL.

Admins also see:

- **Data sources:** add a database (the connection is tested first), sync it, choose what data may be sent to the AI model, and choose who can use it: all users or only picked users.
- **Users:** add users, block or unblock them, make them admins, set a new password, or remove them.
- **Settings:** model, keys and tuning, as described above.

Only admins can change anything shared: data sources, users and settings. Accounts use email and password, hashed with Argon2. Blocking a user, or changing their password or role, ends their sessions at once.

Errors never show internals: people see a short message, and the details (with a reference id matching the API's `x-request-id`) go to the server logs.

Everything the UI does goes through a REST API, so other tools can use it too; the API service serves interactive docs at `/docs`.

## Command line

The `datalyst` command starts the services and covers the few tasks an operator needs without the UI:

| Command | What it does |
|---|---|
| `datalyst api` | Runs the HTTP API (`--host`, `--port`, `--workers`). |
| `datalyst worker` | Runs the background sync worker until stopped; `--once` processes queued jobs and exits. |
| `datalyst migrate` | Creates or upgrades the metastore tables. |
| `datalyst config list / get-value / options` | Shows the effective configuration. |
| `datalyst reset-password <email>` | Sets a password when nobody can sign in. |

## Uploaded files

Admins can upload CSV files (`.csv`, `.tsv`, `.txt`) and Excel workbooks (`.xlsx`, `.xlsm`) on the Data sources page. Each CSV file becomes a table named after the file, so several files in one source can be joined. A workbook with one sheet is named after the file; a workbook with several sheets gives one table per sheet, named after the sheet (hidden and empty sheets are skipped). Datalyst reads the types once (numbers, dates, text, yes/no), cleans the column names, keeps the data as Parquet in the folder set by `files.dir` and answers questions with DuckDB. Uploading a file with the same name replaces that table.

For Excel, title lines above the table are skipped, error cells such as `#DIV/0!` become empty, and a formula is read as the value Excel last saved with it (a workbook written by a program that never calculated its formulas has empty cells there). Old `.xls` files must be saved as `.xlsx` first.

Questions can read only the uploaded tables: DuckDB is locked to the source's own folder, with file, network and extension access switched off, and queries that call file functions are refused before they run. The API and every worker must see the same folder (the deployment compose file shares one volume between them).

## How it understands your data

Datalyst is built for databases with hundreds or thousands of tables, including badly documented ones.

- **Schema catalog.** Each database's structure is read once and stored in the metastore, shared by every user and process. Questions read it from there, not from the database. Each process keeps an in-memory copy and reloads it only when the catalog version changes.
- **Staying in sync.** The worker checks every source on its own interval (5 minutes by default, set per source). A check is one query that hashes each table's columns, types, keys and comments; only tables whose hash changed are re-read. Added, altered and removed tables are logged, and descriptions of changed tables are marked stale for review. Several workers can run at once: jobs are claimed with `FOR UPDATE SKIP LOCKED`, failed jobs retry with backoff, and jobs left by a stopped worker are picked up again.
- **Profiling.** The worker profiles each table from Postgres statistics (MySQL has none, so it uses a bounded sample) and a small sample: null rate, distinct count, value range, examples, and a detected meaning such as email, URL, phone, UUID, country or currency code, yes/no flag, category, money, money in cents, percent, JSON (with its keys), or a date stored as text with its format (for example `YYYYMMDD`). Values of low-cardinality columns are indexed for trigram search, so words in a question can be matched to real values.
- **Relationship inference.** Many databases declare no foreign keys. Datalyst proposes joins from column naming (`cust_ref` to `cust_mstr.cust_id`, `mgr_emp_id` to `emp.emp_id`) and confirms each by checking that sampled values exist in the parent column. Each join gets a confidence; those at 0.8 or above are used. Declared keys are never overridden, and a join an admin rejects stays rejected. On the 40-table test database with no declared keys, it recovers all 42 true joins with no false ones.
- **AI descriptions.** For undocumented tables, the worker asks the LLM to describe each table and column from the evidence above: what a row is, units, likely meanings of codes, synonyms and example questions. Human-written text always wins over database comments, which win over AI text. A table is described again only when its evidence changes. Per source, admins choose what may be sent: statistics, common values and 5 sample rows; statistics only; or names only.
- **Finding the right tables.** Candidate tables come from several signals fused by rank: keyword search over names, descriptions and synonyms; meaning search over embeddings; tables whose values match words in the question; tables used by similar verified examples; and glossary terms. With many candidates the LLM picks the ones it needs, and the join graph adds the tables that connect them. Follow-up style questions are first rewritten into clear ones.
- **Checking before running.** Every table and column in a generated query is checked against the catalog; an unknown name goes back to the model with a "did you mean" suggestion before anything runs.
- **Self-healing.** If a query fails because a table or column no longer exists, Datalyst re-syncs the schema on the spot and retries.

```
question ──► rewrite ──► retrieve ──► select (many candidates) ──► generate
                                                                      │
          answer ◄── execute ◄── validate (safety + catalog) ◄────────┘
                        │             │ error: back to generate with the reason
                        └── table or column gone: re-sync, retry once
```

## Safety

Datalyst never needs write access, and it does not trust the model to behave. Every query passes three independent layers:

1. **Query validation.** The SQL is parsed into a syntax tree with [sqlglot](https://github.com/tobymao/sqlglot). Only a single `SELECT` (or `UNION`/`INTERSECT`/`EXCEPT`) statement is allowed. Writes, DDL, `COPY`, `SET`, locking, transaction control and dangerous functions (`pg_sleep`, `pg_read_file`, `dblink`, `set_config` and others) are rejected before reaching the database.
2. **Read-only sessions.** Every connection is opened read-only, each query has a statement timeout, and results are capped (**Queries** settings).
3. **Read-only database role.** Connect with a login that only has `SELECT`:

```sql
CREATE ROLE datalyst_ro LOGIN PASSWORD '<choose-a-password>';
GRANT CONNECT ON DATABASE your_db TO datalyst_ro;
GRANT USAGE ON SCHEMA public TO datalyst_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO datalyst_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO datalyst_ro;
ALTER ROLE datalyst_ro SET default_transaction_read_only = on;
```

If one layer has a gap, the next one still blocks the write.

## Development

Requirements: Python 3.12+, [uv](https://docs.astral.sh/uv/) and Docker.

```bash
uv sync --all-extras
```

Start the development Postgres (also the metastore) and MySQL used by the tests:

```bash
docker compose up -d
```

```bash
cp .env.example .env
```

Create the tables, then start the services, each in its own terminal:

```bash
uv run datalyst migrate
```

```bash
uv run datalyst api --port 8000
```

```bash
uv run datalyst worker
```

```bash
DATALYST_UI_API_URL=http://localhost:8000 uv run streamlit run ui/app.py
```

Run the tests. Integration tests create throwaway databases on the development Postgres and MySQL, including sample databases loaded from `tests/fixtures`, and drop them afterwards:

```bash
uv run pytest
```

Lint and format:

```bash
uv run ruff check . && uv run ruff format .
```

### Project layout

```
src/datalyst/
├── core/          models, interfaces and exceptions
├── connectors/    data source implementations and the connector registry
│   ├── postgres/  connection pool, introspection, profiling queries, query validator
│   ├── mysql/     connections, introspection, profiling queries, query validator
│   └── files/     uploaded CSV and Excel files: import to Parquet, DuckDB queries, locked-down validator
├── metastore/     Postgres metastore: migrations and repositories
├── sync/          background worker, job handlers, incremental sync
├── enrichment/    profiling, relationship inference, AI descriptions, embeddings
├── catalog/       schema snapshots, retrieval, SQL reference checks
├── llm/           chat and embedding model factories for all providers
├── agent/         LangGraph agent, prompts and response parsing
├── services/      analyst, questions, auth, settings and the per-source registry
├── api/           FastAPI app, routers, auth dependencies, error mapping
├── security/      password hashing, sign-in tokens, permissions
├── bootstrap.py   wires the pieces together
├── config.py      startup settings and the runtime option registry
└── cli.py         service and operator commands
ui/                Streamlit web UI (talks only to the API)
deploy/            production compose file, Caddy config, env template
docs/              deployment guide
tests/             unit and integration tests, sample databases in fixtures/
Dockerfile         one image for the API, worker and UI
docker-compose.yml the development Postgres and MySQL
```

New data sources plug in by implementing the `DataSource` interface and registering a URL scheme with `@register_connector`.

## Roadmap

- Evaluations: a golden dataset of questions with reference queries, scored on execution accuracy and safety
- Observability: OpenTelemetry tracing of every question, LLM call and query
- Conversation memory and follow-up questions
- Single sign-on (OIDC)
- More data sources: Parquet, MongoDB, Cassandra and others
- MCP server

## Contributing

Issues and pull requests are welcome. Please run the tests and ruff before opening a pull request.
