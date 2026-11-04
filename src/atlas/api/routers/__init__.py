"""Per-domain APIRouter modules split out of atlas/api/main.py (P2-1).

main.py keeps the app assembly, middleware, lifespan and exception handling;
each module here owns one business domain. Handlers are imported back from
main so the split changes file layout only, not behaviour.
"""
