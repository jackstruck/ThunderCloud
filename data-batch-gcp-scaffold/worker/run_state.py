from __future__ import annotations

from enum import StrEnum


class RunState(StrEnum):
    AWAITING_MEDIA = "awaiting_media"
    FETCHING = "fetching"
    QUEUED = "queued"
    DETECTING = "detecting"
    AWAITING_FACE_SELECTION = "awaiting_face_selection"
    MATCHING = "matching"
    ENROLLING = "enrolling"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


TERMINAL_STATES = frozenset({RunState.SUCCEEDED, RunState.CANCELLED, RunState.EXPIRED})

_TRANSITIONS = {
    RunState.AWAITING_MEDIA: {
        RunState.QUEUED,
        RunState.EXPIRED,
        RunState.CANCELLED,
    },
    RunState.FETCHING: {RunState.QUEUED, RunState.FAILED, RunState.CANCELLED},
    RunState.QUEUED: {RunState.DETECTING, RunState.FAILED, RunState.CANCELLED},
    RunState.DETECTING: {
        RunState.AWAITING_FACE_SELECTION,
        RunState.SUCCEEDED,
        RunState.FAILED,
        RunState.CANCELLED,
    },
    RunState.AWAITING_FACE_SELECTION: {
        RunState.MATCHING,
        RunState.EXPIRED,
        RunState.CANCELLED,
    },
    RunState.MATCHING: {
        RunState.SUCCEEDED,
        RunState.ENROLLING,
        RunState.FAILED,
    },
    RunState.ENROLLING: {RunState.SUCCEEDED, RunState.FAILED},
}


def can_transition(current: RunState, target: RunState) -> bool:
    """Return whether a principal (non-retry) state transition is valid."""
    return target in _TRANSITIONS.get(current, set())


def require_transition(current: RunState, target: RunState) -> None:
    if not can_transition(current, target):
        raise ValueError(f"invalid run transition: {current} -> {target}")


def retry_target(failed_step: str) -> RunState:
    """Map the persisted owning step of a retryable failure to its restart state."""
    try:
        target = RunState(failed_step)
    except ValueError as error:
        raise ValueError(f"invalid failed step: {failed_step}") from error
    if target not in {
        RunState.FETCHING,
        RunState.QUEUED,
        RunState.DETECTING,
        RunState.MATCHING,
    }:
        raise ValueError(f"step is not retryable: {failed_step}")
    return target
