# MemoPilot

**Local memory bank and context assembler for VS Code Copilot and Cursor**

MemoPilot is a local-first context system for the workspace currently loaded in VS Code or Cursor. It indexes code, stores governed project memory, assembles bounded retrieval context, and exposes that context to Copilot Chat and Cursor before cloud LLM calls are made.

## Core Concept

Before MemoPilot sends a single token of project context to an external model, it has already:

1. Indexed the project and built local memory from source code
2. Summarized all symbols via LLM (GitHub Copilot by default — no API key needed)
3. Loaded and resolved all global and project rules
4. Identified applicable skills and workspace constraints
5. Selected only relevant files, symbols, and memory items
6. Assembled a bounded, inspectable context block
7. Redacted detected secrets from the context
8. Tracked privacy and tool-call provenance

## Current Surface

- **4 retrieval-first tools** exposed to Copilot Chat and Cursor Chat: `memopilot-search`, `memopilot-symbols`, `memopilot-memory`, `memopilot-profile`
- **Default VS Code command:** `MemoPilot: Search Project Context`
- **Status bar:** opens retrieval-first context search; shows pending file changes between reindex runs

## Architecture

```
VS Code/Cursor Extension (TypeScript)
  ├── Copilot Chat (@memopilot LM tools) — primary interaction surface
  ├── SynthesisHostClient — relays LLM requests from backend to vscode.lm API
  └── HTTP (HMAC token auth, dynamic port)
        └── Python Agent Backend (FastAPI, 127.0.0.1 only)
              └── SQLite + FTS5 + sqlite-vec (local memory)
```

**LLM modes** (switchable at runtime via `MemoPilot: Switch LLM Mode`):

| Mode | Providers | Requires | Config Key |
|------|-----------|----------|------------|
| Copilot (default) | GitHub Copilot via `vscode.lm` | GitHub Copilot subscription | `provider: host` |
| Local | Ollama, LM Studio, generic OpenAI-compatible | Local model running at configured URL | `provider: local\|ollama\|lmstudio` |
| Cloud | Anthropic, OpenAI, Google, OpenRouter | API key in `.memopilot/config.yaml` or env | `provider: anthropic\|openai\|google\|openrouter` |

**Environment Variables** (optional, override YAML config):
- `MEMOPILOT_PROVIDER` — Which provider to use
- `MEMOPILOT_ANTHROPIC_KEY`, `MEMOPILOT_OPENAI_KEY`, `MEMOPILOT_GOOGLE_KEY`, `MEMOPILOT_OPENROUTER_KEY` — API keys
- `MEMOPILOT_RETRY_ENABLED`, `MEMOPILOT_RETRY_MAX_ATTEMPTS`, `MEMOPILOT_RETRY_BASE_DELAY_SECONDS`, `MEMOPILOT_RETRY_MAX_DELAY_SECONDS` — Retry policy (cloud providers only)

```
packages/
  extension/   — VS Code extension (TypeScript, esbuild)
  agent/       — Python backend (FastAPI local service)
docs/          — Product documentation
```

## Features

### LLM Provider Support

MemoPilot supports multiple LLM providers with automatic retry/backoff for transient failures:

| Provider | Mode | Requires | Status |
|----------|------|----------|--------|
| GitHub Copilot | `copilot` | VS Code subscription | Default; no API key |
| Anthropic (Claude) | `cloud` | API key in config | Production-ready |
| OpenAI (GPT-4o, etc.) | `cloud` | API key in config | Production-ready |
| Google Gemini | `cloud` | API key in config | Production-ready |
| OpenRouter | `cloud` | API key in config | Production-ready with free-tier models |
| Ollama (local) | `local` | Running instance at localhost:11434 | Production-ready |
| LM Studio (local) | `local` | Running instance at localhost:1234 | Production-ready |
| Generic OpenAI-compatible | `local` | Any OpenAI-compatible server | Supported |

**Retry/Backoff Behavior (Cloud Providers Only):**
- Automatic retry on transient failures (429, 500, 502, 503, 504)
- Includes httpx transport errors (connection, timeout, TLS)
- Respects `Retry-After` header when present; falls back to exponential backoff with jitter
- Configurable: `retry_max_attempts` (default 3), `retry_base_delay_seconds` (default 1.0), `retry_max_delay_seconds` (default 60.0)
- Local providers (Ollama, LM Studio, generic local) are hard-exempt from retry

### Implemented and Active

| Feature | Notes |
|---------|-------|
| Multi-language symbol indexing | Python, TypeScript/JS, C# |
| FTS5 full-text search + vector/semantic search | Hybrid retrieval |
| LLM symbol summarization | Batch size 25/50/75; all supported providers |
| Memory seeding from summaries | Auto-seeds memory items after summarization completes |
| File-watcher pending-changes notification | Status bar shows new/modified/deleted files; click to reindex |
| Jedi cross-module call resolution | Cross-file caller/callee relationships for Python |
| Memory Manager | Browse, filter, approve/reject items; language badges |
| Individual + bulk memory approve/reject | Per-item inline actions + bulk approve |
| Rules & Policy Packs | Hierarchical rules; org policy packs with precedence |
| Skill Store | Skills with conflict detection and optimizer |
| Context Pack sidebar | Populated after Search Project Context — files, tokens, quality score, callers not in context |
| LLM mode toggle | Copilot / Local / Cloud (any provider) — switchable at runtime |
| Per-provider retry configuration | Global + per-provider override in `.memopilot/config.yaml` |
| Budget profiles | Affect cost multiplier math (`balanced`, `cost_saver`, `strict_local`, `enterprise_privacy`) |
| Select Budget Profile command | Wired to GET/POST /v1/budget/profiles |
| Workspace Profile | Auto-detected stack, languages, frameworks |
| Privacy Dashboard | Local-only vs. sent-to-provider data |
| Task History | Past tasks with model, cost, duration |
| MCP Tools sidebar | Connected servers and available tools |
| Usage Stats | Symbols indexed/summarized (%), memory items, session queries |
| Backup / Restore Memory | Timestamped backup with FTS rebuild |
| AI Call Replay | Re-run a past task with the same context |
| Secret redaction | `detect-secrets` in context packs |
| API key storage | SecretStorage for cloud provider keys; environment variables for all config |
| Configuration validation | Safe numeric/boolean coercion; clamping of invalid retry parameters |

### Backend-Only (No Extension UI)

| Feature | Backend Location | Details |
|---------|------------------|----------|
| Document ingestion | `/v1/evidence/extract-*` endpoints | PDF, Excel, DOCX, PPTX, image OCR via pytesseract + optional Ollama LLaVA or cloud vision |
| Patch generation & validation | `patcher.py`, `validation_runner.py` | No extension commands wired; available for internal pipelines |
| Git history analysis | `git_history_indexer.py` | Analyzes commit patterns and author history |
| Workspace initialization | `workspace_init.py` | Automatic `.memopilot/` directory setup and gitignore wiring |
| Retention enforcement | `retention.py` | Automatic cleanup of stale items per policy |
| Configuration loading | `config_loader.py` | Multi-source config merging with safe type coercion |
| Migration runner | `migration_runner.py` | Database schema versioning and upgrade automation |

## Sidebar Views

| View | Shows |
|------|-------|
| Status | Backend health, schema version, indexing progress |
| Workspace Profile | Detected stack, languages, frameworks |
| Memory Manager | Browse/filter/approve/reject memory items |
| Rules & Skills | Active rules by source, skills |
| Context Pack | Files, tokens, quality, cost for active context pack |
| Usage Stats | Symbols indexed/summarized, memory items, session queries |
| Privacy Dashboard | Local-only vs. sent-to-provider data |
| Task History | Past tasks |
| MCP Tools | Connected servers and available tools |

## Development

### Prerequisites

- Node.js >= 18, pnpm
- Python >= 3.11

### Extension

```bash
pnpm install
cd packages/extension
npm run build      # Build extension
npm run package    # Produce .vsix
```

### Backend

```bash
cd packages/agent
uv sync
uv run pytest
```

### Install Extension

```bash
# VS Code
code --install-extension packages/extension/memopilot-1.0.1.vsix
# Cursor
cursor --install-extension packages/extension/memopilot-1.0.1.vsix
```

Close your editor completely before installing to avoid EBUSY file-lock errors.

## Documentation

- [Extension README](packages/extension/README.md)
- [Master Product & Implementation Reference](docs/master-reference.md)

## License

MIT
