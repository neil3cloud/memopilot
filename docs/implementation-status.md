# MemoPilot Implementation Status

> Last updated: 2026-06-17 | Schema v21 | Extension v1.0.1 (Hardening)

## Overview

MemoPilot's architecture is fully scaffolded end-to-end — the UI workflow, backend APIs, data models, and pipeline orchestration are all wired and functional. However, **no real LLM integration exists yet**. All AI-dependent steps return mock/deterministic data.

---

## Layer Status

| Layer | Component | Status | Details |
|-------|-----------|--------|---------|
| **UI — Extension** | TaskEntryPanel (New Task webview) | ✅ Implemented | Card-based, stepper, step-aware buttons, approval flow |
| **UI — Extension** | Sidebar views (Memory, Rules, Context Pack, etc.) | ✅ Implemented | TreeView providers for all sidebar items |
| **UI — Extension** | Diff Preview panel | ✅ Implemented | Shows patch diffs before approval |
| **UI — Extension** | Cost Dashboard | ✅ Implemented | Reads from backend cost tracking |
| **Backend — API** | Task Analysis (`/v1/task/analyze`) | ⚠️ Heuristic only | Keyword-based intent detection, no LLM |
| **Backend — API** | Context Build (`/v1/context/build`) | ✅ Implemented | Reads workspace files, counts tokens, applies rules |
| **Backend — API** | Model Routing (`/v1/model/route`) | ⚠️ Mock routing | Selects model from static pool based on token count; never calls it |
| **Backend — API** | Patch Generation (`/v1/task/generate-patch`) | ❌ Mock only | Returns deterministic placeholder diffs — **no LLM call** |
| **Backend — API** | Validation (`/v1/task/validate`) | ✅ Implemented | Runs real commands (compileall, ruff, pytest) |
| **Backend — API** | Patch Apply | ❌ Not implemented | Controller writes files but backend has no apply endpoint |
| **Backend — Core** | Workspace Indexing | ✅ Implemented | File indexing, git history, symbol extraction |
| **Backend — Core** | Memory System | ✅ Implemented | Store/recall/forget with recall traces |
| **Backend — Core** | Policy Packs (Rules) | ✅ Implemented | YAML-based rules, enforcement, evaluation |
| **Backend — Core** | Cost Guard | ✅ Implemented | Budget tracking, per-call cost recording, limits |
| **Backend — Core** | Provider Registry | ⚠️ Scaffolded | Lists providers (ollama, openai, anthropic) but no API client |
| **Backend — Core** | Skill Loader | ✅ Implemented | Loads skill definitions from workspace |
| **Backend — Core** | Image Analysis | ⚠️ Partial | Ollama/LLaVA integration exists but disconnected from task flow |
| **Backend — Core** | Privacy Dashboard | ✅ Implemented | Tracks cloud calls, local/cloud classification |
| **Backend — Core** | Evidence Board | ✅ Implemented | Stores and queries evidence items |
| **Backend — Core** | Workspace Profile | ✅ Implemented | Language detection, framework inference, export |
| **Pipeline — Controller** | TaskFlowController (extension) | ✅ Hardened | Non-chaining steps, mode normalization, duplicate patch prevention, transactional file apply |
| **Pipeline — Controller** | Step-aware UI transitions | ✅ Implemented | Buttons show/hide per active step |
| **Pipeline — Controller** | File apply with rollback | ✅ Hardened | Snapshots files before apply; rolls back earlier writes on failure; preserves root error |
| **Backend — API** | Replay AI Call (`/v1/ai/replay/{ai_call_id}`) | ✅ Hardened | Graceful 404 for missing context pack files; no 500 on deleted packs |
| **Testing** | Backend unit tests | ✅ 286+ tests passing | Comprehensive coverage of all backend services |
| **Testing** | Extension type-checking | ✅ Clean | tsc --noEmit passes |

---

## What Works Today (Without AI)

1. **Full UI workflow** — user enters a task, sees analysis, clicks through steps
2. **Local workspace analysis** — file indexing, rules, memory, skill loading
3. **Context packing** — collects relevant files, counts tokens, estimates cost
4. **Model selection logic** — picks best model for task type/size (but never calls it)
5. **Validation** — runs real linters/compilers on workspace
6. **Cost tracking** — records mock costs, enforces budgets
7. **Approval gate** — requires explicit user action before applying patches
8. **Patch apply with rollback** — snapshots files before write; rolls back on failure; preserves root error
9. **Replay error handling** — graceful 404 when context pack files are missing
10. **Flow isolation** — buildContext, routeModel, generatePatch no longer auto-chain; panel controls progression

---

## What Needs Real LLM Integration

### Priority 1 — Patch Generation (Critical)
- **File:** `packages/agent/agent/api.py` → `generate_patch()` (line ~3135)
- **Current:** Returns mock diffs with `# AI-generated change (hash)` comments
- **Needed:** Call actual LLM with context pack + task description → get real code changes
- **Provider options:** OpenAI (gpt-4o), Anthropic (claude-sonnet), Ollama (codellama/deepseek-coder)

### Priority 2 — Task Analysis Enhancement
- **File:** `packages/agent/agent/api.py` → task analyze endpoint
- **Current:** Keyword matching ("add" → create, "fix" → bug, etc.)
- **Needed:** LLM-powered intent classification, file identification, complexity estimation
- **Note:** Could remain local-first with a small model (e.g., Ollama + phi-3)

### Priority 3 — Provider Registry Activation
- **File:** `packages/agent/agent/provider_registry.py`
- **Current:** Returns static model metadata
- **Needed:** Actual HTTP clients for each provider (OpenAI, Anthropic, Ollama REST)
- **Design:** Already has provider abstraction — needs `call()` method per provider

### Priority 4 — Model Routing Intelligence
- **File:** `packages/agent/agent/api.py` → model route endpoint
- **Current:** Picks model by token count threshold
- **Needed:** Consider task complexity, privacy constraints, cost budget, provider availability

---

## Architecture Decisions Already Made

- **Local-first:** Analyze before calling AI; cost guard prevents accidental spend
- **Provider-agnostic:** Model routing abstraction supports any backend
- **Privacy-aware:** Local vs cloud classification; secret redaction before sending context
- **Approval-gated:** No file changes without explicit user approval
- **Cost-governed:** Budget limits, per-call tracking, warnings before threshold

---

## File Map for Next Session

```
packages/agent/agent/
├── api.py                    # All endpoints — generate_patch needs real LLM
├── provider_registry.py      # Provider definitions — needs API clients
├── cost_guard.py             # Budget enforcement — working, tracks mock calls
├── image_analysis.py         # Ollama/LLaVA — partial, disconnected
├── context_builder.py        # Context packing — working
└── workspace_init.py         # Workspace setup — working

packages/extension/src/
├── controllers/TaskFlowController.ts  # Pipeline state machine — working
├── panels/TaskEntryPanel.ts           # New Task UI — working
└── BackendClient.ts                   # API client — all endpoints defined
```

---

## Code Review Hardening (2026-06-17)

### Extension TaskFlowController (packages/extension/src/controllers/TaskFlowController.ts)
- ✅ Removed hidden auto-chaining: buildContext/routeModel/generatePatch are now single-step, non-chaining methods
- ✅ Added mode normalization: resolvedMode() ensures analysis.suggested_mode is preferred, with fallback to 'auto'
- ✅ Duplicate patch prevention: generatePatch() returns early if already in awaiting_approval stage with existing patch
- ✅ Transactional file apply: captureSnapshot() saves original file contents; rollbackAppliedChanges() restores on failure
- ✅ Error preservation: rollback errors are logged but not thrown; original write error is preserved and re-thrown

### Backend Replay Hardening (packages/agent/agent/provider_registry.py)
- ✅ Missing context pack handling: raises ValueError if pack_path does not exist on disk
- ✅ API graceful degradation: HTTPException(404) with "Context pack not available" instead of 500
- ✅ Prevents silent data loss: developer sees which AI call's context is unavailable for replay

### API Contract Enrichment (packages/agent/agent/api.py)
- ✅ BudgetCheck response expanded: now includes optional reason and status fields
- ✅ Backward compatible: reason and status default to None for non-routing contexts
- ✅ Deduplication: _workspace_index_response_kwargs() shared mapper for index_workspace and rebuild_memory endpoints
- ✅ Prevents silent divergence: future fields added to WorkspaceIndexResult auto-sync to both responses

### TaskEntryPanel Consistency (packages/extension/src/panels/TaskEntryPanel.ts)
- ✅ Fallback path alignment: direct buildContextPack call uses task_description (not intent_summary)
- ✅ Logging parity: fallback path logs the same fields as the main flow (mode, files, summary)
- ✅ Explicit progression: runPatchGeneration() checks state and only calls needed steps

### Test Coverage Added
- ✅ test_model_route_basic: verifies budget_check.reason and budget_check.status in response shape
- ✅ test_replay_ai_call_returns_404_when_context_pack_file_is_missing: validates graceful 404 for missing packs

---

## Recommended Next Steps

1. **Add a provider client layer** — create `packages/agent/agent/llm_client.py` with async methods for OpenAI/Anthropic/Ollama
2. **Replace mock in `generate_patch()`** — call the selected model with the context pack
3. **Add API key configuration** — read from `.memopilot/config.yaml` or environment variables
4. **Add streaming support** — for long-running LLM calls, stream tokens back to the UI
5. **Add retry/fallback** — if primary provider fails, fall back to secondary
