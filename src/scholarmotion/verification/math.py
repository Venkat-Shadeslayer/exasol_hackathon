from __future__ import annotations

from sympy import simplify, sympify

from scholarmotion.schemas import VerificationIssue


def verify_equivalence(left: str, right: str) -> bool:
    try:
        return simplify(sympify(left) - sympify(right)) == 0
    except (SyntaxError, TypeError, ValueError):
        return False


def verify_equations(scene_id: str, checks: list[tuple[str, str]]) -> list[VerificationIssue]:
    return [
        VerificationIssue(
            scene_id=scene_id,
            category="math.not_equivalent",
            severity="critical",
            confidence=0.99,
            objects=[left, right],
            description=f"{left} is not equivalent to {right}.",
            suggested_repair="Revise the SceneSpec equation from a sourced derivation.",
            correction_memory_candidate=False,
        )
        for left, right in checks
        if not verify_equivalence(left, right)
    ]
