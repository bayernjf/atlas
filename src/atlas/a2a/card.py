"""Agent Card 单一事实源（docs/90）。修改能力声明只改本文件。"""

from __future__ import annotations

import os

CARD_VERSION = "0.2.0"

SKILLS: list[dict] = [
    {
        "id": "plan-approval-flow",
        "name": "Plan an approval flow",
        "description": (
            "Plan a fixed OA approval Graph (nodes/data flow/human wait) for a "
            "tenant before any run is created: advisory plan only, no graph "
            "mutation, no run started, no LLM token spend."
        ),
        "tags": ["graph", "approval", "plan-only", "human-gate"],
    },
    {
        "id": "run-graph",
        "name": "Run a saved graph",
        "description": (
            "Run a saved non-interactive Atlas graph under the caller's tenant: "
            "real in-process execution, run_store record, trace/output artifact. "
            "Requires a tenant-bound token; human/wait graphs are rejected."
        ),
        "tags": ["graph", "run", "real-run", "tenant-bound"],
    },
    {
        "id": "diagnose-run",
        "name": "Diagnose a run",
        "description": (
            "Plan a read-only diagnosis of an Atlas run (status/suspended frame/"
            "approval wait): advisory plan of what to inspect, no replay, no "
            "resume, no state change."
        ),
        "tags": ["run", "diagnosis", "plan-only", "read-only"],
    },
]

HANDSHAKE: dict = {
    "version": "1",
    "swornTo": "zeus",
    "domain": "ops-orchestration",
    "dataRealms": ["enterprise"],
    "dataPolicy": "write-task-scope",
    "reportBack": True,
    "escalationPolicy": "auto",
    "sla": {"ackSeconds": 10},
    "notes": (
        "Mixed execution agent: plan skills stay advisory; run-graph starts a real "
        "non-interactive run under a tenant-bound token, writes a run_store record, "
        "and returns trace/output. Human/wait graphs are rejected; approvals stay "
        "in the platform session. Task execution is JSON-RPC at POST /api/a2a/tasks "
        "protected by ATLAS_A2A_TASK_TOKENS=caller:tenant:token,... (Bearer); the "
        "legacy ATLAS_A2A_TASK_TOKEN is plan-only. Task ledger is in-memory per "
        "process (single-replica constraint, docs/62)."
    ),
}


def _public_base_url() -> str:
    return os.environ.get("ATLAS_PUBLIC_BASE_URL", "").strip().rstrip("/")


def build_agent_card() -> dict:
    base = _public_base_url()
    return {
        "name": "atlas",
        "description": (
            "Agent orchestration platform (Harness / Graph / Loop). Plan skills "
            "return advisory candidates; run-graph executes saved non-interactive "
            "graphs under a tenant-bound caller."
        ),
        "url": f"{base}/api/a2a/tasks",
        "version": CARD_VERSION,
        "capabilities": {
            "streaming": True,
            "pushNotifications": False,
            "stateTransitionHistory": True,
        },
        "defaultInputModes": ["application/json"],
        "defaultOutputModes": ["application/json"],
        "skills": SKILLS,
        "authentication": {"schemes": ["bearer"]},
        "preferredTransport": "JSONRPC",
        "x-zeus-handshake": HANDSHAKE,
    }
