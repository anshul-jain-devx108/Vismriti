# Azure deployment

The reference DataHub instance Vismriti reads from runs on Azure Container
Apps. This document records what is actually deployed, what was measured
against it, what is deliberately missing, and how to stand up an equivalent
environment.

Everything in the "Measured behaviour" section is reproducible from any laptop
with `curl`. No credentials are required, because the reference GMS runs with
metadata service authentication disabled.

---

## Topology

```
        Azure Container Apps environment
        happyhill-72aa3202 (centralindia)
        │
        │
        ├── datahub-gms           ─── public HTTPS ingress, REST + GraphQL
        │        │
        │        └── metadata store (relational)
        │
        └── (not deployed)
             ├── Kafka / Event Hubs      ← required for the write path
             ├── Elasticsearch           ← required for search
             └── graph index             ← required for lineage traversal
```

Endpoints:

| Service | URL |
|---|---|
| GMS API | <https://datahub-gms.happyhill-72aa3202.centralindia.azurecontainerapps.io> |

Version, from `GET /config`:

```json
{
  "versions": { "acryldata/datahub": { "version": "v1.7.0" } },
  "patchCapable": true,
  "supportsImpactAnalysis": true
}
```

---

## Measured behaviour

Run these yourself. `GMS` is the GMS URL above.

### Reads work

```bash
curl -s "$GMS/config" | head -20

curl -s -H "X-RestLi-Protocol-Version: 2.0.0" \
  "$GMS/entities/urn%3Ali%3Adataset%3A(urn%3Ali%3AdataPlatform%3Apostgres%2Chealthcare.raw.patients%2CPROD)"
```

Returns the full `DatasetSnapshot` with its `DatasetKey`, `SchemaMetadata`,
`Ownership`, and `UpstreamLineage` aspects. All nine seeded healthcare
entities respond this way.

For a full readback across the seeded story:

```bash
./.venv/bin/python scripts/verify_live_datahub.py
```

### Search returns nothing

```bash
curl -s -X POST "$GMS/entities?action=search" \
  -H "Content-Type: application/json" \
  -H "X-RestLi-Protocol-Version: 2.0.0" \
  -d '{"input":"*","entity":"dataset","start":0,"count":5}'
```

```json
{"value":{"numEntities":0,"pageSize":5,"from":0,"entities":[]}}
```

HTTP 200 in about 200ms, and zero results. The endpoint is healthy; the
Elasticsearch index behind it was never populated.

### Lineage traversal returns nothing

```bash
curl -s "$GMS/relationships?direction=INCOMING&types=DownstreamOf&urn=urn%3Ali%3Adataset%3A(urn%3Ali%3AdataPlatform%3Apostgres%2Chealthcare.raw.patients%2CPROD)"
```

```json
{"start":0,"count":0,"relationships":[],"total":0}
```

Same story: the endpoint answers, the graph index is empty.

### Writes hang

```bash
curl -s -m 45 -w "HTTP %{http_code} time=%{time_total}s\n" -o /dev/null \
  -X POST "$GMS/aspects?action=ingestProposal" \
  -H "Content-Type: application/json" \
  -H "X-RestLi-Protocol-Version: 2.0.0" \
  -d '{"proposal":{"entityType":"dataset","entityUrn":"urn:li:dataset:(urn:li:dataPlatform:postgres,probe,PROD)","aspectName":"datasetProperties","changeType":"UPSERT","aspect":{"value":"{\"description\":\"probe\"}","contentType":"application/json"}}}'
```

```
HTTP 000 time=45.003918s
```

The request is accepted and then blocks. GMS publishes every
`MetadataChangeProposal` to Kafka, and with no broker reachable the call never
returns. Compare against the search POST above, which answers in 200ms: POST
itself is fine, this specific path is not.

---

## What this means for Vismriti

Two adaptations, both explicit in the code rather than papered over:

**Seeded discovery and traversal.** With search and graph both empty, the live
client cannot find entities or follow edges through the API. It is instead
given the entity set out of band and reconstructs lineage from each entity's
own aspects, which are readable. Configure with:

```bash
DATAHUB_SEED_URNS=urn:li:dataset:(...),urn:li:dashboard:(...)
DATAHUB_PII_SOURCE_URNS=urn:li:dataset:(...)
```

Both default to the nine seeded healthcare URNs. Against a DataHub with
populated indexes, use the search and relationships endpoints instead.

**Honest write-back.** The ingest call is attempted with a five second timeout
and its real outcome is recorded on the execution report. It does not return a
fabricated success. In this configuration the durable audit record is the JSON
and Markdown pair written under `runs/`.

---

## Standing up an equivalent environment

### Option A: local quickstart

The fastest path to a complete DataHub, indexes and Kafka included:

```bash
pip install acryl-datahub
datahub docker quickstart
```

Then point Vismriti at it:

```bash
DATAHUB_GMS_URL=http://localhost:8080
```

Everything in this document that returns zero against the Azure instance
returns real data against quickstart, because quickstart brings up Kafka,
Elasticsearch, and the graph service.

### Option B: Azure Container Apps

For a deployment that supports the full read and write path, the environment
needs five components rather than two:

| Component | Azure service | Notes |
|---|---|---|
| GMS | Container App, `acryldata/datahub-gms` | Needs ingress and env wiring to the four below. |
| Frontend | Container App, `acryldata/datahub-frontend-react` | Public ingress. |
| Metadata store | Azure Database for PostgreSQL or MySQL | The system of record. |
| Search index | Elasticsearch or OpenSearch | Without it, search returns zero. |
| Event bus | Azure Event Hubs with the Kafka surface, or self-hosted Kafka | Without it, all writes hang. |

Wire GMS to the event bus with the standard DataHub variables
(`KAFKA_BOOTSTRAP_SERVER`, `KAFKA_SCHEMAREGISTRY_URL`) and to search with
`ELASTICSEARCH_HOST` and `ELASTICSEARCH_PORT`.

Confirm the deployment is complete before demoing against it:

```bash
# search must return a non-zero numEntities
curl -s -X POST "$GMS/entities?action=search" \
  -H "Content-Type: application/json" -H "X-RestLi-Protocol-Version: 2.0.0" \
  -d '{"input":"*","entity":"dataset","start":0,"count":1}'

# ingest must return quickly, not hang
curl -s -m 10 -w "\nHTTP %{http_code}\n" -X POST "$GMS/aspects?action=ingestProposal" ...
```

Those two checks are the difference between a DataHub that looks up and one
that is actually finished.

---

## Seeding the healthcare story

The nine entities the demo walks through:

| URN suffix | Type | Role |
|---|---|---|
| `healthcare.raw.patients` | dataset, postgres | PII source: email, phone, name |
| `healthcare.raw.support_tickets` | dataset, postgres | PII source: reporter_email |
| `healthcare.raw.appointments` | dataset, postgres | derived, no tags |
| `healthcare.staging.patients_clean` | dataset, dbt | derived, rebuilt not deleted |
| `healthcare.marts.patient_360` | dataset, postgres | aggregated |
| `healthcare.marts.churn_features` | dataset, postgres | ML feature table |
| `exec_dashboard.patient_health` | dashboard, tableau | BI asset |
| `churn_model_v3` | mlModel, mlflow | trained on the feature table |
| `healthcare.analytics_sandbox.priya_analysis_2024` | dataset, postgres | no owner, no tags: the residual |

The last row is the one the demo turns on. It carries no tags and no owner, so
tag-driven tooling cannot see it, and it only surfaces because lineage is
walked at request time.

Against a write-capable DataHub, seed them with `datahub put` or a recipe
through `datahub ingest`. Against this deployment, seeding had to happen
through the same path that is now blocked, which is why the entity set is
fixed.

The matching warehouse rows live in `scripts/init_healthcare.sql`, mounted by
`docker-compose.yml`.
