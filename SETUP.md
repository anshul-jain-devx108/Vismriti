# Setup

Everything needed to get Vismriti running, from a clean machine to a working
erasure plan. Four interfaces ship in this repo and each one is covered below:
the CLI, the AgentOS HTTP API, the Streamlit report UI, and the Slack bot.

If you only want to see it work, do steps 1 to 4 and then run the CLI in
fixture mode. That path needs no cloud credentials, no database, and no LLM
key.

---

## 1. Prerequisites

| Requirement | Version | Why |
|---|---|---|
| Python | 3.10 or newer | The package declares `requires-python = ">=3.10"`. Verified on 3.12.13. |
| `uv` or `pip` | any recent | Dependency install. `uv` is faster and can fetch Python for you. |
| Docker | optional | Only for the local Postgres warehouse used by non-dry-run execution. |
| Azure OpenAI or OpenAI key | optional | Only for the LLM agent (API, Slack). The CLI and Streamlit UI do not need one. |

Check what you have:

```bash
python3 --version
uv --version     # optional but recommended
docker --version # optional
```

macOS ships Python 3.9, which is too old. `uv` will download a suitable
interpreter for you, so you do not need to touch the system Python.

---

## 2. Get the code

```bash
git clone https://github.com/anshul-jain-devx108/Vismriti.git
cd Vismriti
```

---

## 3. Create the environment and install

Using `uv` (recommended, and what the maintainers use):

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e ".[dev]"
```

Using plain `pip`:

```bash
python3.12 -m venv .venv
./.venv/bin/pip install -e ".[dev]"
```

The install takes a few minutes on a cold cache. It pulls Agno, FastAPI,
Streamlit, the MCP client, and psycopg2.

Confirm it worked:

```bash
./.venv/bin/python -m pytest tests/ -q
./.venv/bin/erase --help
```

The test suite runs entirely offline against the JSON fixtures.

---

## 4. Configure

```bash
cp .env.example .env
```

`.env` is gitignored. Never commit it; it holds live tokens.

Nothing in `.env` is required for fixture mode. Fill in only what you need:

### LLM (needed for the API and Slack, not for the CLI)

Two supported shapes. Azure takes precedence when its endpoint is set.

```bash
# Option A: Azure OpenAI (or any OpenAI-compatible endpoint: LiteLLM, vLLM)
AZURE_OPENAI_ENDPOINT=https://<resource>.openai.azure.com
AZURE_OPENAI_API_KEY=<key>
AZURE_OPENAI_DEPLOYMENT=<deployment-name>

# Option B: OpenAI directly
OPENAI_API_KEY=sk-...
ERASURE_AGENT_MODEL=openai:gpt-5.6
```

`ERASURE_AGENT_MODEL` accepts any Agno model string, so
`anthropic:claude-sonnet-5` and the other 30-odd providers Agno supports work
without code changes.

### DataHub

```bash
DATAHUB_GMS_URL=https://<your-gms-host>
DATAHUB_GMS_TOKEN=          # leave empty if your GMS has auth disabled
```

There is no default GMS URL. Live mode fails loudly if this is unset, rather
than silently pointing at somebody else's metadata server.

### Mode switches

```bash
VISMRITI_USE_FIXTURES=true   # true = offline canned metadata; false = live DataHub
ERASURE_AGENT_DRY_RUN=true   # true = generate SQL but never commit it
```

Both default to the safe value. Keep `ERASURE_AGENT_DRY_RUN=true` until you
have read the SQL a plan produces.

### Warehouse (only for non-dry-run execution)

```bash
PG_HOST=localhost
PG_PORT=5432
PG_DATABASE=healthcare
PG_USER=datahub
PG_PASSWORD=<your-password>
```

Leave these unset in production. See "Production execution model" below.

---

## 5. Run it

### 5a. CLI, fixture mode (no credentials needed)

```bash
./.venv/bin/erase plan --email priya.sharma@example.com --fixtures
```

Prints the per-asset action table: nine assets, one of them flagged as
residual risk. Nothing is executed.

To run the full pipeline including report generation, still without touching
a database:

```bash
./.venv/bin/erase run --email priya.sharma@example.com --fixtures --approve --dry-run
```

Artifacts land in `./runs/` as a Markdown report and a JSON audit trail.

### 5b. CLI, live DataHub

```bash
./.venv/bin/erase plan --email priya.sharma@example.com
```

Drops `--fixtures`, so the client reads from `DATAHUB_GMS_URL`. Read
"Known limits of the reference deployment" before interpreting the output.

### 5c. AgentOS HTTP API

```bash
./.venv/bin/python run.py --no-reload
```

Serves on `AGENTOS_HOST:AGENTOS_PORT` (defaults `127.0.0.1:8000`; the
reference `.env` uses 7777). This is the production entrypoint and it is
plain uvicorn underneath:

```bash
./.venv/bin/uvicorn vismriti.main:app --host 0.0.0.0 --port 8000
```

It exposes the agent run endpoint, the approvals surface that gates every
destructive tool call, and an MCP server endpoint so other agents can call
Vismriti as a tool. Interactive docs are at `/docs`.

### 5d. Streamlit report UI

```bash
./.venv/bin/streamlit run src/vismriti/ui/app.py
```

Opens on port 8501. The UI drives the deterministic orchestrator directly, so
it renders plans and reports without an LLM key.

### 5e. Slack

```bash
SLACK_ENABLED=true
SLACK_BOT_TOKEN=xoxb-...
SLACK_SIGNING_SECRET=...
```

Both secrets must be non-empty or the service refuses to boot rather than
running a half-wired bot. With Slack on, `run.py` mounts the event and
interaction endpoints, and destructive actions render as per-action approve
and reject buttons. Point your Slack app's Request URL at
`https://<your-host>/slack/events`.

---

## 6. Optional: local Postgres warehouse

Only needed if you want to execute generated SQL for real rather than
dry-running it.

```bash
docker compose up -d postgres
```

`scripts/init_healthcare.sql` seeds the seven-table healthcare topology the
metadata describes, including the demo subject (`patient_id` 48291,
`priya.sharma@example.com`). Verify:

```bash
docker exec -it erasure-agent-pg psql -U datahub -d healthcare \
  -c "SELECT patient_id, name, email FROM raw.patients;"
```

Then, once you have read the plan and are willing to mutate rows:

```bash
./.venv/bin/erase run --email priya.sharma@example.com --fixtures --approve --dry-run=false
```

---

## 7. Verify a live DataHub connection

```bash
./.venv/bin/python scripts/verify_live_datahub.py
```

Reports GMS version, whether impact analysis is supported, and which of the
seeded healthcare entities are readable.

---

## Production execution model

Vismriti's own job is to read metadata and produce an auditable plan. It does
not need warehouse credentials to do that, and most deployments should not
give it any.

The recommended shape:

1. Leave `PG_*` unset.
2. Let Vismriti plan, gate each action behind an approval, and write the audit
   trail.
3. Consume the emitted plan (`actions[].sql` and `actions[].command`) from your
   existing governance channel: a dbt job, an Airflow DAG, whatever already
   holds production credentials.

That keeps the agent out of the credential business while still giving you a
signed-off record of what was erased and why.

---

## Known limits of the reference deployment

The Azure Container Apps DataHub instance referenced in the README runs GMS
v1.7.0 without Kafka and without a populated search or graph index. Concretely,
as measured against it:

| Operation | Result |
|---|---|
| `GET /entities/{urn}` | Works. All nine seeded healthcare entities are readable. |
| `POST /entities?action=search` | Returns HTTP 200 with `numEntities: 0`. The search index is empty, so tag-based discovery over the API is not possible. |
| `GET /relationships` | Returns HTTP 200 with `total: 0`. The graph index is empty, so lineage cannot be traversed over the API. |
| `POST /aspects?action=ingestProposal` | Blocks and times out. Kafka is not deployed, so writes back to DataHub cannot land. |

Two consequences you should know about before reading the code:

- Because discovery and lineage traversal return nothing, the live client is
  seeded with an explicit URN list and reconstructs lineage from each entity's
  own `UpstreamLineage`, `DashboardInfo`, and `MLModelProperties` aspects.
  Override that list with `DATAHUB_SEED_URNS` and `DATAHUB_PII_SOURCE_URNS`.
- Because writes cannot land, the write-back records an explicit failure in the
  execution report instead of reporting a success that did not happen. The
  local JSON and Markdown audit artifacts under `./runs/` are the durable
  record in that configuration.

Against a DataHub deployment with Kafka and populated indexes, none of these
constraints apply.

---

## Troubleshooting

**`command not found: timeout`** on macOS. That is a GNU coreutils tool; it is
not used by anything in this repo, only by some copy-pasted shell snippets.

**`ModuleNotFoundError: agno`.** The virtualenv is not active or you installed
into a different interpreter. Call binaries by their full path
(`./.venv/bin/erase`) rather than relying on `PATH`.

**Plan comes back with fewer assets than expected in live mode.** The seed URN
list did not include them, or their lineage lives in an aspect the client does
not read. Check with `scripts/verify_live_datahub.py` first to confirm the
entities are readable at all.

**LLM calls fail with a 400 about `max_tokens`.** Newer Azure deployments
require `max_completion_tokens`. The Azure path already drops `max_tokens` for
this reason; if you switched to the plain OpenAI path, set a model that accepts
the older field.

**Service refuses to start with a Slack error.** `SLACK_ENABLED=true` requires
both `SLACK_BOT_TOKEN` and `SLACK_SIGNING_SECRET`. Fill them or set
`SLACK_ENABLED=false`.

**Everything works but the data looks fake.** `VISMRITI_USE_FIXTURES` is still
`true`. Fixture mode logs a warning on startup for exactly this reason.
