# MemoPilot

**Rule-Aware, Local-Memory, Cost-Governed AI Development Agent Extension for VS Code/Cursor**

MemoPilot is a production-ready AI coding governance extension that combines local project memory, rule and skill enforcement, context-pack generation, cost-aware model routing, patch approval, and validation — helping developers use AI accurately and economically inside VS Code/Cursor.

## Core Concept

Before MemoPilot sends a single token to AI, it has already:

1. Indexed the project and built local memory from source code
2. Loaded and resolved all global and project rules
3. Identified applicable skills for the current task
4. Selected only relevant files, symbols, and memory items
5. Classified the task type and risk level without an LLM call
6. Estimated the token cost and selected the cheapest capable model
7. Redacted all detected secrets from the context
8. Presented the developer with a complete, inspectable context pack

After AI generates a response, MemoPilot requires approval before applying any patch, runs validation tools, and proposes memory updates held at low trust until developer approval.

## Architecture

```
VS Code/Cursor Extension (TypeScript)
  └── HTTP (HMAC token auth, dynamic port)
        └── Python Agent Backend (FastAPI, 127.0.0.1 only)
              └── SQLite + FTS5 + sqlite-vec (local memory)
```

```
packages/
  extension/   — VS Code extension (TypeScript, esbuild)
  agent/       — Python backend (FastAPI local service)
docs/          — Product documentation
```

## Features by Version

### MVP (Phases 1–10)

| # | Feature | Phase |
|---|---------|-------|
| 1 | VS Code extension with activity bar, webview panel, commands | 1 |
| 2 | Python FastAPI backend with local-only binding | 2 |
| 3 | HMAC token auth, dynamic port via lockfile | 2 |
| 4 | SQLite database with WAL mode, versioned migrations | 2 |
| 5 | Workspace `.memopilot/` initialization | 2 |
| 6 | Project scanner (file indexing, `.gitignore` respect) | 3 |
| 7 | Python symbol extraction via AST | 3 |
| 8 | Stale file detection via content hashing | 3 |
| 9 | Rule loading (global + workspace + `.cursor/rules` + `copilot-instructions.md`) | 4 |
| 10 | Skill loading from YAML with matching logic | 4 |
| 11 | Rule precedence resolution and conflict detection | 4 |
| 12 | SQLite memory store with trust levels (1–5) | 5 |
| 13 | FTS5 keyword/identifier search | 5 |
| 14 | sqlite-vec semantic vector search | 5 |
| 15 | AST-based file and symbol summaries (no LLM) | 6 |
| 16 | Context pack builder with task classifier (<50ms, no LLM) | 7 |
| 17 | Explainable inclusion/exclusion reasoning | 7 |
| 18 | Token estimation (tiktoken for OpenAI, char/4 for others) | 7 |
| 19 | Model router (local → cheap cloud → frontier) | 8 |
| 20 | Provider adapters (Ollama, LM Studio, OpenAI, Anthropic, Azure) | 8 |
| 21 | Cost estimator (pre-call) | 8 |
| 22 | Patch generation in unified diff format | 9 |
| 23 | Diff preview via VS Code diff editor API | 9 |
| 24 | Approval gate (no silent patching) | 9 |
| 25 | Validation runner (pytest, ruff, mypy) | 10 |

### v1 Production (Phases 11–15)

| Feature | Phase |
|---------|-------|
| Cost Guard with budget tracking and savings reports | 11 |
| Response caching (SHA-256 context pack hash) | 11 |
| MCP integration (Azure DevOps, database) | 12 |
| Agentic tool-call loop (capped at 5 iterations) | 12 |
| DB write blocking, credential redaction | 12 |
| Production hardening, VSIX packaging, error recovery | 13 |
| Secret redaction (`detect-secrets`) | 13 |
| Workspace Profile (auto-detected, YAML) | 14 |
| Memory Manager UI (filters, approve/edit/delete) | 14 |
| Privacy Boundary Dashboard | 14 |
| Human-in-the-loop memory updates | 14 |
| Agent modes (Ask, Plan, Context Pack, Patch, Test, Review, Autofix, Investigate) | 14 |
| Investigation Mode with Evidence Board | 15 |
| Evidence source classification and trust levels | 15 |
| Investigation Context Pack | 15 |
| Context Pack Templates | 17 |
| Patch Risk Classifier (deterministic) | 17 |
| Rule Compliance Score | 17 |
| Provider Capability Matrix | 17 |
| AI Call Replay / Reproduce Mode | 17 |

### v1.5

| Feature |
|---------|
| Context Pack Diffing |
| Memory Backup / Restore |
| Skill Store with version tracking |
| PDF extraction (pdfplumber) |
| Excel extraction (openpyxl) |
| Evidence Source Classifier |
| Tool and Skill Selection Optimizer |
| Model Budget Profiles |

### v2

| Feature |
|---------|
| Image/screenshot analysis (vision model) |
| Team Policy Packs |
| Local Agent Flow Builder |
| Multi-language Skill Marketplace |
| Team-Shared Memory Server |
| Multi-workspace support v2 |
| Word/PowerPoint ingestion |

### Feature Refinement (v2.3)

| Feature |
|---------|
| Budget-aware context pack allocation with per-tier token caps and roll-forward |
| Tiered approval gate (LOW/MEDIUM/HIGH/CRITICAL) with scroll gate and type-to-confirm |
| Memory Manager bulk actions with usage signals and ranked suggestions |
| Outcome-based model routing with failure history escalation |
| Pre-patch validation baseline (isolate new vs pre-existing failures) |
| Graduated cost guard (80% warning → 90% frontier approval → 100% block) |
| Failure categorisation with template-driven hints |
| Per-task cost feedback and savings framing vs frontier baseline |

## Current Implementation Status

**Completed:** Phases 1–29 (All phases through v2 + Feature Refinement) — Full Remediation + Refinement

- **Schema Remediation (26 issues resolved):** Lockfile format with schema/api version, FTS5 sync triggers, governance migration (memory_class, memory_status, visibility_scope, reusable, review_required), trust level inverted (5=best), supersedes_id removed in favor of memory_relations, schema constraints, snapshots folder spec
- **Workflow Correctness:** Patch apply via `git apply --check` with snapshot rollback, response cache quality filter (success-only, disabled for critical tasks), investigation sessions (pre-task evidence), 8 investigation API endpoints, task classifier two-pass priority (file type > directory), workspace profile YAML as source of truth, per-command validation timeouts, MCP per-context caps (pre_fetch=8, patch=5, investigation=12)
- **Governance Hardening (Phase 18A):** Memory recall with UsePolicy and VisibilityScope filtering, write-back safety filter (blocks secrets, full diffs, raw transcripts), memory review queue, recall trace recording, retention policy enforcement (90/180 day + row caps), memory status lifecycle validation
- **v1.5 Features:** Skill Store with versioning and conflict detection, Context Pack Diffing, Memory Backup/Restore, PDF/Excel/CSV extraction, Evidence Source Classifier (deterministic, <100ms), Tool/Skill Selection Optimizer, Model Budget Profiles (strict_local, monthly cap)
- **v2 Features:** Image/screenshot analysis (LLaVA local + OCR), Team Policy Packs with precedence enforcement, Local Agent Flow Builder with YAML validation and approval gates, Multi-workspace support (isolated per-repo), Code Review Memory Mode (Phase 18B), Word/PowerPoint ingestion
- **Feature Refinement (Phases 24–29):** Budget-aware context packs with per-tier token caps, tiered approval gate (scroll gate + type-to-confirm for critical patches), memory manager bulk actions with usage signals and ranked suggestions, outcome-based model routing with cost comparison, validation baseline diffing with auto-retry and failure categorisation, graduated cost guard with status bar integration and savings reporting
- Full UI Implementation (17 views, zero placeholders)
- 171 tests passing, 0 lint errors

### UI Implementation (Latest — June 2026)

Full end-to-end task flow UI now covers all 17 target scenario views:

| Category | Views Delivered |
|----------|---------------|
| **Core Flow** | Task Entry, Context Pack Preview, Model Routing, Patch Preview, Approval Gate, Validation |
| **Governance** | Rules & Skills, Cost Guard, Privacy Dashboard, Provider Matrix |
| **History & Cost** | Task History, Cost Dashboard |
| **External** | MCP Tools, Evidence Board |
| **Management** | Memory Manager, Workspace Profile, Workspace Status |

New architecture additions:
- `TaskFlowController` state machine (analyze → context → route → patch → approve → validate)
- `MemoPilotPanelBase` abstract class (CSP nonce, theme vars, message bridge)
- 5 new webview panels + 5 new tree providers (replacing all placeholders)
- 9 new backend API endpoints with 38 dedicated tests
- Zero remaining placeholder views

Key principle: **Developer approval is mandatory** — the TaskFlowController always stops at the approval gate before any code is applied.

## v2 Implementation Status

All v2 waves are implemented:

| Wave | Scope | Status |
|---|---|---|
| 1 | Image/screenshot analysis (LLaVA + OCR) | ✅ Complete |
| 2 | Team Policy Packs + Local Agent Flow Builder | ✅ Complete |
| 3 | Code Review Memory Mode (Phase 18B) | ✅ Complete |
| 4 | Multi-workspace support + Word/PowerPoint ingestion | ✅ Complete |

### New Backend Modules (Remediation Sprint — June 2026)

| Module | Purpose |
|--------|---------|
| `patcher.py` | git apply with snapshot-based rollback |
| `retention.py` | Trace table retention enforcement |
| `memory_recall.py` | Recall with UsePolicy + visibility filtering |
| `memory_governance.py` | Memory status lifecycle enforcement |
| `watcher.py` | File watcher (watchdog, 1500ms debounce) |
| `backup.py` | Memory backup/restore with FTS rebuild |
| `tool_selector.py` | Pre-pack tool filtering by task type |
| `document_ingestion.py` | PDF, Excel, CSV, Word, PowerPoint |
| `image_analysis.py` | Vision analysis (LLaVA/OCR/cloud) |
| `code_review_memory.py` | Review lesson extraction + write-back |
| `endpoint_registry.py` | API implementation status register |
| `validation_runner.py` | Per-command timeouts |

## Development

### Prerequisites

- Node.js >= 18, pnpm
- Python >= 3.11, uv

### Extension

```bash
pnpm install
pnpm ext:build      # Build extension
pnpm ext:watch      # Watch mode
pnpm ext:package    # Produce .vsix
```

### Backend

```bash
cd packages/agent
uv sync
uv run pytest
```

### Install Extension

```bash
code --install-extension packages/extension/memopilot-1.0.0.vsix
```

## Documentation

- [Master Product & Implementation Reference](docs/master-reference.md)

## License

MIT
