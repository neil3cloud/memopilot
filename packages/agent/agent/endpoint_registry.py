"""Manual endpoint implementation status register."""

from __future__ import annotations

ENDPOINT_STATUS: dict[str, str] = {
    # Core
    "GET /v1/health": "real",
    "POST /v1/workspace/init": "real",
    "POST /v1/workspace/index": "real",
    "POST /v1/task/analyze": "real",
    "POST /v1/context-pack/generate": "real",
    "POST /v1/model/route": "real",
    "POST /v1/task/generate-patch": "real",
    "POST /v1/task/apply-patch": "real",
    "POST /v1/task/validate": "real",
    "POST /v1/task/review-applied-patch": "real",
    "GET /v1/task/history": "real",
    "GET /v1/rules/active": "real",
    "POST /v1/policies/load": "real",
    "GET /v1/policies/active": "real",
    "GET /v1/cost/dashboard": "real",
    "GET /v1/mcp/tools": "real",
    "POST /v1/session/ingest": "real",
    # Memory
    "POST /v1/memory/recall": "real",
    "POST /v1/memory/writeback": "real",
    "POST /v1/memory/recall/{request_id}/usage": "stub",
    "GET /v1/memory/items": "real",
    "GET /v1/memory/unused": "real",
    "GET /v1/memory/review": "real",
    "POST /v1/memory/bulk-approve": "real",
    "POST /v1/memory/bulk-reject": "real",
    "POST /v1/memory/bulk-delete": "real",
    "PATCH /v1/memory/items/{memory_id}/review": "real",
    "POST /v1/reviews/evidence": "real",
    "POST /v1/reviews/approve-lesson": "real",
    "GET /v1/memory/recall-traces/{request_id}": "stub",
    # Investigation
    "POST /v1/investigation/start": "real",
    "GET /v1/investigation/{session_id}": "real",
    "POST /v1/investigation/{session_id}/evidence": "real",
    "DELETE /v1/investigation/{session_id}/evidence/{evidence_id}": "real",
    "POST /v1/investigation/{session_id}/run": "stub",
    "GET /v1/investigation/{session_id}/findings": "stub",
    "POST /v1/investigation/{session_id}/context-pack": "stub",
    "POST /v1/investigation/{session_id}/transition-to-patch": "real",
    "POST /v1/evidence/extract-docx": "real",
    "POST /v1/evidence/extract-pptx": "real",
    "POST /v1/evidence/analyze-image": "real",
    # Workspace
    "GET /v1/workspace/profile": "real",
    "POST /v1/workspace/profile/rebuild": "real",
    "GET /v1/workspace/memory/status": "stub",
    # Cost
    "GET /v1/cost/budget-status": "real",
    "GET /v1/cost/budget/status": "real",
    "POST /v1/cost/budget-check": "real",
    # Context packs
    "GET /v1/context-pack/diff": "real",
    # Tool mode
    "POST /v1/tool-mode/approve-caller": "real",
    "POST /v1/tool-mode/block-caller": "real",
    "GET /v1/tool-mode/session-summary": "real",
}
