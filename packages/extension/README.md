# MemoPilot for VS Code

MemoPilot is a rule-aware, local-memory, cost-governed AI development extension for VS Code.  
It adds a governed workflow around AI-assisted engineering so context, model usage, and code changes are explicit and reviewable.

## Extension Description

MemoPilot runs as a VS Code extension with a local Python backend. It helps teams:

- Keep project memory local and structured
- Apply policy/rule checks before risky actions
- Build investigation-ready context packs from code and non-code evidence
- Control model usage with budget and safety guardrails

## Features

- **Workspace intelligence**: workspace indexing, symbol extraction, stale tracking, and memory rebuild.
- **Governance controls**: policy packs, approval gates, compliance checks, and patch risk assessment.
- **Cost controls**: budget profiles, estimated usage tracking, and optimization helpers.
- **Evidence workflows**: evidence board, source classification, redaction, and investigation context packs.
- **Artifact ingestion**:
  - Text/Markdown/CSV/JSON/XML
  - Excel and PDF
  - Image/screenshot analysis
  - Word (`.docx`) and PowerPoint (`.pptx`)
- **Multi-workspace v2 support**: manage workspace roots and switch active workspace scope.
- **Operational tooling**: memory backup/restore, replay mode, provider capability matrix, and local flow builder.

## How It Works

1. MemoPilot starts a local backend process and initializes `.memopilot/` in your workspace.
2. The backend loads rules, skills, and indexed workspace knowledge.
3. When you run a MemoPilot command, it composes governed context and evaluates policy/risk/cost constraints.
4. For investigation scenarios, it ingests attached evidence and builds a structured context pack.
5. The extension surfaces results in tree views and commands for review, approval, and iteration.

## Requirements

- VS Code `^1.85.0`
- Python 3.11+ available to VS Code
- Permission for the selected Python interpreter to install backend dependencies

## Key Commands

- `MemoPilot: Rebuild Memory`
- `MemoPilot: Attach Evidence`
- `MemoPilot: Run Investigation`
- `MemoPilot: Manage Policy Packs`
- `MemoPilot: Run Local Agent Flow`
- `MemoPilot: Manage Workspaces`
- `MemoPilot: Restart Backend`

## Troubleshooting

- If backend startup fails, run **MemoPilot: Restart Backend**.
- Ensure your configured Python interpreter is valid and can run `pip`.
- If needed, set `memopilot.pythonPath` in settings to a known-good interpreter.
