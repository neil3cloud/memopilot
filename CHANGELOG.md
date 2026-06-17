# Changelog

All notable changes to MemoPilot will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.1] - 2026-06-17

### Added
- **CI/CD Pipeline**: Full GitHub Actions workflow for testing and building
- **Vector Search Foundation**: sqlite-vec dependency for semantic memory recall (Milestone 4-B prep)
- **Image Analysis Module**: Multi-provider vision analysis (Ollama → OpenAI → Anthropic)
- **Investigation Service**: Evidence attachment and findings extraction
- **MCP Server Integration**: JSON-RPC message routing for tool execution

### Fixed
- Extension type checking with TypeScript strict mode
- Test arity and readonly property assignments in test suite
- npm cache configuration for monorepo structure
- Backend tests now require `--extra dev` flag for dev tools

### Changed
- Upgraded pnpm to v9 for Node 20/22 compatibility
- Improved CI workflow reliability with explicit binary paths
- Simplified extension CI by skipping devDependencies that require newer Node versions

### Security
- Added `--ignore-scripts` to CI npm install to skip post-install hooks
- Environment variables for cloud API keys are workspace-configurable via `.memopilot/config.yaml`
- All sensitive data stays local; cloud calls are opt-in and documented

## [1.0.0] - 2026-06-15

### Added
- **Core Extension Architecture**
  - Full task pipeline (analyze → context → route → generate → approve → apply)
  - 11 sidebar TreeView providers with real-time updates
  - 5 webview panels for task management and dashboards
  - Live streaming token display during patch generation

- **Backend Services**
  - FastAPI server with 70+ API endpoints
  - SQLite database with FTS5 full-text search
  - Cost guard with budget enforcement and provider restrictions
  - LLM client supporting Anthropic, OpenAI, Ollama, LM Studio
  - Context builder with versioned diffs

- **Intelligence Features**
  - Outcome-aware model routing with local discovery
  - Memory system with task/evidence/insight storage
  - Workspace profile auto-build and validation
  - Privacy dashboard with cost tracking

### Verified
- 357 backend tests passing (pytest across Python 3.11/3.12/3.13)
- 286+ integration tests for all major services
- Type-safe extension codebase with TypeScript strict mode
- VSIX buildable and deployable locally
