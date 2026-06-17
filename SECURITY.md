# Security Policy

## Overview

MemoPilot is designed with security and privacy as first-class concerns. This document covers:

- **Data handling** — what data is stored locally vs sent to cloud providers
- **API authentication** — how the extension secures communication with the local backend
- **Secret management** — where and how API keys are stored
- **Local-first architecture** — why we run a local backend and how it protects your code

---

## Data Handling

### What Stays Local (Never Sent to Cloud)

✅ **Always Local:**
- Your workspace code and file structure (index only, via workspace profile)
- All task history and memory (stored in SQLite at `~/.memopilot/data.db`)
- All cost metrics and usage logs
- All LLM requests/responses when using local models (Ollama, LM Studio)
- Workspace rules and skill definitions

### What Can Be Sent to Cloud (Opt-In Only)

⚠️ **Only Sent If Explicitly Configured:**
- Code snippets → LLM provider (if cloud provider enabled in config)
- Screenshots/images → vision API (if enabled in `config.yaml`)
- Memory queries → semantic search (if cloud embedding model configured)

**Important:** All cloud API calls require explicit opt-in. By default, MemoPilot only uses local models.

### Default Configuration (Recommended for Security)

```yaml
# ~/.memopilot/config.yaml
provider:
  strategy: "local-only"  # Only use Ollama/LM Studio
  ollama:
    base_url: "http://localhost:11434"
    model: "mistral"
  
# Cloud providers disabled by default
openai:
  enabled: false
anthropic:
  enabled: false
```

---

## API Authentication

### Extension ↔ Backend Communication

The VS Code extension communicates with the local backend via HTTP with HMAC token authentication:

1. **Token Generation**: On first backend start, `BackendManager.ts` generates a random 32-byte token
2. **Storage**: Token is stored in `agent.lock` file (alongside port number)
3. **Signing**: Every extension request includes `Authorization: Bearer <TOKEN>` header
4. **Validation**: Backend validates token before processing any request
5. **Rotation**: Can be manually rotated by deleting `agent.lock` (generates new token on next start)

**File Location**: `~/.memopilot/agent.lock`

```json
{
  "port": 9042,
  "token": "aW51dmFsaWRfdG9rZW4...",
  "pid": 12345
}
```

### Why Local HTTP Over IPC?

- HTTP is simpler to debug and test
- Port is dynamically assigned (no port conflicts)
- Both Windows and Unix support TCP loopback equally well
- HMAC token provides replay-attack protection
- Token is in a restricted file (`600` permissions on Unix)

---

## Secret Management

### API Keys & Credentials

#### OpenAI / Anthropic / Google API Keys

**Recommended Storage**: `~/.memopilot/config.yaml` (user-owned, restricted permissions)

```yaml
# ~/.memopilot/config.yaml (on macOS/Linux: chmod 600)
openai:
  api_key: "sk-..."  # Only if explicitly enabled

anthropic:
  api_key: "sk-ant-..."  # Only if explicitly enabled
```

**Why Not in `.env`?**
- `.env` files can be accidentally committed to version control
- `~/.memopilot/config.yaml` is user-scoped and never checked in
- Different workspaces can have different configurations

**Never Store in:**
- ❌ `.env` (can be committed)
- ❌ `package.json` (can be committed)
- ❌ Extension settings (synced via VS Code settings sync)
- ❌ Workspace settings (stored in `.vscode/settings.json`, can be committed)

### HMAC Token (Backend ↔ Extension)

**Stored in**: `~/.memopilot/agent.lock` (auto-generated, unique per user)

**Permissions**: Should be `0600` (read/write by owner only)

```bash
# On Unix systems, verify permissions:
ls -la ~/.memopilot/agent.lock
# Should show: -rw------- (600)
```

---

## Local-First Architecture

MemoPilot intentionally runs a **private HTTP server on your machine** rather than connecting to a cloud service. Here's why:

### Security Benefits

1. **Code Never Leaves Your Machine**
   - File contents and git history stay local
   - Only snippets you explicitly ask to analyze leave
   - Workspace structure is indexed locally

2. **No Account Required**
   - No login, no user tracking
   - No data sharing with third parties by default
   - Complete control over your data

3. **Works Offline**
   - Full functionality without internet (if using local models)
   - Cloud LLM calls are optional and async
   - No internet outage = no productivity loss

4. **GDPR / Privacy Compliance**
   - All personal data stays under your control
   - No data residency concerns (it's on your machine)
   - Can delete everything by removing `~/.memopilot/`

---

## Threat Model & Assumptions

### What We Protect Against

- **Data exfiltration**: Code only leaves if you explicitly enable cloud providers
- **Replay attacks**: HMAC token prevents request forgery
- **Unauthorized local access**: Token file has restricted permissions (`600`)
- **Accidental commits**: API keys stored in `~/.memopilot/`, not in repo

### What We Don't Protect Against

- **Compromised local machine**: If attacker has local shell access, they can read anything
- **Malicious VS Code extensions**: Other extensions can read workspace files (standard VS Code security model)
- **Compromised cloud LLM provider**: If you enable cloud providers, they can see code snippets (same as ChatGPT)

### Recommendations

1. **Use strong OS-level authentication** (login password, biometric)
2. **Keep OS and VS Code updated** (security patches)
3. **Don't enable cloud providers on untrusted machines**
4. **Review `.memopilot/config.yaml` permissions regularly**
5. **Rotate token periodically**: Delete `agent.lock`, restart backend

---

## Environment Variables

For development and CI/CD, MemoPilot reads from `.env` files (see `.env.example`).

### `.env` File Locations (in priority order)

1. `packages/agent/.env` (project-local, ignored by git)
2. `~/.memopilot/.env` (user-local, optional)
3. Environment variables from shell

### Git Safety

- ✅ `.env.example` is committed (shows available options)
- ❌ `.env` is git-ignored (never committed)
- ✅ CI uses `secrets.GITHUB_TOKEN` for authentication (GitHub Actions)

---

## Release & Distribution

### VS Code Marketplace

MemoPilot can be published to the [VS Code Marketplace](https://marketplace.visualstudio.com/) with:

```bash
pnpm exec vsce publish --pat <VSCE_PAT>
```

#### Marketplace Security

- **Code scanning**: Microsoft scans all published extensions for malware
- **Publisher verification**: Account linked to GitHub organization
- **Transparency**: Source code is open-source on GitHub
- **Auto-updates**: VS Code automatically updates to latest version

### GitHub Release

VSIX files are attached to GitHub releases for manual download/install.

```bash
# Users can install from a release with:
# VS Code Extensions → Install from VSIX... → select .vsix file
```

---

## Reporting Security Issues

If you discover a security vulnerability:

1. **Do not** create a public issue
2. **Email** [security@example.com](mailto:security@example.com) with details
3. **Allow 30 days** for fix and release
4. **Credit**: Your name in the CHANGELOG if desired

---

## Compliance & Standards

- **License**: MIT (permissive, commercial use allowed)
- **GDPR**: ✅ Compliant (data stays local, no third-party sharing)
- **CCPA**: ✅ Compliant (user retains full data control)
- **SOC 2**: Not applicable (local-first, no hosted service)
- **HIPAA**: Potentially compliant (if cloud providers are disabled)

---

## Additional Resources

- [VS Code Extension Security](https://code.visualstudio.com/api/extension-guides/publish-extension#security-considerations)
- [OWASP Top 10](https://owasp.org/www-project-top-ten/)
- [Secure Software Development Framework (SSDF)](https://csrc.nist.gov/publications/detail/sp/800-218/final)
