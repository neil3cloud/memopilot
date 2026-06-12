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

## Current Implementation Status

**Completed:** Phases 1–17 (MVP + v1 production + v1.5)

- Workspace indexing delivered (`/v1/workspace/index`, stale-file handling, symbol extraction, rebuild-memory flow)
- Cost guard and cache delivered (budget checks, task run + usage ledger, savings report, response cache)
- Agentic safety delivered (credential redaction, DB write blocker checks, MCP loop capped to 5 iterations)
- Hardening delivered (provider resilience test call, DB recovery path, `detect-secrets` integration, VSIX packaging hardening)
- Governance UX delivered (Workspace Profile, Memory Manager actions, Privacy Dashboard, agent modes)
- Extension commands/views include Evidence Board and investigation actions
- Backend startup false-timeout fix delivered in extension (`BackendManager` now accepts log-discovered port + health check fallback when lockfile detection lags)
- Investigation mode delivered:
  - Evidence attach endpoint (`/v1/investigation/evidence/attach`) with source classification and trust scoring
  - Evidence board endpoint (`/v1/investigation/evidence`) with extraction status and redaction metadata
  - Investigation run endpoint (`/v1/investigation/run`) generating context packs with extracted/redacted findings, impacted file discovery, related test discovery, and missing test coverage detection
  - PDF/Excel artifact extraction, Excel column mapping, and dedicated evidence classification
- v1.5 delivered:
  - Context Pack diffing and version history
  - Skill Store versioning and conflict detection
  - Memory backup/restore
  - Tool/skill selection optimizer
  - Model budget profiles

## v2 Implementation Plan

| Wave | Scope | Order |
|---|---|---|
| 1 | Image/screenshot analysis | First |
| 2 | Team Policy Packs + Local Agent Flow Builder | Second |
| 3 | Deferred: Multi-language Skill Marketplace + Team-Shared Memory Server | Deferred |
| 4 | Multi-workspace support v2 + Word/PowerPoint ingestion | Next active |

Wave 3 is deferred for now; next active V2 scope is Wave 4 after Wave 2.

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
code --install-extension packages/extension/memopilot-0.1.0.vsix
```

## Documentation

- [Master Product & Implementation Reference](docs/master-reference.md)

## License

MIT
