"""Forward-lineage traversal from PII-tagged source datasets.

Walks DOWNSTREAM through the DataHub graph from each source URN, collecting
every dataset, dashboard, chart, ML feature table, and model that could hold a
derived copy. BFS, so the first depth reached for a URN is the shortest one.
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

    Returns assets shallowest first. Depth is stamped on each returned Asset
    so the planner and report can group by hops. A URN is queued at most once,
    which bounds the traversal even if the graph contains cycles or a URN that
    cannot be fetched.
    """
    found: dict[str, Asset] = {}
    queued: set[str] = set()
    queue: deque[tuple[str, int]] = deque()

    for urn in source_urns:
        if urn not in queued:
            queued.add(urn)
            queue.append((urn, 0))

    while queue:
        urn, depth = queue.popleft()

        asset = await client.get_entity(urn)
        if asset is None:
            continue
        asset.depth = depth
        found[urn] = asset

        if depth >= max_depth:
            continue

        for child in await client.get_downstream_lineage(urn, max_depth=1):
            if child.urn in queued:
                continue
            queued.add(child.urn)
            queue.append((child.urn, depth + 1))

    return sorted(found.values(), key=lambda a: (a.depth, a.urn))
