"""DataHub client with three interchangeable backends.

The same read/write interface is served by pre-canned JSON fixtures on disk,
direct Rest.li calls to a live GMS, or an mcp-server-datahub subprocess spoken
to over stdio. Mode is chosen at construction time and defaults to
settings.datahub_client_mode.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from ..utils.config import settings
from .models import Asset, AssetType, PIIColumn

logger = logging.getLogger(__name__)

FIXTURE_DIR = Path(__file__).parent.parent / "services" / "fixtures"

_READ_TIMEOUT = 20

# Writes are accepted by GMS only if Kafka is reachable behind it. Where Kafka
# is absent the request hangs until the socket gives up, so writes use a short
# timeout and surface the failure instead of stalling the run.
_WRITE_TIMEOUT = 5

_SYSTEM_ACTOR = "urn:li:corpuser:__datahub_system"

# Search and graph are separate indexes from the entity store, and on this
# deployment both are empty: POST /entities?action=search reports numEntities 0
# and GET /relationships reports total 0, so neither PII discovery nor lineage
# traversal can enumerate anything. GET /entities/{urn} still serves full
# snapshots, so the URN set has to be supplied out of band. Point these at a
# different estate with DATAHUB_SEED_URNS / DATAHUB_PII_SOURCE_URNS
# (comma-separated); delete them once the indexes are rebuilt.
DEFAULT_SEED_URNS = (
    "urn:li:dataset:(urn:li:dataPlatform:postgres,healthcare.raw.patients,PROD)",
    "urn:li:dataset:(urn:li:dataPlatform:postgres,healthcare.raw.support_tickets,PROD)",
    "urn:li:dataset:(urn:li:dataPlatform:postgres,healthcare.raw.appointments,PROD)",
    "urn:li:dataset:(urn:li:dataPlatform:dbt,healthcare.staging.patients_clean,PROD)",
    "urn:li:dataset:(urn:li:dataPlatform:postgres,healthcare.marts.patient_360,PROD)",
    "urn:li:dataset:(urn:li:dataPlatform:postgres,healthcare.marts.churn_features,PROD)",
    "urn:li:dashboard:(tableau,exec_dashboard.patient_health)",
    "urn:li:mlModel:(urn:li:dataPlatform:mlflow,churn_model_v3,PROD)",
    (
        "urn:li:dataset:(urn:li:dataPlatform:postgres,"
        "healthcare.analytics_sandbox.priya_analysis_2024,PROD)"
    ),
)

DEFAULT_PII_SOURCE_URNS = (
    "urn:li:dataset:(urn:li:dataPlatform:postgres,healthcare.raw.patients,PROD)",
    "urn:li:dataset:(urn:li:dataPlatform:postgres,healthcare.raw.support_tickets,PROD)",
)

# Entity type to the aspect that carries its customProperties map.
_PROPERTIES_ASPECT = {
    "dataset": "datasetProperties",
    "dashboard": "dashboardInfo",
    "chart": "chartInfo",
    "mlModel": "mlModelProperties",
    "mlFeatureTable": "mlFeatureTableProperties",
}


class ClientMode(str, Enum):
    FIXTURE = "fixture"
    LIVE_REST = "live-rest"
    MCP_STDIO = "mcp-stdio"


def _urns_from_env(var: str, default: tuple[str, ...]) -> list[str]:
    raw = os.getenv(var, "")
    if not raw.strip():
        return list(default)
    return [urn.strip() for urn in raw.split(",") if urn.strip()]


def _as_urn(entry: Any) -> str:
    """Lineage entries appear as bare URN strings or as small wrapper dicts."""
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        for key in ("dataset", "urn", "datasetUrn", "entity", "trainingData"):
            value = entry.get(key)
            if isinstance(value, str) and value:
                return value
    return ""


def _collect_urns(entries: Any) -> list[str]:
    if not isinstance(entries, list):
        return []
    return [urn for urn in (_as_urn(e) for e in entries) if urn]


def _dedupe(urns: list[str]) -> list[str]:
    seen: dict[str, None] = {}
    for urn in urns:
        seen.setdefault(urn, None)
    return list(seen)


def _entity_type_from_urn(urn: str) -> str:
    parts = urn.split(":", 3)
    return parts[2] if len(parts) > 2 else ""


def _pii_columns_from_schema(body: dict) -> list[dict]:
    columns: list[dict] = []
    for field in body.get("fields", []):
        tags = field.get("globalTags", {}).get("tags", [])
        pii_tag = next((t.get("tag", "") for t in tags if "PII." in t.get("tag", "")), None)
        if not pii_tag:
            continue
        columns.append({
            "column_name": field.get("fieldPath", ""),
            "pii_type": pii_tag.split(".")[-1].lower(),
            "tags": [pii_tag.split(":")[-1]],
        })
    return columns


def _error_body(exc: urllib.error.HTTPError) -> str:
    """Best-effort read of an error response body, for the failure reason."""
    try:
        return exc.read().decode("utf-8", "replace")[:200].replace("\n", " ").strip()
    except Exception:  # noqa: BLE001 - a missing body must not mask the HTTP status
        return ""


def _fill_required_fields(aspect_name: str, body: dict[str, Any], urn: str) -> None:
    """dashboardInfo and chartInfo are rejected without title and lastModified."""
    if aspect_name not in ("dashboardInfo", "chartInfo"):
        return
    body.setdefault("title", urn)
    now = int(datetime.now(timezone.utc).timestamp() * 1000)
    stamp = {"time": now, "actor": _SYSTEM_ACTOR}
    body.setdefault("lastModified", {"created": dict(stamp), "lastModified": dict(stamp)})


class DataHubClient:
    """Multi-backend DataHub client.

    DataHubClient(use_fixtures=True) still selects FIXTURE mode; anything else
    defaults to settings.datahub_client_mode, falling back to LIVE_REST when the
    GMS URL is https and MCP_STDIO otherwise.
    """

    def __init__(
        self,
        use_fixtures: bool = False,
        mode: ClientMode | str | None = None,
    ) -> None:
        if mode is not None:
            self.mode = ClientMode(mode) if isinstance(mode, str) else mode
        elif use_fixtures:
            self.mode = ClientMode.FIXTURE
        else:
            configured = getattr(settings, "datahub_client_mode", None)
            if configured:
                self.mode = ClientMode(configured)
            elif settings.datahub_gms_url and settings.datahub_gms_url.startswith("https://"):
                self.mode = ClientMode.LIVE_REST
            else:
                self.mode = ClientMode.MCP_STDIO

        self._session: Any = None
        self._stdio_ctx: Any = None
        # Metadata does not change under a single planning run, and lineage
        # resolution re-reads the same seed URNs once per traversed node.
        self._snapshot_cache: dict[str, dict] = {}

    # ---- Lifecycle ----
    async def connect(self) -> None:
        if self.mode in (ClientMode.FIXTURE, ClientMode.LIVE_REST):
            return

        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
        except ImportError as exc:
            raise RuntimeError(
                "mcp package not installed. `pip install mcp` or "
                "switch to fixture / live-rest mode."
            ) from exc

        params = StdioServerParameters(
            command=settings.datahub_mcp_command,
            args=settings.datahub_mcp_args.split(),
            env={
                "DATAHUB_GMS_URL": settings.datahub_gms_url,
                "DATAHUB_GMS_TOKEN": settings.datahub_gms_token,
            },
        )
        self._stdio_ctx = stdio_client(params)
        read, write = await self._stdio_ctx.__aenter__()
        self._session = ClientSession(read, write)
        await self._session.__aenter__()
        await self._session.initialize()

    async def close(self) -> None:
        if self.mode == ClientMode.MCP_STDIO and self._session is not None:
            await self._session.__aexit__(None, None, None)
            await self._stdio_ctx.__aexit__(None, None, None)

    @property
    def use_fixtures(self) -> bool:
        return self.mode == ClientMode.FIXTURE

    # ---- Backend dispatch ----
    async def _call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        if self.mode == ClientMode.FIXTURE:
            return self._fixture(name, arguments)
        if self.mode == ClientMode.LIVE_REST:
            return self._live_rest(name, arguments)
        result = await self._session.call_tool(name, arguments)
        payload = result.content[0].text if result.content else "{}"
        return json.loads(payload)

    # ---- Backend: FIXTURE ----
    def _fixture(self, name: str, arguments: dict[str, Any]) -> Any:
        path = FIXTURE_DIR / f"{name}.json"
        if not path.exists():
            return {}
        data = json.loads(path.read_text())

        if name == "get_entity":
            urn = arguments.get("urn", "")
            return data.get(urn, {})

        if name == "get_lineage":
            urn = arguments.get("urn", "")
            entities = data.get("entities", [])
            children = [e for e in entities if urn in e.get("upstreams", [])]
            return {"entities": children}

        return data

    # ---- Backend: LIVE_REST ----
    def _live_rest(self, name: str, arguments: dict[str, Any]) -> Any:
        gms = settings.datahub_gms_url.rstrip("/")

        if name == "get_entity":
            urn = arguments.get("urn", "")
            snap = self._live_get_snapshot(gms, urn)
            return self._snapshot_to_fixture_shape(snap) if snap else {}

        if name == "get_lineage":
            source_urn = arguments.get("urn", "")
            return {"entities": self._live_get_lineage(gms, source_urn)}

        if name == "search_datasets":
            return self._live_find_pii(gms)

        if name == "add_annotation":
            return self._live_annotate(
                gms,
                arguments.get("urn", ""),
                arguments.get("key", ""),
                arguments.get("value", ""),
            )

        if name == "create_entity":
            return self._live_create_entity(gms, arguments)

        return {}

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {"X-RestLi-Protocol-Version": "2.0.0"}
        token = (settings.datahub_gms_token or "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if extra:
            headers.update(extra)
        return headers

    def _http_get(self, url: str, timeout: int = _READ_TIMEOUT) -> dict:
        req = urllib.request.Request(url, headers=self._headers())
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())

    def _live_get_snapshot(self, gms: str, urn: str) -> dict:
        if urn in self._snapshot_cache:
            return self._snapshot_cache[urn]

        snapshot: dict = {}
        try:
            encoded = urllib.parse.quote(urn, safe="")
            resp = self._http_get(f"{gms}/entities/{encoded}")
        except Exception as exc:  # noqa: BLE001 - a read failure must not abort traversal
            # Callers treat an empty snapshot as "asset not found", which would
            # silently shrink an erasure plan, so record why it was empty.
            logger.warning("DataHub read failed for %s: %s: %s", urn, type(exc).__name__, exc)
            self._snapshot_cache[urn] = snapshot
            return snapshot

        for snap in resp.get("value", {}).values():
            snapshot = snap
            break
        if not snapshot:
            logger.warning("DataHub returned an empty snapshot for %s", urn)
        self._snapshot_cache[urn] = snapshot
        return snapshot

    def _snapshot_to_fixture_shape(self, snap: dict) -> dict:
        """Flatten a GMS Rest.li snapshot into the shape the fixtures use.

        Keeps _parse_asset mode-agnostic: it cannot tell a JSON file from a live
        REST response. Upstream URNs are gathered from every aspect that can
        declare inputs, since only datasets use UpstreamLineage.
        """
        urn = snap.get("urn", "")
        out: dict[str, Any] = {"urn": urn, "pii_columns": [], "owners": []}
        key_name = ""
        props_name = ""
        platform = ""
        upstreams: list[str] = []

        for aspect in snap.get("aspects", []):
            for aspect_type, body in aspect.items():
                if not isinstance(body, dict):
                    continue
                short = aspect_type.split(".")[-1]
                if short == "DatasetKey":
                    key_name = body.get("name", "").split(".", 1)[-1]  # trim the db prefix
                    platform = body.get("platform", "").split(":")[-1]
                elif short == "DashboardKey":
                    key_name = body.get("dashboardId", "")
                    platform = body.get("dashboardTool", "")
                elif short in ("MLModelKey", "MlModelKey"):
                    key_name = body.get("name", "")
                    platform = body.get("platform", "").split(":")[-1]
                elif short == "DatasetProperties":
                    props_name = body.get("name") or ""
                elif short == "DashboardInfo":
                    props_name = body.get("title") or body.get("name") or ""
                    # A dashboard names its inputs here, not in UpstreamLineage.
                    upstreams += _collect_urns(body.get("datasets"))
                elif short == "MLModelProperties":
                    props_name = body.get("name") or ""
                    # An ML model names its inputs here, not in UpstreamLineage.
                    upstreams += _collect_urns(body.get("trainingData"))
                    upstreams += _collect_urns(body.get("trainingJobs"))
                elif short == "Ownership":
                    out["owners"] = [o.get("owner", "") for o in body.get("owners", [])]
                elif short == "UpstreamLineage":
                    upstreams += _collect_urns(body.get("upstreams"))
                elif short == "SchemaMetadata":
                    out["pii_columns"] = _pii_columns_from_schema(body)

        # Aspect order is not guaranteed, so resolve the name once at the end.
        name = key_name or props_name
        if name:
            out["name"] = name
        if platform:
            out["platform"] = platform
        out["upstreams"] = _dedupe(upstreams)
        return out

    def _live_get_lineage(self, gms: str, source_urn: str) -> list[dict]:
        """Return the seed entities that declare source_urn as an input.

        The graph index is empty, so edges are recovered by reading each seed
        snapshot and inspecting its own lineage-bearing aspects.
        """
        children: list[dict] = []
        for candidate in _urns_from_env("DATAHUB_SEED_URNS", DEFAULT_SEED_URNS):
            snap = self._live_get_snapshot(gms, candidate)
            if not snap:
                continue
            flat = self._snapshot_to_fixture_shape(snap)
            if source_urn in flat.get("upstreams", []):
                children.append(flat)
        return children

    def _live_find_pii(self, gms: str) -> dict:
        """Return PII columns for the seeded PII sources by reading their schemas."""
        cols: list[dict] = []
        for urn in _urns_from_env("DATAHUB_PII_SOURCE_URNS", DEFAULT_PII_SOURCE_URNS):
            snap = self._live_get_snapshot(gms, urn)
            if not snap:
                continue
            flat = self._snapshot_to_fixture_shape(snap)
            if not flat.get("pii_columns"):
                # No SchemaMetadata means no column-level tags to match on, so
                # this source drops out of the plan entirely.
                logger.warning("No PII-tagged columns found on seeded source %s", urn)
            for pii in flat.get("pii_columns", []):
                cols.append({
                    "dataset_urn": urn,
                    "column_name": pii["column_name"],
                    "pii_type": pii["pii_type"],
                    "tags": pii.get("tags", []),
                })
        return {"columns": cols}

    # ---- Write-back over Rest.li ----
    def _post_ingest_proposal(
        self,
        gms: str,
        urn: str,
        entity_type: str,
        aspect_name: str,
        aspect_value: dict[str, Any],
    ) -> dict[str, Any]:
        """POST one MetadataChangeProposal. Never raises; reports what happened."""
        payload = {
            "proposal": {
                "entityType": entity_type,
                "entityUrn": urn,
                "changeType": "UPSERT",
                "aspectName": aspect_name,
                "aspect": {
                    "contentType": "application/json",
                    "value": json.dumps(aspect_value, separators=(",", ":")),
                },
            }
        }
        req = urllib.request.Request(
            f"{gms}/aspects?action=ingestProposal",
            data=json.dumps(payload).encode("utf-8"),
            headers=self._headers({"Content-Type": "application/json"}),
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=_WRITE_TIMEOUT) as r:
                if 200 <= r.status < 300:
                    return {"success": True, "urn": urn, "error": None}
                error = f"HTTP {r.status} from ingestProposal"
        except urllib.error.HTTPError as exc:
            detail = _error_body(exc)
            error = f"HTTP {exc.code} from ingestProposal{': ' + detail if detail else ''}"
        except Exception as exc:  # noqa: BLE001 - the write outcome is reported, not raised
            error = f"{type(exc).__name__}: {exc}"

        logger.warning("DataHub write failed for %s (%s): %s", urn, aspect_name, error)
        return {"success": False, "urn": urn, "error": error}

    def _fetch_properties_aspect(
        self, gms: str, urn: str, aspect_name: str
    ) -> tuple[dict | None, str | None]:
        """Read one aspect. Returns ({} , None) when it does not exist yet.

        A None body means the current value is unknown, which is not the same as
        empty: overwriting an aspect we could not read would drop its contents.
        """
        encoded = urllib.parse.quote(urn, safe="")
        url = f"{gms}/aspects/{encoded}?aspect={aspect_name}&version=0"
        try:
            resp = self._http_get(url, timeout=_WRITE_TIMEOUT)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return {}, None
            return None, f"HTTP {exc.code} reading {aspect_name}"
        except Exception as exc:  # noqa: BLE001 - an unreadable aspect blocks the write
            return None, f"{type(exc).__name__} reading {aspect_name}: {exc}"

        for body in resp.get("aspect", {}).values():
            if isinstance(body, dict):
                return body, None
        return {}, None

    def _live_annotate(self, gms: str, urn: str, key: str, value: str) -> dict[str, Any]:
        """Merge one key/value into the entity's customProperties and upsert it."""
        entity_type = _entity_type_from_urn(urn)
        aspect_name = _PROPERTIES_ASPECT.get(entity_type)
        if not aspect_name:
            return {
                "success": False,
                "urn": urn,
                "error": f"no properties aspect known for entity type '{entity_type}'",
            }

        body, error = self._fetch_properties_aspect(gms, urn, aspect_name)
        if body is None:
            return {"success": False, "urn": urn, "error": error}

        merged = dict(body)
        custom = dict(merged.get("customProperties") or {})
        custom[key] = value
        merged["customProperties"] = custom
        _fill_required_fields(aspect_name, merged, urn)
        return self._post_ingest_proposal(gms, urn, entity_type, aspect_name, merged)

    def _live_create_entity(self, gms: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Upsert the audit-trail entity. The URN is deterministic, the result is not."""
        entity_type = arguments.get("entity_type") or "erasureRequest"
        request_id = arguments.get("id", "")
        urn = f"urn:li:{entity_type}:{request_id}"
        properties = dict(arguments.get("properties") or {})
        result = self._post_ingest_proposal(
            gms, urn, entity_type, f"{entity_type}Properties", properties
        )
        # Callers need the URN for the local audit record whether or not the
        # remote write landed, so it is always returned alongside the outcome.
        result["urn"] = urn
        return result

    # ---- Discovery / traversal / write-back (mode-agnostic) ----
    async def find_pii_columns(
        self, pii_types: tuple[str, ...] = ("email", "phone", "name", "ssn", "patient_id")
    ) -> list[PIIColumn]:
        raw = await self._call_tool(
            "search_datasets",
            {"query": " OR ".join(f"tag:PII.{t}" for t in pii_types), "limit": 200},
        )
        return [
            PIIColumn(
                dataset_urn=item["dataset_urn"],
                column_name=item["column_name"],
                pii_type=item.get("pii_type", "unknown"),
                tags=item.get("tags", []),
            )
            for item in raw.get("columns", [])
        ]

    async def get_downstream_lineage(
        self, source_urn: str, max_depth: int = 5
    ) -> list[Asset]:
        raw = await self._call_tool(
            "get_lineage",
            {"urn": source_urn, "direction": "DOWNSTREAM", "depth": max_depth},
        )
        return [self._parse_asset(item) for item in raw.get("entities", [])]

    async def get_entity(self, urn: str) -> Asset | None:
        raw = await self._call_tool("get_entity", {"urn": urn})
        if not raw:
            return None
        return self._parse_asset(raw)

    def _parse_asset(self, item: dict[str, Any]) -> Asset:
        urn = item["urn"]
        return Asset(
            urn=urn,
            name=item.get("name", urn.split(",")[-2] if "," in urn else urn),
            asset_type=self._infer_type(urn),
            platform=item.get("platform"),
            owners=item.get("owners", []),
            pii_columns=[
                PIIColumn(
                    dataset_urn=urn,
                    column_name=c["column_name"],
                    pii_type=c.get("pii_type", "unknown"),
                    tags=c.get("tags", []),
                )
                for c in item.get("pii_columns", [])
            ],
            upstreams=item.get("upstreams", []),
            depth=item.get("depth", 0),
        )

    @staticmethod
    def _infer_type(urn: str) -> AssetType:
        if urn.startswith("urn:li:dataset"):
            return AssetType.DATASET
        if urn.startswith("urn:li:dashboard"):
            return AssetType.DASHBOARD
        if urn.startswith("urn:li:chart"):
            return AssetType.CHART
        if urn.startswith("urn:li:mlModel"):
            return AssetType.ML_MODEL
        if urn.startswith("urn:li:mlFeatureTable"):
            return AssetType.ML_FEATURE_TABLE
        return AssetType.UNKNOWN

    # ---- Write-back ----
    async def annotate_entity(
        self, urn: str, annotation_key: str, annotation_value: str
    ) -> dict[str, Any]:
        """Annotate one entity. The payload carries the reason on failure."""
        raw = await self._call_tool(
            "add_annotation",
            {"urn": urn, "key": annotation_key, "value": annotation_value},
        )
        if isinstance(raw, dict):
            return raw
        ok = bool(raw)
        return {
            "success": ok,
            "urn": urn,
            "error": None if ok else "DataHub client returned no annotation result",
        }

    async def create_erasure_request(
        self,
        request_id: str,
        subject_email_hash: str,
        affected_urns: list[str],
    ) -> dict[str, Any]:
        """Create the audit-trail entity, reporting whether the write landed."""
        raw = await self._call_tool(
            "create_entity",
            {
                "entity_type": "erasureRequest",
                "id": request_id,
                "properties": {
                    "subject_email_hash": subject_email_hash,
                    "affected_urns": affected_urns,
                },
            },
        )
        if not isinstance(raw, dict):
            return {
                "urn": f"urn:li:erasureRequest:{request_id}",
                "success": False,
                "error": "DataHub client returned no create result",
            }
        result = dict(raw)
        result.setdefault("urn", f"urn:li:erasureRequest:{request_id}")
        return result
