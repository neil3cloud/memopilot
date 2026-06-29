# MemoPilot for VS Code

> **Retrieval-First Context System for VS Code Copilot and Cursor**

MemoPilot is a VS Code/Cursor extension that indexes your workspace, builds local project memory, and supplies governed, rule-aware context to Copilot Chat and Cursor before cloud LLM calls are made. It uses **GitHub Copilot** (via the VS Code Language Model API) as its primary LLM — no API key required. Local model (Ollama/LM Studio) and cloud API modes are also supported and switchable at any time.

---

## Why MemoPilot?

AI coding assistants are powerful — but uncontrolled. They send too much context, ignore project rules, repeat expensive calls, hallucinate architecture, and silently modify code. MemoPilot fixes this by putting **you** in control.

| Problem | How MemoPilot Solves It |
|---------|------------------------|
| Copilot lacks project context | Indexes workspace symbols and assembles a bounded, relevant context block before each chat |
| AI ignores your project conventions | Loads and enforces global + workspace rules before every task |
| No memory between sessions | Builds persistent local memory from source code, decisions, and session activity |
| Copilot answers feel generic | Symbol summaries, call graph, and git history give Copilot accurate project-specific context |
| Hard to know what AI is using | Shows a complete, inspectable **Context Pack** before any AI call |
| Secrets leak into AI context | Redacts credentials from context packs using `detect-secrets` |
| Can't investigate bugs with evidence | Investigation mode ingests logs, PDFs, screenshots, and work items as structured evidence |

---

## Who Is This For?

| Role | What You Get |
|------|-------------|
| **Software Developers** | Accurate AI assistance with cost control and local memory |
| **Tech Leads** | Rule enforcement and safer AI-assisted PRs across your team |
| **QA Engineers** | Test-aware context selection and evidence-driven investigation |
| **Architects** | Project convention compliance in all generated code |
| **Security Reviewers** | Full auditability — every AI call is traced and logged locally |
| **Enterprise Teams** | Privacy governance, policy packs, budget caps, and no-cloud modes |

---

## Features

### 🧠 Local Project Memory
- Indexes your workspace (files, symbols, dependencies) into a local SQLite database
- Builds and maintains memory from source code, validated decisions, and task history
- FTS5 full-text search across all memory items
- Trust levels (1–5) — higher means more reliable

### 📋 Rule & Skill Enforcement
- Loads rules from `.cursor/rules/`, `copilot-instructions.md`, `.memopilot/rules/`, and global config
- Resolves conflicts with clear precedence (safety > policy > workspace > global)
- Skills match by language, path, and task type — no irrelevant tool inclusion

### 📦 Governed Context Packs
- Assembles only relevant files, symbols, rules, and memory into a minimal context
- **Budget-aware allocation** — per-tier token caps prevent any single source from crowding out others
- **Structural call graph** — callers and callees from AST relationships included alongside primary files
- **Git commit history** — recent commits for context files included so AI knows what changed and why
- **Content deduplication** — 5-gram shingling removes redundant chunks before sending to AI
- Shows token cost estimates before any AI call
- Explains why each item is included or excluded — reasons generated at selection time, not post-hoc
- Stale memory items surfaced with rebuild prompt
- Task-type-aware tier ordering (bug fixes prioritise stack traces; investigations prioritise history)
- Context pack diffing shows what changed between versions
- **Context quality score** — 6-factor weighted score with verdict (good/acceptable/poor/rebuild) visible in sidebar before patching

### 🤖 LLM Mode Toggle
- **Three modes:** Copilot (default), Local (Ollama/LM Studio), Cloud (OpenAI/Anthropic)
- Switch mode any time via **MemoPilot: Switch LLM Mode** — no restart needed
- **Copilot mode** uses `vscode.lm` — GitHub Copilot subscription handles all tokens; no API key, no token cost
- **Local mode** — Ollama or LM Studio at a configurable URL; fully offline
- **Cloud mode** — direct API call to OpenAI or Anthropic with your own key
- Current mode shown in the Status sidebar
- Provider capability matrix shows Copilot, Ollama, LM Studio, and cloud models in a single view

### 🔒 Patch Safety & Approval
- Task classifier determines risk level deterministically (no LLM needed)
- **Tiered approval** — LOW (one-click), MEDIUM (expanded diff), HIGH (scroll gate), CRITICAL (type filename to confirm)
- Diff files sorted by risk level descending — riskiest changes shown first
- Compliance warnings with inline actions (generate missing test, add docstring)
- `git apply --check` pre-validation before any patch
- File snapshots for instant rollback on failure
- **Autofix pipeline** — safe validation failures (unused imports, whitespace) auto-fixed after patch without AI call
- **Developer must approve** before any code is applied
- **Pre-patch baseline** — validation runs before AND after patch to isolate new failures from pre-existing ones
- Auto-retry with configurable policy (up to 2 retries + optional frontier escalation)
- Failure categorisation with template-driven hints (assertion, import, fixture, syntax, type errors)
- Validation runner (pytest, mypy, ruff) with per-command timeouts

### 🔍 Evidence-Aware Investigation
- Attach evidence: logs, stack traces, PDFs, Excel, screenshots, work items
- Automatic source classification with trust levels
- Build investigation context packs with extracted findings
- Transition seamlessly from investigation → patch mode

### 🏢 Team Governance
- Policy packs enforce org-wide rules (frontier blocking, approved providers)
- Skill Store with versioning and conflict detection
- Memory review queue — AI suggestions stay `pending_review` until you approve
- Write-back safety filter blocks secrets, raw transcripts, and oversized diffs

### 📊 Memory Manager
- **Run Summarization** button — sends pending symbols to LLM in configurable batches (25/50/75/100) without wiping existing summaries
- Shows spinner while summarization is active; shows warning badge when symbols are pending but summarization is not running
- **Auto session synthesis** — after 5 minutes of Copilot Chat inactivity, MemoPilot distills the session's queries into 1–5 memory facts and writes them as `learned` items
- **Bulk actions** — multi-select approve, reject, or delete (confirmation for 6+ items)
- **Usage signals** — every memory item shows recall count, last used date, and days since use
- "Unused (30+ days)" filter identifies candidates for cleanup
- **Ranked suggestions** — post-task memory updates scored by 5 factors (file change, class, frequency, validation, contradiction)
- **Decay detection** — pending review items older than 14 days with changed source flagged as DECAYED
- Keyboard shortcuts: `A` approve, `R` reject, `E` edit, `D` delete, `Space` preview

### 🧩 Workflow Intelligence
- **Plan Mode** — store multi-step plans, recall them in context, and check compliance at patch time
- **Autofix** — validation failures classified safe/unsafe; safe issues (unused imports, whitespace) auto-fixed without AI
- **Structured Rejection** — rejected patches create per-category lessons (style, logic, security) injected into future context
- **Investigation → Plan** — investigation findings automatically generate executable action plans
- **Task Patterns** — recurring tasks detected; similar past tasks recalled to inform current context
- **Smart Memory Timing** — memory update suggestions triggered by context signals; auto-confirm only for trusted derivations (git diff, call graph)

### 🔌 Tool Mode (Copilot Chat & Cursor Chat Integration)
- **6 callable tools** exposed to Copilot Chat via VS Code Language Model Tools API
- Tools: `memopilot-context`, `memopilot-recall`, `memopilot-rules`, `memopilot-workspace-profile`, `memopilot-memory-search`, `memopilot-review-applied-patch`
- **MCP Server** for Cursor Chat — same tools via Model Context Protocol (stdio transport)
- Bounded context rendering (8000 tokens for context packs, 2000 for other tools)
- **Memory writeback** — applied diffs generate memory proposals (outcome, symbols, rules, tests)
- All proposals enter `pending_review` — nothing auto-confirmed
- Tool call audit logging with per-caller session tracking
- First-use approval gate per caller
- Automatic Cursor token injection (`.memopilot/.cursor-mcp-env`, never committed)

### 📄 Document & Artifact Ingestion

| Format | Trust Level | Notes |
|--------|-------------|-------|
| `.py`, `.ts`, `.cs`, `.java` | 5 (highest) | Source code |
| `.md`, `.sql`, `.ddl` | 4 | Docs and schemas |
| `.log` with stack traces | 4 | Auto-detected |
| `.pdf` (text-based) | 3 | Tables + text via pdfplumber |
| `.xlsx` / `.csv` | 3 | Column mapping + sheet selection |
| `.docx` / `.pptx` | 3 | Sections + slides via python-docx/pptx |
| `.png`, `.jpg` (screenshots) | 2 | OCR + optional vision model |

---

## How It Works

```
┌──────────────────────────────────────────────────────────────────┐
│  VS Code Extension (TypeScript)                                   │
│    Activity Bar → Tree Views → Webview Panels → Commands          │
└───────────────────────┬──────────────────────────────────────────┘
                        │ HTTP (HMAC token, localhost only)
┌───────────────────────▼──────────────────────────────────────────┐
│  Python Backend (FastAPI, 127.0.0.1, OS-assigned port)            │
│    Indexer · Rules · Memory · Context · Router · Patcher          │
│    Validation · Investigation · MCP · Usage Stats                  │
└───────────────────────┬──────────────────────────────────────────┘
                        │
┌───────────────────────▼──────────────────────────────────────────┐
│  .memopilot/ (workspace-local, never sent to cloud)               │
│    memory/memopilot.db · rules/ · context-packs/ · logs/          │
└──────────────────────────────────────────────────────────────────┘
```

### The Task Flow

1. **You describe a task** → MemoPilot classifies type and risk (no AI call needed)
2. **Context pack is built** → Relevant files, rules, memory, symbols, callers, and git history assembled and deduplicated
3. **Context quality is scored** → Verdict shown (✅ Good / ⚠️ Acceptable / 🔴 Poor / 🔴 Rebuild needed) with missing signals and callers not in context
4. **You inspect the context** → See exactly what AI will receive, token cost, model selected
5. **AI generates a patch** → Diff preview shown in VS Code editor
6. **You approve or reject** → Nothing happens without your consent
7. **Validation runs** → pytest, mypy, ruff verify the patch
8. **Memory updates** → Learnings stored locally for next time

---

## Getting Started

### Prerequisites

- **VS Code** 1.85+ (or Cursor)
- **Python** 3.11+ on PATH or in workspace `.venv`
- Internet access only needed for cloud model calls — local models work offline

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
5. Open Copilot Chat and type `@memopilot` — MemoPilot will now supply context for your queries

---

## Usage Examples

### Bug Fix with Evidence

```
1. Run "MemoPilot: Run Investigation"
2. Attach a stack trace log + related work item (via MCP)
3. MemoPilot classifies evidence, extracts findings, builds context
4. Transition to patch mode → AI generates fix
5. Review diff → Approve → Validate → Done
```

### Cost-Controlled Feature Work

```
1. Describe feature in Task Entry
2. MemoPilot selects cheapest capable model (local Ollama if sufficient)
3. Context pack shows: "$0.002 / 1,200 tokens / gpt-4o-mini"
4. Proceed if budget allows; MemoPilot blocks and explains if not
5. Patch generated → approved → validated → memory updated
```

### Team Policy Enforcement

```
1. Place .memopilot-policy/org-policy.yaml in your repo
2. Policy says: "no frontier models for this repository"
3. Developer runs a task → routed to local model only
4. If local fails: "Policy blocks frontier. Contact admin."
```

---

## Configuration

### VS Code Settings

| Setting | Default | Description |
|---------|---------|-------------|
| `memopilot.pythonPath` | `""` (auto-detect) | Path to Python interpreter for the backend |
| `memopilot.backendLogLevel` | `"info"` | Log level: `debug`, `info`, `warning`, `error` |
| `memopilot.summarizationBatchSize` | `25` | Symbols per LLM request during summarization: `25`, `50`, or `75`. Higher values are faster but may hit rate limits |

### Workspace Settings (`.memopilot/settings.yaml`)

```yaml
log_level: info

budget:
  monthly_budget_usd: 20.0
  profile: balanced    # balanced | cost_saver | strict_local | enterprise_privacy

mcp:
  iteration_caps:
    pre_fetch: 8
    patch_generation: 5
    investigation: 12
  hard_absolute_cap: 20

validation:
  default_timeout_seconds: 60
  commands:
    pytest:
      timeout_seconds: 120
    mypy:
      timeout_seconds: 60
    ruff:
      timeout_seconds: 30

tool_mode:
  max_output_tokens: 8000     # Context tool output cap
  other_tool_tokens: 2000     # Other tools output cap
  writeback_max_proposals: 10 # Max proposals per diff
```

### Budget Profiles

| Profile | Behavior |
|---------|----------|
| `balanced` | Uses cheapest capable model; allows frontier when needed |
| `cost_saver` | Blocks frontier unless explicitly approved per-task |
| `strict_local` | All AI calls must use local models (Ollama, LM Studio) |
| `enterprise_privacy` | Only local-privacy providers permitted |

### Provider Config (`.memopilot/config.yaml`)

This file is auto-created and gitignored on first run. It is never committed.

```yaml
provider: host           # host (VS Code Copilot) | ollama | anthropic | openai | lmstudio
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
| **Reindex & Summarize** | Full workspace re-index then LLM summarization (clears stale entries) |
| **Run Summarization** | Summarize pending symbols without wiping the index — use after interruption |
| **Switch LLM Mode** | Toggle between Copilot, Local (Ollama/LM Studio), and Cloud (OpenAI/Anthropic) modes |
| **Index Workspace Memory** | Scan workspace and build/update memory |
| **Rebuild Memory** | Full re-index (clears stale entries) |
| **Analyze Current Task** | Classify task, estimate risk, select model |
| **Generate Context Pack** | Build and preview the context pack |
| **Attach Evidence** | Add logs/PDFs/screenshots to investigation |
| **Run Investigation** | Start evidence-aware bug investigation |
| **Open Rules** | View and manage active rules |
| **Manage Skill Store** | View skills, detect conflicts, import YAML |
| **Show Privacy Dashboard** | View what data goes where |
| **Show Provider Capabilities** | Compare model features and costs |
| **Replay AI Call** | Re-run a past task with exact same context |
| **Backup Memory** | Create timestamped backup |
| **Restore Memory** | Restore from a previous backup |
| **Manage Policy Packs** | Load/view organization policy packs |
| **Run Local Agent Flow** | Execute a multi-step YAML workflow |
| **Manage Workspaces** | Switch workspace in multi-root setups |
| **Review Applied Patch** | Submit a diff for memory writeback proposals |
| **Refresh Memory Review Queue** | Refresh pending memory proposals from tool mode |
| **Restart Backend** | Stop and restart the Python backend |

---

## Sidebar Views

| View | Shows |
|------|-------|
| **Status** | Backend health, schema version, indexing progress |
| **Workspace Profile** | Detected stack, languages, frameworks |
| **Memory Manager** | Browse, filter, approve/reject memory items |
| **Rules & Skills** | Active rules by source, skills with match criteria |
| **Context Pack** | Files, tokens, rules, cost, **quality score** (verdict + missing signals + callers not in context) |
| **Usage Stats** | Symbols indexed/summarized (%), memory items (total + learned from sessions), queries this session |
| **Privacy Dashboard** | Local-only vs. sent-to-provider data |
| **Evidence Board** | Attached evidence with classification + trust |
| **Task History** | Past tasks with status, model, cost, duration |
| **MCP Tools** | Connected tool servers and available tools |

### New Task Screen (Redesigned)

The **New Task** screen is a card-based workflow screen guiding you through:

1. **Describe** your task (description, notes, mode selection)
2. **Guardrails** shown as chips (rules, approval, redaction, validation)
3. **Local analysis** runs without any AI call
4. **Structured result** — intent, mode, complexity, risk badges
5. **Suggested files** with operation indicators (+create, ~modify, −delete)
6. **AI boundary** — clearly shows "No AI call yet" until model is invoked
7. **Next actions** — Generate Context Pack, Generate Patch, or Edit Task

Docs-only tasks (`.md`, `.txt`, `.rst`) skip test validation messaging.

---

## Privacy & Security

- **All memory stays local** — `.memopilot/memory/memopilot.db` in your workspace
- **Backend binds to 127.0.0.1 only** — never exposed to the network
- **HMAC token authentication** on every request
- **Secrets redacted** from context packs via `detect-secrets`
- **No telemetry** — MemoPilot sends nothing home
- **You choose** what goes to cloud AI (visibility scopes: `local_only`, `workspace`, `restricted`)
- **Write-back safety filter** blocks secrets, transcripts, and oversized diffs from memory

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
| No `@memopilot` suggestions | Ensure LLM mode is set to Copilot and GitHub Copilot is signed in. |
| Policy blocks action | Contact team admin to update `.memopilot-policy/` rules. |

**Logs:** `.memopilot/logs/` — set `memopilot.backendLogLevel` to `debug` for verbose output.

---

## License

MIT
