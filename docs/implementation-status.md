# MemoPilot Implementation Status

> Last updated: 2026-06-27 | Schema v22 | Extension v1.0.1

## Overview

MemoPilot's broad platform architecture remains intact, but the default product surface has now shifted to **retrieval-first context assembly** for Copilot Chat and Cursor. The extension defaults to search-and-assemble project context, while the older task pipeline — analyze → context → route → patch → approve → validate → apply — remains available only as a legacy mode.

As of this update:

- Retrieval-first MCP tools are live: `memopilot-search`, `memopilot-symbols`, `memopilot-memory`, `memopilot-profile`
- A dedicated assembled-context API is live at `/v1/context/assemble`
- The default extension command now opens context search rather than the task-flow UI
- Legacy patch/task flow remains implemented behind `memopilot.legacyAgentMode`

---

## Layer Status

| Layer | Component | Status | Details |
|-------|-----------|--------|---------|
| **UI — Extension** | Retrieval-first default command | ✅ Implemented | `MemoPilot: Search Project Context` is now the primary entry point |
| **UI — Extension** | TaskEntryPanel (Legacy task webview) | ✅ Implemented | Card-based task-flow UI preserved behind `memopilot.legacyAgentMode` |
| **UI — Extension** | Sidebar views (Memory, Rules, Context Pack, etc.) | ✅ Implemented | TreeView providers for all sidebar items |
| **UI — Extension** | Diff Preview panel | ✅ Implemented | Shows patch diffs before approval |
| **UI — Extension** | Cost Dashboard | ✅ Implemented | Reads from backend cost tracking |
| **Backend — API** | Task Analysis (`/v1/task/analyze`) | ⚠️ Heuristic only | Keyword-based intent detection, no LLM |
| **Backend — API** | Context Build (`/v1/context/build`) | ✅ Implemented | Reads workspace files, counts tokens, applies rules |
| **Backend — API** | Context Assemble (`/v1/context/assemble`) | ✅ Implemented | Returns bounded rendered markdown for retrieval-first callers |
| **Backend — API** | Model Routing (`/v1/model/route`) | ⚠️ Mock routing | Selects model from static pool based on token count; never calls it |
| **Backend — API** | Patch Generation (`/v1/task/generate-patch`) | ✅ Live | Real LLM via Copilot relay + provider fallback chain (host→ollama→anthropic→openai) |
| **Backend — API** | Validation (`/v1/task/validate`) | ✅ Implemented | Runs real commands (compileall, ruff, pytest) |
| **Backend — API** | Patch Apply | ✅ Implemented | `TaskFlowController` writes files via VS Code FS API on approval |
| **Backend — Core** | Workspace Indexing | ✅ Implemented | File indexing, git history, symbol extraction |
| **Backend — Core** | Memory System | ✅ Implemented | Store/recall/forget with recall traces |
| **Backend — Core** | Policy Packs (Rules) | ✅ Implemented | YAML-based rules, enforcement, evaluation |
| **Backend — Core** | Cost Guard | ✅ Implemented | Budget tracking, per-call cost recording, limits |
| **Backend — Core** | Provider Registry | ✅ Activated | Anthropic, OpenAI, Ollama, LM Studio clients live; Copilot relay via `HostModelClient` |
| **Backend — Core** | Skill Loader | ✅ Implemented | Loads skill definitions from workspace |
| **Backend — Core** | Image Analysis | ⚠️ Partial | Ollama/LLaVA integration exists but disconnected from task flow |
| **Backend — Core** | Privacy Dashboard | ✅ Implemented | Tracks cloud calls, local/cloud classification |
| **Backend — Core** | Evidence Board | ✅ Implemented | Stores and queries evidence items |
| **Backend — Core** | Workspace Profile | ✅ Implemented | Language detection, framework inference, export |
| **Pipeline — Controller** | TaskFlowController (extension) | ✅ Hardened | Non-chaining steps, mode normalization, duplicate patch prevention, transactional file apply; Copilot relay wired |
| **Pipeline — Controller** | Step-aware UI transitions | ✅ Implemented | Buttons show/hide per active step |
| **Pipeline — Controller** | File apply with rollback | ✅ Hardened | Snapshots files before apply; rolls back earlier writes on failure; preserves root error |
| **Pipeline — Controller** | GitHub Copilot relay | ✅ Implemented | `HostModelClient` wired into `generatePatch()`; tokens streamed via SSE back to backend |
| **Backend — API** | Replay AI Call (`/v1/ai/replay/{ai_call_id}`) | ✅ Hardened | Graceful 404 for missing context pack files; no 500 on deleted packs |
| **Testing** | Backend unit tests | ✅ 357 tests passing | Comprehensive coverage of all backend services |
| **Testing** | Extension type-checking | ✅ Clean | tsc --noEmit passes |
| **CI/CD** | GitHub Actions CI | ✅ Full Coverage | Runs backend tests (3.11/3.12/3.13), lint, and extension build on all PRs |
| **Release** | VSIX Build & Release | ✅ Automated | GitHub Actions releases tagged versions to GitHub Release + Marketplace (optional) |
| **Security** | Secret Management | ✅ Documented | API keys in `~/.memopilot/config.yaml`, HMAC token in `agent.lock`, local-first by default |
| **Documentation** | Changelog | ✅ Up to date | CHANGELOG.md tracks all changes by version |
| **Documentation** | Security Policy | ✅ Comprehensive | SECURITY.md covers data handling, authentication, threats, and compliance |

---

## What Works End-to-End (Verified 2026-06-27)

1. **Retrieval-first context assembly** — user can request project context and receive bounded rendered markdown via `/v1/context/assemble`
2. **Retrieval-first MCP contract** — stdio MCP now exposes `memopilot-search`, `memopilot-symbols`, `memopilot-memory`, and `memopilot-profile`
3. **GitHub Copilot integration** — authenticated Copilot models discovered via `vscode.lm.selectChatModels`; tool integration remains active in VS Code
4. **Local workspace analysis** — file indexing, rules, memory, skill loading
5. **Context packing and rendering** — collects relevant files, counts tokens, estimates cost, and renders bounded markdown
6. **Legacy full task pipeline** — analyze→context→route→patch→approve→validate→apply still completes when legacy mode is enabled
7. **Real LLM patch generation** — `generate_patch()` still calls GitHub Copilot (via `HostModelClient` SSE relay), Ollama, Anthropic, or OpenAI in configured fallback order
8. **Validation** — runs real linters/compilers on workspace
9. **Cost tracking** — records per-call costs, enforces budgets
10. **Patch apply with rollback** — snapshots files before write; rolls back on failure; preserves root error

---

## Known Gaps (Upgrade Path Available)

### Task Analysis
- **Current:** Keyword-based intent classification ("add" → create, "fix" → bug, etc.)
- **Upgrade:** Optional LLM-powered intent classification with a small local model (Ollama + phi-3)

### Model Routing Intelligence
- **Current:** Picks model from configured fallback order by token count threshold
- **Upgrade:** Factor in task complexity, privacy constraints, cost budget, provider latency history

### Streaming Token UI
- **Current:** Tokens stream from Copilot back to backend via SSE but are not forwarded to the TaskEntryPanel UI in real-time
- **Upgrade:** Surface `streamingToken` state from `TaskFlowController` in the webview during generation

---

## Architecture Decisions Already Made

- **Local-first:** Analyze before calling AI; cost guard prevents accidental spend
- **Provider-agnostic:** Model routing abstraction supports any backend; Copilot is first in fallback order
- **Privacy-aware:** Local vs cloud classification; secret redaction before sending context
- **Approval-gated:** No file changes without explicit user approval
- **Cost-governed:** Budget limits, per-call tracking, warnings before threshold
- **Copilot relay:** Extension listens for `LLM_REQUEST` SSE events and relays via `vscode.lm` API; no API key needed when authenticated

---

## File Map for Next Session

```
packages/agent/agent/
├── api.py                    # All endpoints — generate_patch now calls real LLM
├── llm_client.py             # LLM client — Anthropic, OpenAI, Ollama, LM Studio adapters
├── provider_registry.py      # Provider definitions — live, seeded with real providers
├── cost_guard.py             # Budget enforcement — working, tracks real call costs
├── image_analysis.py         # Ollama/LLaVA — partial, disconnected
├── context_builder.py        # Context packing — working
└── workspace_init.py         # Workspace setup — working

packages/extension/src/
├── controllers/TaskFlowController.ts  # Pipeline state machine — live, drives full pipeline
├── HostModelClient.ts                 # Copilot relay — wired, streams tokens via SSE
├── panels/TaskEntryPanel.ts           # New Task UI — sequential pipeline steps fixed
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

### Phase 2: Marketplace & Production Hardening

1. **Watchdog & Reliability** (Milestone 1)
   - Backend auto-restart on crash with 3x backoff retry
   - Liveliness probe every 30s to detect hangs
   - Extension test suite expansion (TaskFlow, BackendClient, BackendManager units)

2. **UI Polish** (Milestone 2)
   - Real-time token streaming in TaskEntryPanel (currently shows spinner)
   - Visual feedback during generation with token counter
   - Error recovery flows for network/provider failures

3. **Real MCP Execution** (Milestone 3)
   - Wire `mcp_orchestrator.py` to `mcp_server.py` dispatcher
   - Replace simulated calls with real tool execution
   - MCP test coverage for tool invocations

4. **Intelligence Upgrades** (Milestone 4)
   - **4-A**: LLM-powered task analysis with local model fallback
   - **4-B**: sqlite-vec for semantic memory recall (hybrid FTS5+vector search)
   - **4-C**: Image analysis wired into task flow (cloud vision as optional fallback)

5. **Marketplace Distribution** (Milestone 5 — In Progress)
   - ✅ VSIX automation workflow (GitHub Actions on git tags)
   - ✅ GitHub Release with auto-generated CHANGELOG
   - ✅ Security & secret management documentation
   - ⏳ E2E Copilot verification checklist
   - ⏳ Publish to VS Code Marketplace (requires PAT)

### How to Release

```bash
# 1. Update version in packages/extension/package.json
# 2. Update CHANGELOG.md with new features
# 3. Commit and tag:
git tag v1.0.2
git push origin v1.0.2

# 4. GitHub Actions release.yml will:
#    - Extract version from tag
#    - Build VSIX
#    - Create GitHub Release with VSIX attached
#    - (Optional) Publish to Marketplace if VSCE_PAT secret is configured
```

### VS Code Marketplace Setup (One-Time)

1. Create publisher account at [Visual Studio Marketplace](https://marketplace.visualstudio.com/manage)
2. Generate Personal Access Token (PAT)
3. Add to GitHub Secrets as `VSCE_PAT`
4. Push a tag — release workflow will auto-publish
