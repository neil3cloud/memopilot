# MemoPilot E2E Copilot Verification Checklist

> For QA/verification before releasing to marketplace
> Last updated: 2026-06-17

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

## Core Pipeline Test (Happy Path)

### 1. Open a Real Workspace

```bash
# Test on a real repo with Python or TypeScript code
cd ~/dev/some-project  # With git history, .py or .tsx files
code .
```

- [ ] Workspace Profile auto-builds (check Memory view → Workspace Profile)
- [ ] Profile shows detected language (Python, TypeScript, etc.)
- [ ] File index appears in Context Pack view (sample files listed)

### 2. Create a Task via UI

In TaskEntryPanel (MemoPilot → New Task button or Command Palette):

```
Task: Add error handling to the login function
Evidence files: (optional, skip for now)
```

- [ ] Task entry form appears with title input
- [ ] Tab switches work: Analyze → Context → Route → Generate → Approve → Validate → Apply
- [ ] "Next Step" button shows on Analyze tab

### 3. Analyze Stage ✅

Click "Next Step":

- [ ] **Analyze tab shows:**
  - Task description echoed
  - Suggested files populated (keyword matches: "login", "auth", etc.)
  - Task type detected (likely "feature" or "fix")
  - Signals shown (file matches, keyword strength, etc.)
- [ ] No errors in backend logs

### 4. Context Stage ✅

Click "Next Step":

- [ ] **Context tab shows:**
  - File list with relevance scores
  - Token count estimate (should be <8000 by default)
  - Cost estimate in dollars (tiny, like $0.001)
  - Rules applied (if any rules in workspace)
  - Approval status before proceeding (usually auto-approve for <8000 tokens)
- [ ] **Copilot detected** in Rules/Providers tab
  - Blue "copilot" badge visible in Provider Matrix
  - Cost estimate shows $0.00 for Copilot (no cost if using Microsoft account)

### 5. Route Stage ✅

Click "Next Step":

- [ ] **Route tab shows:**
  - Selected model: "GitHub Copilot" (or "Ollama: mistral" if Copilot unavailable)
  - Fallback chain visible: Host → Ollama → Anthropic → OpenAI
  - Confidence score displayed
- [ ] **Status updates:** "Routing task to [model name]..." briefly appears

### 6. Generate Patch Stage ✅✨ (The Main Event)

Click "Next Step":

- [ ] **Generation starts:**
  - Status shows "Generating patch with [model]..."
  - Loading spinner appears (placeholder for streaming tokens in v2)
  - **In Provider Matrix view:** Token count increments live as generation progresses
- [ ] **Generation completes (10-30s typical with Copilot/Ollama):**
  - Spinner stops
  - Patch preview appears in new panel: "Patch Preview"
  - Diff view shows: `- old code` (red) / `+ new code` (green)
  - File path and line numbers displayed
- [ ] **No errors in backend logs** — look for "error" or "exception" keywords

### 7. Approve Patch Stage ✅

In Patch Preview:

- [ ] Patch diff is syntactically correct (no garbled output)
- [ ] Files modified match suggested files from Analyze stage
- [ ] Changes are logical for the task ("add error handling" → shows try/except or if/error blocks)
- [ ] **Cost Dashboard tab shows:**
  - Call recorded with model, tokens, cost
  - Total spend updated
  - Budget still plenty (green)

Click "Approve" button:

- [ ] Task moves to Validate stage
- [ ] Patch content freezes (no more editing)

### 8. Validate Stage ✅

Click "Next Step":

- [ ] **Validation runs:**
  - For Python: `compileall` + `ruff check`
  - For TypeScript: `npx tsc --noEmit` + linter
  - Status shows "Running tests..." then validation results
  - Results show PASS/FAIL for each validator
- [ ] **All validators pass** (no syntax errors introduced):
  - Green checkmarks for each validator
  - No blocking errors

### 9. Apply Stage ✅

Click "Apply" button:

- [ ] **Files written to disk:**
  - Task status shows "Files applied successfully"
  - Modified files appear in VS Code file explorer with white dot (unsaved marker)
- [ ] **Verify in editor:**
  - Open the modified file
  - Changes are present (the generated code is there)
  - Syntax highlighting works (file recognized correctly)
- [ ] **Cost Guard records the complete cycle:**
  - Task shown in Cost Dashboard with all stages (analyze, generate, validate, apply)
  - Total cost calculated correctly

### 10. Memory & History

- [ ] Task appears in **Memory view → Recent Tasks**
- [ ] Memory view shows: task description, files modified, model used, cost
- [ ] Can "Recall" the task (click on it) to see full details
- [ ] **Privacy Dashboard shows:**
  - 1 Local call (Ollama if used)
  - 1 Host call (Copilot)
  - OR correct counts based on fallback path used

---

## Provider Fallback Test (Important for Reliability)

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
  - "New Task" button opens TaskEntryPanel
  - "Next Step" advances through stages
  - "Approve"/"Reject" buttons toggle appropriately
  - "Apply" writes files without prompts
  - Diff view colors are visible (red/green)

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
