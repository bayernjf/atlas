"""P2-1 guard: the main.py router split must not change the HTTP surface.

The snapshot in tests/api_surface_snapshot.json was generated from the
pre-split single-file app, then the same extraction runs against the split
app. Both must produce an identical (method, path, handler) set and an
identical canonical OpenAPI document hash, so a handler that silently loses
a route, a prefix or a dependency shows up here instead of in production.

Regenerate with ``uv run python scripts/api_surface_snapshot.py`` only for
deliberate route changes.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Iterator

from fastapi.routing import _IncludedRouter
from starlette.routing import BaseRoute

from atlas.api.main import app

SNAPSHOT = Path(__file__).with_name('api_surface_snapshot.json')


def _flatten(routes: list[BaseRoute]) -> Iterator[tuple[str, list[str], str]]:
    for route in routes:
        if isinstance(route, _IncludedRouter):
            for ctx in route.effective_route_contexts():
                yield ctx.path, sorted(ctx.original_route.methods or []), ctx.original_route.name
        else:
            path = getattr(route, 'path', None)
            if path:
                yield path, sorted(getattr(route, 'methods', None) or []), getattr(route, 'name', '')


def _current_routes() -> list[list[str]]:
    routes: list[list[str]] = []
    for path, methods, name in _flatten(app.routes):
        for method in methods:
            if method in ('HEAD', 'OPTIONS'):
                continue
            routes.append([method, path, name])
    return sorted(routes)


def _openapi_hash() -> str:
    canonical = json.dumps(app.openapi(), sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def test_route_surface_matches_snapshot() -> None:
    snapshot = json.loads(SNAPSHOT.read_text(encoding='utf-8'))
    assert len(_current_routes()) == snapshot['route_count']
    assert _current_routes() == snapshot['routes']


def test_openapi_document_matches_snapshot_hash() -> None:
    snapshot = json.loads(SNAPSHOT.read_text(encoding='utf-8'))
    assert len(app.openapi()['paths']) == snapshot['openapi_path_count']
    assert _openapi_hash() == snapshot['openapi_sha256']
