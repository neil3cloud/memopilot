"""Team policy packs: CRUD and evaluation (v2 capability)."""

from __future__ import annotations

import fnmatch
import json
import uuid
from dataclasses import dataclass
from pathlib import Path

import yaml

from .config import Config
from .db import DatabaseManager
from .workspace_roots import WorkspaceRootsService

_PRECEDENCE = {
    "safety_rules": 400,
    "policy_pack_rules": 300,
    "workspace_rules": 200,
    "global_dev_rules": 100,
}
_FRONTIER_MARKERS = ("gpt-4", "gpt-5", "claude", "sonnet", "opus", "frontier")


@dataclass(frozen=True)
class PolicyPackItem:
    pack_id: str
    name: str
    description: str
    enforcement_mode: str
    rules: list[str]
    active: bool
    version: int


@dataclass(frozen=True)
class ActivePolicyRule:
    rule: str
    source: str
    source_kind: str
    precedence: int
    enforcement_mode: str
    pack_id: str | None = None
    pack_name: str | None = None


@dataclass(frozen=True)
class PolicyEvaluationResult:
    allowed: bool
    decision: str
    stage: str
    active_pack_id: str | None
    active_pack_name: str | None
    violations: list[str]
    applied_policies: list[str]


@dataclass(frozen=True)
class PolicyConflict:
    rule: str
    source: str
    source_kind: str
    overridden_by_rule: str
    overridden_by_source: str
    overridden_by_kind: str
    conflict_key: str


class PolicyPacksService:
    """Manages team policy packs and evaluates policies against task context."""

    def __init__(self, *, config: Config, db: DatabaseManager) -> None:
        self._config = config
        self._db = db

    async def list_policy_packs(self, *, limit: int = 100) -> list[PolicyPackItem]:
        conn = await self._db.connect()
        cursor = await conn.execute(
            """
            SELECT p.id, p.name, p.description, p.enforcement_mode, p.rules_json, p.active,
                   COALESCE(v.version, 0) AS version
            FROM policy_packs p
            LEFT JOIN (
                SELECT pack_id, MAX(version) AS version
                FROM policy_pack_versions
                GROUP BY pack_id
            ) v ON v.pack_id = p.id
            ORDER BY p.active DESC, p.updated_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = await cursor.fetchall()
        return [
            PolicyPackItem(
                pack_id=row["id"],
                name=row["name"],
                description=row["description"] or "",
                enforcement_mode=row["enforcement_mode"],
                rules=self._load_rules(row["rules_json"]),
                active=bool(row["active"]),
                version=int(row["version"] or 0),
            )
            for row in rows
        ]

    async def save_policy_pack(
        self,
        *,
        name: str,
        description: str,
        enforcement_mode: str,
        rules: list[str],
    ) -> PolicyPackItem:
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("policy pack name is required")
        clean_rules = [rule.strip() for rule in rules if rule.strip()]
        if not clean_rules:
            raise ValueError("policy pack requires at least one rule")
        if enforcement_mode not in {"enforce", "advisory"}:
            raise ValueError("enforcement_mode must be 'enforce' or 'advisory'")

        conn = await self._db.connect()
        cursor = await conn.execute(
            """
            SELECT id, active
            FROM policy_packs
            WHERE lower(name) = lower(?)
            LIMIT 1
            """,
            (clean_name,),
        )
        existing = await cursor.fetchone()
        pack_id = existing["id"] if existing is not None else uuid.uuid4().hex
        active = bool(existing["active"]) if existing is not None else False
        content_json = json.dumps(
            {
                "name": clean_name,
                "description": description,
                "enforcement_mode": enforcement_mode,
                "rules": clean_rules,
            }
        )

        await conn.execute(
            """
            INSERT INTO policy_packs
            (id, name, description, enforcement_mode, rules_json, active, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                description = excluded.description,
                enforcement_mode = excluded.enforcement_mode,
                rules_json = excluded.rules_json,
                updated_at = datetime('now')
            """,
            (
                pack_id,
                clean_name,
                description.strip(),
                enforcement_mode,
                json.dumps(clean_rules),
                1 if active else 0,
            ),
        )

        version_cursor = await conn.execute(
            """
            SELECT COALESCE(MAX(version), 0) AS max_version
            FROM policy_pack_versions
            WHERE pack_id = ?
            """,
            (pack_id,),
        )
        version_row = await version_cursor.fetchone()
        next_version = int(version_row["max_version"] or 0) + 1
        await conn.execute(
            """
            INSERT INTO policy_pack_versions
            (id, pack_id, version, content_json, created_at)
            VALUES (?, ?, ?, ?, datetime('now'))
            """,
            (uuid.uuid4().hex, pack_id, next_version, content_json),
        )
        await conn.commit()
        return PolicyPackItem(
            pack_id=pack_id,
            name=clean_name,
            description=description.strip(),
            enforcement_mode=enforcement_mode,
            rules=clean_rules,
            active=active,
            version=next_version,
        )

    async def activate_policy_pack(self, *, pack_id: str) -> None:
        conn = await self._db.connect()
        cursor = await conn.execute(
            "SELECT id FROM policy_packs WHERE id = ? LIMIT 1",
            (pack_id,),
        )
        existing = await cursor.fetchone()
        if existing is None:
            raise ValueError(f"Policy pack not found: {pack_id}")
        await conn.execute("UPDATE policy_packs SET active = 0")
        await conn.execute(
            "UPDATE policy_packs SET active = 1, updated_at = datetime('now') WHERE id = ?",
            (pack_id,),
        )
        await conn.commit()

    async def load_from_directory(self, policy_dir: Path) -> list[PolicyPackItem]:
        if not policy_dir.exists() or not policy_dir.is_dir():
            return []

        loaded: list[PolicyPackItem] = []
        conn = await self._db.connect()
        for policy_file in sorted(policy_dir.glob("*.yaml")) + sorted(policy_dir.glob("*.yml")):
            payload = self._read_policy_file(policy_file)
            item = await self.save_policy_pack(
                name=str(payload.get("name") or policy_file.stem),
                description=str(payload.get("description") or ""),
                enforcement_mode=str(payload.get("enforcement_mode") or "enforce"),
                rules=[str(rule) for rule in payload.get("rules", [])],
            )
            is_active = bool(payload.get("active", True))
            await conn.execute(
                "UPDATE policy_packs SET active = ?, updated_at = datetime('now') WHERE id = ?",
                (1 if is_active else 0, item.pack_id),
            )
            loaded.append(
                PolicyPackItem(
                    pack_id=item.pack_id,
                    name=item.name,
                    description=item.description,
                    enforcement_mode=item.enforcement_mode,
                    rules=item.rules,
                    active=is_active,
                    version=item.version,
                )
            )
        await conn.commit()
        return loaded

    async def load_policy_directory(
        self, *, workspace_root: str | None = None
    ) -> list[PolicyPackItem]:
        root = await self._resolve_workspace_root(workspace_root)
        return await self.load_from_directory(root / ".memopilot-policy")

    async def list_active_policy_rules(
        self,
        *,
        workspace_root: str | None = None,
    ) -> list[ActivePolicyRule]:
        raw_rules = await self._collect_policy_rules(workspace_root=workspace_root)
        return sorted(
            raw_rules,
            key=lambda item: (
                -item.precedence,
                0 if self._is_deny_rule(item.rule) else 1,
                item.source,
            ),
        )

    def resolve_conflicts(self, rules: list[ActivePolicyRule]) -> list[PolicyConflict]:
        prioritized = sorted(
            rules,
            key=lambda item: (
                -item.precedence,
                0 if self._is_deny_rule(item.rule) else 1,
                item.source,
            ),
        )
        winners: dict[tuple[str, str], ActivePolicyRule] = {}
        conflicts: list[PolicyConflict] = []
        for rule in prioritized:
            key = self._conflict_key(rule.rule)
            if key is None:
                continue
            winner = winners.get(key)
            if winner is None:
                winners[key] = rule
                continue
            if winner.rule == rule.rule and winner.source_kind == rule.source_kind:
                continue
            conflicts.append(
                PolicyConflict(
                    rule=rule.rule,
                    source=rule.source,
                    source_kind=rule.source_kind,
                    overridden_by_rule=winner.rule,
                    overridden_by_source=winner.source,
                    overridden_by_kind=winner.source_kind,
                    conflict_key=f"{key[0]}:{key[1]}",
                )
            )
        return conflicts

    def resolve_policy_conflicts(self, rules: list[ActivePolicyRule]) -> list[ActivePolicyRule]:
        prioritized = sorted(
            rules,
            key=lambda item: (
                -item.precedence,
                0 if self._is_deny_rule(item.rule) else 1,
                item.source,
            ),
        )
        resolved: list[ActivePolicyRule] = []
        seen_keys: set[tuple[str, str]] = set()
        for rule in prioritized:
            key = self._conflict_key(rule.rule)
            if key is not None and key in seen_keys:
                continue
            resolved.append(rule)
            if key is not None:
                seen_keys.add(key)
        return resolved

    async def evaluate_policy(
        self,
        *,
        stage: str,
        task_text: str,
        files_changed: list[str],
        selected_model: str | None,
        workspace_root: str | None = None,
    ) -> PolicyEvaluationResult:
        raw_rules = await self._collect_policy_rules(workspace_root=workspace_root)
        active_rules = self.resolve_policy_conflicts(raw_rules)
        if not active_rules:
            return PolicyEvaluationResult(
                allowed=True,
                decision="allow",
                stage=stage,
                active_pack_id=None,
                active_pack_name=None,
                violations=[],
                applied_policies=[],
            )

        selected = (selected_model or "").lower()
        changed = [path.lower() for path in files_changed]
        task_lower = task_text.lower()
        enforced_violations: list[str] = []
        advisory_violations: list[str] = []
        blocking_rule: ActivePolicyRule | None = None

        for entry in active_rules:
            violation = self._evaluate_rule(
                entry=entry,
                stage=stage,
                task_lower=task_lower,
                changed=changed,
                selected=selected,
            )
            if violation is None:
                continue
            if entry.enforcement_mode == "enforce":
                enforced_violations.append(violation)
                blocking_rule = blocking_rule or entry
            else:
                advisory_violations.append(violation)
                blocking_rule = blocking_rule or entry

        if enforced_violations:
            return PolicyEvaluationResult(
                allowed=False,
                decision="block",
                stage=stage,
                active_pack_id=blocking_rule.pack_id if blocking_rule else None,
                active_pack_name=blocking_rule.pack_name if blocking_rule else None,
                violations=enforced_violations + advisory_violations,
                applied_policies=[entry.rule for entry in active_rules],
            )
        if advisory_violations:
            return PolicyEvaluationResult(
                allowed=True,
                decision="warn",
                stage=stage,
                active_pack_id=blocking_rule.pack_id if blocking_rule else None,
                active_pack_name=blocking_rule.pack_name if blocking_rule else None,
                violations=advisory_violations,
                applied_policies=[entry.rule for entry in active_rules],
            )
        return PolicyEvaluationResult(
            allowed=True,
            decision="allow",
            stage=stage,
            active_pack_id=active_rules[0].pack_id,
            active_pack_name=active_rules[0].pack_name,
            violations=[],
            applied_policies=[entry.rule for entry in active_rules],
        )

    async def _collect_policy_rules(
        self,
        *,
        workspace_root: str | None = None,
    ) -> list[ActivePolicyRule]:
        root = await self._resolve_workspace_root(workspace_root)
        active_packs = await self._active_policy_packs()
        raw_rules: list[ActivePolicyRule] = []

        for pack in active_packs:
            source_kind = self._infer_pack_kind(pack.name)
            precedence = _PRECEDENCE[source_kind]
            for rule in pack.rules:
                raw_rules.append(
                    ActivePolicyRule(
                        rule=rule,
                        source=f"policy-pack:{pack.name}",
                        source_kind=source_kind,
                        precedence=precedence,
                        enforcement_mode=pack.enforcement_mode,
                        pack_id=pack.pack_id,
                        pack_name=pack.name,
                    )
                )

        raw_rules.extend(
            self._load_rule_file_entries(
                directory=root / ".memopilot" / "rules",
                source_kind="workspace_rules",
                default_enforcement_mode="enforce",
                root=root,
            )
        )
        raw_rules.extend(
            self._load_rule_file_entries(
                directory=self._config.global_dir / "rules",
                source_kind="global_dev_rules",
                default_enforcement_mode="advisory",
                root=root,
            )
        )
        return raw_rules

    async def _active_policy_packs(self) -> list[PolicyPackItem]:
        conn = await self._db.connect()
        cursor = await conn.execute(
            """
            SELECT id, name, description, enforcement_mode, rules_json, active
            FROM policy_packs
            WHERE active = 1
            ORDER BY updated_at DESC
            """
        )
        rows = await cursor.fetchall()
        return [
            PolicyPackItem(
                pack_id=row["id"],
                name=row["name"],
                description=row["description"] or "",
                enforcement_mode=row["enforcement_mode"],
                rules=self._load_rules(row["rules_json"]),
                active=bool(row["active"]),
                version=0,
            )
            for row in rows
        ]

    async def _resolve_workspace_root(self, workspace_root: str | None) -> Path:
        service = WorkspaceRootsService(config=self._config, db=self._db)
        return await service.resolve_workspace_root(workspace_root)

    def _read_policy_file(self, policy_file: Path) -> dict[str, object]:
        raw = policy_file.read_text(encoding="utf-8")
        if policy_file.suffix.lower() == ".json":
            payload = json.loads(raw)
        else:
            payload = yaml.safe_load(raw)
        if not isinstance(payload, dict):
            raise ValueError(f"Policy file must contain an object: {policy_file}")
        rules = payload.get("rules", [])
        if not isinstance(rules, list):
            raise ValueError(f"Policy file rules must be a list: {policy_file}")
        return payload

    def _load_rule_file_entries(
        self,
        *,
        directory: Path,
        source_kind: str,
        default_enforcement_mode: str,
        root: Path,
    ) -> list[ActivePolicyRule]:
        if not directory.exists() or not directory.is_dir():
            return []
        entries: list[ActivePolicyRule] = []
        for rule_file in sorted(directory.glob("*.y*ml")) + sorted(directory.glob("*.json")):
            try:
                payload = self._read_policy_file(rule_file)
            except Exception:
                continue
            effective_kind = source_kind
            if source_kind == "workspace_rules" and "global" in rule_file.stem.lower():
                effective_kind = "global_dev_rules"
            rules = payload.get("rules", [])
            for rule in rules if isinstance(rules, list) else []:
                entries.append(
                    ActivePolicyRule(
                        rule=str(rule),
                        source=str(rule_file),
                        source_kind=effective_kind,
                        precedence=_PRECEDENCE[effective_kind],
                        enforcement_mode=str(
                            payload.get("enforcement_mode") or default_enforcement_mode
                        ),
                    )
                )
        return entries

    def _infer_pack_kind(self, name: str) -> str:
        lowered = name.lower()
        if "safety" in lowered:
            return "safety_rules"
        return "policy_pack_rules"

    def _evaluate_rule(
        self,
        *,
        entry: ActivePolicyRule,
        stage: str,
        task_lower: str,
        changed: list[str],
        selected: str,
    ) -> str | None:
        lower = entry.rule.lower()
        if lower.startswith("deny_model:") and stage == "model_call":
            denied_tokens = self._split_csv(lower.split(":", 1)[1])
            if selected and any(token in selected for token in denied_tokens):
                return f"Denied model by policy: {entry.rule}"
        if lower.startswith("allow_model:") and stage == "model_call":
            return None
        if lower.startswith("forbid_path:") and stage == "patch_execution":
            patterns = self._split_csv(lower.split(":", 1)[1])
            if any(any(fnmatch.fnmatch(path, pattern) for pattern in patterns) for path in changed):
                return f"Forbidden file path by policy: {entry.rule}"
        if lower.startswith("require_keyword:") and stage == "model_call":
            required = self._split_csv(lower.split(":", 1)[1])
            missing = [token for token in required if token not in task_lower]
            if missing:
                return f"Missing required task keywords: {', '.join(missing)}"
        if (
            ("require_test_file" in lower or "must include tests" in lower)
            and stage == "patch_execution"
            and changed
            and not any("test" in path for path in changed)
        ):
            return "Policy requires at least one test file change."
        if (
            stage == "model_call"
            and self._rule_denies_frontier(lower)
            and self._is_frontier_model(selected)
        ):
            return f"Frontier model usage denied by policy: {entry.rule}"
        return None

    def _rule_denies_frontier(self, lower_rule: str) -> bool:
        if lower_rule in {
            "deny_frontier_models",
            "forbid_frontier_models",
            "block_frontier_models",
        }:
            return True
        if lower_rule.startswith("allow_frontier:"):
            return lower_rule.split(":", 1)[1].strip() in {"false", "0", "no"}
        return False

    def _is_frontier_model(self, model: str) -> bool:
        return bool(model) and any(marker in model for marker in _FRONTIER_MARKERS)

    def _conflict_key(self, rule: str) -> tuple[str, str] | None:
        lower = rule.lower().strip()
        if lower.startswith("deny_model:") or lower.startswith("allow_model:"):
            prefix, values = lower.split(":", 1)
            keys = self._split_csv(values)
            return (prefix.split("_", 1)[1], ",".join(sorted(keys)))
        if lower.startswith("forbid_path:") or lower.startswith("allow_path:"):
            prefix, values = lower.split(":", 1)
            keys = self._split_csv(values)
            return (prefix.split("_", 1)[1], ",".join(sorted(keys)))
        if self._rule_denies_frontier(lower) or lower in {"allow_frontier", "allow_frontier:true"}:
            return ("frontier", "frontier")
        return None

    def _is_deny_rule(self, rule: str) -> bool:
        lower = rule.lower().strip()
        return lower.startswith(("deny_", "forbid_", "block_")) or self._rule_denies_frontier(lower)

    def _load_rules(self, raw: str | None) -> list[str]:
        if not raw:
            return []
        value = json.loads(raw)
        if not isinstance(value, list):
            return []
        return [str(item) for item in value if str(item).strip()]

    def _split_csv(self, values: str) -> list[str]:
        return [item.strip() for item in values.split(",") if item.strip()]
