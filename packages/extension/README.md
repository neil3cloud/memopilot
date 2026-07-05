# MemoPilot for VS Code

> **Retrieval-First Context System for VS Code Copilot and Cursor**

MemoPilot is a VS Code/Cursor extension that indexes your workspace, builds local project memory, and supplies governed, rule-aware context to Copilot Chat and Cursor before cloud LLM calls are made. It uses **GitHub Copilot** (via the VS Code Language Model API) as its primary LLM — no API key required. Local model (Ollama) and cloud API modes are also supported and switchable at any time.

---

## Why MemoPilot?

AI coding assistants are powerful — but uncontrolled. They send too much context, ignore project rules, repeat expensive calls, and hallucinate architecture. MemoPilot fixes this by putting **you** in control.

| Problem | How MemoPilot Solves It |
|---------|------------------------|
| Copilot lacks project context | Indexes workspace symbols and assembles a bounded, relevant context block before each chat |
| AI ignores your project conventions | Loads and enforces global + workspace rules and policy packs |
| No memory between sessions | Builds persistent local memory from source code, symbol summaries, and session activity |
| Hard to know what AI is using | Shows an inspectable Context Pack — files, rules, memory, token cost — before any AI call |
| Secrets leak into AI context | Redacts credentials from context packs using `detect-secrets` |

---

## Who Is This For?

| Role | What You Get |
|------|-------------|
| **Software Developers** | Accurate AI assistance with cost control and local memory |
| **Tech Leads** | Rule and policy pack enforcement across your team |
| **Architects** | Project convention compliance surfaced in context |
| **Security Reviewers** | Local-only memory, redaction, and a visible privacy dashboard |
| **Enterprise Teams** | Policy packs, budget caps, and no-cloud modes |

---

## Features

### 🧠 Multi-Language Project Memory
- Indexes Python, TypeScript/JavaScript, and C# source into a local SQLite database
- Extracts symbols (classes, functions, methods), call relationships, and routes
- Detects which languages are present in the workspace and shows badges (e.g. `[Py]`, `[TS]`, `[C#]`) in the Memory Manager
- FTS5 full-text search plus an optional vector index for semantic recall
- Trust levels (1–5) — higher means more reliable
- **File-watcher pending-changes notification** — silently tracks new/modified/deleted source files between indexing runs and shows a status bar prompt ("MemoPilot: 2 new, 3 modified, 1 deleted — click to update") so you control when the next (incremental) reindex runs
- **Memory seeding** — after symbols are summarized, MemoPilot automatically seeds memory items from substantial summaries, workspace-profile conventions, and the test-file registry

### 📋 Rule & Policy Enforcement
- Loads rules from `.cursor/rules/`, `copilot-instructions.md`, `.memopilot/rules/`, and global config
- **Policy packs** enforce org-wide rules (e.g. blocking frontier models) with clear precedence: safety > policy pack > workspace > global
- Skill Store tracks skills with conflict detection and an optimizer for tool/skill selection

### 📦 Governed Context Packs
- Assembles only relevant files, symbols, rules, and memory into a minimal context
- Budget-aware allocation — per-tier token caps prevent any single source from crowding out others
- Stale memory items surfaced with a rebuild prompt

### 🤖 LLM Mode Toggle & Multi-Provider Support
- **Six provider options:** Copilot (default), Anthropic, OpenAI, Google Gemini, OpenRouter, plus local modes (Ollama, LM Studio)
- Switch mode any time via **MemoPilot: Switch LLM Mode** — no restart needed
- **Copilot mode** uses `vscode.lm` — GitHub Copilot subscription handles all tokens; no API key, no token cost
- **Local modes** — Ollama, LM Studio, or any OpenAI-compatible server; fully offline
- **Cloud modes** — direct API calls (Anthropic, OpenAI, Google Gemini, OpenRouter) with your own API key
- **Automatic retry/backoff** on cloud providers for transient failures (rate limits, 5xx errors, network timeouts)
- **Free-tier model support** — OpenRouter exposes free-tier models (e.g., Deepseek) requiring no credits
- Current mode and active model shown in the Status sidebar
- Per-provider retry configuration in `.memopilot/config.yaml`

### 📊 Memory Manager
- **Run Summarization** button — sends pending symbols to the LLM in configurable batches (25/50/75) without wiping existing summaries
- Shows a spinner while summarization is active; shows a warning badge when symbols are pending but summarization is not running
- **Bulk and individual actions** — approve/reject one item, or multi-select for bulk approve
- Filters: all, rules, symbols, file summaries, stale, pending approval
- Language badges per item, inferred from item type or source file extension

### 🔌 Tool Mode (Copilot Chat & Cursor Chat Integration)
- **4 callable tools** exposed to Copilot Chat via the VS Code Language Model Tools API: `memopilot-search`, `memopilot-symbols`, `memopilot-memory`, `memopilot-profile`
- **MCP server** exposes the same 4 tools over stdio JSON-RPC for Cursor Chat and other MCP clients
- Tool call audit logging with per-caller session tracking

### 📄 Document Ingestion (backend)
The backend can extract text and structure from several document types via `/v1/evidence/extract-*` endpoints — useful when building context from non-code sources:

| Format | Tool | Notes |
|--------|------|-------|
| `.pdf` | pdfplumber | Text extraction + table detection |
| `.xlsx` / `.csv` | openpyxl / csv | Column mapping + sheet selection |
| `.docx` / `.pptx` | python-docx / python-pptx | Sections + slides with formatting |
| `.png` / `.jpg` | pytesseract (local) or Gemini Vision (cloud) | OCR with optional Ollama LLaVA or cloud vision fallback |

These are backend primitives without a dedicated sidebar workflow in the current extension UI.

---

## How It Works

```
┌──────────────────────────────────────────────────────────────────┐
│  VS Code Extension (TypeScript)                                   │
│    Activity Bar → Tree Views → Webview Panel → Commands           │
└───────────────────────┬──────────────────────────────────────────┘
                        │ HTTP (HMAC token, localhost only)
┌───────────────────────▼──────────────────────────────────────────┐
│  Python Backend (FastAPI, 127.0.0.1, OS-assigned port)            │
│    Indexer · Extractors (Py/TS/C#) · Rules · Memory · Context     │
│    Policy Packs · Skill Store · Cost Guard · MCP Server            │
└───────────────────────┬──────────────────────────────────────────┘
                        │
┌───────────────────────▼──────────────────────────────────────────┐
│  .memopilot/ (workspace-local, never sent to cloud)               │
│    memory/memopilot.db · rules/ · context-packs/ · logs/          │
└──────────────────────────────────────────────────────────────────┘
```

---

## Getting Started

### Prerequisites

- **VS Code** 1.85+ (or Cursor)
- **Python** 3.11+ on PATH or in workspace `.venv`
- Internet access only needed for Copilot/cloud model calls — local (Ollama) mode works offline

### Installation

1. Install from the VS Code Marketplace, or for a local VSIX:
   ```bash
   # VS Code
   code --install-extension memopilot-1.0.1.vsix
   # Cursor
   cursor --install-extension memopilot-1.0.1.vsix
   ```
   Close your editor completely before installing to avoid file-lock errors.
2. Open a workspace. MemoPilot activates automatically.
3. The backend starts, creates `.memopilot/`, and indexes your workspace.

### First Run

1. Open the **MemoPilot** sidebar (brain icon in activity bar)
2. Sign in to **GitHub Copilot** in VS Code/Cursor if you haven't already — Copilot mode requires no API key
3. Click **Reindex & Summarize** (↺ button in the Status panel) — scans files and sends symbols to Copilot for summarization
4. Wait for the Memory Manager to show a green spinner, then completion. Use **Run Summarization** (▶ button in Memory Manager) to resume if interrupted
5. Open Copilot Chat and reference `@memopilot` tools, or use the MCP server from Cursor Chat

---

## Configuration

### VS Code Settings

| Setting | Default | Description |
|---------|---------|-------------|
| `memopilot.pythonPath` | `""` (auto-detect) | Path to Python interpreter for the backend |
| `memopilot.backendLogLevel` | `"info"` | Log level: `debug`, `info`, `warning`, `error` |
| `memopilot.ollamaUrl` | `"http://localhost:11434"` | URL of the local Ollama server |
| `memopilot.preferredProvider` | `"auto"` | Preferred LLM provider: `auto`, `ollama`, `openai`, `anthropic` |
| `memopilot.summarizationBatchSize` | `25` | Symbols per LLM request during summarization: `25`, `50`, or `75` |
| `memopilot.indexedLanguages` | `["python"]` | Languages to index: `python`, `typescript`, `csharp` |
| `memopilot.showLanguageBadges` | `true` | Show language badges (e.g. `[Py]`, `[TS]`, `[C#]`) in the Memory Manager |

### Workspace Settings (`.memopilot/settings.yaml`)

Not created automatically for a workspace — create it by hand if you want to set a budget. There is currently no command or UI to change `profile` after the fact; edit the file directly and restart the backend.

```yaml
log_level: info

budget:
  monthly_budget_usd: 20.0
  profile: balanced    # balanced | cost_saver | strict_local | enterprise_privacy
```

`monthly_budget_usd` is enforced directly by the cost guard. `profile` currently only adjusts the effective budget multiplier — it does **not** restrict which providers/models can be selected (e.g. `strict_local` does not block cloud calls today).

### Provider Config (`.memopilot/config.yaml`)

This file is auto-created and gitignored on first run. It is never committed.

```yaml
provider: host           # host (VS Code Copilot) | ollama | anthropic | openai
fallback_order:
  - host                 # GitHub Copilot via vscode.lm API — no API key needed
  - ollama               # Local, free, no API key
  - anthropic            # Requires anthropic_api_key
  - openai               # Requires openai_api_key

# anthropic_api_key: sk-ant-...
# openai_api_key: sk-...
```

**GitHub Copilot is the default and requires no configuration** — just sign in to Copilot in VS Code.

### Rules (`.memopilot/rules/*.yaml`)

```yaml
- id: always-type-hints
  scope: workspace
  rule_text: "Always use type hints on function parameters and return values"
  priority: 10

- id: no-print-statements
  scope: workspace
  rule_text: "Use logging module instead of print() in production code"
  priority: 5
```

---

## Commands

| Command | Description |
|---------|-------------|
| **Index Workspace Memory** | Scan workspace and build/update memory |
| **Update Index for Pending Changes** | Apply an incremental reindex for files flagged by the file watcher |
| **Reindex and Summarize** | Full workspace re-index then LLM summarization (clears stale entries) |
| **Run Summarization** | Summarize pending symbols without wiping the index — use after interruption |
| **Search Project Context** | Analyze the current task and assemble context |
| **Generate Context Pack** | Build and preview the context pack |
| **Switch LLM Mode** | Toggle between Copilot, Local (Ollama), and Cloud (OpenAI/Anthropic) modes |
| **Configure Providers** | Set up and select LLM providers |
| **Open Rules** | View and manage active rules |
| **Open / Rebuild / Validate / Export Workspace Profile** | Manage the detected workspace profile |
| **Review Memory** | Open the memory review queue |
| **Approve / Reject Memory Item** | Act on an individual memory item |
| **Bulk Approve Memory** | Approve multiple pending items at once |
| **Refresh Memory Review Queue** | Refresh pending memory proposals |
| **Rebuild Memory** | Full re-index (clears stale entries) |
| **Backup / Restore Memory** | Create or restore a timestamped backup |
| **Manage Skill Store** | View skills, detect conflicts |
| **Optimize Tools and Skills** | Run the tool/skill optimizer |
| **Manage Policy Packs** | Load/view organization policy packs |
| **Manage Workspaces** | Switch workspace in multi-root setups |
| **Show Privacy Dashboard** | View what data goes where |
| **Replay AI Call** | Re-run a past task with the exact same context |
| **Open Panel** | Open the MemoPilot webview panel |
| **Restart Backend** | Stop and restart the Python backend |

---

## Sidebar Views

| View | Shows | Key Features |
|------|-------|----------------|
| **Status** | Backend health, schema version, indexing progress, detected languages | Quick restart button; shows current LLM mode, provider, and active model; progress bar during indexing |
| **Workspace Profile** | Detected stack, languages, frameworks | Auto-inferred from source files; editable for teams; shows test framework and build tools |
| **Memory Manager** | Browse, filter, approve/reject memory items, language badges | Filters: all, rules, symbols, file summaries, stale, pending; language badges (`[Py]`, `[TS]`, `[C#]`); bulk approve/reject actions |
| **Rules & Skills** | Active rules by source, skills with match criteria | Hierarchical precedence visualization (safety > policy > workspace > global); conflict detection; source traceability |
| **Context Pack** | Files, tokens, rules, memory, and cost for the active context pack | Stale memory warnings; callers not included; budget allocation breakdown; estimated token usage and cost |
| **Usage Stats** | Symbols indexed/summarized (%), memory items, queries this session | Real-time percentage completion; cumulative cost tracking; session query count and latency stats |
| **Privacy Dashboard** | Local-only vs. sent-to-provider data | Audit trail showing which data leaves the machine; redaction counts; per-call transparency |
| **Task History** | Past tasks with status, model, cost, duration | Re-run button for any task with same context; filter by provider/model; export task details |
| **MCP Tools** | Connected MCP servers and available tools | Live server list with connection status; tool schemas and parameter details; availability per provider |

---

## Privacy & Security

- **All memory stays local** — `.memopilot/memory/memopilot.db` in your workspace
- **Backend binds to 127.0.0.1 only** — never exposed to the network
- **HMAC token authentication** on every request
- **Secrets redacted** from context packs via `detect-secrets`
- **No telemetry** — MemoPilot sends nothing home
- **You choose** what goes to cloud AI via budget profiles and provider fallback order

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| Backend won't start | Run **Restart Backend**. Verify Python 3.11+ on PATH. |
| "Version conflict" warning | Schema mismatch — restart backend to re-migrate. |
| FTS search returns nothing | Run **Rebuild Memory** to re-index and rebuild FTS. |
| VSIX install fails (EBUSY) | Close VS Code/Cursor completely before running the install command. |
| Summarization stalls mid-run | Click **Run Summarization** (▶ in Memory Manager) to resume from where it stopped. |
| Memory Manager shows warning badge | Symbols are pending but not being summarized. Click **Run Summarization** to continue. |
| Memory Manager shows 0 items right after indexing | Memory items are seeded once summarization finishes in the background — wait for the summarization spinner to complete, then refresh. |
| No `@memopilot` suggestions | Ensure LLM mode is set to Copilot and GitHub Copilot is signed in. |
| Wrong language shown in Workspace Profile / Memory Manager | Enable the relevant language(s) in `memopilot.indexedLanguages`, then run **Reindex and Summarize**. |
| Policy blocks action | Contact team admin to update policy pack rules. |

**Logs:** `.memopilot/logs/` — set `memopilot.backendLogLevel` to `debug` for verbose output.

---

## License

MIT
