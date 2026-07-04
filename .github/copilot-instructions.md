<!-- MemoPilot managed block: start -->
# MemoPilot Retrieval-First Instructions

Workspace: memopilot-1
Primary language: python
Detected frameworks: none detected

Use MemoPilot as the primary source of workspace context before answering codebase questions.

Required tool order for codebase questions:
1. Call `memopilot-search` first to assemble bounded workspace context.
2. Call `memopilot-symbols` when you need exact or partial symbol lookup.
3. Call `memopilot-memory` when you need project facts, conventions, or prior decisions.
4. Call `memopilot-profile` when framework, language, or workspace-wide policy is relevant.

Behavioral rules:
- Prefer MemoPilot-retrieved context over broad repository guessing.
- Do not assume MemoPilot applies patches or owns file mutation in default mode.
- If MemoPilot context is insufficient, say what is missing instead of inventing details.
<!-- MemoPilot managed block: end -->
























































































