# MemoPilot: Master Product and Implementation Reference

**Document Version:** 2.5 (Context Accuracy Refinement) **Target Product:** MemoPilot — Rule-Aware, Local-Memory, Cost-Governed AI Development Agent Extension for VS Code/Cursor **Status:** Production Reference

---

## Table of Contents

1. [App / Extension Overview](#1-app--extension-overview)  
2. [The 5 Ws](#2-the-5-ws)  
3. [Guiding Principles](#3-guiding-principles)  
4. [Product Capabilities](#4-product-capabilities)  
5. [Workspace Platform Comparison and Lessons](#5-workspace-platform-comparison-and-lessons)  
6. [High-Level Architecture](#6-high-level-architecture)  
7. [Extension Responsibilities](#7-extension-responsibilities)  
8. [Python Backend Responsibilities](#8-python-backend-responsibilities)  
9. [Workspace Design](#9-workspace-design)  
10. [Local Memory Design](#10-local-memory-design)  
11. [Database Schema](#11-database-schema)  
12. [Rule and Skill System](#12-rule-and-skill-system)  
13. [Context Pack System](#13-context-pack-system)  
14. [Evidence-Aware Bug and User Story Investigation](#14-evidence-aware-bug-and-user-story-investigation)  
15. [Non-Code Artifact Analysis](#15-non-code-artifact-analysis)  
16. [Model Provider Strategy](#16-model-provider-strategy)  
17. [Cost Guard](#17-cost-guard)  
18. [Patch Generation and Validation](#18-patch-generation-and-validation)  
19. [Security and Privacy](#19-security-and-privacy)  
20. [MCP Integration](#20-mcp-integration)  
21. [Production Readiness](#21-production-readiness)  
22. [Testing Strategy](#22-testing-strategy)  
23. [Complete Implementation Plan](#23-complete-implementation-plan)  
24. [MVP Scope](#24-mvp-scope)  
25. [Risks and Mitigations](#25-risks-and-mitigations)  
26. [Final Product Positioning](#26-final-product-positioning)
27. [UI Implementation Progress (v2.1)](#27-ui-implementation-progress-v21--june-2025)
28. [Remediation and Feature Hardening (v2.2)](#28-remediation-and-feature-hardening-v22--june-2026)
29. [Feature Refinement Phase (v2.3)](#29-feature-refinement-phase-v23--june-2026)
30. [Tool Mode Integration (v2.4)](#30-tool-mode-integration-v24--june-2026)
31. [Context Accuracy Refinement (v2.5)](#31-context-accuracy-refinement-v25--june-2026)

---

## 1\. App / Extension Overview

### Product Name

**MemoPilot**

### One-Sentence Summary

A rule-aware, local-memory, cost-governed AI development agent extension for VS Code/Cursor that helps developers create, modify, investigate, and validate code accurately while minimizing unnecessary frontier-model usage.

### Executive Summary

MemoPilot is a production-ready AI development assistant extension for VS Code/Cursor. It is not a chatbot inside the editor. It is a **rule-aware, local-memory, cost-governed AI agent** that helps developers create, modify, review, investigate, and validate code with less dependency on expensive frontier models.

The extension builds and maintains a local application memory before sending any context to AI. It reads local and global rules, project conventions, skills, source code, documentation, symbols, test patterns, previous decisions, and task history. It then generates a minimal, explainable context pack and routes the task to the cheapest capable model available to the developer.

The extension must respect existing developer rules, project rules, Cursor rules, Copilot instructions, repository documentation, code patterns, test requirements, and safety policies. It must never operate as a black box.

### Target Users

| User | Primary Need |
| :---- | :---- |
| Software developers | Accurate AI assistance with cost control |
| Tech leads | Rule enforcement and safer AI-assisted PRs |
| QA engineers | Test-aware context selection |
| Architects | Project rule compliance in generated code |
| Security reviewers | Auditability of AI-generated patches |
| Enterprise teams | Privacy governance and AI usage control |

### Problem Statement

The problem is not the lack of AI tools. The problem is **uncontrolled, inaccurate, and expensive AI usage**. Common AI coding assistant failures:

- Sends too much context  
- Ignores project rules  
- Repeats expensive AI calls  
- Hallucinates architecture  
- Misses business rules  
- Generates code without tests  
- Modifies code without understanding conventions  
- Cannot explain why it selected files  
- Does not track cost  
- Does not remember validated project facts locally

### What MemoPilot Is

- A rule-aware AI development agent extension  
- A local project memory system for software development  
- A context-pack builder with controlled, explainable context selection  
- A cost-aware model router  
- A patch governance and approval workflow  
- An evidence-aware bug and user story investigation tool  
- A privacy boundary enforcer

### What MemoPilot Is Not

- A Cursor clone  
- A generic AI chatbot  
- A fully autonomous coding bot  
- A replacement for developer review  
- A system that blindly sends full repositories to cloud AI  
- A system that silently applies patches  
- A general-purpose RAG application  
- A generic document AI platform

### Core Value Proposition

Local application memory \+ rule enforcement \+ cost-aware model routing \+ explainable context \+ patch approval \+ validation \= higher accuracy at lower cost.

### Differentiator vs. Generic AI Coding Assistants

Generic assistants dump context and call the most powerful model available. MemoPilot builds a governed memory layer, resolves project rules, selects the cheapest capable model, generates inspectable context, and requires developer approval before touching any code.

### Differentiator vs. Broad Document AI Platforms

Broad document AI and workspace platforms are general-purpose knowledge systems. MemoPilot is narrower and sharper: it is an AI coding governance layer inside VS Code/Cursor. See [Section 5](#5-workspace-platform-comparison-and-lessons) for a full comparison.

### Primary Use Cases

- Feature implementation respecting project conventions and rules  
- Bug investigation using code, logs, stack traces, and work items  
- User story implementation with acceptance criteria alignment  
- Safe refactoring with rule compliance checking  
- Test generation with project-specific patterns  
- Code review with rule and risk scoring  
- Architecture analysis against documented decisions  
- AI context preparation and governance

### Supported Editor Environments

- **Primary:** Visual Studio Code (stable channel, v1.85+)  
- **Compatible:** Cursor (via VS Code extension host; host model access treated as optional)

### High-Level User Journey

1\. Developer opens a solution in VS Code/Cursor.

2\. MemoPilot indexes project files, symbols, rules, and docs.

3\. Developer selects a mode (Ask, Plan, Patch, Test, Investigate, etc.).

4\. Developer enters a task or attaches evidence.

5\. MemoPilot resolves active rules and skills.

6\. MemoPilot retrieves relevant local memory.

7\. MemoPilot builds a minimal, inspectable context pack.

8\. MemoPilot estimates token count and cost.

9\. MemoPilot selects the cheapest capable model.

10\. Developer reviews and approves the context pack and model selection.

11\. AI generates a plan or patch.

12\. Developer reviews diff and approves patch application.

13\. MemoPilot applies patch and runs validation tools.

14\. MemoPilot proposes memory updates; developer approves.

15\. Task history and cost are logged.

---

## 2\. The 5 Ws

### 2.1 Who

**Primary Users**

- Python developers  
- Full-stack developers  
- Enterprise developers working with complex existing solutions  
- Developers using GitHub Copilot, Cursor, Claude, OpenAI, Gemini, Ollama, or LM Studio  
- Developers working with business rules where accuracy matters  
- Teams that maintain local project rules, architecture docs, coding standards, and AI instructions

**Secondary Users**

- Tech leads who want safer AI-assisted development  
- Engineering managers who want lower AI tool cost  
- QA engineers who want test-aware AI changes  
- Architects who want project rules respected before code generation  
- Security reviewers who want auditability of AI-generated patches

**System Actors**

Developer

  \-\> sends task/request, reviews context pack, approves model usage,

     approves patch application, manages evidence sources

MemoPilot Extension

  \-\> integrates with editor UI, collects workspace context,

     displays rules, memory, cost, evidence, and patches

Python Agent Backend

  \-\> performs scanning, indexing, retrieval, routing, investigation,

     validation, and orchestration

Local Memory Store

  \-\> stores project memory, rules, symbols, summaries, task history,

     cost logs, and evidence findings

Model Providers

  \-\> host account models, local models, cheap cloud models, frontier models

Local Tools

  \-\> git, pytest, ruff, mypy, npm, dotnet, linters, type checkers, build tools

### 2.2 What

MemoPilot is a VS Code/Cursor extension with AI agent capability, providing:

- Local application memory  
- Workspace profiles  
- Rule and skill resolution  
- Context-pack generation with templates  
- Agent modes (Ask, Plan, Patch, Test, Review, Investigate, Autofix)  
- Cost-aware model routing  
- AI-assisted code review and patch generation  
- Test and lint validation  
- User-approved file changes  
- Task history and cost tracking  
- Evidence-aware bug and user story investigation  
- Non-code artifact analysis (logs, PDFs, spreadsheets, screenshots)  
- Memory Manager UI  
- Privacy Boundary Dashboard  
- Provider Capability Matrix  
- AI Call Replay / Reproduce Mode  
- Human-in-the-loop memory updates

### 2.3 When

MemoPilot is used during active software development:

- Bug investigation and root-cause analysis  
- User story implementation with acceptance criteria alignment  
- Feature development following project conventions  
- Code review and refactoring  
- Test generation  
- Architecture analysis  
- AI context preparation for manual use  
- Post-incident investigation using logs and evidence

### 2.4 Where

VS Code/Cursor Extension Host

  \-\> TypeScript extension UI and editor integration

Local Python Backend

  \-\> FastAPI local service on 127.0.0.1:\<dynamic-port\>

Project Directory

  \-\> \<repo\>/.memopilot/ (workspace memory and rules)

User Home Directory

  \-\> \~/.memopilot/ (global developer rules and global skills)

Optional External AI Services

  \-\> OpenAI, Anthropic, Gemini, Azure OpenAI, etc.

Optional Local AI Runtime

  \-\> Ollama, LM Studio, llama.cpp server, local embedding model

Optional MCP Servers

  \-\> Azure DevOps, database schema/query tools

### 2.5 Why

**Business Value**

- Lower AI cost through model routing and context pruning  
- Higher coding accuracy through local project memory and rule enforcement  
- Better project-rule compliance via governed context selection  
- Less context pollution and reduced developer rework  
- Safer patch workflow through diff preview and approval gates  
- Auditable AI usage with full task and cost history  
- Reduced dependency on frontier models

**Technical Value**

- Local-first memory with hybrid FTS \+ vector retrieval  
- Structured rule resolution with precedence and conflict detection  
- Trust-level-aware memory system  
- Deterministic task classification without AI calls  
- Token/cost estimation before every AI call  
- Patch risk classification from deterministic signals  
- Stale memory detection and rebuild

---

## 3\. Guiding Principles

### 3.1 Local First

Inspect local memory, local rules, and local code before calling any AI model.

Wrong:  User asks task → send current file to frontier model

Correct: User asks task → load rules → search memory → inspect code

          → build context pack → route model

### 3.2 Rules Before AI

Rules are applied before context selection, model routing, patch generation, and validation. AI suggestions never override hard rules.

**Rule precedence:**

1\. Safety rules

2\. Task-specific user instruction

3\. Project/workspace rules

4\. Global developer rules

5\. Inferred solution conventions

6\. AI suggestions

### 3.3 Memory Must Be Trust-Aware

Trust 1: Verified from source code

Trust 2: Parsed from repository docs

Trust 3: User-approved rule or architecture decision

Trust 4: AI-generated summary

Trust 5: Inferred pattern

High-trust memory wins over low-trust memory. AI-generated memory stays at trust level 4 or 5 until explicitly approved.

### 3.4 Explain Every AI Call

Before calling AI, MemoPilot must show: selected model, reason for model selection, estimated tokens, estimated cost, active rules, active skills, files/snippets included and excluded, and whether a frontier model is required.

### 3.5 No Silent Patching

AI proposes → developer approves → patch applies → tests run → memory updates

No patch is ever applied automatically. No memory is updated before validation.

---

## 4\. Product Capabilities

### 4.1 Core Capabilities

- Local application memory (SQLite \+ FTS5 \+ sqlite-vec)  
- Workspace profile  
- Rule-aware behavior  
- Skill-aware behavior  
- Context-pack generation  
- Cost-aware model routing  
- AI-assisted code analysis  
- AI-assisted patch generation  
- Patch approval workflow  
- Test/lint validation  
- Task history  
- Cost tracking  
- Privacy and secret redaction

### 4.2 Production Capabilities (v1)

- Memory Manager UI  
- Workspace Profile UI  
- Privacy Boundary Dashboard  
- Provider Capability Matrix  
- AI Call Replay / Reproduce Mode  
- Context Pack Templates  
- Agent Modes (Ask, Plan, Context Pack, Patch, Test, Review, Autofix)  
- Patch Risk Classifier  
- Rule Compliance Score  
- Human-in-the-loop memory updates  
- Intelligent Context Selection with inclusion/exclusion reasoning  
- Bug/User Story Investigation Mode  
- Evidence Board  
- Investigation Context Pack

### 4.3 Production Capabilities (v1.5)

- Context Pack Diffing  
- Memory backup/restore  
- Skill Store  
- Evidence Source Classifier  
- Non-Code Artifact Analyzer (PDF, Excel, CSV)  
- Tool and Skill Selection Optimizer  
- Model Budget Profiles

### 4.4 Optional / Future Capabilities (v2)

- Team policy packs  
- Local agent flow builder  
- Team-shared memory server  
- Multi-workspace support v2  
- Image and UI screenshot analysis  
- Word/Excel/PowerPoint ingestion

---

## 5\. Workspace Platform Comparison and Lessons

### 5.1 What Broad Document AI Platforms Do

Broad document AI and workspace platforms typically include:

- Multi-workspace document management  
- RAG over uploaded documents  
- Multi-user support with role-based access  
- Model routing across providers  
- Local-first storage with vector and document databases  
- MCP server support  
- No-code AI workflow builder  
- Agent tools (web search, code execution, etc.)  
- Memory scopes (global and workspace)  
- Document ingestion (PDF, Markdown, Excel, images, and more)

### 5.2 What MemoPilot Is

MemoPilot is narrower and sharper: it is an AI coding governance layer inside VS Code/Cursor. It does not try to be a general-purpose document AI platform.

| Dimension | Broad document AI platform | MemoPilot |
| :---- | :---- | :---- |
| Primary interface | Web app | VS Code/Cursor extension |
| Memory scope | Documents and conversations | Codebase, rules, symbols, decisions |
| Model routing | Multi-provider | Cost-tiered, rule-constrained |
| Agent authority | Broad, agentic | Governed, patch-only, approval-required |
| Target user | Any knowledge worker | Software developer |
| Context pack | None | Controlled, inspectable, templated |
| Patch governance | None | Diff preview, approval gate, risk classifier |
| Validation | None | pytest, ruff, mypy, lint, build |
| Cost guard | None | Token estimation, budget profiles, savings report |
| Privacy boundary | Configurable | Explicit, dashboarded, secret-redacting |
| Rule system | None | Hierarchical rules with conflict detection |
| Skills | None | Task-specific YAML-based skills |

### 5.3 What MemoPilot Should Borrow from External Connectors

- **Workspaces**: Per-project memory scopes (MemoPilot's Workspace Profiles)  
- **Local-first philosophy**: Memory stays on disk; external calls are opt-in  
- **Model routing**: Provider-agnostic routing with fallback tiers  
- **Intelligent tool selection**: Select tools per task, not all tools always  
- **Memory scopes**: Global developer rules vs. project-specific rules  
- **MCP concepts**: Opt-in external tools with approval flows  
- **Document ingestion ideas**: Structured ingestion with trust levels (applied to evidence sources in MemoPilot)

### 5.4 What MemoPilot Must Not Become

MemoPilot must not become a generic RAG platform. It must remain focused on:

- Codebase memory and symbol indexing  
- Rule and skill enforcement  
- Patch generation and validation  
- Cost governance for developer AI usage  
- Evidence-aware bug and user story investigation  
- VS Code/Cursor native integration

---

## 6\. High-Level Architecture

VS Code/Cursor Extension

  ├── Activity Bar View

  ├── Webview Panel

  ├── Command Palette Commands

  ├── Workspace Profile UI

  ├── Memory Manager UI

  ├── Evidence Board

  ├── Context Pack Preview

  ├── Diff Preview

  ├── Rule/Skill Viewer

  ├── Cost Guard UI

  ├── Privacy Boundary Dashboard

  ├── Approval Controls

  └── MCP Tool Client (vscode.lm.invokeTool — primary)

Python Agent Backend

  ├── Project Scanner

  ├── File Watcher

  ├── Rule Resolver

  ├── Skill Loader

  ├── Local App Memory Manager

  ├── Document Ingestion Manager

  ├── Evidence Source Classifier

  ├── Symbol Extractor

  ├── Summary Generator

  ├── Hybrid Retriever

  ├── Context Pack Builder

  ├── Model Router

  ├── Cost Estimator

  ├── AI Provider Adapter Layer

  ├── Patch Generator

  ├── Patch Validator

  ├── Validation Runner

  ├── Memory Updater

  └── MCP Tool Runner (allowlist enforcement, credential resolution, result redaction)

Local Storage

  ├── SQLite (structured source of truth)

  ├── SQLite FTS5 (exact identifier and keyword search)

  ├── sqlite-vec (embedded vector search, same connection)

  ├── YAML Rules

  ├── JSON Settings

  └── JSONL Logs

Model Providers

  ├── Host Account Model (VS Code lm API, where available)

  ├── Local Model (Ollama, LM Studio)

  ├── Cheap Cloud Model (OpenAI GPT-4o-mini, Gemini Flash, etc.)

  ├── Frontier Model (Claude Opus, GPT-4o, Gemini Pro, etc.)

  └── Context-Pack-Only Fallback (no AI call; pack only)

---

## 7\. Extension Responsibilities

The extension is written in TypeScript and is responsible only for UI, editor integration, and communication with the Python backend.

### 7.1 Commands

MemoPilot: Index Workspace Memory

MemoPilot: Show Project Memory

MemoPilot: Analyze Current Task

MemoPilot: Generate Context Pack

MemoPilot: Review Current File

MemoPilot: Generate Patch

MemoPilot: Show Diff

MemoPilot: Apply Approved Patch

MemoPilot: Run Validation

MemoPilot: Show Cost Report

MemoPilot: Open Rules

MemoPilot: Open Skills

MemoPilot: Rebuild Memory

MemoPilot: Fetch Work Item

MemoPilot: Show MCP Tool Results

MemoPilot: Configure MCP Servers

MemoPilot: Open Workspace Profile

MemoPilot: Rebuild Workspace Profile

MemoPilot: Validate Workspace Profile

MemoPilot: Export Workspace Profile

MemoPilot: Review Memory

MemoPilot: Delete Stale Memory

MemoPilot: Approve AI Summary

MemoPilot: Reject AI Summary

MemoPilot: Rebuild Selected Memory

MemoPilot: Search Memory

MemoPilot: Create Context Template

MemoPilot: Edit Context Template

MemoPilot: Apply Context Template

MemoPilot: Preview Context Template

MemoPilot: Replay AI Call

MemoPilot: Open Context Pack Used

MemoPilot: Open AI Response

MemoPilot: Open Patch Attempt

MemoPilot: Re-run Validation

MemoPilot: Compare Replayed Output

MemoPilot: Backup Memory

MemoPilot: Restore Memory

MemoPilot: Export Rules

MemoPilot: Import Skills

MemoPilot: Reset Memory

MemoPilot: Open Investigation

MemoPilot: Attach Evidence

MemoPilot: Run Investigation

### 7.2 UI Sections

Header

  \-\> Workspace Indexed status, backend status

Workspace Profile

  \-\> project name, detected stack, active rules, active skills,

     model budget profile, privacy mode, MCP status, memory health

Local App Memory (Memory Manager)

  \-\> memory items grouped by type, filters, approve/edit/delete actions

Active Rules/Skills

  \-\> loaded global rules, project rules, detected skills

Context Pack

  \-\> selected files, snippets, rules, memory, token estimate,

     inclusion/exclusion reasons, template used, redaction summary

Model Router

  \-\> host account model, local model, cheap cloud model, frontier model

  \-\> selected model, reason, cost estimate

Cost Guard

  \-\> estimated tokens, estimated cost, avoided frontier call, monthly savings

Current Task

  \-\> mode selector (Ask | Plan | Context Pack | Patch | Test | Review | Autofix | Investigate)

  \-\> task input, analyze, generate patch, show diff

Approval Gate

  \-\> apply patch only after explicit approval

  \-\> patch risk level, rule compliance score

Evidence Board (Investigation Mode)

  \-\> evidence sources, extracted findings, code matches, AI analysis,

     patch plan, validation requirements

MCP Tools

  \-\> configured servers, connection status, last tool result summary,

     tool call approval prompt

Privacy Boundary

  \-\> local memory status, cloud provider usage, secret redaction summary,

     files excluded from context, MCP data included/excluded

Task History

  \-\> task, date/time, mode, model used, cost, context hash,

     patch status, validation status, replay button

Suggested Memory Updates

  \-\> suggested memory items with approve/edit/reject actions

### 7.3 Webview Safety

All VS Code webview panels must declare a strict Content Security Policy. AI-generated content is rendered as escaped plain text or sanitized Markdown — never injected as raw innerHTML. Diff previews use the VS Code diff editor API, not a custom HTML renderer.

Required CSP on all webview panels:

  default-src 'none';

  script-src \<nonce\>;

  style-src \<nonce\> 'unsafe-inline';

  img-src vscode-resource: https:;

  connect-src 'none';

---

## 8\. Python Backend Responsibilities

The Python backend is the agent brain. It is a FastAPI local service bound exclusively to `127.0.0.1:<dynamic-port>`.

### 8.1 Backend Module Structure

agent/

  main.py

  api.py

  config.py

  scanner.py

  watcher.py

  rule\_resolver.py

  skill\_loader.py

  memory\_store.py

  fts\_search.py

  vector\_store.py

  symbol\_extractor.py

  summary\_generator.py

  retriever.py

  context\_builder.py

  classifier.py

  model\_router.py

  cost\_estimator.py

  providers/

    host\_model.py

    ollama.py

    lmstudio.py

    openai\_provider.py

    anthropic\_provider.py

    azure\_openai\_provider.py

  patcher.py

  validator.py

  tool\_runner.py

  audit\_logger.py

  mcp\_client.py

  mcp\_tool\_runner.py

  evidence\_classifier.py

  document\_ingestion\_manager.py

  investigation\_runner.py

### 8.2 IPC Contract

**Port Negotiation**

1\. Backend starts and requests an OS-assigned port (bind to 127.0.0.1:0).

2\. Backend writes the assigned port to .memopilot/agent.lock on startup.

3\. Extension reads .memopilot/agent.lock to discover the port.

4\. Extension deletes the lockfile when it stops the backend.

**Authentication**

\- A 32-byte HMAC token is generated per session by the extension.

\- Passed to the backend process via MEMOPILOT\_TOKEN environment variable.

\- Every request includes the header: X-Agent-Token: \<token\>

\- Backend rejects requests with missing or invalid token with HTTP 401\.

\- Token is never written to disk or logged.

**API Versioning**

\- All routes use the prefix /v1/

\- GET /v1/health returns:

    { "schema\_version": \<int\>, "api\_version": \<int\>, "status": "ok" }

\- Extension reads schema\_version and api\_version on startup.

\- Version mismatch surfaces an error in the VS Code status bar and

  blocks further requests until backend is restarted or updated.

**Reconnect Policy**

\- On request failure: retry 3 times with 500ms linear backoff.

\- If all retries fail: mark backend as unavailable in status bar.

\- Extension surfaces "MemoPilot backend unavailable — restart backend" action.

\- Backend is restarted only on explicit developer action.

### 8.3 Python Environment Resolution

**Detection order:**

1\. Workspace .venv/Scripts/python.exe (Windows) or .venv/bin/python (Unix)

2\. python.defaultInterpreterPath VS Code setting

3\. python3 (or python) found in PATH

4\. User-configured interpreter in MemoPilot settings

**Minimum requirements:**

- Python \>= 3.11  
- Packages validated against `agent/requirements.txt` on backend startup

---

## 9\. Workspace Design

### 9.1 Workspace Scope

One workspace \= one opened repository/project. MemoPilot v1 supports one active workspace at a time. Multi-root workspaces are detected and a warning is shown, but isolated multi-workspace support is deferred to v2.

**Hard rule:** Never merge memory from multiple repositories into one database. Memory databases are workspace-scoped.

### 9.2 Workspace Folder Structure

\<repo\>/.memopilot/

  workspace.profile.yaml        \<- workspace profile

  agent.lock                    \<- port and PID of running backend

  rules/

    project.rules.yaml

    skills/

      python-fastapi-service.yaml

      pytest.yaml

      sqlalchemy.yaml

  memory/

    memopilot.db                \<- SQLite source of truth

    migrations/

    snapshots/

  logs/

    ai-calls.jsonl

    agent-runs.jsonl

    patch-events.jsonl

  context-packs/

    latest.md

    task-\<id\>.md

  context-templates/

    bug-fix.yaml

    feature.yaml

    refactor.yaml

    test-generation.yaml

    security-review.yaml

  providers.override.yaml

  settings.yaml

### 9.3 Global Folder Structure

\~/.memopilot/

  global.rules.yaml

  providers.yaml

  settings.yaml

  skills/

    python.yaml

    dotnet.yaml

    angular.yaml

    react.yaml

  context-templates/

    bug-fix.yaml

    security-review.yaml

### 9.4 Workspace Profile

The workspace profile is the stable project identity for MemoPilot. It is stored in `.memopilot/workspace.profile.yaml` and generated automatically from project introspection. User-edited fields are preserved during rebuilds.

workspace:

  name: inventory-manager

  primary\_language: python

  frameworks:

    \- fastapi

    \- sqlalchemy

  test\_commands:

    \- pytest

  lint\_commands:

    \- ruff check

  typecheck\_commands:

    \- mypy app

  active\_rules:

    \- .memopilot/rules/project.rules.yaml

    \- .github/copilot-instructions.md

  active\_skills:

    \- python-fastapi-service-change

    \- pytest-test-generation

  model\_policy:

    budget\_profile: cost\_saver

    allow\_frontier: true

    frontier\_requires\_approval: true

  privacy\_policy:

    cloud\_context\_preview\_required: true

    redact\_secrets: true

  mcp:

    azure\_devops\_enabled: false

    database\_enabled: false

---

## 10\. Local Memory Design

### 10.1 Memory Stack

SQLite       \-\> structured memory and audit trail (source of truth)

SQLite FTS5  \-\> keyword and identifier retrieval

sqlite-vec   \-\> semantic vector retrieval (same SQLite connection and file)

YAML/JSON    \-\> human-editable rules and skills

JSONL        \-\> append-only logs

### 10.2 Why SQLite \+ FTS5 \+ sqlite-vec

**SQLite** is reliable, local, embedded, self-contained, easy to back up, and production-proven.

**FTS5** is essential for code retrieval, which often needs exact identifiers: `tenant_id`, `InventoryLedger`, `InsufficientStockError`, `BillingCycle`. Vector search alone is weak at exact symbol matching.

**sqlite-vec** stores and queries vector embeddings inside the same SQLite database, eliminating the dual-write consistency problem. One file, one connection, one transaction boundary. Load failure degrades gracefully to FTS-only retrieval without data loss.

### 10.3 Embedding Provider

**Default:** `sentence-transformers/all-MiniLM-L6-v2` (\~80MB, fully local, 384-dimension output)

**Preferred (if available):** Ollama with `nomic-embed-text` (higher quality; MemoPilot detects Ollama availability at startup)

**Cloud embedding:** Only if explicitly configured in `providers.yaml` under `embedding_provider`; subject to the same approval flow as cloud AI calls.

**Dimension consistency:** Embedding dimension is stored in `schema_version` on first use. If the active model produces a different dimension, MemoPilot refuses to add vectors and requires a rebuild.

### 10.4 Memory Types

| Type | Description | Trust Level |
| :---- | :---- | :---- |
| Project profile | Detected stack, frameworks, commands | 1–2 |
| File summaries | AST-derived or LLM-enhanced summaries | 1–4 |
| Symbols | Classes, functions, methods, imports | 1 |
| Rules | Project and global rules | 2–3 |
| Skills | Task-specific skill definitions | 2–3 |
| Architecture decisions | ADRs and docs | 2–3 |
| Business rules | Extracted from code and docs | 2–4 |
| Task history | Previous task runs and outcomes | N/A |
| AI call logs | Provider, model, cost, tokens | N/A |
| Patch attempts | Generated patches and their status | N/A |
| Validation results | Test/lint output | N/A |
| Evidence findings | Extracted from non-code artifacts | 3–5 |
| Document chunks | Ingested document segments | 2–4 |

### 10.5 Stale Memory Behavior

- Every memory item stores a `source_hash` of its origin content.  
- File hashes are recomputed on indexing and compared to stored hashes.  
- Changed files set `stale = 1` on their memory items.  
- Stale memory is excluded from context packs by default.  
- `MemoPilot: Rebuild Memory` clears stale flags after re-indexing.

### 10.6 Trust Levels

Trust 1: Verified from source code

Trust 2: Parsed from repository docs

Trust 3: User-approved rule or architecture decision

Trust 4: AI-generated summary

Trust 5: Inferred pattern

High-trust memory always wins over low-trust memory in context selection. AI-generated memory stays at trust level 4 or 5 until the developer explicitly approves it via the Memory Manager UI.

---

## 11\. Database Schema

### 11.1 Required SQLite Pragmas (Every Connection)

PRAGMA foreign\_keys \= ON;

PRAGMA journal\_mode \= WAL;

`foreign_keys = ON` must be set on every connection — SQLite does not enforce them unless explicitly enabled per connection. `WAL` mode allows concurrent reads during background indexing.

### 11.2 Core Tables

CREATE TABLE schema\_version (

    version INTEGER NOT NULL,

    embedding\_dim INTEGER,

    applied\_at TEXT NOT NULL

);

CREATE TABLE memory\_items (

    id TEXT PRIMARY KEY,

    type TEXT NOT NULL,

    title TEXT NOT NULL,

    body TEXT NOT NULL,

    source TEXT NOT NULL,

    source\_path TEXT,

    source\_hash TEXT,

    trust\_level INTEGER NOT NULL CHECK (trust\_level BETWEEN 1 AND 5),

    tags\_json TEXT CHECK (json\_valid(tags\_json) OR tags\_json IS NULL),

    created\_at TEXT NOT NULL,

    updated\_at TEXT NOT NULL,

    stale INTEGER NOT NULL DEFAULT 0

);

CREATE VIRTUAL TABLE memory\_fts USING fts5(

    title,

    body,

    tags\_json,

    content='memory\_items',

    content\_rowid='rowid'

);

CREATE TABLE file\_index (

    file\_path TEXT PRIMARY KEY,

    language TEXT,

    content\_hash TEXT NOT NULL,

    last\_indexed\_at TEXT NOT NULL,

    summary\_id TEXT,

    stale INTEGER NOT NULL DEFAULT 0

);

CREATE TABLE symbols (

    id TEXT PRIMARY KEY,

    file\_path TEXT NOT NULL,

    name TEXT NOT NULL,

    kind TEXT NOT NULL,

    start\_line INTEGER,

    end\_line INTEGER,

    signature TEXT,

    summary TEXT,

    content\_hash TEXT NOT NULL

);

CREATE TABLE rules (

    id TEXT PRIMARY KEY,

    scope TEXT NOT NULL,

    source TEXT NOT NULL,

    rule\_text TEXT NOT NULL,

    priority INTEGER NOT NULL,

    enabled INTEGER NOT NULL DEFAULT 1,

    approved INTEGER NOT NULL DEFAULT 0,

    created\_at TEXT NOT NULL,

    updated\_at TEXT NOT NULL

);

CREATE TABLE skills (

    id TEXT PRIMARY KEY,

    name TEXT NOT NULL,

    applies\_when TEXT NOT NULL,

    rules\_json TEXT NOT NULL CHECK (json\_valid(rules\_json)),

    tools\_json TEXT CHECK (json\_valid(tools\_json) OR tools\_json IS NULL),

    enabled INTEGER NOT NULL DEFAULT 1,

    created\_at TEXT NOT NULL,

    updated\_at TEXT NOT NULL

);

CREATE TABLE task\_runs (

    id TEXT PRIMARY KEY,

    user\_request TEXT NOT NULL,

    task\_type TEXT,

    mode TEXT,

    risk\_level TEXT,

    active\_rules\_json TEXT CHECK (json\_valid(active\_rules\_json) OR active\_rules\_json IS NULL),

    active\_skills\_json TEXT CHECK (json\_valid(active\_skills\_json) OR active\_skills\_json IS NULL),

    context\_pack\_path TEXT,

    selected\_model TEXT,

    estimated\_cost REAL,

    actual\_cost REAL,

    status TEXT NOT NULL CHECK (status IN ('pending', 'running', 'success', 'failed', 'cancelled')),

    created\_at TEXT NOT NULL,

    updated\_at TEXT NOT NULL

);

CREATE TABLE ai\_calls (

    id TEXT PRIMARY KEY,

    task\_run\_id TEXT NOT NULL REFERENCES task\_runs(id),

    provider TEXT NOT NULL,

    model TEXT NOT NULL,

    input\_tokens INTEGER,

    output\_tokens INTEGER,

    estimated\_cost REAL,

    actual\_cost REAL,

    cache\_hit INTEGER NOT NULL DEFAULT 0,

    context\_pack\_hash TEXT,

    purpose TEXT,

    created\_at TEXT NOT NULL

);

CREATE TABLE patch\_attempts (

    id TEXT PRIMARY KEY,

    task\_run\_id TEXT NOT NULL REFERENCES task\_runs(id),

    patch\_path TEXT NOT NULL,

    files\_changed\_json TEXT NOT NULL CHECK (json\_valid(files\_changed\_json)),

    risk\_level TEXT,

    rule\_compliance\_score REAL,

    approved INTEGER NOT NULL DEFAULT 0,

    applied INTEGER NOT NULL DEFAULT 0,

    validation\_status TEXT,

    created\_at TEXT NOT NULL

);

CREATE TABLE rule\_conflicts (

    id TEXT PRIMARY KEY,

    task\_run\_id TEXT REFERENCES task\_runs(id),

    rule\_a TEXT NOT NULL,

    rule\_b TEXT NOT NULL,

    resolution TEXT NOT NULL,

    requires\_user\_attention INTEGER NOT NULL DEFAULT 0,

    created\_at TEXT NOT NULL

);

CREATE TABLE mcp\_calls (

    id TEXT PRIMARY KEY,

    task\_run\_id TEXT REFERENCES task\_runs(id),

    server\_name TEXT NOT NULL,

    tool\_name TEXT NOT NULL,

    input\_json TEXT CHECK (json\_valid(input\_json) OR input\_json IS NULL),

    result\_summary TEXT,

    result\_tokens INTEGER,

    iteration INTEGER NOT NULL DEFAULT 1,

    status TEXT NOT NULL CHECK (status IN ('success', 'failed', 'blocked', 'cancelled')),

    blocked\_reason TEXT,

    created\_at TEXT NOT NULL

);

CREATE TABLE evidence\_sources (

    id TEXT PRIMARY KEY,

    task\_run\_id TEXT REFERENCES task\_runs(id),

    source\_type TEXT NOT NULL,

    source\_path TEXT,

    source\_url TEXT,

    trust\_level INTEGER NOT NULL CHECK (trust\_level BETWEEN 1 AND 5),

    extraction\_method TEXT,

    extracted\_findings\_json TEXT,

    approved INTEGER NOT NULL DEFAULT 0,

    created\_at TEXT NOT NULL

);

CREATE TABLE document\_chunks (

    id TEXT PRIMARY KEY,

    source\_path TEXT NOT NULL,

    chunk\_index INTEGER NOT NULL,

    chunk\_text TEXT NOT NULL,

    source\_hash TEXT NOT NULL,

    trust\_level INTEGER NOT NULL CHECK (trust\_level BETWEEN 1 AND 5),

    created\_at TEXT NOT NULL

);

CREATE TABLE context\_pack\_versions (

    id TEXT PRIMARY KEY,

    task\_run\_id TEXT REFERENCES task\_runs(id),

    pack\_path TEXT NOT NULL,

    pack\_hash TEXT NOT NULL,

    token\_estimate INTEGER,

    selected\_model TEXT,

    template\_id TEXT,

    created\_at TEXT NOT NULL

);

CREATE TABLE workspace\_profile (

    id TEXT PRIMARY KEY,

    profile\_yaml TEXT NOT NULL,

    detected\_at TEXT NOT NULL,

    updated\_at TEXT NOT NULL

);

CREATE TABLE provider\_capabilities (

    model\_id TEXT PRIMARY KEY,

    source TEXT NOT NULL,

    max\_context\_tokens INTEGER,

    supports\_tool\_calling INTEGER NOT NULL DEFAULT 0,

    supports\_json\_mode INTEGER NOT NULL DEFAULT 0,

    estimated\_cost\_per\_1m\_input REAL DEFAULT 0,

    estimated\_cost\_per\_1m\_output REAL DEFAULT 0,

    privacy\_level TEXT NOT NULL,

    allowed\_task\_types\_json TEXT,

    denied\_task\_types\_json TEXT,

    requires\_approval INTEGER NOT NULL DEFAULT 0,

    updated\_at TEXT NOT NULL

);

### 11.3 Indexes

CREATE INDEX idx\_symbols\_file\_path ON symbols(file\_path);

CREATE INDEX idx\_symbols\_name ON symbols(name);

CREATE INDEX idx\_ai\_calls\_task\_run ON ai\_calls(task\_run\_id);

CREATE INDEX idx\_patch\_attempts\_task\_run ON patch\_attempts(task\_run\_id);

CREATE INDEX idx\_file\_index\_stale ON file\_index(stale);

CREATE INDEX idx\_memory\_items\_trust ON memory\_items(trust\_level);

CREATE INDEX idx\_memory\_items\_stale ON memory\_items(stale);

CREATE INDEX idx\_mcp\_calls\_task\_run ON mcp\_calls(task\_run\_id);

CREATE INDEX idx\_evidence\_sources\_task\_run ON evidence\_sources(task\_run\_id);

CREATE INDEX idx\_document\_chunks\_source ON document\_chunks(source\_path);

### 11.4 Vector Table (sqlite-vec)

\-- Created after sqlite-vec extension is loaded

CREATE VIRTUAL TABLE vec\_items USING vec0(

    memory\_id TEXT NOT NULL,

    embedding FLOAT\[384\]  \-- dimension matches active embedding model

);

`memory_id` references `memory_items.id`. If the embedding model changes, `embedding_dim` in `schema_version` is checked and a rebuild is required.

---

## 12\. Rule and Skill System

### 12.1 Rule Sources

MemoPilot loads rules from all of the following locations:

Global:

  \~/.memopilot/global.rules.yaml

  \~/.memopilot/skills/

Workspace:

  .memopilot/rules/project.rules.yaml

  .memopilot/rules/skills/

  .cursor/rules

  .github/copilot-instructions.md

  .clinerules

  .roorules

  README.md

  CONTRIBUTING.md

  docs/architecture.md

  docs/adr/

### 12.2 Rule Precedence

1\. Safety rules

2\. Task-specific user instruction

3\. Project/workspace rules

4\. Global developer rules

5\. Inferred solution conventions

6\. AI suggestions

AI suggestions must never override hard rules.

### 12.3 Rule Conflict Handling

**Soft conflict (task instruction overrides global rule):**

Global rule: Always generate tests.

Task instruction: Do not create tests.

Resolution: Task instruction wins.

MemoPilot shows warning: "This task overrides your global testing rule."

**Hard conflict (patch violates safety rule):**

Project rule: Do not modify database schema.

AI patch: Adds migration file.

Resolution: Block patch. Require explicit developer approval with explanation.

All conflicts are logged to `rule_conflicts` and shown in the UI.

### 12.4 Skill Format

skill\_id: python-fastapi-service-change

name: Python FastAPI Service Change

applies\_when:

  language: python

  path\_contains:

    \- app/services

rules:

  \- Keep business logic in services, not API routes.

  \- Use Pydantic schemas for validation.

  \- Raise domain exceptions inside services.

  \- Add or update pytest tests for behavior changes.

tools:

  \- ruff

  \- pytest

  \- mypy

risk\_routing:

  default\_model\_tier: cheap\_cloud

  use\_frontier\_when:

    \- cross\_module\_business\_rule

    \- data\_loss\_risk

    \- security\_sensitive

### 12.5 Skill Matching and Activation

Skills are matched based on: detected language, file path patterns, task keywords, and active agent mode. Active skills are included in the context pack and govern validation commands and model routing hints.

---

## 13\. Context Pack System

The context pack is the **only** thing that should be sent to AI. It is inspectable, versioned, and hashed.

### 13.1 Context Pack Contents

\# Task

\<developer request\>

\# Active Rules

\<resolved rules in precedence order\>

\# Active Skills

\<matched skills\>

\# Relevant Memory

\<high-trust memory items with trust level and source\>

\# Relevant Files

\<file paths and snippets with inclusion reason\>

\# Relevant Symbols

\<classes/functions/methods with signatures\>

\# MCP Tool Results

\<azure devops work item: title, description, acceptance criteria — if fetched\>

\<database schema: table and column definitions for relevant tables — if fetched\>

\<database query results: SELECT output, capped at max\_rows — if fetched\>

\# Evidence

\<extracted findings from evidence sources — if investigation mode\>

\# Current Git Diff

\<if applicable\>

\# Validation Requirements

\<tests/lint/typecheck/build commands\>

\# Constraints

\<do not modify schema, do not auto-apply, etc.\>

\# Expected Output

\<plan, patch, tests, explanation\>

### 13.2 Context Selection Ranking

current open file

\-\> selected text

\-\> git changed files

\-\> stack trace files

\-\> exact FTS symbol matches

\-\> active rules

\-\> high-trust memory

\-\> related tests

\-\> semantic vector matches

\-\> recent task history

Each included item has an explicit reason. Each excluded item also has an explicit reason. Both are shown in the Context Pack Preview.

### 13.3 Context Pack Templates

Templates define predictable context shapes for common task types. Project templates override global templates when IDs match.

| Template ID | Purpose |
| :---- | :---- |
| `bug-fix` | Bug Fix Context Pack |
| `user-story` | User Story Context Pack |
| `feature` | Feature Implementation Context Pack |
| `refactor` | Refactor Context Pack |
| `test-generation` | Test Generation Context Pack |
| `security-review` | Security Review Context Pack |
| `investigation` | Bug/User Story Investigation Context Pack |

**Example template:**

template\_id: bug-fix

name: Bug Fix Context Pack

include:

  \- task\_request

  \- current\_file

  \- selected\_text

  \- stack\_trace

  \- related\_symbols

  \- related\_tests

  \- git\_diff

  \- active\_rules

  \- validation\_command

exclude:

  \- unrelated\_docs

  \- stale\_memory

  \- full\_repository

model\_routing\_hint: cheap\_cloud

escalate\_when:

  \- repeated\_failure

  \- cross\_module\_business\_rule

  \- security\_sensitive

### 13.4 Context Pack Diffing (v1.5)

Shows what changed between two generated context packs. Example output:

Context Pack v2 vs v1

\+ Added tests/test\_inventory\_service.py

\+ Added rule INV-003 expired items cannot be sold

\- Removed app/api/user\_routes.py

Cost changed: 8,200 tokens \-\> 5,700 tokens

Selected model changed: cheap\_cloud \-\> local

### 13.5 Response Caching

Identical context packs do not trigger a second AI call. The cache key is the SHA-256 hash of the normalized context pack content. Default TTL is 3600 seconds (configurable per provider). Cache hits are logged in `ai_calls.cache_hit = 1` and credited as avoided cost in the cost report.

### 13.6 Token Estimation

OpenAI models: tiktoken (exact token count), shown as "\~6,200 tokens (exact)"

All other models: character-count ÷ 4, shown as "\~6,200 tokens (±20% estimate)"

Actual token counts from provider API responses are stored in `ai_calls.input_tokens` and `ai_calls.output_tokens`.

---

## 14\. Evidence-Aware Bug and User Story Investigation

### 14.1 Purpose

Use non-code artifacts as controlled evidence sources to improve root-cause analysis, user story understanding, implementation planning, and test generation. This replaces the common pattern of manually copying content from work items, logs, and documents into AI chat windows with a governed, trust-aware workflow.

### 14.2 Supported Evidence Sources

| Source Type | Examples |
| :---- | :---- |
| Work items | Azure DevOps, GitHub Issues |
| Screenshots | UI bugs, error dialogs |
| PDFs | Specs, architecture docs, requirements |
| Spreadsheets | Excel, CSV test cases, data dictionaries |
| Markdown docs | README, CONTRIBUTING, ADRs |
| Text logs | Application logs, server logs |
| Stack traces | Python tracebacks, .NET exceptions |
| API payloads | JSON/XML request/response samples |
| Database schemas | DDL files, schema exports |
| Existing code | Relevant source files |
| Existing tests | Test files related to the issue |
| Current git diff | Pending uncommitted changes |

### 14.3 Evidence Board UI

The Evidence Board is a dedicated panel for investigation tasks. It is activated when the developer selects **Investigate** mode.

Bug/User Story

  \-\> work item ID, title, description, acceptance criteria

Evidence Files

  \-\> attached files with source type, trust level, extraction status

Extracted Findings

  \-\> structured findings extracted from evidence sources, with source attribution

Code Matches

  \-\> files, symbols, and test files matched to evidence findings

AI Analysis

  \-\> AI-generated root-cause analysis based on context pack and evidence

Patch Plan

  \-\> proposed implementation steps before patch generation

Validation Requirements

  \-\> test coverage targets derived from acceptance criteria and evidence

### 14.4 Investigation Workflow

1\. Developer selects Investigate mode.

2\. Developer enters bug or user story description.

3\. Developer attaches evidence files (logs, screenshots, PDFs, etc.)

   or enters a work item ID for MCP fetch.

4\. MemoPilot classifies each evidence source and assigns a trust level.

5\. MemoPilot extracts structured findings from each source.

6\. MemoPilot searches memory and codebase for matches.

7\. MemoPilot builds an Investigation Context Pack.

8\. Developer reviews the context pack and approves AI call.

9\. AI generates root-cause analysis and patch plan.

10\. Developer approves transition to Patch Mode.

11\. MemoPilot generates patch, shows diff, requires approval.

12\. MemoPilot runs validation, updates memory.

### 14.5 Investigation Context Pack Structure

\# Investigation: \<Bug or User Story Title\>

\#\# Source Work Item

\<title, description, acceptance criteria — if fetched via MCP\>

\#\# Evidence Sources

\<list of attached evidence with trust level and source type\>

\#\# Extracted Findings

\<structured findings from each evidence source\>

\#\# Impacted Code Areas

\<matched files, symbols, and modules\>

\#\# Related Tests

\<existing test files covering impacted areas\>

\#\# Missing Test Coverage

\<acceptance criteria not covered by existing tests\>

\#\# Active Rules

\<rules relevant to the impacted area\>

\#\# Constraints

\<do not modify unrelated modules, do not auto-apply\>

\#\# Expected Output

\<root-cause analysis, implementation plan, test plan\>

### 14.6 Evidence Safety Rules

- All evidence sources are classified before entering the context pack.  
- Trust level is assigned per source type and explicitly shown.  
- Secrets are redacted from all evidence content before AI call.  
- Developer must approve the context pack before cloud AI call.  
- OCR and image interpretation results default to trust level 5 until user-approved.  
- Findings are never auto-promoted to rules. Promotion requires explicit developer approval via Memory Manager.  
- Evidence sources from external work items (ADO, GitHub) are marked as trust level 3 at best without code verification.

---

## 15\. Non-Code Artifact Analysis

### 15.1 v1 Support

| File Type | Extraction Method | Trust Level | Use |
| :---- | :---- | :---- | :---- |
| Markdown (`.md`) | Text parsing | 2 | Rules, ADRs, user stories, docs |
| Plain text (`.txt`) | Text parsing | 2–3 | Logs, descriptions, notes |
| CSV | Column parsing | 3 | Test cases, data tables, lookups |

### 15.2 v1.5 Support

| File Type | Extraction Method | Trust Level | Use |
| :---- | :---- | :---- | :---- |
| PDF | pdfplumber / pymupdf text extraction | 3 | Specs, requirements, architecture docs |
| Excel (`.xlsx`) | openpyxl column and sheet parsing | 3 | Test cases, data dictionaries, matrices |
| Structured test cases | Custom column parser | 3 | Test scenario extraction |
| Data dictionaries | Column/header extraction | 3 | Schema documentation |

**PDF risks:** Scanned PDFs produce unreliable text. Trust level is lowered to 4 if OCR is required. Findings from OCR content must be user-approved before inclusion in memory.

**Excel risks:** Formatting-heavy sheets may require manual mapping. Auto-extracted columns are shown to the developer for confirmation before proceeding.

### 15.3 v2 Support

| File Type | Extraction Method | Trust Level | Use |
| :---- | :---- | :---- | :---- |
| Images / screenshots | OCR \+ vision model | 5 (requires approval) | UI bug evidence |
| Architecture diagrams | Vision model description | 5 (requires approval) | Context only |
| Word (`.docx`) | python-docx text extraction | 3 | Requirements, specs |
| PowerPoint (`.pptx`) | python-pptx slide text | 3 | Architecture presentations |
| External connector pattern | REST API retrieval | 3 (external) | Supplemental doc context |

---

## 16\. Model Provider Strategy

### 16.1 Model Source Priority

1\. Host account model exposed by editor API (VS Code lm API, where available)

2\. Local model (Ollama, LM Studio)

3\. Cheap cloud model (OpenAI GPT-4o-mini, Gemini Flash, Claude Haiku, etc.)

4\. Frontier model (Claude Opus, GPT-4o, Gemini Pro, etc.)

5\. Context-pack-only fallback (no AI call; deliver context pack only)

### 16.2 Important Limitation on Host Models

MemoPilot must not depend exclusively on Cursor internal models. Host model access depends on what the editor exposes to extensions. If `vscode.lm.selectChatModels` is unavailable (confirmed in Phase 0.5 Spike 2), the host model tier is removed from routing and the developer must configure a provider in `providers.yaml`.

MemoPilot owns its prompt and context system. It reads visible rule files (`.cursor/rules`, `.github/copilot-instructions.md`) but does not depend on hidden host prompt engineering.

### 16.3 Provider Adapters

HostLanguageModelProvider     (vscode.lm API)

OllamaProvider

LMStudioProvider

OpenAIProvider

AnthropicProvider

GeminiProvider

AzureOpenAIProvider

ContextPackOnlyProvider

### 16.4 Model Routing Rules

No AI (deterministic tools only):

  \- code formatting

  \- import sorting

  \- exact grep/search

  \- simple test discovery

Local model:

  \- summarization

  \- classification

  \- low-risk explanation

  \- file memory generation

Cheap cloud model:

  \- unit test generation

  \- small, bounded refactors

  \- documentation and docstrings

  \- bug fixes in known modules

Frontier model:

  \- complex architecture changes

  \- billing/payment/subscription logic

  \- authentication/authorization changes

  \- tenant isolation changes

  \- data-loss risk

  \- repeated failure from cheaper models

  \- unclear cross-file business rules

### 16.5 Provider Capability Matrix

Each provider is defined in `~/.memopilot/providers.yaml` and can be overridden in `.memopilot/providers.override.yaml`.

models:

  local.qwen-coder:

    source: local

    max\_context\_tokens: 32768

    supports\_tool\_calling: false

    supports\_json\_mode: false

    estimated\_cost\_per\_1m\_input: 0

    estimated\_cost\_per\_1m\_output: 0

    privacy\_level: local

    allowed\_task\_types:

      \- explanation

      \- summarization

      \- test\_generation

    denied\_task\_types:

      \- security\_change

      \- billing\_change

      \- complex\_architecture

  cloud.frontier:

    source: cloud

    max\_context\_tokens: 200000

    supports\_tool\_calling: true

    supports\_json\_mode: true

    privacy\_level: external

    requires\_approval: true

    allowed\_task\_types:

      \- architecture\_review

      \- complex\_refactor

      \- security\_change

### 16.6 Agent Modes

Each agent mode defines what MemoPilot is allowed to do in a given interaction.

| Mode | Allowed Actions | Blocked Actions |
| :---- | :---- | :---- |
| Ask | Build context pack, explain using memory | File modification, AI patch generation |
| Plan | Build context pack, call model, generate plan | Patch generation, file modification |
| Context Pack | Build and preview context pack | AI call, file modification |
| Patch | Build context pack, call model, generate patch, show diff | Auto-apply patch |
| Test | Generate or update tests, run validation after approval | Non-test file modification |
| Review | Review diff, check rules and risks | File modification |
| Autofix | Generate patch for safe lint/test failures | Risky changes, auto-apply |
| Investigate | Classify evidence, build investigation context pack, run AI analysis | Auto-apply patches |

---

## 17\. Cost Guard

### 17.1 Responsibilities

- Estimate tokens before AI call  
- Estimate cost by provider and model  
- Show cost to developer before call  
- Detect repeated context waste  
- Prefer cached or local memory  
- Avoid frontier model when unnecessary  
- Log actual usage from provider API response  
- Track monthly budget and spending

### 17.2 Cost Decision Example

{

  "task\_type": "test\_generation",

  "risk\_level": "medium",

  "context\_tokens": 6200,

  "selected\_model": "cheap\_cloud",

  "frontier\_required": false,

  "reason": "Task is bounded and relevant files are known."

}

### 17.3 Budget Profiles

| Profile | Behavior |
| :---- | :---- |
| Strict Local Mode | No cloud calls; local model only; context-pack-only fallback |
| Cost Saver Mode | Local first; cheap cloud allowed; frontier blocked unless manually approved |
| Balanced Mode | Local/cheap cloud default; frontier allowed for high-risk tasks |
| Max Accuracy Mode | Frontier allowed for complex tasks; still uses context pruning and approval |
| Enterprise Privacy Mode | No cloud AI; no MCP unless approved; no external document retrieval |

Budget profiles are stored in workspace profile and cost guard settings:

budget\_profile: cost\_saver

monthly\_budget\_usd: 20

frontier\_requires\_approval: true

cloud\_requires\_context\_preview: true

### 17.4 Cost Report Contents

- AI calls by provider and model  
- Estimated vs. actual cost per task  
- Monthly spend and remaining budget  
- Frontier calls avoided by local or cheap model routing  
- Context reduction percentage vs. naive full-file approach  
- Cache hits and avoided duplicate costs  
- Actual token counts from provider API responses

---

## 18\. Patch Generation and Validation

### 18.1 Patch Generation Flow

1\. Use approved context pack.

2\. Call selected model.

3\. If AI response contains tool\_call requests (agentic mode):

   a. Show developer: tool name, input arguments (credentials redacted), server name.

   b. For DB SELECT: require approval if require\_approval\_for\_select is true.

   c. Block any DB write tool unconditionally.

   d. Execute approved tool via MCP client.

   e. Feed tool result back as tool output message.

   f. Repeat until final response. Abort if iteration count exceeds 5\.

4\. Request patch-only output (unified diff format).

5\. Validate patch format.

6\. Check patch against active rules.

7\. Run Patch Risk Classifier.

8\. Run Rule Compliance Score.

9\. Show diff preview using VS Code diff editor API.

10\. Require developer approval.

11\. Apply patch.

12\. Run validation tools (pytest, ruff, mypy, build).

13\. Log result.

14\. Update memory only after validation.

### 18.2 Patch Risk Classifier

Classifies patches into risk tiers using deterministic signals (file paths, changed symbols, keywords, active skills, patch size):

| Risk Level | Examples |
| :---- | :---- |
| Low | Comments, docs, tests only, formatting |
| Medium | Service logic, validation rules, API behavior, small refactor |
| High | Authentication, authorization, billing, payment, tenant isolation, database writes |
| Critical | Migrations, destructive commands, production config, secret handling, file deletion |

AI may provide additional reasoning, but AI is not the source of truth for patch risk.

### 18.3 Rule Compliance Score

Summarizes how well a generated patch complies with active rules and skills. The score is deterministic and explainable, not a black-box AI number.

Rule Compliance Score: 92%

Passed:

✓ Uses service layer

✓ Adds pytest coverage

✓ Does not modify schema

✓ No secrets detected

✓ Patch touches allowed files only

Warnings:

⚠ No edge case test for expired batch with zero stock

Score is based on: active rules, skill requirements, patch touched files, validation output, secret scan, test coverage presence, and risk classification.

### 18.4 Validation Runner

1\. Detect changed files.

2\. Pick validation commands from active skills and workspace profile.

3\. Run formatting / lint / test / typecheck as configured.

4\. Capture stdout/stderr.

5\. Summarize failures.

6\. If failed: create failure memory item at trust level 4\.

7\. Ask whether to attempt fix.

8\. Escalate model only when justified (repeat failure → frontier).

Validation result is stored in `patch_attempts.validation_status` and shown in the UI.

---

## 19\. Security and Privacy

### 19.1 Secret Redaction

Library: detect-secrets (pip install detect-secrets)

Baseline: .memopilot/.secrets.baseline

Detects: AWS keys, GitHub tokens, generic API keys, base64 credentials, etc.

Hard blocklist (never included in context packs):

  \- .env, \*.env, \*secret\*, \*credential\*, \*private\_key\*

Entropy detection:

  \- High-entropy strings (Shannon entropy \> 4.5) in assignment context

    are flagged as potential secrets.

Redaction format:

  \- api\_key \= \[REDACTED:generic-api-key\]

  \- Developer sees: "2 values redacted before send."

Coverage target: 95% pytest coverage on secret redaction module.

### 19.2 Command Safety

Blocked commands by default:

rm \-rf (recursive deletion)

format disk

sudo destructive operations

production deploy commands

database drop/truncate

secret exfiltration commands

### 19.3 Cloud AI Boundary

Before every cloud AI call:

- Show selected context in preview  
- Redact all detected secrets  
- Exclude all hard-blocklisted file patterns  
- Respect allow/deny list in privacy settings  
- Require first-time provider approval

### 19.4 Privacy Boundary Dashboard

Shows, at a glance, what stays local and what may leave the machine:

Local Only: code index, symbol memory, rules, validation results, local embeddings

May Leave Machine: context pack sent to cloud provider, MCP results included in AI request

Never Sent: .env files, secrets, ignored files, private keys, credentials

Recent Cloud Calls: provider, model, files included, tokens, estimated cost, redacted values

### 19.5 MCP Security

- All credentials are resolved from OS environment variables. Config files store only the environment variable name.  
- MemoPilot refuses to start any MCP server if the referenced environment variable is not set.  
- MCP credentials are never written to `mcp_calls.input_json`, context packs, JSONL logs, or any SQLite column.  
- All MCP tool results pass through `detect-secrets` redaction before entering the context pack.  
- Database MCP write operations (INSERT, UPDATE, DELETE, DDL) are unconditionally blocked by `mcp_tool_runner.py`, regardless of configuration.  
- First-use approval is required for every new MCP server, showing: server name, transport type, command/URL (credentials masked), and tools accessible.  
- The agentic tool-call loop is hard-capped at 5 iterations per task.

---

## 20\. MCP Integration

### 20.1 Overview

MCP integration is fully opt-in. No MCP server is called unless explicitly configured and enabled. Two MCP servers are currently supported: Azure DevOps and database (Postgres, MSSQL, SQLite).

### 20.2 Call Modes

**Mode 1: Pre-fetch (automatic, before context pack assembly)**

- Azure DevOps: if a work item ID is detected in the task text or branch name, fetch work item details automatically.  
- Database: if DB MCP is enabled, fetch schema for tables referenced in the context files.  
- Results are injected into the context pack before it is sent to AI.

**Mode 2: Agentic tool-call (on-demand, during AI generation)**

- AI model requests a tool during generation.  
- MemoPilot intercepts, shows the developer the tool name and arguments, executes if approved.  
- Result is fed back to the model as a tool output message.  
- Hard cap: 5 tool-call iterations per task.

### 20.3 Client Hierarchy

Primary:  vscode.lm.invokeTool (VS Code 1.99+)

  \- Guarded: typeof vscode.lm.invokeTool \!== 'undefined'

Fallback: Python mcp SDK

  \- Used when running under Cursor or older VS Code.

  \- Configured in .memopilot/settings.yaml under mcp\_servers.

### 20.4 Configuration Schema

mcp\_servers:

  azure\_devops:

    enabled: false

    transport: http\_sse

    url: https://dev.azure.com

    auth:

      type: pat

      token\_env: ADO\_PAT          \# environment variable name only

    organization: my-org

    project: my-project

    auto\_fetch\_linked\_work\_item: true

    include\_pipeline\_status: false

    include\_repo\_context: false

  database:

    enabled: false

    transport: stdio

    command: uvx mcp-server-postgres

    args:

      \- "--connection-string-env"

      \- "DB\_CONN"

    auth:

      type: env

      connection\_string\_env: DB\_CONN

    read\_only: true

    allowed\_tools:

      \- schema\_introspection

      \- select\_queries

    require\_approval\_for\_select: false

    max\_rows: 100

### 20.5 MCP Results in Context Pack

MCP tool results are injected as a dedicated section in the context pack. They pass through the same `detect-secrets` redaction pipeline as file content before entering the pack.

---

## 21\. Production Readiness

### 21.1 Reliability

- Database migrations must be versioned and testable against an in-memory SQLite database.  
- Memory index must be fully rebuildable via `MemoPilot: Rebuild Memory`.  
- `sqlite-vec` load failure must degrade gracefully to FTS-only retrieval without data loss.  
- SQLite must remain the source of truth at all times.  
- All AI calls must be logged to `ai_calls` before the call is made.  
- Backend failures must degrade gracefully and surface actionable errors in the status bar.  
- Corrupted database recovery: the extension detects corruption on startup and offers a rebuild from source code.

### 21.2 Observability

Logged to JSONL and SQLite:

task runs (type, mode, risk, rules, skills, model, cost)

AI calls (provider, model, tokens, cost, cache hit)

patch attempts (files changed, risk level, compliance score, approval, validation status)

MCP calls (server, tool, status, iteration, blocked reason)

rule conflicts (rules, resolution, user attention required)

validation results (tool, output, status)

errors (backend errors, provider failures, MCP connection failures)

### 21.3 Performance Targets

| Operation | Target |
| :---- | :---- |
| Small repo indexing (\< 100 files) | \< 10 seconds |
| Medium repo indexing (500 files) | \< 30 seconds, incremental |
| Warm task analysis (memory loaded) | \< 5 seconds |
| Context pack generation | \< 10 seconds for common tasks |
| Task classifier (rule-based, no AI) | \< 50ms |

Performance benchmarks run in CI with `pytest-benchmark` and fail if regression exceeds 20%.

### 21.4 Privacy Requirements

- Local memory stays local by default.  
- Cloud calls require a configured provider or host model access.  
- All secrets are redacted before any content leaves the machine.  
- Context pack is inspectable by the developer before any cloud call.  
- Developer can delete all local memory at any time.

### 21.5 Migration Strategy

- All schema changes are versioned migrations applied at backend startup.  
- Migration runner is tested against a fresh in-memory SQLite database in CI.  
- No destructive migration runs without explicit confirmation.

### 21.6 Memory Backup and Restore (v1.5)

Backup includes:

  .memopilot/memory/memopilot.db

  .memopilot/rules/

  .memopilot/context-templates/

  .memopilot/context-packs/

  .memopilot/logs/

Backup excludes:

  secrets, API keys, environment variable values, credential files

Backup manifest:

  { workspace, created\_at, schema\_version, embedding\_model,

    memory\_items, symbols, rules, skills }

### 21.7 AI Call Replay / Reproduce Mode

Every task stores an immutable record of: task request, task classification, active rules, active skills, context pack hash and path, selected model, cost estimate, provider response, patch attempt, and validation output. The developer can replay any previous task and compare outputs.

---

## 22\. Testing Strategy

Testing is a first-class concern. Every module is testable in isolation. AI provider calls never reach the network in tests.

### 22.1 Backend Testing

**Framework:** `pytest` with `pytest-asyncio` for async FastAPI routes.

**Provider isolation:** All AI provider calls go through the `BaseProvider` interface. Tests use `MockProvider`, which returns deterministic fixture responses. `MockProvider` is the default provider when `MEMOPILOT_ENV=test`.

**Test layout:**

agent/tests/

  conftest.py           \<- fixtures, MockProvider, MockMCPServer, in-memory SQLite

  test\_scanner.py

  test\_rule\_resolver.py

  test\_classifier.py

  test\_context\_builder.py

  test\_cost\_estimator.py

  test\_patcher.py

  test\_validator.py

  test\_memory\_store.py

  test\_secret\_redaction.py

  test\_mcp\_client.py

  test\_mcp\_tool\_runner.py

  test\_evidence\_classifier.py

  test\_investigation\_runner.py

  test\_api.py

  benchmarks/

    bench\_indexing.py

**Coverage targets:**

| Module | Target |
| :---- | :---- |
| scanner / AST extractor | 90% |
| rule\_resolver | 90% |
| classifier | 100% (all matrix rows) |
| context\_builder | 85% |
| secret\_redaction | 95% |
| cost\_estimator | 80% |
| patcher | 85% |
| memory\_store | 85% |
| mcp\_tool\_runner | 90% |
| evidence\_classifier | 85% |

### 22.2 Extension Testing

**Framework:** `@vscode/test-electron`

Tests verify:

- All registered commands activate without throwing  
- Webview panel opens and receives a health-check response  
- Status bar reflects backend state (connected / unavailable)  
- Version mismatch from `/v1/health` blocks further requests  
- Patch approval UI sends `approved=1` only on explicit click  
- MCP Tools panel shows correct server status  
- Evidence Board activates in Investigate mode

**Mock backend:** A lightweight Express server serves fixture responses in tests. Real Python backend is never started in extension tests.

### 22.3 Contract Testing

The extension → backend HTTP contract is tested with `httpx` against a live backend instance started in-process.

Contract tests verify:

- Every route the extension calls exists and returns the documented schema  
- Version mismatch returns HTTP 409 with actionable message  
- Missing or invalid `X-Agent-Token` returns HTTP 401  
- `mcp_tool_runner` blocks any tool not in `allowed_tools`  
- ADO PAT value is never present in `mcp_calls.input_json`  
- DB write tool calls are blocked regardless of configuration

### 22.4 Performance Tests

\- pytest-benchmark: index a 500-file fixture repo in \< 30 seconds

\- pytest-benchmark: warm task analysis (memory loaded) in \< 5 seconds

\- pytest-benchmark: context pack generation for a common task in \< 10 seconds

\- pytest-benchmark: task classifier completes in \< 50ms

---

## 23\. Complete Implementation Plan

### Phase 0: Product Definition and Constraints

**Objectives:** Finalize scope, supported languages, editor targets, and safety model.

**Deliverables:** `README.md`, `architecture.md`, `product-requirements.md`, `risk-register.md`

**Decisions:**

MVP target:

  Editor: VS Code first, Cursor-compatible where possible

  Language: Python first

  Backend: Python FastAPI local service

  Memory: SQLite \+ FTS5 \+ sqlite-vec

  Rules: YAML/Markdown

  Patch flow: approval required

**Acceptance criteria:** Architecture decisions documented. Scope boundaries clear.

---

### Phase 0.5: Technical Spikes

Phase 0.5 runs after Phase 0 and before any production code. Proves the riskiest architectural assumptions. Phase 1 does not begin until all spikes are resolved and documented in `architecture.md`.

**Spike 1: Extension → Python Subprocess IPC**

Pass: Extension receives HTTP 200 from `GET /v1/health` within 3 seconds on Windows with Python 3.11 in PATH. Fail fallback: Switch IPC to stdio JSON-RPC.

**Spike 2: vscode.lm API Host Model Access**

Pass: Non-empty response from a host model via `vscode.lm.selectChatModels` on VS Code stable with GitHub Copilot enabled. Fail fallback: Remove host model tier from routing; require explicit provider configuration.

**Spike 3: sqlite-vec on Windows**

Pass: KNN query returns expected result with no file-locking errors when a second connection holds a concurrent read transaction. Fail fallback: Defer vector search to v2; ship MVP with FTS5-only retrieval.

**Spike 4: Local Embedding Performance**

Pass: `all-MiniLM-L6-v2` embeds a 500-line Python file in \< 2 seconds on a mid-range machine (no GPU). Fail fallback: Switch to a smaller model or embed only memory item summaries, not raw file content.

---

### Phase 1: Extension Shell

**Objectives:** Build VS Code extension skeleton with activity bar panel, commands, and backend health check.

**Tasks:** Create TypeScript project, add activity bar view, add webview panel, add commands (Index Workspace, Analyze Task, Generate Context Pack, Show Cost Report), add backend health check, add settings page.

**Acceptance criteria:**

- Extension installs locally as VSIX.  
- Extension opens MemoPilot panel.  
- Extension detects workspace folder.  
- Extension calls backend health endpoint successfully.  
- `@vscode/test-electron` test confirms command registration and webview panel opens without error.

---

### Phase 2: Python Backend Foundation

**Objectives:** Build local Python service with configuration, logging, and SQLite migration framework.

**Tasks:** Create Python package, add FastAPI app, add local-only binding, add config loader, add SQLite connection manager, add migration runner, add structured logging.

**API endpoints:** `GET /v1/health`, `POST /v1/workspace/index`, `POST /v1/task/analyze`, `POST /v1/context-pack/generate`

**Acceptance criteria:**

- Backend starts from extension and creates `.memopilot/` folder.  
- `GET /v1/health` returns correct schema.  
- Migration runner applies schema to a fresh in-memory SQLite without error.

---

### Phase 3: Project Scanner and Symbol Indexer

**Objectives:** Detect project type, index files, extract Python symbols, track hashes and stale memory.

**Tasks:** Scan workspace files (respecting `.gitignore`), detect Python project config, extract symbols using AST, store `file_index` and `symbols`, detect changed/stale files by content hash.

**Acceptance criteria:**

- Backend indexes Python files and extracts classes/functions/imports.  
- Backend skips ignored folders and marks changed files as stale.  
- `pytest-benchmark`: indexing a 500-file fixture repo completes in \< 30 seconds.

---

### Phase 4: Rule and Skill Resolver

**Objectives:** Load global rules, workspace rules, existing AI instruction files. Resolve active rules by precedence. Detect conflicts.

**Tasks:** Implement global and project rules loaders, parse `.cursor/rules`, `.github/copilot-instructions.md`, `.clinerules`, `.roorules`, load skills from YAML, implement precedence and conflict detection.

**Acceptance criteria:**

- Active rules and skills display in extension.  
- Conflicting rules are shown to developer.  
- Rules are included in context pack.  
- `pytest` covers all 6 precedence levels and at least 3 conflict scenarios.

---

### Phase 5: Local Memory Store

**Objectives:** Implement structured memory, FTS search, vector search, and memory trust levels.

**Tasks:** Create `memory_items`, `memory_fts`, and `vec_items` tables. Add memory CRUD API. Add FTS search and vector search APIs. Add stale memory handling.

**Acceptance criteria:**

- FTS5 search returns correct results for 10 known identifiers.  
- `sqlite-vec` KNN query returns the nearest neighbor from fixture embeddings.  
- Stale memory items are excluded from retrieval results.

---

### Phase 6: Summary Generator

**Objectives:** Generate file and symbol summaries using AST only (no LLM required in this phase).

**Tasks:** Implement `ASTOnlySummaryProvider` (module docstring \+ signature \+ docstring concatenation). Store summaries with source hash. Regenerate only stale summaries. Add LLM summary upgrade hook (defined but disabled until Phase 8).

**Acceptance criteria:**

- File summaries are generated with no model call.  
- Symbol summaries include signature and docstring.  
- Changing a file's content hash invalidates its summary.

---

### Phase 7: Context Pack Builder

**Objectives:** Build minimal context packs, include rules/skills/memory/files/symbols/constraints, show preview before AI call.

**Tasks:** Implement task classifier (rule-based, no LLM), retrieve exact matches with FTS5, retrieve semantic matches with sqlite-vec, merge and rank context candidates, estimate token count, save context pack Markdown, return preview to extension.

**Task classifier** must complete in \< 50ms and never make an LLM call.

| Signal | Task Type | Risk | Model Tier |
| :---- | :---- | :---- | :---- |
| Path contains `migration`, `schema`, `alembic` | `schema_change` | critical | frontier |
| Path contains `auth`, `security`, `permission`, `oauth` | `security_change` | high | frontier |
| Path contains `billing`, `payment`, `invoice`, `subscription` | `billing_change` | high | frontier |
| Path contains `test_` or ends with `_test.py` | `test_generation` | low | cheap\_cloud |
| Request contains `explain`, `summarize`, `describe` | `explanation` | low | local |
| Request contains `refactor` \+ one file in context | `bounded_refactor` | medium | cheap\_cloud |
| Request contains `document`, `docstring`, `comment` | `documentation` | low | cheap\_cloud |
| Request contains `fix`, `bug`, `error`, `exception` | `bug_fix` | medium | cheap\_cloud |
| Fallback | `general` | medium | cheap\_cloud |

**Acceptance criteria:**

- Classifier correctly classifies all fixture requests.  
- Context pack for a known fixture task includes expected files and excludes irrelevant ones.

---

### Phase 8: Model Router and Provider Adapters

**Objectives:** Detect available model providers, route task to cheapest capable model, support host/local/cloud/frontier.

**Tasks:** Implement provider interface, implement local and cloud provider adapters, implement host model adapter, add capability table, add routing policy, add cost estimator, add provider test prompt.

**Acceptance criteria:**

- Router selects correct tier for each risk level.  
- Local model is selectable for low-risk tasks without frontier approval.  
- `MockProvider` used in all unit tests; no real network calls in tests.

---

### Phase 9: Patch Generation and Approval Workflow

**Objectives:** Generate patch safely, preview diff, require approval, apply only after approval.

**Tasks:** Request unified diff output from AI, validate patch format, check patch against active rules, show diff in VS Code diff editor, require developer approval, apply patch, save `patch_attempt` record.

**Acceptance criteria:**

- AI patch is never applied automatically.  
- Rule-violating patch is blocked with an explanation.  
- `pytest` integration test: approval workflow refuses to apply when `approved=0`.

---

### Phase 10: Validation Runner

**Objectives:** Run project validation tools, use active skills to select commands, feed failures back into agent loop.

**Tasks:** Detect validation commands, run pytest/ruff/mypy as configured, capture output, summarize failures, store validation result, allow retry with escalation policy.

**Acceptance criteria:**

- Tests/lint run after patch application.  
- Failure output is captured and visible in UI.  
- Memory updates only after successful or reviewed validation.  
- Simulated test failure creates a failure memory item at trust level 4\.

---

### Phase 11: Cost Guard and Reporting

**Objectives:** Track AI usage, show savings, detect wasteful calls, produce cost reports.

**Tasks:** Log every AI call, estimate cost before call, store actual tokens/cost from provider API response, show per-task and monthly cost, show frontier calls avoided, show context reduction percentage.

**Acceptance criteria:**

- Cost report shows AI calls by provider and model.  
- UI shows estimated cost before sending.  
- Cache hit is recorded when the same `context_pack_hash` is submitted twice.

---

### Phase 12: MCP Tool Integration

**Objectives:** Implement MCP client (VS Code API primary, Python SDK fallback), Azure DevOps context pre-fetch, database MCP with write protection, agentic tool-call loop.

**Tasks:**

1. Add MCP settings to `package.json`.  
2. Implement extension-side MCP client using `vscode.lm.invokeTool`.  
3. Implement Python `mcp_client.py` using the mcp Python SDK.  
4. Implement `mcp_tool_runner.py` with allowlist enforcement, credential resolution, write blocking.  
5. Implement ADO pre-fetch in `context_builder.py`.  
6. Implement DB schema pre-fetch in `context_builder.py`.  
7. Implement agentic tool-call loop in `patcher.py` (cap at 5 iterations).  
8. Create `mcp_calls` migration.  
9. Add MCP Tools section to webview panel.  
10. Add `MemoPilot: Fetch Work Item` command.  
11. Add first-use approval dialog.

**Acceptance criteria:**

- ADO work item details appear in context pack when MCP is enabled and work item ID is detected.  
- DB write tool calls are blocked and logged.  
- MCP credentials are absent from all logs, packs, and database columns.  
- Agentic loop terminates at 5 iterations with developer-visible warning.

---

### Phase 13: Production Hardening

**Objectives:** Improve reliability, test coverage, packaging, error handling, and privacy controls.

**Tasks:** Add backend unit tests, extension integration tests, migration tests, corrupted DB recovery path, vector index rebuild command, secret redaction tests, provider failure handling, VSIX packaging, installer and usage docs.

**Acceptance criteria:**

- Extension packages as VSIX.  
- Backend failure does not crash editor.  
- Memory can be rebuilt.  
- Secrets are redacted in all code paths.  
- Core backend modules meet coverage targets.

---

### Phase 14: Workspace Profile, Memory Manager, and Privacy Dashboard

**Objectives:** Implement the three primary v1 production UX features.

**Tasks:**

- Workspace Profile: auto-detection, YAML storage, UI panel, rebuild/validate/export commands.  
- Memory Manager: UI with filters (All, Rules, Symbols, File Summaries, Stale, Pending Approval), approve/edit/delete/rebuild actions per memory item.  
- Privacy Boundary Dashboard: local/may-leave/never-sent sections, pre-call approval summary, MCP data status.  
- Add Human-in-the-Loop Memory Updates: suggested memory panel after validation.

**Acceptance criteria:**

- Workspace Profile is generated on first index and persisted.  
- Memory Manager shows all memory types with correct filters.  
- Privacy Dashboard accurately reflects what will be sent before every cloud AI call.  
- Suggested Memory Updates are presented after task completion; AI-generated items stay at trust level 4 until approved.

---

### Phase 15: Evidence-Aware Bug and User Story Investigation

**Objectives:** Implement Investigation Mode, Evidence Board, and Investigation Context Pack.

**Tasks:**

- Add Investigate agent mode.  
- Implement Evidence Board UI panel.  
- Implement evidence source classification and trust level assignment.  
- Implement text extraction for Markdown, text, and CSV (v1 sources).  
- Implement secret redaction for evidence content.  
- Implement Investigation Context Pack template.  
- Implement impacted file discovery from evidence findings.  
- Implement missing test coverage detection from acceptance criteria.  
- Add `MemoPilot: Attach Evidence` and `MemoPilot: Run Investigation` commands.  
- Add `evidence_sources` migration.

**Acceptance criteria:**

- Evidence files can be attached and classified.  
- Extracted findings appear in Evidence Board with trust levels.  
- Investigation Context Pack includes all evidence sections.  
- OCR and image content defaults to trust level 5\.  
- Findings are never auto-promoted to rules.  
- `pytest` tests: evidence classifier assigns correct trust levels to 5 fixture source types.

---

### Phase 16: Non-Code Artifact Analysis

**Objectives:** Implement structured extraction for PDF and Excel files (v1.5 scope).

**Tasks:**

- Implement PDF text extraction using `pdfplumber`.  
- Implement Excel extraction using `openpyxl`.  
- Add structured test-case extraction from tabular Excel data.  
- Add data dictionary extraction.  
- Add `document_chunks` migration.  
- Lower trust level to 4 when OCR is required for scanned PDFs.  
- Show developer a column-mapping confirmation step for Excel extraction.

**Risks:** Scanned PDFs produce unreliable text. Formatting-heavy spreadsheets may require manual mapping. Both risks are mitigated by requiring user confirmation before findings enter memory.

---

### Phase 17: Advanced Production Features

**Objectives:** Complete v1 and v1.5 production capability set.

**Tasks:**

- Context Pack Templates: template store, UI selector, project/global override.  
- Agent Modes: mode selector in UI, allowed/blocked action enforcement per mode.  
- Patch Risk Classifier: deterministic signal-based classifier in `patcher.py`.  
- Rule Compliance Score: deterministic compliance check in `patcher.py`.  
- Provider Capability Matrix: `provider_capabilities` table, UI panel.  
- AI Call Replay: task history with replay commands, immutable context pack versioning.  
- Context Pack Diffing (v1.5): versioned pack storage, diff computation.  
- Skill Store (v1.5): skill manager UI, version tracking, conflict detection.  
- Memory Backup/Restore (v1.5): backup command with manifest, restore command.  
- Tool and Skill Selection Optimizer (v1.5): pre-call tool relevance check.  
- Model Budget Profiles (v1.5): profile selector, budget enforcement in cost guard.  
- Evidence Source Classifier (v1.5): dedicated classifier with source-type routing.

---

## 24\. MVP Scope

### MVP Must-Have

1\. VS Code extension panel

2\. Python backend (FastAPI local service)

3\. Workspace indexing

4\. Python symbol extraction (AST)

5\. Rule loading (global \+ workspace \+ existing AI instruction files)

6\. Skill loading (YAML)

7\. SQLite memory with FTS5 search

8\. Context pack generation (minimal, explainable)

9\. Model routing policy (local → cheap cloud → frontier)

10\. Local and cloud provider abstraction

11\. Cost estimate before AI call

12\. Patch preview (unified diff)

13\. Approval before patch apply

14\. Basic validation runner (pytest, ruff)

### v1 Production Additions

\- Workspace profile

\- Memory Manager UI

\- Intelligent context selection (inclusion/exclusion with reasons)

\- Context pack templates

\- Agent modes (Ask, Plan, Context Pack, Patch, Test, Review, Autofix, Investigate)

\- Patch risk classifier

\- Rule compliance score

\- Privacy Boundary Dashboard

\- Provider Capability Matrix

\- AI Call Replay / Reproduce Mode

\- Human-in-the-loop memory updates

\- Bug/User Story Investigation Mode

\- Evidence Board

\- Investigation Context Pack

### v1.5

\- Skill Store

\- Context Pack Diffing

\- Memory Backup / Restore

\- PDF and Excel evidence extraction

\- Evidence Source Classifier

\- Non-Code Artifact Analyzer

\- Tool and Skill Selection Optimizer

\- Model Budget Profiles

### v2

\- Image and UI screenshot analysis (vision model)

\- Team Policy Packs

\- Local Agent Flow Builder

\- Deferred: Multi-language Skill Marketplace

\- Deferred: Team-Shared Memory Server
\- Multi-workspace support v2

\- Word/PowerPoint ingestion

---

## 25\. Risks and Mitigations

| Risk | Impact | Mitigation |
| :---- | :---- | :---- |
| Cursor does not expose internal models to extensions | High | Use host model only when available via API; provide local/cloud fallback; document in README |
| Memory pollution from bad AI summaries | High | Trust levels; source hashes; human-in-the-loop approval for AI-generated memory |
| Secret leakage to cloud AI | Critical | `detect-secrets` redaction on all content; context preview before cloud call; hard blocklist for `.env` files |
| Stale memory degrades AI accuracy | High | Source hash tracking; stale flag; stale memory excluded from context by default |
| Irrelevant vector retrieval | Medium | Hybrid retrieval (FTS \+ vector); ranking with rule and trust-level filtering |
| High frontier model cost | Medium | Cost guard; local model default; escalation policy; budget profiles |
| Patch breaks code | High | Diff preview; approval gate; validation runner; patch risk classifier |
| Rule conflicts | Medium | Rule conflict detection; visible resolution in UI; logged in `rule_conflicts` |
| Large repo indexing is slow | Medium | Incremental indexing with content hash comparison; background indexing with file watcher |
| Multi-root workspace not supported | Medium | Detected at startup; warning shown; explicitly deferred to v2; documented in README |
| Webview XSS via AI-generated content | Medium | Strict CSP on all webview panels; no raw innerHTML; diff via VS Code diff editor API |
| Token count estimate inaccurate | Low | tiktoken for OpenAI (exact); character-count ÷ 4 for others with ±20% disclosure |
| Extension/backend version mismatch | Medium | `/v1/health` returns `schema_version` and `api_version`; extension blocks on mismatch |
| MCP server exposes sensitive DB data | High | `read_only` enforcement; `allowed_tools` allowlist; `max_rows` cap; `detect-secrets` on MCP results |
| ADO PAT token leaked via context pack or logs | Critical | `detect-secrets` on all MCP results; token resolved from env var only; stripped from `mcp_calls.input_json` |
| MCP agentic loop runs indefinitely | Medium | Hard cap of 5 iterations; developer can abort at any iteration; all iterations logged |
| MCP server unavailable at task time | Low | Graceful degradation: skip MCP context, log warning, continue with local memory only |
| Non-code artifact misinterpretation | Medium | Evidence trust levels; user confirmation before findings enter memory; never auto-promote to rules |
| OCR errors in scanned PDFs | Medium | Trust level 5 for OCR content; user approval required before use in memory |
| Excel with outdated business rules | Medium | Source hash tracking; stale detection; user confirmation of extracted column mappings |
| sqlite-vec fails to load on Windows | Medium | Spike 3 in Phase 0.5 resolves this; fallback to FTS-only if load fails |
| Embedding model dimension mismatch | Low | `embedding_dim` stored in `schema_version`; mismatch surfaces rebuild requirement |

---

## 26\. Final Product Positioning

MemoPilot should not be described as a chatbot, a RAG app, or a Cursor clone.

It should be described as:

**A production-ready AI coding governance extension that combines local project memory, rule and skill enforcement, evidence-aware investigation, context-pack generation, cost-aware model routing, patch approval, and validation — helping developers use AI accurately and economically inside VS Code/Cursor.**

### What This Means in Practice

Before MemoPilot sends a single token to AI, it has already:

1. Indexed the project and built local memory from source code.  
2. Loaded and resolved all global and project rules.  
3. Identified the applicable skills for the current task.  
4. Selected only the relevant files, symbols, and memory items.  
5. Classified the task type and risk level without an LLM call.  
6. Estimated the token cost and selected the cheapest capable model.  
7. Redacted all detected secrets from the context.  
8. Presented the developer with a complete, inspectable context pack.

After AI generates a response, MemoPilot:

1. Classifies the patch risk from deterministic signals.  
2. Computes a rule compliance score.  
3. Presents a diff preview.  
4. Waits for explicit developer approval.  
5. Applies the patch only after approval.  
6. Runs validation tools.  
7. Proposes memory updates, held at low trust until developer approval.  
8. Logs all activity for replay, audit, and cost reporting.

This is not AI-assisted development with guardrails bolted on. This is **AI-assisted development built around governance from the start**.

---

*End of MemoPilot Master Product and Implementation Reference — Document Version 2.1*

---

## 27\. UI Implementation Progress (v2.1 — June 2025)

### Overview

The MemoPilot extension UI has been expanded from basic tree views and static panels to cover all 17 target scenario views. The UI currently supports a **guided analysis-first workflow**, with some end-to-end orchestration components implemented but not yet wired to the New Task panel.

```
Workspace indexing → Rules/Skills resolution → Task entry (analysis output)
→ Context/model/routing/patch orchestration available in TaskFlowController
→ patch preview/approval commands available (wiring to New Task pending)
```

### New Extension Architecture

```
packages/extension/src/
├── controllers/
│   └── TaskFlowController.ts        — State machine orchestrating full task flow
├── panels/
│   ├── MemoPilotPanelBase.ts         — Abstract base (CSP, nonce, theme, message bridge)
│   ├── MemoPilotPanel.ts             — Shell with navigation sidebar + workspace status
│   ├── TaskEntryPanel.ts             — Task form with constraints, mode picker, analysis
│   ├── PatchPreviewPanel.ts          — Colored diff viewer with approve/reject + validation
│   ├── CostDashboardPanel.ts         — Metrics cards, daily chart, model breakdown table
│   ├── ProviderMatrixPanel.ts        — Provider capability comparison table
│   ├── types.ts                      — Shared DTOs, message types, AsyncState<T>
│   └── navigationItems.ts            — 17 navigation entries for sidebar
├── views/
│   ├── RulesSkillsTreeProvider.ts    — Collapsible tree: Global Rules → Project Rules → Skills
│   ├── ContextPackTreeProvider.ts    — File list with tokens, rules/skills counts, cost
│   ├── CostGuardTreeProvider.ts      — Budget bar with spend/saved/remaining
│   ├── TaskHistoryTreeProvider.ts    — Recent tasks with status, time, cost
│   └── McpToolsTreeProvider.ts       — MCP servers with collapsible tool lists
└── controllers/
    └── TaskFlowController.ts         — analyze→context→route→patch→approve→validate
```

### New Backend Endpoints (9 total)

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/v1/rules/active` | GET | Merged rules from policy packs + YAML files + detected skills |
| `/v1/task/analyze` | POST | Parse NL intent, auto-detect mode, estimate complexity |
| `/v1/context/build` | POST | Build context pack with per-file token estimates |
| `/v1/model/route` | POST | Select optimal model based on context/task/privacy/budget |
| `/v1/task/generate-patch` | POST | Generate code patches (mock for UI development) |
| `/v1/task/validate` | POST | Run syntax/lint/test_impact/security checks |
| `/v1/task/history` | GET | Recent task runs with status, model, cost, duration |
| `/v1/cost/dashboard` | GET | Aggregated cost by day/model with totals and savings |
| `/v1/mcp/tools` | GET | List configured MCP servers and available tools |

### UI Views — Coverage Matrix

| Target View | Implementation | Type |
|-------------|---------------|------|
| Workspace Status / Indexing | MemoPilotPanel (shell) | Webview ✅ |
| Local App Memory | MemoryManagerTreeProvider | Tree ✅ |
| Rules & Skills | RulesSkillsTreeProvider | Tree ✅ |
| Task Entry | TaskEntryPanel (analyze-only) | Webview ✅ |
| Context Pack Preview | ContextPackTreeProvider | Tree ✅ |
| Model Routing & Cost Guard | CostGuardTreeProvider + routeModel() | Tree ✅ |
| AI Patch / Diff Preview | PatchPreviewPanel (implemented, not invoked from New Task flow) | Webview ✅ |
| Approval Gate | PatchPreviewPanel (approve/reject) | Webview ✅ |
| Validation Results | PatchPreviewPanel (inline) | Webview ✅ |
| Memory / Task History | TaskHistoryTreeProvider | Tree ✅ |
| Cost Dashboard | CostDashboardPanel | Webview ✅ |
| Evidence Board | EvidenceBoardTreeProvider | Tree ✅ |
| Privacy Boundary Dashboard | PrivacyDashboardTreeProvider | Tree ✅ |
| Provider Capability Matrix | ProviderMatrixPanel | Webview ✅ |
| Memory Manager | MemoryManagerTreeProvider | Tree ✅ |
| Workspace Profile | WorkspaceProfileTreeProvider | Tree ✅ |
| MCP / External Context | McpToolsTreeProvider | Tree ✅ |

**All 17 target views have live UI implementations.** End-to-end New Task → generated patch linkage is still pending.

### TaskFlowController State Machine

```
idle → analyzing → context_building → routing → generating_patch
  → awaiting_approval (STOP — developer must approve)
    → validating → applying → done
    → reject → idle
```

The state machine supports automatic progression through analysis, context building, model routing, and patch generation, then **stops at the approval gate**. This controller flow is implemented but is not yet triggered by the current New Task webview submit path.

### Test Coverage

| Test File | Tests | Scope |
|-----------|-------|-------|
| test_rules_active.py | 5 | Rules aggregation endpoint |
| test_task_analyze.py | 7 | Task analysis + mode detection |
| test_context_build.py | 5 | Context pack building |
| test_model_route.py | 6 | Model selection + budget check |
| test_patch_validate.py | 8 | Patch generation + validation |
| test_history_dashboard.py | 5 | Task history + cost dashboard |
| test_mcp_tools.py | 2 | MCP tools listing |
| **Total** | **38** | All new endpoints covered |

### Key Design Decisions

1. **Hybrid Tree + Webview**: Tree views for glanceable sidebar data, webview panels for rich interaction
2. **MemoPilotPanelBase**: All webview panels inherit CSP nonce injection, VS Code theme CSS vars, and typed message bridge
3. **Mock-first backend**: Patch generation and validation use deterministic mocks to enable UI development ahead of AI integration
4. **Developer-in-control**: TaskFlowController always stops at approval gate; cost visibility is first-class throughout
5. **Incremental delivery**: Each phase ships independently; no big-bang rewrites  

---

## 28\. Remediation and Feature Hardening (v2.2 — June 2026)

### Overview

A comprehensive remediation sprint resolved **26 open issues** (5 P0, 9 P1, 7 P2, 5 P3) identified across two architectural review cycles, and delivered all remaining v1.5 and v2 features. The result: 34 files changed, +5,280/-983 lines, 127 tests passing, 0 lint errors.

### Fix Track Summary

| Track | Issues Resolved | Scope |
|-------|----------------|-------|
| F1: Schema Foundation | P0-A, P0-C, P0-E, P1-D, P1-F, P3-A, P3-B, P3-C | Lockfile format, FTS5 triggers, governance migration, trust level inversion, memory_relations consolidation, schema constraints, snapshots spec |
| F2: Workflow Correctness | P0-B, P1-A, P1-B, P1-C, P2-A, P2-B, P2-C, P2-D, P3-D, P3-E | Patch apply mechanism, cache quality filter, investigation sessions, investigation API, classifier fix, workspace profile YAML source-of-truth, validation timeout, document_chunks FK, file watcher, MCP per-context caps |
| F3: Governance Wiring | P1-E, P1-G, P1-H, P1-I, P2-E, P2-F, P2-G | Governance field integration, retention policy, recall API contract (UsePolicy + VisibilityScope), memory_artifacts, API path reconciliation, endpoint status register, Phase 18 dep fix |
| F4: Phase Restructure | P0-D | Phase 17 decomposed into 17A–17D with individual acceptance criteria |

### New Modules Added

| Module | Purpose |
|--------|---------|
| `patcher.py` | `git apply --check` → snapshot → apply → rollback on failure |
| `retention.py` | Retention policy enforcement for recall_traces, audit_events (90/180 day, row caps) |
| `memory_recall.py` | Recall service with UsePolicy, VisibilityScope/Target filtering, recall trace recording |
| `memory_governance.py` | Memory status lifecycle validation (valid transitions enforced) |
| `watcher.py` | File watcher via watchdog with 1500ms debounce, excluded dirs, async queue |
| `backup.py` | WAL checkpoint, DB + rules + templates backup/restore, FTS rebuild |
| `tool_selector.py` | Pre-context-pack tool filtering by task_type, budget_profile enforcement |
| `document_ingestion.py` | PDF (pdfplumber), Excel (openpyxl), CSV, Word (python-docx), PowerPoint (python-pptx) |
| `image_analysis.py` | Local LLaVA → OCR (pytesseract) → cloud fallback; trust_level=2 |
| `code_review_memory.py` | Review lesson extraction from PR comments, maintainer-approved write-back |
| `endpoint_registry.py` | API implementation status register (real/stub/mock/missing per endpoint) |
| `validation_runner.py` | Per-command timeouts via asyncio.wait_for, timeout = validation failure |

### New Migrations

| Migration | Version | Contents |
|-----------|---------|----------|
| `006_schema_remediation.sql` | 6 | Governance columns (memory_class, memory_status, visibility_scope, reusable, review_required, use_policy_json, provenance_json), memory_relations table with CHECK constraint, retention_config, recall_traces, audit_events, memory_artifacts, investigation_sessions, evidence/task_run FKs, document_chunks memory_id FK, workspace_profile cache columns, rules/status validation triggers, trust level data inversion, FTS rebuild |
| `007_response_cache_quality.sql` | 7 | response_status and raw_response columns on ai_calls for cache quality filtering |
| `008_context_pack_snapshot.sql` | 8 | pack_content_snapshot on context_pack_versions for diffing |

### Key Architectural Decisions

1. **Trust level inverted**: Trust 5 = source-verified (highest), Trust 1 = inferred (lowest). All queries sort DESC.
2. **supersedes_id removed**: Supersession handled exclusively via `memory_relations` with `relation_type='supersedes'` and cycle detection.
3. **YAML is source of truth**: workspace.profile.yaml is authoritative; SQLite workspace_profile is a read cache synced on startup/watch.
4. **Lockfile enhanced**: Now includes `started_at`, `schema_version`, `api_version` alongside `port` and `pid`.
5. **Patch safety**: Full pre-check → snapshot → apply → rollback lifecycle. Never `shell=True`.
6. **MCP caps per-context**: pre_fetch=8, patch_generation=5, investigation=12, hard_absolute_cap=20.
7. **Write-back safety filter**: Blocks secrets, full diffs (>200 lines), raw transcripts. Blocked content saved as memory_artifacts.
8. **Memory status lifecycle**: Enforced transitions (discovered → pending_review → confirmed, etc.). Terminal states: evidence_only, rejected, superseded.

### New API Endpoints (added in this sprint)

| Endpoint | Method | Status |
|----------|--------|--------|
| `/v1/context-pack/generate` | POST | real |
| `/v1/context-pack/diff` | GET | real |
| `/v1/investigation/start` | POST | real |
| `/v1/investigation/{session_id}` | GET | real |
| `/v1/investigation/{session_id}/evidence` | POST | real |
| `/v1/investigation/{session_id}/evidence/{evidence_id}` | DELETE | real |
| `/v1/investigation/{session_id}/transition-to-patch` | POST | real |
| `/v1/memory/recall` | POST | real |
| `/v1/memory/writeback` | POST | real |
| `/v1/memory/review` | GET | real |
| `/v1/memory/items/{item_id}/review` | PATCH | real |
| `/v1/memory/backup` | POST | real |
| `/v1/memory/restore` | POST | real |
| `/v1/memory/review-lessons/extract` | POST | real |
| `/v1/memory/review-lessons/approve` | POST | real |
| `/v1/evidence/extract-pdf` | POST | real |
| `/v1/evidence/extract-excel` | POST | real |
| `/v1/evidence/extract-csv` | POST | real |
| `/v1/evidence/extract-docx` | POST | real |
| `/v1/evidence/extract-pptx` | POST | real |
| `/v1/evidence/analyze-image` | POST | real |
| `/v1/skills` | GET | real |
| `/v1/skills/import` | POST | real |
| `/v1/skills/conflicts` | GET | real |
| `/v1/policies/load` | POST | real |
| `/v1/policies/active` | GET | real |
| `/v1/cost/budget-status` | GET | real |
| `/v1/endpoints/status` | GET | real |
| `/v1/task/apply-patch` | POST | real |

### Test Coverage (post-remediation)

| Test File | Tests | Scope |
|-----------|-------|-------|
| test_health.py | 2 | Health + schema version |
| test_main.py | 3 | Lockfile read/write |
| test_migrations.py | 4 | Migration runner |
| test_workspace_init.py | 3 | Workspace initialization |
| test_workspace_index.py | 4 | Indexing + rebuild |
| test_auth.py | 3 | HMAC token auth |
| test_rules_active.py | 5 | Rules aggregation |
| test_task_analyze.py | 10 | Task analysis + classifier priority |
| test_context_build.py | 5 | Context pack building |
| test_model_route.py | 6 | Model selection + budget |
| test_patch_validate.py | 10 | Patch + validation + timeout |
| test_patcher.py | 7 | Patch apply + snapshot + rollback |
| test_history_dashboard.py | 5 | Task history + cost dashboard |
| test_mcp_tools.py | 2 | MCP tools listing |
| test_symbol_extractor.py | 4 | Symbol extraction |
| test_group1_cost_cache.py | 8 | Cost guard + cache quality filter |
| test_group2_agentic_security.py | 10 | MCP caps + credential redaction |
| test_group3_hardening.py | 4 | Provider resilience + DB recovery |
| test_group4_profile_memory_privacy.py | 12 | Profile sync + recall + privacy |
| test_group5_investigation.py | 10 | Investigation API + evidence |
| test_group6_waveb_core.py | 4 | Evidence classifier + endpoints |
| test_group7_wavec_v15.py | 6 | Skills + document ingestion |
| test_group8_wave2_policy_flow.py | 5 | Policy packs + flow builder |
| test_group9_wave4_multi_workspace_ingestion.py | 5 | Multi-workspace + DOCX/PPTX |
| **Total** | **127** | All critical paths covered |

### Phase Structure (Revised)

```
Phase 0:   Product Definition and Constraints
Phase 0.5: Technical Spikes
Phase 1:   Extension Shell
Phase 2:   Python Backend Foundation
Phase 3:   Project Scanner and Symbol Indexer + File Watcher
Phase 4:   Rule and Skill Resolver
Phase 5:   Local Memory Store (with FTS5 triggers, governance defaults)
Phase 6:   Summary Generator
Phase 7:   Context Pack Builder (with updated classifier signal priority)
Phase 8:   Model Router and Provider Adapters
Phase 9:   Patch Generation and Approval (with git apply spec, snapshots)
Phase 10:  Validation Runner (with per-command timeout)
Phase 11:  Cost Guard and Reporting
Phase 12:  MCP Tool Integration (per-context caps)
Phase 13:  Production Hardening
Phase 14:  Workspace Profile, Memory Manager, Privacy Dashboard
Phase 15:  Evidence-Aware Investigation (with investigation_sessions)
Phase 16:  PDF and Excel Artifact Analysis
Phase 17A: Context Governance (templates, agent modes, intelligent selection)
Phase 17B: Patch Governance (risk classifier, compliance score, AI replay)
Phase 17C: Provider and Cost Governance (capability matrix, budget profiles)
Phase 17D: v1.5 Structural Features (skill store, diffing, backup)
Phase 18A: Memory Governance Hardening — Core (recall, write-back, review, retention)
Phase 18B: Memory Governance Hardening — Advanced (usage, provenance, code review mode)
Phase 19:  Image and Screenshot Analysis
Phase 20:  Team Policy Packs
Phase 21:  Local Agent Flow Builder
Phase 22:  Multi-Workspace Support
Phase 23:  Word and PowerPoint Ingestion
```

---

## 29\. Feature Refinement Phase (v2.3 — June 2026)

### Overview

A post-validation refinement phase addressing six feature areas identified through production usage. All refinements are additive and backward-compatible. Result: 6 new migrations (010–015), 4 new modules, 44 new tests (171 total passing), 0 regressions.

### Refinement Summary

| # | Area | Key Change | New Tests |
|---|------|-----------|-----------|
| 1 | Context Pack Quality | Budget-aware tier allocation with per-tier token caps, selection-time inclusion/exclusion reasons, stale memory surfacing, task-type-aware tier reordering | 7 |
| 2 | Approval Gate | Tiered approval (LOW/MEDIUM/HIGH/CRITICAL), scroll gate for high-risk patches, type-to-confirm for critical patches, risk-sorted diff ordering, actionable compliance warnings with task handoff | 8 |
| 3 | Memory Manager | Bulk actions (approve/reject/delete with confirmation), usage signal per memory item, ranked suggested updates with deterministic scoring, review queue decay detection, keyboard shortcuts | 7 |
| 4 | Model Routing | Outcome-based frontier escalation (2+ failures trigger upgrade), per-model cost comparison UI, inline routing override, routing reason explains escalation conditions | 6 |
| 5 | Validation Runner | Pre-patch baseline run (isolate new vs pre-existing failures), configurable auto-retry policy, failure output categorisation with template-driven hints | 8 |
| 6 | Cost Guard | Status bar cost integration with graduated states, savings framing (dollar value vs frontier baseline), per-task cost feedback, graduated budget enforcement (80%/90%/100% tiers) | 8 |

### New Modules

| Module | Purpose |
|--------|---------|
| `context_budget.py` | Budget-aware context pack allocation with `ContextBudget`, `ContextItem`, `ExcludedItem`, `ExclusionReason` enum, task-type tier ordering, roll-forward budget logic |
| `model_router.py` | Outcome-based routing with `ModelTier` enum, `get_outcome_routing_hint()` (failure-history query), `RoutingDecision` dataclass, escalation source tracking |
| `approval_gate.py` | Tiered approval with `ApprovalTier` enum, `FILE_RISK_SIGNALS` pattern matching, `rank_patch_files()`, `ApprovalConfig`, `ComplianceWarning`/`ComplianceAction` for task handoff |
| `memory_suggestions.py` | Deterministic memory suggestion ranking (5 factors: file change, memory class, task frequency, validation source, contradiction), review queue decay detection |

### New Migrations

| Migration | Version | Contents |
|-----------|---------|----------|
| `010_context_pack_budget.sql` | 10 | budget_summary_json, stale_exclusion_count, included_items_json, excluded_items_json on context_pack_versions |
| `011_model_routing_outcome.sql` | 11 | routing_escalation_source, routing_base_tier, model_override on task_runs |
| `012_cost_guard_savings.sql` | 12 | hypothetical_frontier_cost on ai_calls |
| `013_validation_baseline.sql` | 13 | baseline_validation_json, pre_existing_failures_json, new_failures_json, fixed_by_patch_json, retry_count, auto_retry_stopped_reason on patch_attempts |
| `014_approval_gate_tiers.sql` | 14 | approval_tier, scroll_gate_cleared, type_confirm_required, type_confirm_completed, compliance_warnings_dismissed_json, compliance_actions_triggered_json, ranked_files_json on patch_attempts |
| `015_memory_manager_usage.sql` | 15 | last_used_at, usage_count on memory_items; indexes on last_used_at and (memory_status, created_at) |

### New API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/v1/patch/rank-files` | POST | Rank changed files by risk level, return approval tier |
| `/v1/memory/bulk-approve` | POST | Bulk approve memory items (max 500) |
| `/v1/memory/bulk-reject` | POST | Bulk reject memory items |
| `/v1/memory/bulk-delete` | POST | Bulk delete memory items |
| `/v1/memory/unused` | GET | List memory items unused for 30+ days |

### Extended API Responses

| Endpoint | New Fields |
|----------|-----------|
| `POST /v1/context-pack/generate` | budget_summary, stale_exclusions, included_items, excluded_items (opt-in via model_max_tokens) |
| `POST /v1/model/route` | options (all tiers with costs), base_tier, escalation_source, model_override |
| `GET /v1/cost/budget-status` | pct_used, at_limit, at_warning, warning_threshold |
| `GET /v1/cost/dashboard` | savings_report (actual vs hypothetical frontier cost, reduction percentage) |
| `POST /v1/task/validate` | pre_existing_failures, new_failures, fixed_by_patch, failure categories with hints |
| `GET /v1/memory/items` | usage_stats per item (recalled_count, used_count, last_used_at, days_since_last_use) |

### Key Architectural Decisions

1. **Budget allocation is opt-in**: Existing context build behavior is preserved unless `model_max_tokens` is provided in the request. This ensures backward compatibility.
2. **Tier roll-forward**: Unused budget from any context tier passes to the next tier in task-type-specific order, preventing waste.
3. **Selection-time reasons**: Inclusion and exclusion reasons are generated at retrieval time, not post-hoc — ensuring transparency accuracy.
4. **Deterministic ranking**: Memory suggestion ranking uses a 5-factor scoring algorithm with no model calls. Contradicting suggestions always surface first.
5. **Graduated budget enforcement**: Three-tier response (80% warning → 90% frontier approval → 100% block) replaces binary cutoff. Local models are never blocked.
6. **Pre-patch baseline**: Validation runs before and after patch to isolate new failures from pre-existing ones. Capped at 30 seconds for large test suites.
7. **Failure categorisation**: Template-driven hints per failure category (assertion, import, fixture, syntax, type, lint, timeout) — no AI call needed.
8. **Batched usage stats**: Memory item listing uses batched queries instead of N+1 pattern, keeping response time under 500ms for 100 items.

### Test Coverage (post-refinement)

| Test File | Tests | Scope |
|-----------|-------|-------|
| test_context_builder_budget.py | 7 | Budget allocation, tier caps, roll-forward, stale exclusions, task-type reordering |
| test_model_router.py | 6 | Outcome escalation, per-model options, inline override, escalation conditions |
| test_cost_guard_budget.py | 8 | Budget gate (80/90/100%), savings calculation, per-task cost, local model immunity |
| test_validation_runner_baseline.py | 8 | Baseline diff, failure categorisation, auto-retry, escalation approval |
| test_approval_gate.py | 8 | Tier configs, risk classification, file ranking, compliance actions |
| test_memory_manager_bulk.py | 7 | Bulk actions, usage stats, unused filter, ranking, decay detection |
| **Refinement total** | **44** | All acceptance criteria covered |
| **Full suite total** | **171** | Including all pre-existing tests |

### Phase Structure (Updated)

```
Phase 24:  Feature Refinement — Context Pack Quality (budget allocation, selection-time reasons, stale surfacing)
Phase 25:  Feature Refinement — Model Routing (outcome escalation, cost comparison, inline override)
Phase 26:  Feature Refinement — Cost Guard (status bar, savings framing, graduated enforcement)
Phase 27:  Feature Refinement — Validation Runner (pre-patch baseline, auto-retry, failure categorisation)
Phase 28:  Feature Refinement — Approval Gate (tiered approval, scroll gate, type-to-confirm, actionable warnings)
Phase 29:  Feature Refinement — Memory Manager (bulk actions, usage signals, ranking, decay, keyboard nav)
```

### Code Review Fixes Applied

Three issues identified during code review and resolved:

1. **Unbounded bulk action list**: `BulkMemoryActionRequest.memory_ids` capped at `max_length=500` to stay within SQLite parameter limits.
2. **N+1 query in memory listing**: `_rows_to_items()` replaced with batched `_batch_usage_stats()` — single query for base stats + single query for events table.
3. **Stale schema version default**: `config.py` schema_version updated from 13 to 15 to match latest migration.

### Session Update — New Task Webview Fix (June 2026)

Changes implemented in this session:

1. **Fixed Analyze Task click no-op**: `TaskEntryPanel.ts` now attaches the submit handler on `DOMContentLoaded`, ensuring the button listener is always bound when the form is rendered.
2. **Hardened delegated click handling**: Added an `Element` guard before using `closest()` for dynamic action buttons (e.g., "Edit Task").
3. **Documented current behavior**: The New Task panel currently performs **analysis only** (intent/mode/complexity/rules/files) and does not yet trigger patch generation/apply directly.
4. **Current practical task-to-patch path**: Analyze in New Task, then use existing flows/commands for implementation and post-hoc review (`memopilot.reviewAppliedPatch`).

---

## 30\. Tool Mode Integration (v2.4 — June 2026)

### Overview

MemoPilot v2.4 adds **Tool Mode**: MemoPilot can now act as a callable tool surface for both Copilot Chat and Cursor Chat while preserving the same local-memory, rules, privacy, and governance model used by the native MemoPilot task flow. Tool Mode is additive: the extension UI remains the primary guided experience, while tool callers get bounded Markdown responses optimized for LLM consumption.

### Tool Surface

| Tool | Purpose |
|---|---|
| `memopilot-context` | Build a governed context pack for a task description |
| `memopilot-recall` | Search local project memory by query |
| `memopilot-rules` | Retrieve active rules applicable to a file or task |
| `memopilot-workspace-profile` | Return the workspace technology profile, commands, and memory health |
| `memopilot-memory-search` | Deep memory search with semantic + keyword matching |
| `memopilot-review-applied-patch` | Submit a diff for post-hoc review and writeback proposals |

### LM Tools API Integration

Tool Mode registers six callable tools through the **VS Code Language Model Tools API** using `src/tools/LanguageModelToolsRegistrar.ts`. Registration is feature-gated for VS Code 1.99+ so older editor versions silently skip tool registration rather than breaking extension startup. Each tool call is forwarded to the local MemoPilot backend and requests bounded Markdown output for direct model consumption.

### MCP Server Architecture

MemoPilot also exposes the same tool surface through a standalone **stdio MCP server** launched as:

```bash
python -m agent.mcp_server
```

The MCP server is a separate process that reads from stdin, writes to stdout, resolves the local backend port from `.memopilot/agent.lock`, authenticates with the backend token, and forwards tool requests over localhost HTTP. This allows Cursor Chat to use MemoPilot without coupling the tool surface to the extension host process.

### Context Renderer

The new `context_renderer.py` module converts context packs, rules, workspace profiles, and memory search results into structured Markdown with hard output ceilings:

- **8000 tokens** maximum for context pack output
- **2000 tokens** maximum for rules, workspace profile, recall, and related tool responses
- Truncation notices when files are omitted to stay inside the token budget
- Governance and redaction notices so tool callers understand what was filtered

### Writeback Pipeline

Tool Mode adds a dedicated writeback path for post-hoc patch review and memory capture via `tool_mode_writeback.py`.

- A submitted diff produces capped memory proposals instead of direct memory writes
- Proposal mix is bounded for quality: **1 outcome**, **0–5 symbol changes**, **0–2 rule compliance**, **0–2 test coverage**
- Maximum **10 proposals per diff**
- Proposal bodies pass through a safety filter that strips raw diff markers and redacts secrets
- Duplicate processing is prevented with a **SHA-256 diff hash**
- No proposal is auto-confirmed; every item enters **`pending_review`**
- A dismiss endpoint lets developers mark a writeback as not needed

### Tool Call Logging and Session Management

Tool Mode introduces auditable per-caller session tracking:

- Every tool invocation is logged to `tool_call_events`
- Session-level aggregates are tracked in `tool_mode_sessions`
- Logged fields include caller, tool name, returned token counts, redaction counts, stale exclusions, and writeback/patch-review flags
- First-use caller approval is explicit via approve/block endpoints
- A session summary endpoint feeds the privacy dashboard with per-caller usage totals and pending writeback counts

### Token Injection for Cursor

`BackendManager.ts` now writes backend token data into `.memopilot/.cursor-mcp-env` for Cursor MCP launches. The file is workspace-local, supports automatic token injection for the MCP process, and is never committed to git.

### New API Endpoints

| Endpoint | Method | Purpose |
|---|---|---|
| `/v1/context-pack/generate` | POST | Tool-mode context generation with bounded Markdown output |
| `/v1/memory/recall` | POST | Recall or search local project memory for tool callers |
| `/v1/rules/active` | GET | Return active rules and matched skills |
| `/v1/workspace/profile` | GET | Return workspace profile data rendered for tool mode |
| `/v1/task/review-applied-patch` | POST | Review an applied diff and return risk/compliance guidance |
| `/v1/tool-mode/writeback` | POST | Generate memory proposals from an applied diff outcome |
| `/v1/tool-mode/dismiss-writeback` | POST | Mark a pending writeback as not needed |
| `/v1/tool-mode/approve-caller` | POST | Approve first-use access for a tool caller |
| `/v1/tool-mode/block-caller` | POST | Block a caller for the current session |
| `/v1/tool-mode/session-summary` | GET | Return per-caller session totals for the privacy dashboard |

### New Database Tables and Migrations

| Migration | Version | Contents |
|---|---|---|
| `016_tool_mode.sql` | 16 | `task_runs` source/patch governance fields plus `tool_mode_sessions` and `tool_call_events` |
| `017_tool_mode_writeback.sql` | 17 | `tool_mode_writebacks`, `task_runs` outcome/writeback fields, and `memory_items.writeback_id` |

| Table | Purpose |
|---|---|
| `tool_mode_sessions` | Track active tool-mode sessions by caller and workspace |
| `tool_call_events` | Record each tool call for audit, privacy, and usage reporting |
| `tool_mode_writebacks` | Store deduplicated diff writebacks and proposal-generation metadata |

### Key Architectural Decisions

1. **Tool Mode is additive**: native MemoPilot task flow remains unchanged; tool integrations reuse the same backend primitives.
2. **Two front ends, one governance path**: Copilot LM tools and Cursor MCP both flow through the same local APIs, filters, and logging.
3. **Markdown over raw JSON**: tool output is rendered for direct LLM consumption, not for human-only UI views.
4. **Bounded output is mandatory**: Tool Mode always caps output to avoid flooding chat context windows.
5. **Writeback is review-first**: memory proposals are suggestions only and always enter `pending_review`.
6. **Dedup happens at diff level**: the same patch cannot repeatedly generate duplicate proposals.
7. **Caller approval is explicit**: first-use approval/blocking protects privacy when a new tool surface begins calling MemoPilot.
8. **Cursor auth stays local**: token handoff uses `.memopilot/.cursor-mcp-env`, not committed config.

### Test Coverage (post-tool-mode)

| Test File | Tests | Scope |
|---|---|---|
| `test_tool_mode.py` | 22 | Renderer output, bounded Markdown, session tracking, audit logging, caller approval, tool-mode review flows |
| `test_mcp_server.py` | 7 | MCP stdio bootstrap, tool definitions, schema validation, backend forwarding |
| `test_tool_mode_writeback.py` | 17 | Proposal generation, safety filtering, deduplication, task status transitions, dismiss flow |
| **Tool mode total** | **46** | Tool Mode acceptance coverage across T1–T5 |
| **Full suite total** | **217** | Including all pre-existing tests |

### Phase Structure

```
Phase 30:  Tool Mode — LM Tools + MCP + Writeback
  T1: Bounded Markdown renderer + Copilot Chat LM tool surface
  T2: Cursor Chat MCP server (stdio) + backend forwarding
  T3: Tool call audit logging + per-caller session tracking
  T4: First-use approval gate + privacy session summary
  T5: Post-hoc patch review + memory writeback proposals
```

---

## 31\. Context Accuracy Refinement (v2.5 — June 2026)

### Overview

A focused accuracy sprint adding four layers to the context-pack pipeline: structural call graph (Layer 3), git commit history (Layer 4), content deduplication, and context quality scoring. The goal is to close the most common class of bad AI patches: patches that fail because the AI lacked callers, lacked historical intent, or received redundant chunks that crowded out signal.

Result: 6 new modules, 1 new migration, 5 new test files (32 tests), 249 total tests passing, 0 regressions. All 6 code-review issues fixed.

### Layer Summary

| Layer | Name | What It Adds |
|-------|------|-------------|
| 1 | Content Deduplication | Remove near-duplicate context chunks (5-gram shingling, 70% overlap threshold) before pack assembly |
| 2 | Context Quality Scoring | 6-factor weighted score per context pack; verdicts (good / acceptable / poor / rebuild); missing-signal diagnosis |
| 3 | Structural Call Graph | Recursive callers/callees from AST-extracted relationships; finds callers NOT already in context |
| 4 | Git Commit History | Index recent commits per file; retrieve recency-weighted history; show what changed and why |

### New Backend Modules

| Module | Class / Function | Purpose |
|--------|-----------------|---------|
| `graph_retriever.py` | `GraphRetriever` | Recursive CTE callers/callees; `find_callers_not_in_context()`; `store_relationships()` |
| `repo_map_generator.py` | `RepoMapGenerator` | Compact ~500-token structural overview of workspace symbols |
| `context_quality_scorer.py` | `score_context_pack()` | 6-factor weighted scoring → `ContextQualityScore`; `build_quality_warning()` |
| `context_deduplicator.py` | `deduplicate_context_items()` | 5-gram shingling dedup; higher trust_level wins; returns `DeduplicationResult` with savings_pct |
| `git_history_indexer.py` | `GitHistoryIndexer` | `index_git_history()`, `get_commits_for_files()`, `get_blame_context()`, `format_commit_history_for_context()` |
| `symbol_extractor.py` (extended) | `extract_relationships()` | Emits `SymbolRelationshipRecord` (caller→callee) from AST call analysis |

### New Migration

| Migration | Version | Contents |
|-----------|---------|----------|
| `018_context_accuracy.sql` | 18 | `symbol_relationships` (caller/callee edges), `commit_history`, `commit_file_changes`, `commit_fts` (FTS5 virtual table + triggers), quality/rejection columns on `task_runs` and `patch_attempts` |

### Memory Recall Enhancement

`memory_recall.py` extended with `_recency_boost()`:

- BM25 scores (negative floats from SQLite FTS5) are now correctly negated before use
- Items recalled within 7 days receive an additive recency boost proportional to days elapsed
- Prevents recently-relevant memory items from being displaced by older but keyword-dense items

### Extended API Responses

| Endpoint | New Fields |
|----------|-----------|
| `POST /v1/context-pack/generate` | `quality_score` (verdict, score, missing_signals, dedup_savings_pct, graph_expansion_files), `callers_not_in_context` (file paths), `repo_map` (compact symbol overview), `commit_history` (recent commits for context files) |
| `GET /v1/cost/dashboard` | `quality_metrics` (avg_score, good_pct, acceptable_pct, poor_pct, rebuild_pct, avg_dedup_savings_pct) |

### New API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `POST /v1/context/blame` | POST | Git blame for a line range; enriches result with stored commit messages |
| `POST /v1/patch/reject` | POST | Record a patch rejection; stores context snapshot for rejection-learning queries |

### Extension Changes

#### `ContextPackTreeProvider.ts`

The Context Pack sidebar tree now shows:

- **Quality indicator node** — verdict icon (✅ / ⚠️ / 🔴), score, and dedup savings
- **Missing signals** — expandable list of what the context pack lacks (e.g., "no git history", "no callers found")
- **Callers not in context** — files calling the primary symbol that were not included in the pack

#### `BackendClient.ts`

`ContextBuildResponse` extended with optional `quality_score`, `callers_not_in_context`, `repo_map`, and `commit_history` fields.

#### `LspContextProvider.ts` (new)

Real-time LSP enrichment provider using `vscode.executeReferenceProvider` and `vscode.executeDefinitionProvider`. Entry points: `getContextForPosition()` and `getContextForSymbol()`. Allows the extension to augment context packs with editor-side call references without a backend round-trip.

### End-User Workflow (Updated)

The context accuracy refinement adds a **quality gate** between Analyze and Patch:

```
1. Analyze Task
   → Call graph indexed (callers/callees extracted from workspace)
   → Git history indexed (recent commits per relevant file)
   → Context assembled and deduplicated

2. Review Context Quality (NEW)
   → Context Pack tree shows quality verdict:
       ✅ Good         — proceed to patch
       ⚠️ Acceptable   — minor gaps, patch may still work
       🔴 Poor         — key callers/files missing
       🔴 Rebuild      — context too thin, re-index or add files
   → Missing signals listed (e.g., "no callers found", "no git history")
   → Callers not in context surfaced for manual inclusion

3. Patch (unchanged)
   → AI receives: deduplicated context + call graph + commit history + repo map
   → Fewer hallucinated architectures; AI knows what changed and why

4. Reject Feedback (NEW)
   → POST /v1/patch/reject stores rejection with context snapshot
   → Future context builds for same files avoid repeating the same bad context
```

### Key Architectural Decisions

1. **Call graph via recursive CTE**: Callers/callees are retrieved from `symbol_relationships` using a recursive CTE with depth cap (default 3). No runtime AST parsing needed after index.
2. **Async subprocess**: `git log` and `git blame` use `asyncio.get_event_loop().run_in_executor()` to avoid blocking the FastAPI event loop.
3. **5-gram shingling**: Deduplication samples up to 50 shingles per item to cap O(n²) cost. 70% overlap triggers replacement by the higher-trust item.
4. **6 quality factors**: symbol coverage (primary symbol in pack), caller coverage (known callers present), file diversity (multiple files), rule presence, git history presence, deduplication efficiency. Weighted average → verdict.
5. **BM25 polarity**: SQLite FTS5 `bm25()` returns negative floats. Memory recall now negates before use — previously all relevance scores were silently clipped to 0.
6. **Rejection learning**: Stored rejections enable future context builds to penalise configurations that previously produced rejected patches for the same files.
7. **Repo map is ~500 tokens**: Structural overview fits comfortably in budget tier without crowding file content.

### Code Review Fixes (6 issues resolved)

| # | Severity | File | Issue | Fix |
|---|----------|------|-------|-----|
| 1 | High | `workspace_indexer.py` | DELETE of `symbol_relationships` used subquery on `symbols` that was already deleted | Swapped DELETE order: relationships first, then symbols |
| 2 | High | `symbol_extractor.py` | ~57 lines of unreachable dead code after `return None` in `_name_of()` | Removed dead block entirely |
| 3 | High | `api.py` | `graph_expansion_files` always 0 (inverted filter: `if c.file_path in included_file_paths`) | Removed contradictory condition |
| 4 | High | `memory_recall.py` | BM25 scores negated by `max(rank, 0.0)` — all relevance_scores became 1.0 | Negated rank; changed to additive boost |
| 5 | Medium | `git_history_indexer.py` | Blocking `subprocess.run()` in async handlers froze event loop | Replaced with `run_in_executor()` |
| 6 | Low | `git_history_indexer.py` | `_human_age()` naive `.split("-0")` corrupted Jan–Sep dates | Replaced with regex timezone strip |

### Test Coverage (post-context-accuracy)

| Test File | Tests | Scope |
|-----------|-------|-------|
| `test_graph_retriever.py` | 5 | Store relationships, callers/callees, callers-not-in-context, empty graph |
| `test_repo_map.py` | 4 | Empty workspace, single file, multi-file, truncation at symbol limit |
| `test_context_quality_scorer.py` | 8 | All 6 factors, verdict thresholds, warning builder, missing signals |
| `test_git_history_indexer.py` | 7 | Commit parsing, file filtering, recency weighting, blame context, `_human_age()` |
| `test_context_deduplicator.py` | 8 | Exact dup, near-dup, trust-level winner, distinct items, savings_pct |
| **Context accuracy total** | **32** | All new modules covered |
| **Full suite total** | **249** | Including all pre-existing tests |

### Phase Structure

```
Phase 31:  Context Accuracy Refinement
  CA1: Call graph extraction (symbol_relationships, graph_retriever)
  CA2: Git commit history indexing (git_history_indexer, migration 018)
  CA3: Content deduplication (context_deduplicator, 5-gram shingling)
  CA4: Context quality scoring (context_quality_scorer, 6 factors, verdicts)
  CA5: API wiring (quality_score + callers_not_in_context + repo_map + commit_history in /v1/context-pack/generate)
  CA6: New endpoints (blame, reject, quality dashboard metrics)
  CA7: Extension UI (ContextPackTreeProvider quality indicator, LspContextProvider)
  CA8: Memory recall enhancement (recency boost, BM25 polarity fix)
  CA9: Tests (32 new tests across 5 test files)
```
