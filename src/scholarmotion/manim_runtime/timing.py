from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TimingAdjustment:
    scale: float
    extra_wait_seconds: float
    requires_repair: bool
    reason: str


def reconcile_timing(
    estimated_seconds: float,
    actual_seconds: float,
    *,
    tolerance: float = 0.12,
    repair_threshold: float = 0.35,
) -> TimingAdjustment:
    if estimated_seconds <= 0 or actual_seconds <= 0:
        raise ValueError("durations must be positive")
    relative = abs(actual_seconds - estimated_seconds) / estimated_seconds
    if relative <= tolerance:
        return TimingAdjustment(
            1.0, max(0.0, actual_seconds - estimated_seconds), False, "within tolerance"
        )
    scale = actual_seconds / estimated_seconds
    if relative <= repair_threshold:
        return TimingAdjustment(
            min(1.3, max(0.75, scale)), 0.0, False, "retime animation run_time values"
        )
    return TimingAdjustment(
        1.0, 0.0, True, "audio duration differs too much; code timing repair required"
    )
