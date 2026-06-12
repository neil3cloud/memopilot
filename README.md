# MemoPilot

**Rule-Aware, Local-Memory, Cost-Governed AI Development Agent Extension for VS Code/Cursor**

MemoPilot builds and maintains local application memory before sending any context to AI. It reads rules, project conventions, skills, source code, documentation, symbols, test patterns, previous decisions, and task history. It then generates a minimal, explainable context pack and routes the task to the cheapest capable model available.

## Architecture

```
packages/
  extension/   — VS Code extension (TypeScript)
  agent/       — Python backend (FastAPI local service)
```

## Development

### Extension

```bash
cd packages/extension
pnpm install
pnpm run build
```

### Backend

```bash
cd packages/agent
uv sync
uv run pytest
```

## License

MIT
