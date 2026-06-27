# MemoPilot E2E Copilot Verification Checklist

> For QA/verification before releasing to marketplace
> Last updated: 2026-06-27

## Pre-Flight Checks

### System Setup
- [ ] VS Code 1.85+ installed
- [ ] Node.js 20+ installed
- [ ] Python 3.11+ installed with `uv` package manager
- [ ] Git installed and configured
- [ ] GitHub Copilot extension installed and authenticated
- [ ] Extension built locally: `pnpm run build` in `packages/extension/`

### Backend Setup
- [ ] Ollama running (for local model fallback): `ollama serve`
- [ ] Ollama has `mistral` model: `ollama pull mistral`
- [ ] Backend starts: `cd packages/agent && uv run python -m api` (should see "FastAPI running on 127.0.0.1:PORT")
- [ ] `agent.lock` file created in `~/.memopilot/`
- [ ] `~/.memopilot/config.yaml` exists with local-only settings

### Extension Setup
- [ ] MemoPilot VSIX loaded in VS Code (Extensions → Install from VSIX)
- [ ] No extension errors in VS Code output: `View → Output → MemoPilot`
- [ ] Backend connected: Status view shows "✅ Connected" in green
- [ ] Sidebar shows all 11 views (Status, Profile, Memory, Rules, Context Pack, Cost Guard, Privacy, Providers, Evidence, Audit, Workspace)

---

## Retrieval-First Verification (Happy Path)

### 1. Open a Real Workspace

```bash
# Test on a real repo with Python or TypeScript code
cd ~/dev/some-project  # With git history, .py or .tsx files
code .
```

- [ ] Workspace Profile auto-builds (check Memory view → Workspace Profile)
- [ ] Profile shows detected language (Python, TypeScript, etc.)
- [ ] File index appears in Context Pack view (sample files listed)

### 2. Verify Bootstrap Artifacts

- [ ] `.vscode/mcp.json` exists in the opened workspace
- [ ] `.github/copilot-instructions.md` exists in the opened workspace
- [ ] `.cursor/rules/memopilot.mdc` exists in the opened workspace
- [ ] `.vscode/mcp.json` contains a `memopilot` stdio server entry
- [ ] Copilot instructions mention `memopilot_context`
- [ ] Cursor rule mentions `memopilot-search`

### 3. Verify Tool Discovery

Use MemoPilot's backend or MCP tools view to confirm discovery:

- [ ] `GET /v1/mcp/tools` returns a configured `memopilot` server
- [ ] Configured `memopilot` server advertises exactly:
  - `memopilot-search`
  - `memopilot-symbols`
  - `memopilot-memory`
  - `memopilot-profile`
- [ ] Built-in MemoPilot MCP listing also shows the same retrieval-first tool set

### 4. Retrieval-First UI Command

Run **MemoPilot: Search Project Context** from the Command Palette.

- [ ] Input box appears asking for a code path, symbol, or behavior
- [ ] Entering a query opens a Markdown preview rather than the legacy task-flow panel
- [ ] Rendered output includes:
  - `MemoPilot Context`
  - relevant code snippets
  - rules or skills when present
  - context quality or repo/history sections when available
- [ ] No patch approval UI appears in the default path

### 5. Copilot Tool-Call Verification

Ask Copilot Chat a repository question such as:

```
Explain the billing validation flow and point me to the main symbols involved.
```

- [ ] Copilot discovers MemoPilot tools without manual setup beyond the generated files
- [ ] Copilot calls `memopilot_context` before answering the repository question
- [ ] If needed, Copilot also calls `memopilot_memory_search` or `memopilot_workspace_profile`
- [ ] Copilot's final answer references retrieved code rather than broad guessing
- [ ] No legacy patch generation or apply flow is triggered for a read-only question

### 6. Symbol and Memory Retrieval Verification

Verify the dedicated retrieval tools with targeted queries:

- [ ] `memopilot-symbols` returns exact or partial symbol matches with file locations
- [ ] `memopilot-memory` returns durable project facts when memory exists
- [ ] `memopilot-profile` returns workspace-level language/framework information
- [ ] Responses are bounded and readable in Copilot/Cursor chat

### 7. Legacy Mode Boundary

With `memopilot.legacyAgentMode` left at the default `false`:

- [ ] Running **MemoPilot: Search Project Context** opens retrieval preview, not TaskEntryPanel
- [ ] Running legacy commands from internal bindings shows a message that legacy mode must be enabled
- [ ] Command Palette does not surface legacy patch review / investigation / local agent flow commands by default

### 8. Optional Legacy Check

Set `memopilot.legacyAgentMode` to `true` and reload the window.

- [ ] Legacy commands reappear in the Command Palette
- [ ] **MemoPilot: Search Project Context** may still be used, but task-flow UI is again reachable
- [ ] Patch review / investigation / local agent flow commands work only in this mode

---

## Automated Verification Anchors

These checks should remain green in CI or focused local runs:

- [ ] `tests/test_workspace_init.py` verifies generated `.vscode/mcp.json`, `.github/copilot-instructions.md`, and `.cursor/rules/memopilot.mdc`
- [ ] `tests/test_mcp_tools.py` verifies `/v1/mcp/tools` sees the configured `memopilot` server from generated workspace bootstrap
- [ ] `tests/test_context_build.py` verifies `/v1/context/assemble` returns rendered MemoPilot context
- [ ] `tests/test_mcp_server.py` verifies retrieval-first MCP dispatch stays stable

---

## Legacy Patch Pipeline

The legacy patch pipeline can still be regression-tested separately, but it is no longer the primary release gate for MemoPilot's default surface.

---

## Provider Fallback Test (Legacy / Secondary)

### Test Ollama Fallback

1. **Disable Copilot** in Provider Matrix (toggle off)
2. Create a new task (same as Core Pipeline Test, steps 1-6)
3. **Verify:**
   - Model selection shows "Ollama: mistral" (not Copilot)
   - Generation uses Ollama (watch CPU spike on machine)
   - Patch quality is decent (similar structure to Copilot patch)
   - Cost shows $0.00

### Test Anthropic Fallback (Optional, Requires API Key)

1. Add Anthropic API key to `~/.memopilot/config.yaml`
2. Disable Copilot and Ollama
3. Create a new task
4. **Verify:**
   - Model selection shows "Anthropic: claude-3-5-sonnet"
   - Generation uses Anthropic (watch cost dashboard)
   - Patch appears with reasonable quality

---

## UI/UX Verification

- [ ] **Extension loads without errors**
  - No red X on MemoPilot sidebar icon
  - Output view shows no exceptions

- [ ] **All sidebar views are clickable and populate**
  - Status: Shows backend connection status
  - Workspace Profile: Shows language, frameworks
  - Memory: Can expand to see items
  - Rules: Shows rules from workspace
  - Context Pack: Shows sample files
  - Cost Guard: Shows budget and spending
  - Privacy: Shows local vs cloud calls
  - Provider Matrix: Shows available models with badges
  - Evidence: Shows attachments if any
  - Audit Log: Shows recent actions
  - Workspace Map: Shows file tree

- [ ] **Buttons and interactions work**
  - "Search Project Context" opens retrieval-first context preview
  - Workspace Profile and Memory commands populate without errors
  - Legacy task-flow controls appear only when `memopilot.legacyAgentMode` is enabled
  - No patch approval controls appear in the default retrieval-first path

---

## Error Handling & Recovery

### Test Backend Crash Recovery

1. **Kill the backend process** (PID from `agent.lock`)
2. **Try to create a new task:**
   - Extension should detect disconnection
   - Status view shows "❌ Disconnected" (red)
   - Auto-restart attempts (should reconnect within 5 seconds)
3. **Task should work** after reconnection (no manual restart required)

### Test Provider Failure Fallback

1. **Start with only Copilot available**, create a task
2. **Mid-generation, disable Copilot** (or unplug network)
3. **Verify:**
   - Generation times out (~10 seconds)
   - Fallback to next provider (Ollama) is attempted
   - Patch eventually completes via fallback

### Test Budget Limit

1. **Set `MAX_MONTHLY_SPEND_USD=0.10` in `.env`**
2. **Create tasks until spend approaches $0.10**
3. **Verify:**
   - Cost Dashboard shows red warning
   - New task creation shows: "Budget limit approaching"
   - Can still create tasks but with warning
   - Once $0.10 spent, new tasks blocked with: "Monthly budget exceeded"

---

## Security Verification

- [ ] **API keys not exposed in logs**
  - Backend output contains no `sk-...` or `sk-ant-...` strings
  - Check `~/.memopilot/` — no API keys in agent.lock

- [ ] **HMAC token in agent.lock**
  - File contains `"token": "..."` field
  - Token is different each restart (delete `agent.lock`, restart backend)

- [ ] **Local data stays local**
  - No files appear in cloud provider dashboards (OpenAI, Anthropic, etc.) unless intentionally enabled
  - Network tab in DevTools shows only localhost requests (127.0.0.1)

- [ ] **env.example document**
  - No real API keys in `.env.example`
  - File lists all available configuration options
  - Comments explain security implications

---

## Build & Release Verification

- [ ] **VSIX builds locally**
  ```bash
  cd packages/extension
  pnpm run package  # Should output memopilot-1.0.1.vsix
  ```

- [ ] **VSIX can be installed**
  - VS Code: Extensions → Install from VSIX
  - Select `memopilot-1.0.1.vsix`
  - Should load without errors

- [ ] **CI passes**
  - Push a branch to GitHub (e.g., `git push origin test-branch`)
  - GitHub Actions CI runs automatically:
    - ✅ Backend Tests (Python 3.11/3.12/3.13)
    - ✅ Backend Lint (Ruff)
    - ✅ Extension Build & Type Check (Node 20/22)
  - All jobs show green checkmarks

---

## Documentation Verification

- [ ] **README.md exists and is up-to-date**
  - Installation instructions are clear
  - Quick start guide works
  - Links to docs are not broken

- [ ] **SECURITY.md covers**
  - [ ] Data handling (local vs cloud)
  - [ ] API authentication
  - [ ] Secret management
  - [ ] Local-first architecture benefits
  - [ ] Threat model
  - [ ] Compliance (GDPR, CCPA, etc.)

- [ ] **CHANGELOG.md lists**
  - [ ] v1.0.1: CI/CD, vector prep, image analysis, MCP routing
  - [ ] v1.0.0: Core features, backend services, intelligence

- [ ] **.env.example is comprehensive**
  - [ ] All configuration options documented
  - [ ] Security warnings for sensitive settings
  - [ ] Provider priorities explained

---

## Final Sign-Off

- [ ] **All checks above pass**
- [ ] **No critical bugs or crashes**
- [ ] **Performance acceptable** (tasks complete in <30s typical)
- [ ] **User experience is smooth** (UI responsive, clear feedback)
- [ ] **Ready for beta release on GitHub**
- [ ] **Ready for VS Code Marketplace submission** (once secrets configured)

---

## Known Limitations (Document Before Release)

- ⚠️ **Task analysis is keyword-only** — doesn't use LLM for intent detection (Milestone 4-A upgrade)
- ⚠️ **Streaming tokens not visible in UI** — shows spinner instead of live tokens (Milestone 2 upgrade)
- ⚠️ **Image analysis disconnected** — can analyze images locally but not wired into task flow (Milestone 4-C upgrade)
- ⚠️ **MCP execution is simulated** — tool calls log output but don't run real tools (Milestone 3 upgrade)
- ⚠️ **sqlite-vec not yet integrated** — FTS5 only, no vector embeddings (Milestone 4-B upgrade)

---

## Sign-Off Template

```
Date: ___________
QA Tester: ___________
Result: ✅ PASS / ❌ FAIL

Notes:
[Any issues, improvements, or observations]

Recommendation:
[ ] Ready for GitHub Release
[ ] Ready for VS Code Marketplace
[ ] Needs fixes before release (list issues above)
```
