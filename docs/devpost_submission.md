# Devpost submission

Paste-ready text for <https://datahub.devpost.com/>. Fields marked FILL need
something only you can supply.

Before pasting, run through [`submission_checklist.md`](submission_checklist.md).
Every number below should match what the code actually prints on the day.

---

## Project name

Vismriti

## Tagline

The agent that helps your data forget. GDPR Article 17 erasure, executed across
DataHub lineage.

## Track

Agents That Do Real Work

---

## Elevator pitch

A right-to-erasure request arrives as one email address. The obligation is to
find every copy of that person across the warehouse, and the fines are for the
copies you miss. Vismriti takes the email, walks DataHub lineage at request
time, and produces a per-asset erasure plan a data protection officer approves
action by action. It writes the audit trail back so the next auditor inherits
proof instead of a Slack thread.

---

## Inspiration

Privacy teams answer erasure requests with a spreadsheet and a lot of asking
around. The reason is structural: PII tags are applied at ingest, and derived
tables rename, hash, and join that PII into synthetic keys the tags cannot
follow. An analyst forks a mart into a sandbox and the copy carries no tags and
no owner at all.

Regulators have fined for exactly this failure mode. IDDesign was fined 1.5M DKK
in Denmark in 2019 for failing to delete personal data from an older system. The
data that gets missed is not the data anyone forgot about; it is the data no
catalog could see.

That makes erasure a lineage question, and lineage is what DataHub already
knows. The gap was that nobody was walking it at request time.

## What it does

Given a subject's email, Vismriti:

1. Finds every PII-tagged column DataHub knows about.
2. Resolves the email to the internal identifiers the warehouse actually keys
   on, including the SHA-256 hash that hash-keyed derived tables carry.
3. Walks forward lineage from every PII source, across datasets, dashboards, and
   ML models.
4. Emits one deterministic action per asset: anonymize the source row, delete
   derived rows, re-run the dbt model rather than deleting from it, invalidate
   the dashboard extract, flag the model for retrain, or escalate for review.
5. Halts and waits for a human to approve or reject each destructive action
   individually.
6. Executes only what was approved, dry-run by default.
7. Writes annotations and an `erasureRequest` audit entity back to DataHub, and
   emits a standalone Markdown and JSON audit record.

The action that matters most is the one it refuses to take. An asset with no
owner and no PII tag that sits downstream of a tagged source is flagged as
residual risk rather than deleted, because nobody can confirm what depends on
it, and a wrong delete is not reversible.

## How we built it

Python, with a deliberate split. `ErasureOrchestrator` is the deterministic
core: resolve, discover, traverse, plan, execute, write back, report. It
contains zero LLM calls and no agent framework import, which is why the CLI, the
Streamlit UI, and the test suite drive the full pipeline offline with no API
key.

The LLM layer is Agno, wrapping that core in four tools. The model decides which
tool to call and explains the result. It never composes SQL. Destructive SQL
comes from Jinja templates driven by explicit ordered rules in the planner, and
identifiers arriving from metadata are validated and quoted before they reach a
template.

The DataHub client has three interchangeable backends behind one interface:
canned fixtures for offline demos and CI, direct REST against a live GMS, and
`mcp-server-datahub` over stdio for MCP on the wire.

The runtime is AgentOS on FastAPI, which also exposes Vismriti as an MCP server,
so other agents can call its erasure tools. Slack is where the approvals
happen, because that is where DPOs already are.

## Challenges we ran into

The reference DataHub deployment on Azure Container Apps runs GMS v1.7.0 without
Kafka and with empty search and graph indexes. Measured against it:
`GET /entities/{urn}` works, `POST /entities?action=search` returns
`numEntities: 0`, `GET /relationships` returns `total: 0`, and
`POST /aspects?action=ingestProposal` blocks and times out because every
proposal is published to a broker that is not there.

That removed discovery, traversal, and writes over the API in one go. The
adaptation was to seed the live client with an explicit URN set and reconstruct
lineage from each entity's own aspects, which are readable, and to make the
write-back attempt the real ingest call with a short timeout and record its
actual outcome.

The tempting shortcut was to return success from the write path so the demo
looked clean. We took the other option: the execution report states that the
DataHub write failed and why, and the local JSON and Markdown pair is the
durable audit record in that configuration. A compliance tool that fakes a
successful deletion record is worse than no tool.

The second challenge was subtler. Lineage lives in different aspects per entity
type. Datasets declare `UpstreamLineage`, but dashboards declare their inputs in
`DashboardInfo` and ML models in `MLModelProperties`. Reading only the first one
silently dropped the dashboard and the model from live plans, which is exactly
the class of miss the project exists to prevent.

## Accomplishments that we are proud of

The deterministic core is genuinely separable and genuinely tested. The model
cannot fabricate a destructive statement: the execute tool ignores any SQL in
its arguments and looks the statement up from the stored plan by request id and
asset URN.

Approval is per action, not all or nothing, and it survives a restart, because a
real DPO workflow stretches over hours.

And the residual-risk rule works. On the seeded story it surfaces the analyst
sandbox that no tag-based tooling can see.

## What we learned

Deciding where the model is not allowed to operate turned out to be the main
design work. Everything good about the system follows from the orchestrator
containing no LLM calls: it is auditable, it is testable in under a second
offline, and the blast radius is bounded by construction.

We also learned how much of an integration's real behaviour you only discover by
probing it. Every limitation in the challenges section came from curl, not from
documentation.

## What is next for Vismriti

Deploy the missing DataHub components so the write path lands, then remove the
seeded URN list in favour of the search and relationships endpoints. Pluggable
executors for dbt and Airflow, so the plan is consumed by whichever system
already holds warehouse credentials. Retention on stored plans, since they
contain subject identifiers and a right-to-erasure tool holding those
indefinitely is its own problem.

---

## Built with

`python` `agno` `datahub` `mcp` `fastapi` `streamlit` `slack` `postgresql`
`jinja2` `pydantic` `typer` `azure-container-apps` `azure-openai`

---

## Links

| Field | Value |
|---|---|
| Repository | <https://github.com/anshul-jain-devx108/Vismriti> |
| Demo video | FILL: YouTube link |
| DataHub UI | <https://datahub-frontend.happyhill-72aa3202.centralindia.azurecontainerapps.io> |
| GMS API | <https://datahub-gms.happyhill-72aa3202.centralindia.azurecontainerapps.io> |
| Setup guide | `SETUP.md` in the repository |
| License | Apache-2.0 |

---

## Try it yourself

No credentials needed:

```bash
git clone https://github.com/anshul-jain-devx108/Vismriti && cd Vismriti
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e ".[dev]"
./.venv/bin/erase plan --email priya.sharma@example.com --fixtures
```

Against the live DataHub:

```bash
./.venv/bin/python scripts/verify_live_datahub.py
```

---

## AI assistance disclosure

FILL: confirm this matches the README's disclosure before submitting, and keep
both accurate. State plainly which parts were drafted with model assistance and
which engineering decisions were the authors'. An accurate disclosure costs
nothing. An inaccurate or missing one can invalidate the entry.
