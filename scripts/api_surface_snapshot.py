"""Regenerate the API surface snapshot used by tests/test_api_surface_snapshot.py.

P2-1 split atlas/api/main.py into per-domain routers. The split must not change
the reachable HTTP surface, so the snapshot records every (method, path, handler)
triple plus a canonical hash of the whole OpenAPI document. Run this only when a
route is intentionally added/renamed/removed, and mention it in the review.

    uv run python scripts/api_surface_snapshot.py
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'src'))

from fastapi.routing import _IncludedRouter  # noqa: E402

from atlas.api.main import app  # noqa: E402

SNAPSHOT = ROOT / 'tests' / 'api_surface_snapshot.json'


def flatten(routes):
    for route in routes:
        if isinstance(route, _IncludedRouter):
            for ctx in route.effective_route_contexts():
                yield ctx.path, sorted(ctx.original_route.methods or []), ctx.original_route.name
        else:
            path = getattr(route, 'path', None)
            if path:
                yield path, sorted(getattr(route, 'methods', None) or []), getattr(route, 'name', '')


def main() -> None:
    routes = []
    for path, methods, name in flatten(app.routes):
        for method in methods:
            if method in ('HEAD', 'OPTIONS'):
                continue
            routes.append([method, path, name])
    routes.sort()
    openapi = app.openapi()
    canonical = json.dumps(openapi, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    snapshot = {
        'route_count': len(routes),
        'routes': routes,
        'openapi_sha256': hashlib.sha256(canonical.encode()).hexdigest(),
        'openapi_path_count': len(openapi['paths']),
    }
    SNAPSHOT.write_text(json.dumps(snapshot, indent=1, ensure_ascii=False) + '\n', encoding='utf-8')
    print(f'wrote {SNAPSHOT} ({len(routes)} routes, {len(openapi["paths"])} paths)')


if __name__ == '__main__':
    main()
