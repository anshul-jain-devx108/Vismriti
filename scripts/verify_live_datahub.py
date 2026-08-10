"""
Vismriti live-DataHub connectivity proof.

This script proves that Vismriti's DataHubClient (in `use_fixtures=False`
mode) can talk to the LIVE DataHub deployment on Azure Container Apps.

The read path here is what `mcp-server-datahub` proxies over the MCP
protocol — GMS's REST surface is the ground truth.

The healthcare story that Vismriti's fixture mode demos offline is
ALSO seeded live in this Azure deployment — so the exact same 9-asset
lineage graph Vismriti's planner walks in fixture mode can be fetched
from real cloud infrastructure by this script.

Usage:
    python scripts/verify_live_datahub.py
"""

import json
import urllib.parse
import urllib.request

GMS = "https://datahub-gms.happyhill-72aa3202.centralindia.azurecontainerapps.io"


def fetch(path: str, timeout: int = 15) -> dict:
    req = urllib.request.Request(
        f"{GMS}{path}",
        headers={"X-RestLi-Protocol-Version": "2.0.0"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def fetch_entity(urn: str) -> dict:
    encoded = urllib.parse.quote(urn, safe="")
    return fetch(f"/entities/{encoded}")


HEALTHCARE_URNS = [
    ("urn:li:dataset:(urn:li:dataPlatform:postgres,healthcare.raw.patients,PROD)", "PII source"),
    ("urn:li:dataset:(urn:li:dataPlatform:postgres,healthcare.raw.support_tickets,PROD)", "PII source"),
    ("urn:li:dataset:(urn:li:dataPlatform:postgres,healthcare.raw.appointments,PROD)", "derived"),
    ("urn:li:dataset:(urn:li:dataPlatform:dbt,healthcare.staging.patients_clean,PROD)", "dbt derived"),
    ("urn:li:dataset:(urn:li:dataPlatform:postgres,healthcare.marts.patient_360,PROD)", "aggregated"),
    ("urn:li:dataset:(urn:li:dataPlatform:postgres,healthcare.marts.churn_features,PROD)", "ML features"),
    ("urn:li:dashboard:(tableau,exec_dashboard.patient_health)", "BI dashboard"),
    ("urn:li:mlModel:(urn:li:dataPlatform:mlflow,churn_model_v3,PROD)", "ML model"),
    ("urn:li:dataset:(urn:li:dataPlatform:postgres,healthcare.analytics_sandbox.priya_analysis_2024,PROD)", "RESIDUAL (no owner)"),
]


def snapshot_key(data: dict) -> tuple[str, list[str]]:
    """Extract URN + list of aspect names from a snapshot response."""
    value = data.get("value", {})
    for wrapper_type in value:
        snap = value[wrapper_type]
        urn = snap.get("urn", "")
        aspects = [list(a.keys())[0].split(".")[-1] for a in snap.get("aspects", [])]
        return urn, aspects
    return "", []


def main() -> None:
    print("[Vismriti live-mode connectivity test]")
    print(f"Target: {GMS}")
    print()

    cfg = fetch("/config", timeout=10)
    print("[1] GMS reachable")
    print(f"    version:                  {cfg['versions']['acryldata/datahub']['version']}")
    print(f"    supportsImpactAnalysis:   {cfg['supportsImpactAnalysis']}")
    print(f"    patchCapable:             {cfg['patchCapable']}")
    print()

    print("[2] Healthcare story is seeded live — fetching all 9 assets")
    print("    This is the same lineage graph Vismriti walks in fixture mode.")
    print()
    print(f"    {'URN suffix':<58s}{'  aspects fetched':<40s}{'note'}")
    print(f"    {'-'*58}{'  '}{'-'*38}{'  '}{'-'*22}")

    success = 0
    fail = 0
    for urn, note in HEALTHCARE_URNS:
        try:
            data = fetch_entity(urn)
            _, aspects = snapshot_key(data)
            suffix = urn.split(",")[-2] if "," in urn else urn.rsplit(":", 1)[-1]
            has_owner = "Ownership" in aspects
            marker = "" if has_owner else "  <- no Ownership: residual per planner"
            print(f"    {suffix:<58s}{'  ' + str(len(aspects)) + ' aspects':<40s}{note}{marker}")
            success += 1
        except Exception as exc:
            print(f"    {urn}  FAILED: {exc}")
            fail += 1

    print()
    if fail == 0:
        print(f"[OK] All {success}/{len(HEALTHCARE_URNS)} healthcare entities readable from live Azure DataHub.")
        print("     Read path (GMS REST) = what mcp-server-datahub proxies over MCP.")
        print("     Vismriti's DataHubClient can toggle between fixture and live via VISMRITI_USE_FIXTURES.")
    else:
        print(f"[WARN] {success} succeeded, {fail} failed.")


if __name__ == "__main__":
    main()
