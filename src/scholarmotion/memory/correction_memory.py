from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True)
class Correction:
    correction_id: str
    category: str
    trigger_conditions: str
    anti_pattern: str
    required_behavior: str
    recommended_fix: str
    evidence_count: int
    confidence: float
    tags: tuple[str, ...]
    applicable_model: str = "any"
    status: str = "active"

    def prompt_fragment(self) -> str:
        return f"{self.correction_id} [{self.category}]\nTrigger: {self.trigger_conditions}\nRequired: {self.required_behavior}\nFix: {self.recommended_fix}"


class CorrectionMemory:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.entries: list[Correction] = []
        if self.path.exists():
            self.entries = self._parse(self.path.read_text(encoding="utf-8"))

    @staticmethod
    def _parse(text: str) -> list[Correction]:
        entries: list[Correction] = []
        for block in re.split(r"(?=^## CORR-)", text, flags=re.MULTILINE):
            header = re.match(r"## (CORR-[\w-]+)", block)
            if not header:
                continue
            values = {
                key.lower().replace(" ", "_"): value.strip()
                for key, value in re.findall(r"^- \*\*([^*]+)\*\*:\s*(.*)$", block, re.MULTILINE)
            }
            entries.append(
                Correction(
                    correction_id=header.group(1),
                    category=values.get("category", "general"),
                    trigger_conditions=values.get("trigger_conditions", ""),
                    anti_pattern=values.get("anti-pattern", ""),
                    required_behavior=values.get("required_behavior", ""),
                    recommended_fix=values.get("recommended_fix", ""),
                    evidence_count=int(values.get("evidence_count", "1")),
                    confidence=float(values.get("confidence", ".5")),
                    tags=tuple(
                        item.strip()
                        for item in values.get("applicable_scene_tags", "").split(",")
                        if item.strip()
                    ),
                    applicable_model=values.get("applicable_model", "any"),
                    status=values.get("status", "active"),
                )
            )
        return entries

    def retrieve(
        self,
        *,
        tags: list[str],
        primitives: list[str] | None = None,
        failures: list[str] | None = None,
        model: str = "any",
        limit: int = 5,
    ) -> list[Correction]:
        terms = {term.lower() for term in [*tags, *(primitives or []), *(failures or [])]}
        scored: list[tuple[float, Correction]] = []
        for entry in self.entries:
            if entry.status != "active" or entry.applicable_model not in {"any", model}:
                continue
            haystack = {entry.category.lower(), *[tag.lower() for tag in entry.tags]}
            score = (
                len(terms & haystack)
                + 0.2 * entry.confidence
                + 0.01 * min(entry.evidence_count, 20)
            )
            if score > 0.1:
                scored.append((score, entry))
        return [
            entry for _, entry in sorted(scored, key=lambda item: item[0], reverse=True)[:limit]
        ]

    def append_validated(self, entry: Correction, *, validation_tests: list[str]) -> None:
        if entry.evidence_count < 2 or entry.confidence < 0.8:
            raise ValueError("corrections need repeated, high-confidence evidence")
        if any(item.correction_id == entry.correction_id for item in self.entries):
            raise ValueError("correction ID already exists")
        today = datetime.now(UTC).date().isoformat()
        block = f"""\n## {entry.correction_id}\n\n- **Category**: {entry.category}\n- **Trigger conditions**: {entry.trigger_conditions}\n- **Anti-pattern**: {entry.anti_pattern}\n- **Required behavior**: {entry.required_behavior}\n- **Recommended fix**: {entry.recommended_fix}\n- **Evidence count**: {entry.evidence_count}\n- **Confidence**: {entry.confidence}\n- **Applicable scene tags**: {", ".join(entry.tags)}\n- **Applicable model**: {entry.applicable_model}\n- **First seen**: {today}\n- **Last seen**: {today}\n- **Validation tests**: {", ".join(validation_tests)}\n- **Status**: active\n"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(block)
        self.entries.append(entry)
