# Vismriti

**The agent that helps your data forget.**

Vismriti automates GDPR Article 17 (right to erasure). Give it one email
address and it walks DataHub lineage at request time, produces a per-asset
erasure plan a human approves action by action, executes only what was
approved, and writes an audit trail so the next auditor inherits proof instead
of a Slack thread.

**Hackathon submission:** [Build with DataHub, The Agent Hackathon](https://datahub.devpost.com/)
**Track:** Agents That Do Real Work
**License:** Apache-2.0
**Setup:** [`SETUP.md`](SETUP.md) takes you from a clean machine to a working plan.

---

## Demo



Fastest way to see it work, with no credentials, no database, and no LLM key:

```bash
git clone https://github.com/anshul-jain-devx108/Vismriti && cd Vismriti
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e ".[dev]"
./.venv/bin/erase plan --email priya.sharma@example.com --fixtures
```

### Live Azure Container Apps deployment

- **GMS API:** https://datahub-gms.happyhill-72aa3202.centralindia.azurecontainerapps.io *(v1.7.0, `supportsImpactAnalysis: true`)*

Reproducible from any laptop, no auth:

```bash
./.venv/bin/python scripts/verify_live_datahub.py
```

```
[Vismriti live-mode connectivity test]
[1] GMS reachable
    version:                  v1.7.0
    supportsImpactAnalysis:   True
    patchCapable:             True
[2] Healthcare story is seeded live, fetching all 9 assets
    healthcare.raw.patients                                     5 aspects  PII source
    healthcare.raw.support_tickets                              4 aspects  PII source
    healthcare.raw.appointments                                 5 aspects  derived
    healthcare.staging.patients_clean                           5 aspects  dbt derived
    healthcare.marts.patient_360                                5 aspects  aggregated
    healthcare.marts.churn_features                             5 aspects  ML features
    urn:li:dashboard:(tableau                                   4 aspects  BI dashboard
    churn_model_v3                                              4 aspects  ML model
    healthcare.analytics_sandbox.priya_analysis_2024            4 aspects  RESIDUAL (no owner)

[OK] All 9/9 healthcare entities readable from live Azure DataHub.
```

That deployment has real constraints, and they shaped the design. See
[what the deployment can and cannot do](#what-the-reference-deployment-can-and-cannot-do)
before reading the client code, and [`docs/azure_deploy.md`](docs/azure_deploy.md)
for the measurements.

---

## The problem

Every erasure request is a lineage question: *given one email, find every
table, model, and dashboard that touched this person's data*. Privacy teams
answer it by hand today, with Slack and a spreadsheet. Regulators fine
companies for the copies they miss.

| Regulator action | Amount | Why it matters |
|---|---|---|
| IDDesign A/S (Denmark, 2019) | **1.5M DKK** | *"Failure to delete personal data from an older system"*, the exact failure mode Vismriti prevents. |
| Google Sweden (2020) | **SEK 75M (~€7M)** | Right-to-be-forgotten violations. |
| BKR Netherlands (2020) | **€840K** | An operational process failure, not a collection failure. |

The data that gets missed is not the data anyone forgot about. It is the data
no catalog could see. Static PII catalogs tag columns at ingest, but derived
tables rename, hash, and join that PII into synthetic keys where tags cannot
follow, and analyst sandboxes carry no tags at all. Walking lineage at request
time is the only way to catch every copy.

---

## What Vismriti does

Priya, a data protection officer, types in Slack:

```
@vismriti erase priya.sharma@example.com
```

Vismriti replies with a plan, one row per affected asset:

| # | Asset | Action | Reason |
|---|---|---|---|
| 1 | `postgres,healthcare.raw.patients` | `anonymize_row` | Null 3 PII columns, keep the row so foreign keys survive |
| 2 | `postgres,healthcare.raw.support_tickets` | `anonymize_row` | Null the reporter email |
| 3 | `dbt,healthcare.staging.patients_clean` | `dbt_rerun` | Deleting rows would be undone by the next build. Fix the source, rebuild. |
| 4 | `postgres,healthcare.raw.appointments` | `delete_row` | Derived subject rows |
| 5 | `postgres,healthcare.marts.churn_features` | `delete_row` | Derived, hash-keyed subject rows |
| 6 | `postgres,healthcare.marts.patient_360` | `delete_row` | Aggregated row |
| 7 | `tableau,exec_dashboard.patient_health` | `dashboard_invalidate` | Stale extracts keep rendering PII after the source is clean |
| 8 | `mlflow,churn_model_v3` | `ml_model_annotate` | Flag training-data erasure for retrain per policy |

**Residual risk, auto-detected:**

| Asset | Reason |
|---|---|
| `analytics_sandbox.priya_analysis_2024` | Downstream of a tagged source, but no `Ownership` aspect and no PII tag. **Static classification would miss this entirely.** |

That last row is the argument for the whole project, and the action it takes is
to *refuse*. No owner means nobody can confirm what depends on the table.
Guessing breaks a downstream report, and a wrong delete is not reversible, so
Vismriti escalates instead of generating SQL.

Priya then approves or rejects each destructive action individually. A plan
with eight actions can end with six approved and two rejected. Only approved
actions run, dry-run by default, and the audit trail records what actually
happened, including anything that failed and why.

---

## Design: where the model is not allowed to operate

The central constraint is that a language model is good at deciding *which*
question to ask, and is the wrong tool for deciding *what to delete*. So the
system is split, with a hard boundary.

```
                 ┌──────────── LLM layer ─────────────┐
   Slack ───┐    │  Agno Agent, 4 tools               │
   HTTP  ───┼──▶ │  chooses tools, explains results   │
   MCP   ───┘    │  never composes SQL                │
                 └────────────────┬───────────────────┘
                                  │ calls into, cannot bypass
                 ┌────────────────▼───────────────────┐
   CLI   ───┐    │   Deterministic core, zero LLM     │
   UI    ───┼──▶ │   ErasureOrchestrator              │
   pytest───┘    │   resolve → discover → traverse →  │
                 │   plan → execute → write → report  │
                 └────────────────┬───────────────────┘
                                  │
                 ┌────────────────▼───────────────────┐
                 │  DataHub client                    │
                 │  fixture | live-rest | mcp-stdio   │
                 └────────────────────────────────────┘
```

Three properties fall out of `ErasureOrchestrator` containing no LLM calls and
no agent-framework import:

- **Auditable.** A regulator asking why a table was deleted gets a rule with a
  line number, not a model transcript.
- **Testable.** The full pipeline runs offline against fixtures in under a
  second, with no network and no key.
- **Bounded blast radius.** The execute tool ignores any SQL in its arguments
  and looks the statement up from the stored plan by request id and asset URN.
  The model cannot fabricate a destructive statement; it can only ask for
  execution of a plan entry the planner emitted and a human approved.

Destructive tools are gated on explicit confirmation at the framework level, so
the run halts and waits rather than relying on the model to behave.

Full walkthrough: [`docs/architecture.md`](docs/architecture.md).

---

## Why this goes beyond DataHub out of the box

DataHub tags PII at the column level and knows how assets connect. The layer it
cannot cover on its own:

- **Analyst sandboxes** forked from `marts.*` with no owner and no tags.
- **Hash-keyed feature tables** where `email → sha256(email)` breaks tag
  propagation.
- **Backfill tables** created after the last catalog run.

The planner's residual rule treats any asset with no owner, no PII tag, and a
position downstream of a tagged source as residual risk, and surfaces it as a
manual-review item rather than missing it silently. It fires on the seeded live
cloud data, on the sandbox table nobody registered.

Vismriti also **exposes itself as an MCP server** through AgentOS, so other
agents can call its erasure tools.

---

## Interfaces

The same deterministic core is reachable four ways:

| Surface | Command | Needs an LLM key |
|---|---|---|
| CLI | `erase plan --email … --fixtures` | No |
| Streamlit UI | `streamlit run src/vismriti/ui/app.py` | No |
| HTTP API + MCP server | `python run.py` | Yes |
| Slack bot | `python run.py` with `SLACK_ENABLED=true` | Yes |

Setup for each, including the Slack app manifest and scopes, is in
[`SETUP.md`](SETUP.md) and [`docs/slack_setup.md`](docs/slack_setup.md).

---

## What the reference deployment can and cannot do

The Azure GMS runs v1.7.0 without Kafka and with empty search and graph
indexes. Measured with `curl`, reproducible by anyone:

| Operation | Result |
|---|---|
| `GET /entities/{urn}` | Works. All 9 seeded entities readable. |
| `POST /entities?action=search` | HTTP 200, `numEntities: 0`. The search index is empty, so tag-based discovery over the API returns nothing. |
| `GET /relationships` | HTTP 200, `total: 0`. The graph index is empty, so lineage cannot be traversed over the API. |
| `POST /aspects?action=ingestProposal` | Blocks, then times out. Every proposal is published to a broker that is not deployed, so writes cannot land. |

**Live mode plans 7 of the 9 assets that fixture mode plans, and the two
missing ones are ingestion gaps rather than client bugs.** Both were traced by
dumping the entities' actual aspects:

| Asset | Why it is missing live |
|---|---|
| `mlflow,churn_model_v3` | Its `mlModelProperties` carries no `trainingData` or `trainingJobs`. The lineage edge exists only as English prose inside the description string. The client parses those fields and will pick the model up the moment the aspect is ingested. |
| `postgres,healthcare.raw.support_tickets` | The live entity has no `schemaMetadata` aspect at all, so it carries no column-level PII tags and cannot be discovered as a PII source. |

Both need those two entities re-ingested, which requires the write path, which
requires Kafka. Vismriti logs a warning naming any seeded source that yields no
PII columns, so the gap is visible at runtime rather than silent.

Worth stating explicitly, because it was a deliberate choice: the model's
description literally reads *"Trained on marts.churn_features"*, and Vismriti
does **not** regex that string to synthesise a lineage edge. Scraping prose to
decide what a right-to-erasure run touches is the kind of thing that demos
green and misfires in production. It flags rather than fabricates.

Two further consequences, both visible in the code rather than hidden behind a
happy path:

**Discovery and traversal are seeded.** The live client is given an explicit
URN set through `DATAHUB_SEED_URNS` and `DATAHUB_PII_SOURCE_URNS` and
reconstructs lineage from each entity's own `UpstreamLineage`, `DashboardInfo`,
and `MLModelProperties` aspects, which are readable. Against an indexed
DataHub, the search and relationships endpoints are used normally.

**Write-back reports failure honestly.** The ingest call is attempted with a
short timeout and its real outcome is recorded on the execution report. It does
not return a fabricated success. In this configuration the durable audit record
is the JSON and Markdown pair under `runs/`.

A compliance tool that fakes a successful deletion record is worse than no
tool, so the failure is surfaced rather than smoothed over. Against a DataHub
with Kafka and populated indexes, the same code path writes the annotations and
the `erasureRequest` entity. Details and a deployment recipe:
[`docs/azure_deploy.md`](docs/azure_deploy.md).

---

## Where the actual DELETE runs

Vismriti's contract with the outside world is DataHub: it reads PII tags and
lineage, and it writes annotations plus an `erasureRequest` audit entity. It
does not need warehouse credentials for either.

The `UPDATE` and `DELETE` statements have to run somewhere, and there are two
paths:

**Demo.** Vismriti runs the SQL itself through `psycopg2` against the seeded
local Postgres from `docker-compose.yml`. Fine for a fixture warehouse, not how
production should be set up.

**Production.** Leave the `PG_*` variables unset. Vismriti still plans every
action, gates each destructive one behind an approval, emits the plan as JSON,
and writes the audit trail. Your own dbt job or Airflow DAG, the one that
already holds production credentials, consumes `actions[].sql` and
`actions[].command` and runs them through your existing governance channel.
Vismriti stays out of the credential business.

---

## Repository layout

```
Vismriti/
├── SETUP.md                      # clone to working plan
├── README.md
├── LICENSE                       # Apache-2.0
├── pyproject.toml
├── docker-compose.yml            # demo Postgres warehouse
├── run.py                        # AgentOS launcher
│
├── src/vismriti/
│   ├── main.py                   # AgentOS FastAPI app
│   ├── cli.py                    # `erase` CLI (Typer)
│   │
│   ├── core/
│   │   ├── models.py             # pydantic domain models
│   │   └── datahub_client.py     # 3-mode client: fixture / live-rest / mcp-stdio
│   │
│   ├── services/                 # deterministic core, no LLM
│   │   ├── orchestrator.py       # the linear pipeline
│   │   ├── planner.py            # per-asset rules + SQL rendering
│   │   ├── lineage.py            # forward traversal
│   │   ├── subject_resolver.py   # email -> internal ids, email hash
│   │   ├── executor.py           # runs approved SQL, dry-run default
│   │   ├── writeback.py          # DataHub annotations + erasureRequest
│   │   ├── report.py             # Markdown + JSON audit artifacts
│   │   ├── sql_templates/        # Jinja SQL, the only source of destructive statements
│   │   └── fixtures/             # 9-asset offline story
│   │
│   ├── agent/                    # Agno layer
│   │   ├── agent.py              # build_agent(db)
│   │   ├── prompt.py             # system instructions
│   │   └── tools.py              # 4 tools, 2 of them confirmation-gated
│   │
│   ├── utils/config.py           # env-driven settings
│   └── ui/app.py                 # Streamlit review UI
│
├── examples/                     # sample request, plan, and audit trail
├── scripts/
│   ├── verify_live_datahub.py    # live connectivity proof
│   └── init_healthcare.sql       # Postgres seed for the demo warehouse
├── tests/                        # pytest, runs offline
└── docs/
    ├── architecture.md
    ├── azure_deploy.md           # topology + measured endpoint behaviour
    ├── slack_setup.md            # app manifest, scopes, endpoints
    ├── demo_script.md            # 3-minute beat sheet
    ├── devpost_submission.md
    └── submission_checklist.md
```

---

## Judging rubric

| Criterion | Evidence |
|---|---|
| **Use of DataHub** | [`datahub_client.py`](src/vismriti/core/datahub_client.py) reads GMS and attempts write-back, across three interchangeable modes: fixture, live REST, and `mcp-server-datahub` over stdio. Reads are verified live; the write path is implemented and reports truthfully that it cannot land on a Kafka-less deployment. |
| **Technical Execution** | Deterministic core with an offline test suite, a confirmation gate that halts the run, per-action approval that survives restart, identifier validation before any SQL is rendered, and live metadata read from Azure Container Apps. Reproduce with `scripts/verify_live_datahub.py` and `pytest`. |
| **Originality** | Residual-risk detection: the planner surfaces assets that static tags structurally cannot cover, no owner plus no PII tag plus downstream of a tagged source. Vismriti also exposes itself as an MCP server, so other agents can call its erasure tools. |
| **Real-World Usefulness** | Erasure is a lineage question and lineage is what DataHub already knows. Approval happens in Slack, where DPOs already work, and the output is an audit artifact a regulator can read. |
| **Submission Quality** | This README, [`SETUP.md`](SETUP.md), six documents under [`docs/`](docs/), sample artifacts under [`examples/`](examples/), and an honest account of what the reference deployment cannot do. |
---

## Development

```bash
uv pip install --python .venv/bin/python -e ".[dev]"
./.venv/bin/python -m pytest tests/ -q
./.venv/bin/python -m ruff check src/
```

The suite runs in fixture mode with zero external dependencies.

---

## Evidence sources

- [GDPR fines and notices, Wikipedia](https://en.wikipedia.org/wiki/GDPR_fines_and_notices) for IDDesign, Google Sweden, and BKR
- [Monte Carlo, State of Data Quality 2023](https://montecarlo.ai/state-of-data-quality/) for the labour-cost baseline
- [LinkedIn Engineering, DataHub origin](https://www.linkedin.com/blog/engineering/archive/data-hub) for why the lineage-graph approach exists

---

## License

Apache-2.0, see [LICENSE](LICENSE). Built with Agno (Apache-2.0).
