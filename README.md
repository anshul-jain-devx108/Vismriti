# Vismriti

**The agent that helps your data forget.**

Vismriti is a Slack-native AI agent that automates GDPR Article 17 (right-to-erasure) requests. It reads DataHub through the MCP-style client to walk lineage from every PII-tagged source, generates a deterministic per-asset action plan (anonymize / delete / dbt-rerun / dashboard-invalidate / ML-retrain-flag / residual-review), and writes an audit trail entity back to DataHub so the next auditor inherits proof of erasure.

**Hackathon submission:** [Build with DataHub — The Agent Hackathon](https://datahub.devpost.com/)
**Track:** Agents That Do Real Work
**License:** Apache-2.0 (visible in [About](https://github.com/anshul-jain-devx108/Vismriti))
**Repository:** https://github.com/anshul-jain-devx108/Vismriti

---

## Demo

**3-minute video:** *[YouTube link — to be added on submission]*

**Live Slack transcript (screenshottable, verbatim):** [`demo_assets/slack_conversation_verbatim.md`](demo_assets/slack_conversation_verbatim.md)

**Live Streamlit report:** [`demo_assets/live_erasure_report.md`](demo_assets/live_erasure_report.md)

### Live Azure Container Apps deployment

- **DataHub UI:** https://datahub-frontend.happyhill-72aa3202.centralindia.azurecontainerapps.io
- **GMS API:** https://datahub-gms.happyhill-72aa3202.centralindia.azurecontainerapps.io *(v1.7.0, `supportsImpactAnalysis: true`)*

**Reproducible client-side proof (any laptop, no auth):**

```bash
python scripts/verify_live_datahub.py
```

Verified output:

```
[Vismriti live-mode connectivity test]
[1] GMS reachable
    version:                  v1.7.0
    supportsImpactAnalysis:   True
    patchCapable:             True
[2] Healthcare story is seeded live — fetching all 9 assets
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

**Full topology + 15-revision build log:** [`docs/azure_deploy.md`](docs/azure_deploy.md)

---

## The problem — evidence-backed

Every GDPR erasure request is a lineage question: *given one email, find every table, model, and dashboard that touched this person's data*. Privacy teams solve it manually today — Slack + spreadsheets — 6-8 hours per request. Regulators fine companies for the copies they miss:

| Regulator action | Amount | Why it matters |
|---|---|---|
| IDDesign A/S (Denmark, 2019) | **1.5M DKK** | *"Failure to delete personal data from an older system"* — the exact failure mode Vismriti prevents. |
| Google Sweden (2020) | **SEK 75M (~€7M)** | Right-to-be-forgotten violations — ceiling for ROI framing. |
| BKR Netherlands (2020) | **€840K** | Operational failure fine — not just data collection, but *process*. |

**The hidden layer:** static PII catalogs tag columns at ingest but derived tables *rename, hash, or join* PII into synthetic keys where those tags cannot follow. Analyst sandboxes have zero tags. Walking lineage *at request time* is the only way to catch every copy.

---

## What Vismriti actually does

Priya (DPO) types in Slack:

```
@vismriti erase priya.sharma@example.com
```

Vismriti replies with a Block-Kit-rendered plan:

| # | Asset URN | Action | Reason |
|---|---|---|---|
| 1 | `postgres,healthcare.raw.patients` | `anonymize_row` | Null 3 PII columns; keep row for FK integrity |
| 2 | `postgres,healthcare.raw.support_tickets` | `anonymize_row` | Null reporter email |
| 3 | `dbt,healthcare.staging.patients_clean` | `dbt_rerun` | Re-run after source anonymization propagates |
| 4 | `postgres,healthcare.raw.appointments` | `delete_row` | Delete derived subject rows |
| 5 | `postgres,healthcare.marts.churn_features` | `delete_row` | Delete derived (hash-keyed) subject rows |
| 6 | `postgres,healthcare.marts.patient_360` | `delete_row` | Delete aggregated row |
| 7 | `tableau,exec_dashboard.patient_health` | `dashboard_invalidate` | Refresh cache to prevent stale PII |
| 8 | `mlflow,churn_model_v3` | `ml_model_annotate` | Mark training-data erasure pending |

**Residual risk (auto-detected):**

| Asset | Reason |
|---|---|
| `analytics_sandbox.priya_analysis_2024` | Downstream of tagged source, but no `Ownership` aspect and no PII tag. **Static classification would miss this.** |

Priya says "approve all" → the 8 tools run (dry-run by default) → Vismriti writes an `erasureRequest` audit entity back to DataHub → replies with the URN in Slack. Full transcript: [`demo_assets/slack_conversation_verbatim.md`](demo_assets/slack_conversation_verbatim.md).

---

## Hackathon requirements — where each is satisfied

### 1. Working software application
The service runs today at three surfaces:

| Surface | Command | What it does |
|---|---|---|
| Slack bot | `python run.py` + Slack workspace install | Priya's natural interface — full LLM-driven multi-turn flow |
| Streamlit UI | `streamlit run src/vismriti/ui/app.py` | Zero-dependency review-and-approve UI (offline demo) |
| CLI | `erase run --email … --approve` | Scripting / CI |
| REST + MCP | `POST /agents/vismriti/runs` + `GET /mcp` | Programmatic API + other agents can call Vismriti over MCP |

**Verified end-to-end** — Slack transcript, Streamlit report, and pytest suite all in [`demo_assets/`](demo_assets/) and [`tests/`](tests/).

### 2. Uses DataHub as required by the track

**Track:** Agents That Do Real Work.

- **Reads DataHub via MCP-style client** ([`src/vismriti/core/datahub_client.py`](src/vismriti/core/datahub_client.py)) — three interchangeable modes:
  - `fixture` — offline demo (pre-canned JSON)
  - `live-rest` — direct HTTPS calls to GMS (matches what `mcp-server-datahub` proxies over MCP)
  - `mcp-stdio` — spawn `mcp-server-datahub` subprocess for full MCP-protocol-on-the-wire
- **Takes action** — 8 destructive/protective actions selected by deterministic planner rules, executed via `psycopg2` in dry-run by default
- **Writes results back to DataHub** — `erasureRequest` audit-trail entity + `erasure_completed` annotations on every affected asset

### 3. Public URL + repo

- **Public repo:** https://github.com/anshul-jain-devx108/Vismriti
- **Live GMS endpoint (Azure Container Apps):** https://datahub-gms.happyhill-72aa3202.centralindia.azurecontainerapps.io
- **License:** Apache-2.0, visible at the top of the About section on GitHub. Same license bundled at [`LICENSE`](LICENSE).

### 4. Sample outputs (`examples/` folder)

Every generated artifact type is checked into the repo so judges can eyeball quality without running the code:

- [`examples/sample_request.json`](examples/sample_request.json) — what a client submits
- [`examples/sample_plan.md`](examples/sample_plan.md) — human-readable plan with generated SQL
- [`examples/sample_audit_trail.json`](examples/sample_audit_trail.json) — machine-readable audit trail
- [`demo_assets/live_erasure_report.md`](demo_assets/live_erasure_report.md) — actual Streamlit-generated report from a live Azure run
- [`demo_assets/slack_conversation_verbatim.md`](demo_assets/slack_conversation_verbatim.md) — the Slack demo transcript verbatim

### 5. Demo video

3-minute YouTube link — added on submission. Recording follows [`docs/demo_script.md`](docs/demo_script.md) beat sheet.

---

## Architecture at a glance

```
┌────────────┐   ┌──────────────┐   ┌──────────────────┐
│ Priya (DPO)│──▶│ Slack / UI /│──▶│  Vismriti Agent  │
└────────────┘   │  CLI / REST │   │  (Agno + GPT-5.6)│
                 └──────────────┘   └────────┬─────────┘
                                             │  MCP-style tools
                                             ▼
                                    ┌──────────────────┐
                                    │ DataHub GMS      │
                                    │ (v1.7.0, Azure)  │
                                    │ read + write     │
                                    └────────┬─────────┘
                                             │
                        ┌────────────────────┼────────────────────┐
                        ▼                    ▼                    ▼
                ┌───────────────┐   ┌───────────────┐   ┌───────────────┐
                │ Postgres      │   │ Tableau       │   │ MLflow        │
                │ (demo target) │   │ (flagged)     │   │ (annotated)   │
                └───────────────┘   └───────────────┘   └───────────────┘

Write-back (rubric-critical):
    each affected asset  ──▶  annotation  erasure_completed
    all affected assets  ──▶  new entity  erasureRequest  (audit-trail root)
```

**Full diagram + component-by-component walkthrough:** [`docs/architecture.md`](docs/architecture.md) · [`../ARCHITECTURE.md`](../ARCHITECTURE.md) · Eraser diagram: https://app.eraser.io/workspace/wVy1fWdFMhAJG7E0OvMO

---

## Why Vismriti goes beyond DataHub's out-of-the-box features

DataHub already tags PII at the column level. The **hidden layer** — the class of assets DataHub's static tags cannot cover on their own — is:

- **Analyst sandboxes** with no owner, no tags, forked from `marts.*`
- **Hash-keyed feature tables** where `email → sha256(email)` breaks tag propagation
- **Backfill tables** created after the last catalog run

Vismriti's planner rule `_is_orphan()` treats any asset that has (no owner) AND (no PII tag) AND (is downstream of tagged sources) as **residual risk** — surfaced to Priya as a manual-review item rather than silently missed. This is the "beyond DataHub OOB" originality claim. It fires reliably on the seeded live cloud data ([`demo_assets/slack_conversation_verbatim.md`](demo_assets/slack_conversation_verbatim.md), turn 1).

---

## Quickstart

### Option A — offline fixture demo (fastest, no external services)

```bash
git clone https://github.com/anshul-jain-devx108/Vismriti
cd Vismriti
pip install -e .

# Plan only
erase plan --email priya.sharma@example.com --fixtures

# Plan + auto-approve + execute (dry-run) + write-back
erase run --email priya.sharma@example.com --fixtures --approve
```

### Option B — hit the live Azure DataHub deployment

```bash
git clone https://github.com/anshul-jain-devx108/Vismriti
cd Vismriti
pip install -e .

# 9 healthcare entities are already seeded on the live GMS at
# https://datahub-gms.happyhill-72aa3202.centralindia.azurecontainerapps.io
# — no local setup needed.
python scripts/verify_live_datahub.py     # proves 9/9 entities readable

# Full live-mode plan via the Streamlit UI:
streamlit run src/vismriti/ui/app.py
# In the sidebar, pick "Live REST (Azure)" and click "Plan erasure"
```

### Option C — Slack bot (full LLM flow)

```bash
cp .env.example .env
# Fill: AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY, AZURE_OPENAI_DEPLOYMENT,
#       SLACK_BOT_TOKEN, SLACK_SIGNING_SECRET
python run.py
# Slack manifest + install instructions: docs/slack_setup.md
```

### Option D — DataHub REST/MCP server surface

```bash
python run.py
# Now available:
#   http://localhost:7777/docs                       Swagger UI
#   http://localhost:7777/agents                     list registered agents
#   POST http://localhost:7777/agents/vismriti/runs  trigger the agent
#   http://localhost:7777/mcp                        MCP server — other agents can call Vismriti
```

---

## Where the actual DELETE runs (warehouse-coupling policy)

Vismriti's contract with the outside world is **only DataHub**:

- **Read** — Vismriti calls DataHub for PII column tags + lineage.
- **Write** — Vismriti writes annotations + an `erasureRequest` audit entity back to DataHub.

Vismriti **does not require direct warehouse credentials** for either of those.

The *actual* `UPDATE`/`DELETE` SQL has to run somewhere; Vismriti ships two paths:

**Option 1 (default, demo):** Vismriti runs the SQL itself via `psycopg2` against the seeded local Postgres. Fine for a fixture Postgres, **not how production should be set up**.

**Option 2 (production):** Leave `PG_*` env vars unset. Vismriti still plans every action, gates each `execute_erasure_action` behind an HITL confirmation (post-hackathon: full Slack Block Kit approval flow via Agno's `/approvals` REST), emits the plan as JSON, and writes the audit trail. Your own dbt / Airflow / governance workflow — the one that already has warehouse credentials — consumes the plan and runs the SQL. Vismriti stays out of the sensitive-credential business.

**Summary:** the `PG_*` variables are demo plumbing. Production integrations should plug their own executor and leave those env vars empty.

---

## Repository layout

```
Vismriti/
├── LICENSE                       # Apache 2.0
├── README.md                     # this file
├── pyproject.toml
├── docker-compose.yml
├── run.py                        # AgentOS launcher (uvicorn wrapper)
│
├── src/vismriti/                 # Python package
│   ├── main.py                   # AgentOS FastAPI app
│   ├── cli.py                    # `erase` CLI (Typer)
│   │
│   ├── core/                     # Domain models + clients
│   │   ├── models.py             #   pydantic domain models
│   │   └── datahub_client.py     #   3-mode DataHub client (fixture / live-rest / mcp-stdio)
│   │
│   ├── services/                 # Business logic
│   │   ├── orchestrator.py       #   ErasureOrchestrator (linear pipeline)
│   │   ├── planner.py            #   per-asset deterministic rules
│   │   ├── lineage.py            #   BFS forward traversal
│   │   ├── subject_resolver.py   #   email → id, email_hash
│   │   ├── executor.py           #   runs approved SQL (psycopg2, dry-run default)
│   │   ├── writeback.py          #   DataHub annotations + erasureRequest entity
│   │   ├── report.py             #   Markdown + JSON emitters
│   │   ├── sql_templates/        #   Jinja2 SQL templates (anonymize / delete / anonymize_fk)
│   │   └── fixtures/             #   9-asset offline demo JSON
│   │
│   ├── utils/                    # Generic helpers
│   │   └── config.py             #   env-driven pydantic Settings
│   │
│   ├── agent/                    # Agno LLM agent
│   │   ├── agent.py              #   build_agent(db) — factory
│   │   ├── prompt.py             #   system prompt
│   │   └── tools.py              #   4 @tool functions (plan / execute / finalize / list PII)
│   │
│   └── ui/
│       └── app.py                #   Streamlit review-and-approve UI
│
├── skills/erasure_skill/         # DataHub Skill package (OSS bonus contribution)
├── examples/                     # sample_request, sample_plan, sample_audit_trail (JSON + MD)
├── demo_assets/                  # live_erasure_report.md + slack_conversation_verbatim.md
├── scripts/
│   ├── verify_live_datahub.py    # client-side connectivity proof against Azure
│   ├── seed_healthcare_entities.sql   # seeds 9 healthcare entities into live GMS
│   └── init_healthcare.sql       # Postgres seed for local demo
├── tests/                        # pytest suite (8 tests, all passing)
└── docs/
    ├── architecture.md
    ├── azure_deploy.md           # Azure Container Apps topology + 15-revision build log
    ├── slack_setup.md            # Slack app manifest + ngrok + token flow
    ├── demo_script.md            # 3-min video beat sheet + verbatim voice-over
    ├── devpost_submission.md     # paste-ready form text
    └── submission_checklist.md
```

---

## Judging rubric — where each criterion lands

| Criterion | Evidence |
|---|---|
| **Use of DataHub** | `datahub_client.py` reads GMS (search, lineage, get_entity) AND writes back (annotations + `erasureRequest` entity). Three interchangeable client modes: fixture, live-REST, MCP-stdio. **Bidirectional.** |
| **Technical Execution** | End-to-end verified: Slack transcript ([`demo_assets/`](demo_assets/)), Streamlit report, `verify_live_datahub.py` (9/9 assets), `pytest` (8/8 tests), live Azure Container Apps deployment (10 resources), Slack app on real workspace. |
| **Originality** | Residual-risk detection: `_is_orphan()` planner rule surfaces assets DataHub's static tags cannot cover (no owner + no PII tag + downstream). Vismriti also *exposes itself as an MCP server* via AgentOS — other agents can call Vismriti's erasure tools over MCP. |
| **Real-World Usefulness** | Priya persona · 6-8 hours → 5 minutes · fine-cost delta of 500K–7M € avoided per correct erasure. Slack is where DPOs already work. |
| **Submission Quality** | This README + [`docs/`](docs/) (5 docs) + [`demo_assets/`](demo_assets/) + [`examples/`](examples/) + Eraser diagram + Apache-2.0 visible in About. |
| **Bonus — OSS contribution** | Reusable [erasure Skill](skills/erasure_skill/) packaged for `datahub-project/datahub-skills` catalog. |

---

## Evidence sources (real, cited)

- [GDPR fines and notices — Wikipedia](https://en.wikipedia.org/wiki/GDPR_fines_and_notices) — IDDesign, Google Sweden, Google Belgium, BKR
- [Monte Carlo — State of Data Quality 2023](https://montecarlo.ai/state-of-data-quality/) — labor-cost baseline
- [LinkedIn Engineering — DataHub origin (Aug 2019)](https://www.linkedin.com/blog/engineering/archive/data-hub) — justifies why the lineage-graph approach exists at all

---

## Development

```bash
pip install -e ".[dev]"
pytest -v        # 8 tests: 7 planner + 1 e2e fixture run
ruff check src/
```

All 8 tests pass in fixture mode with zero external dependencies. CI-ready.

---

## License

Apache-2.0 — see [LICENSE](LICENSE). Built with Agno (Apache-2.0).

---

## AI-generation disclosure

Portions of this repository (README text, architecture prose, docstrings, and select scaffolding code) were drafted with the assistance of a large language model (Claude / Anthropic) during the hackathon window. The engineering decisions, deterministic planner rules, deployment architecture, Azure infrastructure, live GMS seeding, MCP-style client design, and every commit's final review were performed by the human author. All code has been tested; the pytest suite is green.
