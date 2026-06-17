"""Git commit history indexer and blame context provider (Layer 4).

Indexes recent git commit history for the current workspace and provides:
  - index_git_history()       — parse git log and store to commit_history tables
  - get_relevant_commits()    — recency-weighted FTS search on commit messages
  - get_blame_context()       — targeted git blame for specific line ranges

Capped at 500 commits / 90 days to keep the index compact.  LLM summarization
is only triggered for commits with a short message AND many changed files.
"""

from __future__ import annotations

import re
import subprocess
import uuid
from dataclasses import dataclass, field
from datetime import datetime

from .db import DatabaseManager

_MAX_COMMITS = 500
_MAX_DAYS = 90
_SUMMARIZE_MSG_THRESHOLD = 50       # chars — shorter messages may need expansion
_SUMMARIZE_FILES_THRESHOLD = 5      # min files changed to justify summarization


@dataclass
class CommitRecord:
    id: str
    commit_sha: str
    commit_message: str
    commit_summary: str | None
    author_name: str
    committed_at: str
    files: list[str] = field(default_factory=list)
    workspace_root: str = ""


@dataclass
class CommitFileChange:
    id: str
    commit_id: str
    file_path: str
    change_type: str        # added | modified | deleted | renamed
    lines_added: int = 0
    lines_deleted: int = 0


@dataclass
class BlameEntry:
    sha: str
    author: str
    committed_at: str
    line_number: int
    line_content: str
    commit_message: str | None = None


# ── Git log parsing ───────────────────────────────────────────────────────────

_COMMIT_HEADER_RE = re.compile(
    r"^([a-f0-9]{40})\|([^|]*)\|([^|]*)\|(.*)$"
)
_NUMSTAT_RE = re.compile(r"^(\d+|-)\t(\d+|-)\t(.+)$")


def _parse_git_log(output: str) -> list[CommitRecord]:
    """Parse the output of git log --format=... --numstat."""
    commits: list[CommitRecord] = []
    current: CommitRecord | None = None

    for line in output.splitlines():
        header_m = _COMMIT_HEADER_RE.match(line)
        if header_m:
            if current is not None:
                commits.append(current)
            sha, author, date, message = header_m.groups()
            current = CommitRecord(
                id=uuid.uuid4().hex,
                commit_sha=sha.strip(),
                commit_message=message.strip(),
                commit_summary=None,
                author_name=author.strip(),
                committed_at=date.strip(),
                files=[],
            )
            continue

        if current is not None:
            stat_m = _NUMSTAT_RE.match(line)
            if stat_m:
                current.files.append(stat_m.group(3).strip())

    if current is not None:
        commits.append(current)

    return commits


# ── Main indexer ──────────────────────────────────────────────────────────────

class GitHistoryIndexer:
    """Index recent git history for a workspace root."""

    def __init__(self, *, db: DatabaseManager) -> None:
        self._db = db

    async def index_git_history(
        self,
        workspace_root: str,
        max_commits: int = _MAX_COMMITS,
        max_days: int = _MAX_DAYS,
    ) -> int:
        """Index recent commits.  Returns the number of new commits stored."""
        import asyncio
        import functools
        loop = asyncio.get_event_loop()
        try:
            result = await loop.run_in_executor(
                None,
                functools.partial(
                    subprocess.run,
                    [
                        "git", "log",
                        "--format=%H|%an|%ai|%s",
                        "--numstat",
                        f"--max-count={max_commits}",
                        f"--since={max_days} days ago",
                    ],
                    cwd=workspace_root,
                    capture_output=True,
                    text=True,
                    timeout=30,
                ),
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return 0

        if result.returncode != 0:
            return 0

        commits = _parse_git_log(result.stdout)
        if not commits:
            return 0

        conn = await self._db.connect()

        # Fetch existing SHAs to avoid re-inserting
        cursor = await conn.execute(
            "SELECT commit_sha FROM commit_history WHERE workspace_root = ?",
            (workspace_root,),
        )
        existing_shas = {row["commit_sha"] for row in await cursor.fetchall()}

        stored = 0
        for commit in commits:
            if commit.commit_sha in existing_shas:
                continue

            import json
            summary = _cheap_summary(commit)
            await conn.execute(
                """
                INSERT INTO commit_history
                    (id, commit_sha, commit_message, commit_summary,
                     author_name, committed_at, files_changed_json, workspace_root)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT DO NOTHING
                """,
                (
                    commit.id,
                    commit.commit_sha,
                    commit.commit_message,
                    summary,
                    commit.author_name,
                    commit.committed_at,
                    json.dumps(commit.files),
                    workspace_root,
                ),
            )

            change_type_map: dict[str, str] = {}
            for file_path in commit.files:
                change_type_map[file_path] = "modified"

            for file_path in commit.files:
                change_id = uuid.uuid4().hex
                await conn.execute(
                    """
                    INSERT INTO commit_file_changes
                        (id, commit_id, file_path, change_type, lines_added, lines_deleted)
                    VALUES (?, ?, ?, ?, 0, 0)
                    """,
                    (change_id, commit.id, file_path, change_type_map.get(file_path, "modified")),
                )

            stored += 1

        await conn.commit()
        return stored

    async def get_relevant_commits(
        self,
        file_paths: list[str],
        task_description: str = "",
        workspace_root: str = "",
        limit: int = 10,
    ) -> list[CommitRecord]:
        """Return recent commits touching the given files, recency-weighted."""
        if not file_paths:
            return []

        conn = await self._db.connect()
        placeholders = ", ".join("?" * len(file_paths))

        cursor = await conn.execute(
            f"""
            SELECT DISTINCT
                ch.id, ch.commit_sha, ch.commit_message, ch.commit_summary,
                ch.author_name, ch.committed_at, ch.files_changed_json,
                CASE
                    WHEN julianday('now') - julianday(ch.committed_at) <= 7  THEN 1.0
                    WHEN julianday('now') - julianday(ch.committed_at) <= 30 THEN 0.85
                    WHEN julianday('now') - julianday(ch.committed_at) <= 90 THEN 0.70
                    ELSE 0.50
                END AS recency_score
            FROM commit_history ch
            JOIN commit_file_changes cfc ON cfc.commit_id = ch.id
            WHERE cfc.file_path IN ({placeholders})
              AND (ch.workspace_root = ? OR ch.workspace_root = '')
            ORDER BY recency_score DESC, ch.committed_at DESC
            LIMIT ?
            """,
            (*file_paths, workspace_root, limit),
        )
        rows = await cursor.fetchall()

        import json
        results: list[CommitRecord] = []
        for row in rows:
            files = json.loads(row["files_changed_json"] or "[]")
            results.append(
                CommitRecord(
                    id=row["id"],
                    commit_sha=row["commit_sha"],
                    commit_message=row["commit_message"],
                    commit_summary=row["commit_summary"],
                    author_name=row["author_name"],
                    committed_at=row["committed_at"],
                    files=files,
                    workspace_root=workspace_root,
                )
            )
        return results

    async def get_blame_context(
        self,
        file_path: str,
        line_start: int,
        line_end: int,
        workspace_root: str,
    ) -> list[BlameEntry]:
        """Return commit context for specific lines using git blame."""
        import asyncio
        import functools
        loop = asyncio.get_event_loop()
        try:
            result = await loop.run_in_executor(
                None,
                functools.partial(
                    subprocess.run,
                    [
                        "git", "blame",
                        "-L", f"{line_start},{line_end}",
                        "--porcelain",
                        file_path,
                    ],
                    cwd=workspace_root,
                    capture_output=True,
                    text=True,
                    timeout=15,
                ),
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return []

        if result.returncode != 0:
            return []

        entries = _parse_blame_porcelain(result.stdout)

        # Enrich with stored commit messages
        conn = await self._db.connect()
        for entry in entries:
            cursor = await conn.execute(
                "SELECT commit_message FROM commit_history WHERE commit_sha LIKE ? LIMIT 1",
                (entry.sha[:12] + "%",),
            )
            row = await cursor.fetchone()
            if row:
                entry.commit_message = row["commit_message"]

        return entries

    def format_commit_history_for_context(
        self, commits: list[CommitRecord], file_paths: list[str]
    ) -> str:
        """Format commit records as structured decision history for the context pack."""
        if not commits:
            return ""

        file_to_commits: dict[str, list[CommitRecord]] = {fp: [] for fp in file_paths}
        for commit in commits:
            for fp in file_paths:
                if fp in commit.files:
                    file_to_commits[fp].append(commit)

        lines = ["## Recent Changes to Modified Files\n"]
        for fp, file_commits in file_to_commits.items():
            if not file_commits:
                continue
            lines.append(f"{fp}:")
            for commit in file_commits[:3]:  # max 3 commits per file
                age = _human_age(commit.committed_at)
                summary = commit.commit_summary or commit.commit_message
                short_sha = commit.commit_sha[:8]
                changed = ", ".join(commit.files[:5])
                if len(commit.files) > 5:
                    changed += f" (+{len(commit.files) - 5} more)"
                lines.append(f"  {age} — \"{summary}\" [{short_sha}]")
                if changed:
                    lines.append(f"    Changed: {changed}")
            lines.append("")

        return "\n".join(lines)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _cheap_summary(commit: CommitRecord) -> str | None:
    """Return a lightweight summary for commits with short/ambiguous messages."""
    if len(commit.commit_message) >= _SUMMARIZE_MSG_THRESHOLD:
        return None
    if len(commit.files) < _SUMMARIZE_FILES_THRESHOLD:
        return None
    # Without a local model, generate a deterministic structural summary
    files_preview = ", ".join(commit.files[:3])
    if len(commit.files) > 3:
        files_preview += f" (+{len(commit.files) - 3} more)"
    return f"{commit.commit_message} — affects: {files_preview}"


def _human_age(committed_at: str) -> str:
    """Convert a git ISO datetime string to a human-readable age."""
    import re as _re
    try:
        # Strip trailing timezone offset ([+-]HH:MM or [+-]HHMM or Z) robustly
        clean = _re.sub(r'[+-]\d{2}:?\d{2}$', '', committed_at.replace('T', ' ')).strip()
        dt = datetime.fromisoformat(clean)
        now = datetime.now()
        delta_days = (now - dt).days
        if delta_days == 0:
            return "today"
        if delta_days == 1:
            return "yesterday"
        if delta_days <= 30:
            return f"{delta_days} days ago"
        weeks = delta_days // 7
        return f"{weeks} week{'s' if weeks > 1 else ''} ago"
    except Exception:
        return committed_at[:10]  # fallback to date string


def _parse_blame_porcelain(output: str) -> list[BlameEntry]:
    """Parse the output of git blame --porcelain."""
    entries: list[BlameEntry] = []
    sha = ""
    author = ""
    committed_at = ""

    for line in output.splitlines():
        if not line:
            continue
        if re.match(r"^[a-f0-9]{40}", line):
            parts = line.split()
            sha = parts[0][:40]
        elif line.startswith("author "):
            author = line[7:].strip()
        elif line.startswith("committer-time "):
            try:
                ts = int(line[15:].strip())
                committed_at = datetime.fromtimestamp(ts).isoformat()
            except ValueError:
                committed_at = ""
        elif line.startswith("\t"):
            entries.append(
                BlameEntry(
                    sha=sha,
                    author=author,
                    committed_at=committed_at,
                    line_number=len(entries) + 1,
                    line_content=line[1:],
                )
            )

    return entries
