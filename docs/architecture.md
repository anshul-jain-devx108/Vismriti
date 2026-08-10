# Architecture

Vismriti turns one subject identifier into an approvable, auditable erasure
plan across a data warehouse, using DataHub as the map.

The central design constraint is this: a language model is good at deciding
*which* question to ask and at explaining the answer, and it is the wrong tool
for deciding *what to delete*. So the system is split in two, with a hard
boundary between them.

---

## The split

```
                 ┌──────────────── LLM layer ────────────────┐
   Slack ───┐    │  agent/agent.py    Agno Agent             │
   HTTP  ───┼──▶ │  agent/prompt.py   system instructions    │
   MCP   ───┘    │  agent/tools.py    4 tool wrappers        │
                 └───────────────────┬───────────────────────┘
                                     │  calls into, never bypasses
                 ┌───────────────────▼───────────────────────┐
   CLI   ───┐    │        Deterministic core, zero LLM       │
   UI    ───┼──▶ │  services/orchestrator.py                 │
   pytest───┘    │  resolve → discover → traverse → plan      │
                 │        → execute → write back → report    │
                 └───────────────────┬───────────────────────┘
                                     │
                 ┌───────────────────▼───────────────────────┐
                 │  core/datahub_client.py                   │
                 │  fixture | live-rest | mcp-stdio          │
                 └───────────────────────────────────────────┘
```

`ErasureOrchestrator` contains no LLM calls and no Agno import. That is what
makes the destructive logic unit-testable offline, and it is why the CLI, the
Streamlit UI, and the test suite can drive the full pipeline without an API
key. The Agno layer is a wrapper around it, not a replacement for it.

---

## The pipeline

### 1. Discover

`DataHubClient.find_pii_columns()` returns every column DataHub has tagged as
PII. On a healthy DataHub this is a tag search. On a deployment with an empty
search index it falls back to reading a configured set of source URNs directly
(see "Deployment reality" below).

### 2. Resolve

`services/subject_resolver.py` maps the inbound email to the internal
identifiers the warehouse actually keys on: a primary integer id, and the
SHA-256 of the lowercased email. The hash matters because derived tables
routinely drop the raw id and keep only a hashed handle, and those tables are
still copies of the subject.

If resolution fails, that is a hard stop for destructive actions, not a
default. A plan that cannot identify the subject must not emit SQL.

### 3. Traverse

`services/lineage.py` walks forward from every PII source, depth-bounded by
`ERASURE_AGENT_MAX_LINEAGE_DEPTH`, collecting each downstream asset with its
depth, owners, platform, and tags.

Lineage lives in different aspects depending on entity type: datasets declare
`UpstreamLineage`, dashboards declare their inputs in `DashboardInfo`, and ML
models declare `MLModelProperties`. All three are read, which is why a
dashboard and a model appear in the plan alongside the tables.

### 4. Plan

`services/planner.py` maps each asset to exactly one action using explicit
ordered rules, never a model decision:

| Condition | Action | Rationale |
|---|---|---|
| No owner, no PII tag, downstream of a tagged source | `residual_review` | Nobody can confirm what depends on it. Flag, do not guess. |
| Depth 0 with PII columns | `anonymize_row` | Null the PII, keep the row so foreign keys survive. |
| Derived, dbt-managed | `dbt_rerun` | Deleting rows would be undone by the next build. Fix the source, rebuild. |
| Derived dataset | `delete_row` | The subject's rows are a copy; remove them. |
| Dashboard or chart | `dashboard_invalidate` | Stale extracts keep rendering PII after the source is clean. |
| ML model or feature table | `ml_model_annotate` | Flag for retrain per policy. Vismriti does not retrain models. |

Rule order matters: the residual check runs first, so an unowned asset is never
silently deleted just because it also matched a later rule.

SQL comes from Jinja templates in `services/sql_templates/`. Identifiers
arriving from metadata are validated against a strict pattern and quoted before
they reach a template; anything that fails validation degrades to
`residual_review` rather than producing SQL.

### 5. Approve

Destructive tools are gated at the framework level, so the run halts and waits
for a decision rather than relying on the model to behave. Approval is per
action: a plan with eight actions can end with six approved and two rejected.

Residual-risk items are deliberately not approvable through the normal control.
Delete versus anonymize on an unowned table is a policy judgement, and reducing
it to a button would defeat the purpose of flagging it.

### 6. Execute

`services/executor.py` runs only approved, non-residual actions, and refuses
outright to execute an unapproved one. `ERASURE_AGENT_DRY_RUN` defaults to
true, which generates the SQL and records the approval without committing.

Advisory actions, meaning the dbt rerun, the dashboard invalidation, and the
model retrain flag, are recorded as advisory. Vismriti annotates them for the
owning system; it does not claim to have performed work inside dbt, a BI tool,
or an ML platform it never connected to.

### 7. Write back and report

`services/writeback.py` annotates each affected entity and creates an
`erasureRequest` entity linking to all of them, so the next auditor inherits
proof rather than a Slack thread. Every write outcome is captured, successes
and failures alike, and surfaced on the `ExecutionReport`.

`services/report.py` emits a Markdown report and a JSON audit trail under
`ERASURE_AGENT_OUTPUT_DIR`. If the DataHub write failed, the report says so and
says why. A compliance artifact that omits a failed write is worse than no
artifact.

---

## The DataHub client

One interface, three backends, chosen at construction:

| Mode | Use |
|---|---|
| `fixture` | Canned JSON on disk. Offline demos, CI, and the unit tests. |
| `live-rest` | Direct HTTPS to the GMS REST surface. The same endpoints the MCP server proxies, without the subprocess. |
| `mcp-stdio` | Spawns `mcp-server-datahub` and speaks MCP over stdio. |

Everything above the client is mode-agnostic. `_snapshot_to_fixture_shape()`
flattens a live GMS Rest.li snapshot into the same dict shape the fixtures use,
so the parser does not know or care where the metadata came from.

---

## Deployment reality

The reference Azure Container Apps deployment runs GMS v1.7.0 without Kafka and
with empty search and graph indexes. Measured behaviour:

- `GET /entities/{urn}` works.
- `POST /entities?action=search` returns `numEntities: 0`, so tag-based
  discovery over the API returns nothing.
- `GET /relationships` returns `total: 0`, so lineage cannot be traversed over
  the API.
- `POST /aspects?action=ingestProposal` blocks and times out, so writes cannot
  land.

Two design consequences follow, and both are visible in the code rather than
hidden behind a happy path:

**Discovery and traversal are seeded.** Since the indexes return nothing, the
live client is given an explicit URN set through `DATAHUB_SEED_URNS` and
`DATAHUB_PII_SOURCE_URNS`, and reconstructs the lineage graph from each
entity's own aspects. Against an indexed DataHub the client uses the search and
relationships endpoints normally.

**Write-back reports failure honestly.** The ingest call is attempted with a
short timeout, and its real outcome is recorded. It does not return a
fabricated success. In this configuration the durable audit record is the JSON
and Markdown pair under `runs/`.

---

## Why the boundary is where it is

Three properties fall out of keeping the orchestrator LLM-free:

**Auditable.** A regulator asking why a given table was deleted gets a rule,
with a line number, not a model transcript.

**Testable.** The full pipeline runs in the test suite against fixtures in
under a second, with no network and no key.

**Bounded blast radius.** The model cannot compose a `DELETE`. It can only ask
for execution of a plan entry that the deterministic planner already emitted
and a human already approved, looked up server-side by request id and asset
URN rather than taken from anything the model said.

That last point is the one worth stress-testing: the execute tool ignores any
SQL in its arguments and reads the statement from the stored plan.
