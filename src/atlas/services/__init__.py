"""Service layer extracted from the FastAPI route module (Tier0 review P2).

Functions here are intentionally free of FastAPI request/response objects:
routes do parameter extraction and serialization, services hold the logic.
HTTP-shaped failures still use HTTPException for now (the route contract,
including 422 detail shapes, must not change); revisit if non-HTTP callers
appear.
"""
