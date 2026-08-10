## Inspiration

A GDPR erasure request arrives as one email address. The obligation is to find
every copy of that person across the warehouse, and the fines are for the
copies you miss. Denmark fined IDDesign 1.5M DKK in 2019 for exactly this:
"failure to delete personal data from an older system."

Privacy teams answer these by hand today, with Slack and a spreadsheet, because
the data that gets missed is not the data anyone forgot about. It is the data no
catalog could see. PII tags are applied at ingest, but derived tables rename,
hash, and join that PII into synthetic keys the tags cannot follow, and an
analyst's sandbox fork carries no tags and no owner at all.

That makes erasure a lineage question. Lineage is what DataHub already knows.
The gap was that nobody was walking it at request time.

## What it does

Give Vismriti one email address and it:

1. Finds every PII-tagged column DataHub knows about
2. Resolves the email to the identifiers the warehouse actually keys on,
   including the SHA-256 hash that hash-keyed derived tables carry
3. Walks forward lineage across datasets, dashboards, and ML models
4. Emits one deterministic action per asset: anonymize the source row, delete
   derived rows, re-run the dbt model rather than deleting from it, invalidate
   the dashboard extract, flag the model for retrain, or escalate for review
5. Halts and waits for a human to approve or reject **each action individually**
6. Executes only what was approved, dry-run by default
7. Writes an audit trail so the next auditor inherits proof, not a Slack thread

The action we are proudest of is the one it refuses to take. An asset with no
owner and no PII tag sitting downstream of a tagged source gets flagged as
residual risk instead of deleted, because nobody can confirm what depends on it
and a wrong delete is not reversible.

## How we built it

The core design constraint: a language model is good at deciding *which*
question to ask, and is the wrong tool for deciding *what to delete*. So the
system is split with a hard boundary.

`ErasureOrchestrator` is the deterministic core - resolve, discover, traverse,
plan, execute, write back, report. It contains **zero LLM calls** and no agent
framework import, which is why the CLI, the Streamlit UI, and the test suite
drive the full pipeline offline with no API key, in under a second.

The LLM layer is Agno wrapping that core in four tools. The model picks tools
and explains results. It never composes SQL. Destructive SQL comes from Jinja
templates driven by ordered rules, and identifiers arriving from metadata are
validated and quoted before they reach a template.

```python
# The execute tool ignores any SQL in its arguments and looks the
# statement up from the stored plan. The model cannot fabricate a
# destructive statement, only request execution of a planned one.
found = _find_action(request_id, asset_urn)
```

The DataHub client has three interchangeable backends: fixtures for offline CI,
direct REST against live GMS, and `mcp-server-datahub` over stdio. The runtime
is AgentOS on FastAPI, which also **exposes Vismriti as an MCP server**, so
other agents can call its erasure tools.

## Challenges we ran into

Our Azure Container Apps DataHub runs GMS v1.7.0 without Kafka and with empty
search and graph indexes. We found this with curl, not documentation:

| Operation | Result |
|---|---|
| `GET /entities/{urn}` | Works |
| `POST /entities?action=search` | HTTP 200, `numEntities: 0` |
| `GET /relationships` | HTTP 200, `total: 0` |
| `POST /aspects?action=ingestProposal` | Blocks, times out at 45s |

That removed discovery, traversal, and writes over the API in one go. We seeded
the client with an explicit URN set and reconstruct lineage from each entity's
own aspects, which are readable.

The tempting shortcut was to return success from the write path so the demo
looked clean. We took the other option. The execution report states that the
DataHub write failed and why, and a live run exits non-zero with "Run
incomplete: part of this erasure did not happen. Do not close the request."

One judgement call we want to name. The ML model's description literally reads
*"Trained on marts.churn_features"*, and we could have regexed that string to
synthesize the lineage edge and shown 9 of 9 assets instead of 7. We did not.
Scraping prose to decide what a right-to-erasure run touches is the kind of
thing that demos green and misfires in production. It flags rather than
fabricates, and the two missing assets are documented ingestion gaps.

A subtler bug: lineage lives in different aspects per entity type. Datasets
declare `UpstreamLineage`, dashboards declare inputs in `DashboardInfo`, ML
models in `MLModelProperties`. Reading only the first silently dropped the
dashboard from live plans - exactly the class of miss this project exists to
prevent.

## What we learned

Deciding where the model is **not** allowed to operate turned out to be the main
design work. Everything good about the system follows from the orchestrator
containing no LLM calls: it is auditable, testable offline, and the blast radius
is bounded by construction.

We also learned that a compliance tool that fakes a successful deletion record
is worse than no tool at all. Every limitation above came from probing the real
endpoint, and being straight about them is worth more than a green checkmark
that lies.

## What's next for Vismriti

Deploy Kafka so the write path lands, then drop the seeded URN list in favour of
the search and relationships endpoints. Pluggable executors for dbt and Airflow,
so the plan is consumed by whichever system already holds warehouse credentials.
And retention on stored plans - they contain subject identifiers, and a
right-to-erasure tool holding those forever is its own problem.

---

## Built with

`python` `agno` `datahub` `mcp` `fastapi` `streamlit` `slack` `postgresql`
`jinja2` `pydantic` `typer` `azure-container-apps` `azure-openai`
