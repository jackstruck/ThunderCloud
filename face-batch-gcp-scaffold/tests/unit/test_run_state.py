import pytest

from worker.run_state import RunState, can_transition, require_transition, retry_target


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (RunState.AWAITING_MEDIA, RunState.QUEUED),
        (RunState.FETCHING, RunState.QUEUED),
        (RunState.QUEUED, RunState.DETECTING),
        (RunState.DETECTING, RunState.AWAITING_FACE_SELECTION),
        (RunState.DETECTING, RunState.SUCCEEDED),
        (RunState.AWAITING_FACE_SELECTION, RunState.MATCHING),
        (RunState.MATCHING, RunState.SUCCEEDED),
    ],
)
def test_allowed_principal_transitions(current, target):
    assert can_transition(current, target)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (RunState.AWAITING_MEDIA, RunState.FETCHING),
        (RunState.AWAITING_FACE_SELECTION, RunState.QUEUED),
        (RunState.MATCHING, RunState.CANCELLED),
        (RunState.SUCCEEDED, RunState.MATCHING),
        (RunState.ENROLLING, RunState.CANCELLED),
    ],
)
def test_forbidden_principal_transitions(current, target):
    assert not can_transition(current, target)
    with pytest.raises(ValueError, match="invalid run transition"):
        require_transition(current, target)


def test_retry_uses_only_the_persisted_owning_step():
    assert retry_target("fetching") is RunState.FETCHING
    assert retry_target("detecting") is RunState.DETECTING
    assert retry_target("matching") is RunState.MATCHING
    with pytest.raises(ValueError, match="not retryable"):
        retry_target("enrolling")
