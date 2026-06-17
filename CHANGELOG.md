# MemoPilot Changelog

All notable changes to the MemoPilot project will be documented in this file.

## [v1.0.1] - 2026-06-17

### Fixed

#### Extension Flow Controller Hardening
- **Duplicate Patch Generation**: Removed auto-chaining between `buildContext()`, `routeModel()`, and `generatePatch()`. Each method now performs exactly one operation. Added guards to prevent overwriting existing patches.
- **Partial File Application**: Implemented transactional file apply with snapshot capture and automatic rollback on failure. If any file write fails, all prior writes are reverted to maintain consistency.
- **Rollback Error Shadowing**: Errors during rollback are now logged to console without masking the original write error. Original error is preserved and re-thrown to the user.
- **Mode String Resolution**: Added `resolvedMode()` helper to normalize empty "Auto-detect" selections to valid 'auto' enum value.
- **Fallback Path Inconsistency**: Aligned direct context pack call (fallback path) to use `task_description` field consistently with main workflow path. Added matching logging to both paths.

#### Backend Error Handling
- **Missing Context Pack File Handling**: Graceful HTTP 404 response when replaying AI calls with missing context pack files, instead of HTTP 500 error. Added explicit file existence check in `provider_registry.replay_ai_call()`.

#### API Contract Enrichment
- **BudgetCheck Response Contract**: Expanded `BudgetCheck` response model to include optional `reason` and `status` fields. Added `_to_model_route_budget_check_response()` mapper to enrich budget check responses from internal cost guard dataclass.
- **Duplicated Response Mapping**: Extracted `_workspace_index_response_kwargs()` helper function to eliminate duplicate field mapping code in `index_workspace()` and `rebuild_memory()` endpoints.

### Added

- **Test Coverage**: New regression tests added:
  - `test_model_route_basic`: Validates `budget_check.reason` and `budget_check.status` fields in route model response
  - `test_replay_ai_call_returns_404_when_context_pack_file_is_missing`: Validates graceful 404 for missing context pack files during replay

### Changed

- **Extension Version**: v1.0.0 → v1.0.1
- **Schema Version**: Remains v21 (no migration required)
- **Backend Test Suite**: 11 tests passing (including 2 new regression tests)

### Implementation Details

**Files Modified:**
- `packages/extension/src/controllers/TaskFlowController.ts` (removed auto-chaining, added transactional apply with rollback, added mode normalization)
- `packages/extension/src/panels/TaskEntryPanel.ts` (aligned fallback path, added logging parity)
- `packages/agent/agent/provider_registry.py` (added file existence check before read)
- `packages/agent/agent/api.py` (enriched BudgetCheck contract, added response mapping helper)
- `packages/agent/tests/test_model_route.py` (added budget_check assertions)
- `packages/agent/tests/test_group6_waveb_core.py` (added missing context pack 404 test)

**Validation:**
- Extension: `tsc --noEmit` passes (clean, no errors)
- Extension: esbuild produces 187.7kb output
- Backend: `py -m pytest` passes (11 tests, 0 failures)
- All modified files pass type and syntax checking

---

## [v1.0.0] - 2026-06-16

### Added

- Initial public release of MemoPilot extension (v1.0.0)
- Full implementation of task workflow with TaskFlowController state machine
- Context pack generation with rule enforcement
- Cost guard with budget tracking and model routing
- Integration with local Python agent backend
- VS Code webview-based UI with TaskEntryPanel
- Approval gate for patch application
- Validation runner for pytest, ruff, mypy, and build commands
- Memory system with SQLite + FTS5 storage
- Privacy boundary dashboard for cloud vs. local tracking
- Evidence board for investigation mode
- MCP integration support (Azure DevOps, database schema)

### Components

**Extension (TypeScript):**
- TaskFlowController: Pipeline orchestration (analyze → context → route → patch → approve → validate → apply)
- TaskEntryPanel: New Task webview with card-based workflow stepper
- BackendClient: HTTP API client with HMAC token authentication
- Sidebar views: Memory Manager, Rules, Context Pack Preview, Cost Dashboard
- Diff Preview panel: Shows patches before approval

**Backend (Python FastAPI):**
- Workspace indexing with file/symbol extraction
- Rule and skill resolution
- Context pack generation with token estimation
- Model routing across provider tiers (local, cheap cloud, frontier)
- Patch generation coordination (mocked, awaiting real LLM integration)
- Validation runner for workspace tools
- Memory system with retrieval and governance
- Cost tracking with per-call logging

**Database:**
- SQLite with FTS5 for keyword/identifier search
- Schema v21 with 15+ tables covering tasks, AI calls, patches, validation, rules, skills, evidence, and memory
- WAL mode for concurrent reads during indexing

**Testing:**
- 286+ pytest tests covering all backend services
- Test database with in-memory fixtures
- Auth, workspace init, context build, model route, patch assessment coverage

---

## Phase History

### Phase 32 (Workflow Intelligence + UI Redesign)
- Plan Mode: Multi-step plan storage, recall, and compliance checking
- Autofix wiring: Classification of safe vs. unsafe validation failures
- Structured rejection learning: Category-based memory write-back
- Investigation → Plan loop: Evidence-aware root cause analysis
- Task pattern detection: Recurring failure patterns and similar-task recall
- Smart memory timing: Trusted auto-confirm with task_run_id gate
- TaskEntryPanel redesign: 7-step stepper with guardrails and AI boundary visualization

---

## Known Limitations (v1.0.1)

- **LLM Integration**: Patch generation returns mock diffs; no real LLM calls yet
- **Provider Activation**: Provider registry scaffolded but no actual API clients implemented
- **Multi-Workspace**: Single active workspace only; multi-workspace support deferred to v2
- **Vector Search**: sqlite-vec schema prepared but embedding generation deferred to later phase
- **Image Analysis**: Ollama/LLaVA integration partial; disconnected from main task flow
- **MCP Tool Execution**: Azure DevOps fetch implemented; database write operations blocked for safety

---

## Contributing

All code follows TypeScript, Python, and SQL style guides. See [CONTRIBUTING.md](CONTRIBUTING.md) (if present) or the project's coding standards in [master-reference.md](docs/master-reference.md) Section 3 (Guiding Principles).

### Build and Test

**Extension:**
```bash
pnpm install
pnpm --filter memopilot build
```

**Backend:**
```bash
cd packages/agent
python -m pytest
```

---

## Support

For issues, feature requests, or contributions, please refer to the project repository.
