# Deploying Datalyst

This guide runs Datalyst for an organization: a web UI and API behind HTTPS, a background worker that keeps every database's catalog current, and a Postgres metastore.

## What runs

```
                        ┌─────────────── proxy (Caddy, HTTPS) ───────────────┐
 browser ──── 443 ────► │  /api/*, /health, /ready → api     everything → ui │
                        └─────────────┬─────────────────────────┬────────────┘
                                      │                         │
                               ┌──────▼──────┐           ┌──────▼──────┐
                               │ api (8000)  │◄──────────┤  ui (8501)  │
                               │ FastAPI     │   HTTP    │  Streamlit  │
                               └──┬───────┬──┘           └─────────────┘
                   catalog, users │       │ read-only SQL
                   history, jobs  │       │
                         ┌────────▼──┐  ┌─▼────────────────────┐
                         │ metastore │  │ your databases       │
                         │ Postgres  │  │ (read-only accounts) │
                         │ +pgvector │  └─▲────────────────────┘
                         └────────▲──┘    │ fingerprints, profiles, samples
                                  │       │
                               ┌──┴───────┴──┐
                               │   worker    │  sync, profile, infer joins,
                               └─────────────┘  describe, embed
```

| Service | Purpose | Scale |
|---|---|---|
| `migrate` | Applies metastore migrations, then exits | runs once per start |
| `api` | REST API used by the UI and other clients | `API_WORKERS` processes per container; more containers behind the proxy |
| `worker` | Background sync and enrichment jobs | `WORKER_REPLICAS`; workers share the job queue safely |
| `ui` | Web interface | one or more containers |
| `metastore` | Postgres 16 with pgvector and pg_trgm | one; or use a managed Postgres |
| `proxy` | Caddy: HTTPS, routing, security headers | one |

All application services use the same image, built from the repository's `Dockerfile`.

## Requirements

- Docker with Compose v2.
- 2 CPU cores and 4 GB RAM for a small team; the worker and API scale out horizontally.
- An LLM: an API key for OpenAI, Anthropic, Google Gemini or Groq, or a local server (Ollama, LM Studio, vLLM) reachable from the containers. You enter it in the web UI after installing.
- Network access from the Datalyst host to each database you connect.

## Install

From the repository root:

```bash
cp deploy/.env.example deploy/.env
```

`deploy/.env` holds only what is needed to start:

1. `METASTORE_PASSWORD`: any long random value.
2. `DATALYST_SECRET_KEY`: create one with `docker compose -f deploy/compose.yaml run --rm --no-deps migrate secret-key`.

Start everything:

```bash
docker compose -f deploy/compose.yaml up -d --build
```

Open `http://localhost` (or the `HTTP_PORT` you set):

1. **Create the admin account.** On a new installation the first page asks for it; it disappears once any user exists. Do this right after starting, before the address is shared.
2. **Settings → Model:** choose the provider and model, add the API key and click **Test connection**. Optionally set a cheaper model for background descriptions and an embedding model.
3. **Data sources:** add each database with a read-only login (below). Within a minute or two the worker syncs, profiles and links its tables; AI descriptions follow.
4. **People & access:** create accounts and groups, and grant groups the sources and tables they need.

## Configuration

Following the approach Apache Airflow takes, only two values come from the environment: `DATALYST_METASTORE_URL` (set by the compose file) and `DATALYST_SECRET_KEY`. Every other option is kept in the metastore and edited by admins on the **Settings** page. Changes reach all API and worker containers within a few seconds, without a restart. API keys are encrypted with `DATALYST_SECRET_KEY` and are never displayed again.

To manage an option in code or a secret manager instead, add `DATALYST__<SECTION>__<KEY>` to `deploy/.env` (it is passed to every Datalyst container), for example `DATALYST__LLM__API_KEY=...`. An environment variable wins over the stored value and locks the option in the UI. Precedence: environment variable, then Settings page, then default.

To see the effective configuration, use **Settings → Download config**, or:

```bash
docker compose -f deploy/compose.yaml exec api datalyst config list --include-sources
```

`datalyst config options` lists every option with its environment variable. Secrets are always shown as `< hidden >`.

## Production setup

### 1. Domain and HTTPS

Point a DNS record (for example `datalyst.example.com`) at the server, then set in `deploy/.env`:

```dotenv
SITE_ADDRESS=datalyst.example.com
HTTP_PORT=80
HTTPS_PORT=443
```

Caddy obtains and renews a Let's Encrypt certificate automatically (ports 80 and 443 must be reachable from the internet). For an internal-only host, use an address like `datalyst.corp.internal` with your own certificate: mount it into the proxy container and add a `tls /path/cert.pem /path/key.pem` line to `deploy/Caddyfile`.

### 2. Secrets

| Variable | What it protects | Notes |
|---|---|---|
| `DATALYST_SECRET_KEY` | Encrypts stored connection URLs and API keys, and signs sign-in tokens | Store it in your secret manager. If it is lost, every connection and API key must be entered again and everyone signs in again. Changing it signs everyone out. |
| `METASTORE_PASSWORD` | The bundled metastore | Only used by the bundled `metastore` service |
| LLM and embedding keys | Provider access | Entered on the Settings page and stored encrypted. Give Datalyst its own keys so usage can be tracked and revoked |

`deploy/.env` is ignored by git. Keep it readable only by the deploying user (`chmod 600 deploy/.env`).

### 3. Start

```bash
docker compose -f deploy/compose.yaml up -d --build
```

`migrate` applies migrations; `api`, `worker` and `ui` start after it succeeds. Check the state:

```bash
docker compose -f deploy/compose.yaml ps
```

### 4. Connect databases safely

Create a dedicated read-only login in each database you connect. Datalyst also blocks writes itself (query validation and read-only sessions), but a read-only account is the strongest guarantee:

```sql
CREATE ROLE datalyst_ro LOGIN PASSWORD '<long random password>';
GRANT CONNECT ON DATABASE sales TO datalyst_ro;
GRANT USAGE ON SCHEMA public TO datalyst_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO datalyst_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO datalyst_ro;
ALTER ROLE datalyst_ro SET default_transaction_read_only = on;
ALTER ROLE datalyst_ro SET statement_timeout = '60s';
```

Grant `SELECT` only on what people may query; tables the account cannot read are invisible to Datalyst. `pg_stats` statistics are used for profiling, so make sure `ANALYZE` runs (autovacuum does this by default).

When adding the source, choose what may be sent to the LLM to write descriptions:

| Policy | Sent to the LLM |
|---|---|
| `rows` | Names, types, statistics, common values and 5 sample rows per table (best descriptions) |
| `stats` | Everything above except rows |
| `names` | Table and column names, types and keys only |

Questions themselves always send the relevant tables' structure, descriptions and matching values to the LLM. Use a self-hosted model (Ollama, vLLM) if no data may leave your network.

### 5. People and access

- Admins create users under **People & access**. If nobody can sign in, run `docker compose -f deploy/compose.yaml exec api datalyst reset-password <email>`.
- Analysts see only data sources granted to one of their groups, optionally limited to table patterns such as `public.ord_*`. Hidden tables are removed before anything reaches the LLM.
- Changing a password, a role, or deactivating a user ends their sessions immediately.

## Using a managed Postgres as the metastore

Any Postgres 15+ with the `vector` and `pg_trgm` extensions works (Amazon RDS, Cloud SQL, Azure Database for PostgreSQL all provide both). Create a database and a user that owns it, have an administrator run `CREATE EXTENSION vector; CREATE EXTENSION pg_trgm;` if your provider requires it, then set in `deploy/.env`:

```dotenv
DATALYST_METASTORE_URL=postgresql://datalyst:<password>@your-host:5432/datalyst?sslmode=require
```

and remove the `metastore` service and the `migrate` service's `depends_on: metastore` from `deploy/compose.yaml` (or override them in a `compose.override.yaml`).

## Scaling

- **Questions per second:** raise `API_WORKERS` (processes per container) or run more `api` containers behind the proxy. Each process keeps an in-memory copy of each catalog and reloads it only when the catalog version changes, so the metastore load stays low.
- **Many or large databases:** raise `WORKER_REPLICAS`. Jobs are claimed with `FOR UPDATE SKIP LOCKED`, so workers never duplicate work; a worker that dies mid-job has its job picked up again after `DATALYST_WORKER_LEASE_SECONDS`.
- **Very large schemas:** retrieval sends the model only the relevant tables (`DATALYST_RETRIEVAL_*` settings), so prompt size does not grow with the number of tables.

## Upgrading

```bash
git pull
```

```bash
docker compose -f deploy/compose.yaml up -d --build
```

`migrate` applies any new migrations before the API and worker restart. Migrations run inside one transaction under an advisory lock, so a failed migration leaves the metastore unchanged and two starting stacks cannot apply the same migration twice. Back up first (below).

## Backups

Everything Datalyst knows (settings, encrypted connections and API keys, catalogs, descriptions written by people, glossary, examples, users, history) lives in the metastore. Back it up together with `DATALYST_SECRET_KEY`:

```bash
docker compose -f deploy/compose.yaml exec -T metastore pg_dump -U datalyst -Fc datalyst > datalyst-$(date +%F).dump
```

Restore into an empty metastore:

```bash
docker compose -f deploy/compose.yaml exec -T metastore pg_restore -U datalyst -d datalyst --clean --if-exists < datalyst-2026-01-31.dump
```

A metastore restored without the matching secret key cannot decrypt connection URLs or API keys; re-enter them under **Data sources** and **Settings**.

## Monitoring

| Check | How |
|---|---|
| API liveness | `GET /health` |
| API readiness (metastore reachable, migrations applied) | `GET /ready` (503 when not ready) |
| UI | `GET /_stcore/health` on the ui container |
| Worker | **Data sources** page shows running workers, queued and failed jobs, and recent schema changes |
| LLM spend | **People & access → LLM usage** |
| Logs | `docker compose -f deploy/compose.yaml logs -f api worker`; every API request is logged with status, duration and an `x-request-id` |

Containers have health checks; `docker compose ps` shows them.

## Security checklist

- [ ] HTTPS on a real domain (`SITE_ADDRESS`); plain HTTP only for local trials.
- [ ] `DATALYST_SECRET_KEY` stored in a secret manager; `deploy/.env` readable only by the deploying user.
- [ ] Every data source uses a read-only database account with `SELECT` only on what people may query.
- [ ] LLM data policy chosen per source; a self-hosted model if data must not leave the network.
- [ ] Admin account created right after the first start, before the address was shared.
- [ ] Analysts placed in groups with only the sources and tables they need.
- [ ] Metastore backups scheduled and a restore tested.
- [ ] The API and UI ports are not exposed directly; only the proxy is published.
- [ ] Rate limiting for sign-in and questions at your edge (a load balancer, WAF, or a Caddy build with a rate-limit module), if the service is reachable from outside your network.

## Running without Docker

Install with `uv sync --frozen --no-dev --all-extras`, export `DATALYST_METASTORE_URL` and `DATALYST_SECRET_KEY` (or put them in `.env` in the working directory), and run each process under your service manager (systemd, supervisord):

```bash
datalyst migrate
```

```bash
datalyst api --host 0.0.0.0 --port 8000 --workers 2
```

```bash
datalyst worker
```

```bash
DATALYST_UI_API_URL=http://127.0.0.1:8000 streamlit run ui/app.py --server.port 8501 --server.headless true
```

Put a reverse proxy with HTTPS in front, routing `/api/*`, `/health` and `/ready` to the API and everything else to the UI (WebSocket support is required for the UI).

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `migrate` exits with "password authentication failed" | The metastore volume was created earlier with a different `METASTORE_PASSWORD`. Use the original password, or remove the volume to start fresh (this deletes all Datalyst data). |
| Containers from the development `docker-compose.yml` get replaced | Both files share a project name. The deployment file uses `datalyst-deploy`; keep it distinct if you rename things. |
| **Data sources** says no worker is running | Start it: `docker compose -f deploy/compose.yaml up -d worker`, then check its logs. |
| A source shows `error` | The worker cannot reach or read the database; the message is shown on the page. Check the network path and the read-only account. |
| Descriptions or embeddings are slow | Free-tier LLM rate limits; jobs pause and resume automatically. Set a separate, cheaper model under **Settings → Descriptions**. |
| Questions fail with "No API key" | Add the key under **Settings → Model** and use **Test connection**. |
| A setting is greyed out | It is pinned by a `DATALYST__...` environment variable on the containers; change or remove the variable. |
| Answers ignore a new column | The worker checks for schema changes every 5 minutes by default; click **Check for schema changes** to check now. Queries that hit a missing table or column re-sync automatically. |
| Signed out after a while | Sign-ins last 8 hours by default (**Settings → Sign-in**). |
