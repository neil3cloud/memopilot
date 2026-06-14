# MemoPilot for VS Code

> **Rule-Aware, Local-Memory, Cost-Governed AI Development Agent**

MemoPilot is a VS Code extension that gives you full governance over AI-assisted coding. It builds local project memory, enforces your team's rules, controls model costs, and never applies code without your approval.

---

## Why MemoPilot?

AI coding assistants are powerful — but uncontrolled. They send too much context, ignore project rules, repeat expensive calls, hallucinate architecture, and silently modify code. MemoPilot fixes this by putting **you** in control.

| Problem | How MemoPilot Solves It |
|---------|------------------------|
| AI ignores your project conventions | Loads and enforces global + workspace rules before every task |
| No visibility into what AI receives | Shows a complete, inspectable **Context Pack** before any AI call |
| Expensive frontier model usage | Routes tasks to the cheapest capable model; enforces monthly budgets |
| AI modifies code without permission | **Approval gate** — no patch is applied without explicit developer consent |
| No memory between sessions | Builds persistent local memory from your source code, decisions, and validations |
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
- Shows token cost estimates before any AI call
- Explains why each item is included or excluded — reasons generated at selection time, not post-hoc
- Stale memory items surfaced with rebuild prompt
- Task-type-aware tier ordering (bug fixes prioritise stack traces; investigations prioritise history)
- Context pack diffing shows what changed between versions

### 🤖 Cost-Aware Model Routing
- Routes tasks: local model → cheap cloud → frontier (only when needed)
- **Outcome-based escalation** — if a module fails 2+ times with cheaper models, automatically routes to frontier
- Per-model cost comparison shown before every AI call (all tiers side by side)
- Inline model override — switch models without restarting the task
- Routing reason explains both the decision and what would trigger escalation
- Monthly budget enforcement with graduated response (80% warning → 90% frontier approval → 100% block)
- Budget profiles: `balanced`, `cost_saver`, `strict_local`, `enterprise_privacy`
- Provider capability matrix shows what each model can do

### 🔒 Patch Safety & Approval
- Task classifier determines risk level deterministically (no LLM needed)
- **Tiered approval** — LOW (one-click), MEDIUM (expanded diff), HIGH (scroll gate), CRITICAL (type filename to confirm)
- Diff files sorted by risk level descending — riskiest changes shown first
- Compliance warnings with inline actions (generate missing test, add docstring)
- `git apply --check` pre-validation before any patch
- File snapshots for instant rollback on failure
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
- **Bulk actions** — multi-select approve, reject, or delete (confirmation for 6+ items)
- **Usage signals** — every memory item shows recall count, last used date, and days since use
- "Unused (30+ days)" filter identifies candidates for cleanup
- **Ranked suggestions** — post-task memory updates scored by 5 factors (file change, class, frequency, validation, contradiction)
- "Approve High Priority Only" and "Dismiss All Low Priority" batch buttons
- **Decay detection** — pending review items older than 14 days with changed source flagged as DECAYED
- Keyboard shortcuts: `A` approve, `R` reject, `E` edit, `D` delete, `Space` preview

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
│    Validation · Investigation · MCP · Cost Guard                  │
└───────────────────────┬──────────────────────────────────────────┘
                        │
┌───────────────────────▼──────────────────────────────────────────┐
│  .memopilot/ (workspace-local, never sent to cloud)               │
│    memory/memopilot.db · rules/ · context-packs/ · logs/          │
└──────────────────────────────────────────────────────────────────┘
```

### The Task Flow

1. **You describe a task** → MemoPilot classifies type and risk (no AI call needed)
2. **Context pack is built** → Relevant files, rules, memory, symbols assembled
3. **You inspect the context** → See exactly what AI will receive, token cost, model selected
4. **AI generates a patch** → Diff preview shown in VS Code editor
5. **You approve or reject** → Nothing happens without your consent
6. **Validation runs** → pytest, mypy, ruff verify the patch
7. **Memory updates** → Learnings stored locally for next time

---

## Getting Started

### Prerequisites

- **VS Code** 1.85+ (or Cursor)
- **Python** 3.11+ on PATH or in workspace `.venv`
- Internet access only needed for cloud model calls — local models work offline

### Installation

1. Install from the VS Code Marketplace, or:
   ```
   code --install-extension memopilot-1.0.0.vsix
   ```
2. Open a workspace. MemoPilot activates automatically.
3. The backend starts, creates `.memopilot/`, and indexes your workspace.

### First Run

1. Open the **MemoPilot** sidebar (brain icon in activity bar)
2. Run **MemoPilot: Index Workspace Memory** — builds initial memory
3. Run **MemoPilot: Analyze Current Task** — classifies and builds context
4. Review the context pack in the **Context Pack** view
5. Approve or iterate

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
```

### Budget Profiles

| Profile | Behavior |
|---------|----------|
| `balanced` | Uses cheapest capable model; allows frontier when needed |
| `cost_saver` | Blocks frontier unless explicitly approved per-task |
| `strict_local` | All AI calls must use local models (Ollama, LM Studio) |
| `enterprise_privacy` | Only local-privacy providers permitted |

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
| **Rebuild Memory** | Full re-index (clears stale entries) |
| **Analyze Current Task** | Classify task, estimate risk, select model |
| **Generate Context Pack** | Build and preview the context pack |
| **Attach Evidence** | Add logs/PDFs/screenshots to investigation |
| **Run Investigation** | Start evidence-aware bug investigation |
| **Open Rules** | View and manage active rules |
| **Manage Skill Store** | View skills, detect conflicts, import YAML |
| **Show Cost Report** | View spending, savings, and model usage |
| **Select Budget Profile** | Switch budget enforcement modes |
| **Show Privacy Dashboard** | View what data goes where |
| **Show Provider Capabilities** | Compare model features and costs |
| **Replay AI Call** | Re-run a past task with exact same context |
| **Backup Memory** | Create timestamped backup |
| **Restore Memory** | Restore from a previous backup |
| **Manage Policy Packs** | Load/view organization policy packs |
| **Run Local Agent Flow** | Execute a multi-step YAML workflow |
| **Manage Workspaces** | Switch workspace in multi-root setups |
| **Restart Backend** | Stop and restart the Python backend |

---

## Sidebar Views

| View | Shows |
|------|-------|
| **Status** | Backend health, schema version, indexing progress |
| **Workspace Profile** | Detected stack, languages, frameworks |
| **Memory Manager** | Browse, filter, approve/reject memory items |
| **Rules & Skills** | Active rules by source, skills with match criteria |
| **Context Pack** | Files, tokens, rules, cost for current context |
| **Cost Guard** | Budget bar with graduated states (green/orange/red), savings vs frontier baseline, per-task cost feedback |
| **Privacy Dashboard** | Local-only vs. sent-to-provider data |
| **Evidence Board** | Attached evidence with classification + trust |
| **Task History** | Past tasks with status, model, cost, duration |
| **MCP Tools** | Connected tool servers and available tools |

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
| Budget exceeded | Switch to `strict_local` or increase `monthly_budget_usd`. |
| Policy blocks action | Contact team admin to update `.memopilot-policy/` rules. |

**Logs:** `.memopilot/logs/` — set `memopilot.backendLogLevel` to `debug` for verbose output.

---

## License

MIT
