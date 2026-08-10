"""Forward-lineage traversal from PII-tagged source columns.

Given the set of source datasets that contain the subject's PII, walk
DOWNSTREAM through the DataHub graph collecting every dataset, dashboard,
chart, ML feature table, and model that could hold a derived copy.

BFS not DFS - depth matters for the report ("2 hops from source") and
BFS gives deterministic ordering.
"""

from __future__ import annotations

from collections import deque

from ..core.datahub_client import DataHubClient
from ..core.models import Asset


async def collect_downstream(
    client: DataHubClient,
    source_urns: list[str],
    max_depth: int = 5,
) -> list[Asset]:
    """BFS forward from each source URN, deduplicating by URN.

    Returns assets in BFS order (shallowest first). Depth is stamped on
    each returned Asset so the planner and report can group by hops.
    """
    seen: dict[str, Asset] = {}
    queue: deque[tuple[str, int]] = deque((urn, 0) for urn in source_urns)

    while queue:
        urn, depth = queue.popleft()
        if urn in seen:
            continue

        asset = await client.get_entity(urn)
        if asset is None:
            continue
        asset.depth = depth
        seen[urn] = asset

        if depth >= max_depth:
            continue

        downstream = await client.get_downstream_lineage(urn, max_depth=1)
        for child in downstream:
            if child.urn not in seen:
                queue.append((child.urn, depth + 1))

    return sorted(seen.values(), key=lambda a: (a.depth, a.urn))
