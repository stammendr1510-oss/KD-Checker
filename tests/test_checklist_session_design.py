"""Synthetic, offline evidence for Issue #21; no real credentials or documents."""
from dataclasses import fields, replace
import itertools

import pytest

from design.checklist_session_model import (
    Binding, Health, Observation, Reason, RenewalResult, Session, Transport,
    assess, begin_refresh, finish_refresh, record_verified_reauthorization, revoke,
)


USER_A = Binding("project-fiction", "person-a", "login-a", "pc-a")


def ready(binding=USER_A):
    return record_verified_reauthorization(Session(), binding)


def evidence(state):
    return Observation(
        configured=True, configuration_valid=True, identity_verified=True,
        access_current=True, session_current=True, membership_current=True,
        permission_current=True, transport=Transport.OK,
        request_id=11, response_request_id=11, request_epoch=state.epoch,
        request_generation=state.generation, response_binding=state.binding,
        snapshot_valid_and_complete=True,
    )


def begin(state):
    return begin_refresh(state, binding=state.binding, epoch=state.epoch,
                         generation=state.generation)


def test_disabled_and_unconfigured_never_verify():
    assert assess(Session(), Observation()).status == "not_configured"
    assert assess(Session(), Observation(configured=True)).status == "unavailable"
    assert assess(Session(), Observation(configured=True)).reason == Reason.CONFIG_INVALID


def test_fresh_complete_authorized_response_only():
    state = ready()
    assert assess(state, evidence(state)).status == "verified"
    assert assess(state, evidence(state)).reason == Reason.VERIFIED


@pytest.mark.parametrize("name", [
    "configuration_valid", "identity_verified", "access_current", "session_current",
    "membership_current", "permission_current", "snapshot_valid_and_complete",
])
def test_any_missing_evidence_fails_closed(name):
    state = ready()
    assert assess(state, replace(evidence(state), **{name: False})).status == "unavailable"


def test_all_combinations_of_authorization_evidence():
    state = ready()
    names = ("identity_verified", "access_current", "session_current",
             "membership_current", "permission_current", "snapshot_valid_and_complete")
    for flags in itertools.product((False, True), repeat=len(names)):
        decision = assess(state, replace(evidence(state), **dict(zip(names, flags))))
        assert (decision.status == "verified") == all(flags)


@pytest.mark.parametrize(("transport", "reason"), [
    (Transport.TIMEOUT, Reason.TIMEOUT), (Transport.DENIED, Reason.PERMISSION_DENIED),
    (Transport.REDIRECT, Reason.REDIRECT), (Transport.FAILED, Reason.TRANSPORT_FAILED),
])
def test_previous_success_cannot_mask_current_transport_failure(transport, reason):
    state = ready()
    historical = assess(state, evidence(state))
    current = assess(state, replace(evidence(state), transport=transport))
    assert historical.status == "verified"
    assert current.status == "unavailable" and current.reason == reason
    assert historical.status == "verified"  # Immutable historical decision remains historical.


@pytest.mark.parametrize("change", [
    {"response_request_id": 12}, {"request_id": 0, "response_request_id": 0},
    {"request_epoch": 0}, {"request_generation": 1},
])
def test_old_or_unrelated_response_is_not_current_evidence(change):
    state = ready()
    decision = assess(state, replace(evidence(state), **change))
    assert decision.status == "unavailable" and decision.reason == Reason.STALE_RESPONSE


@pytest.mark.parametrize("component", ["project", "user", "session", "device"])
def test_each_identity_binding_component_is_required(component):
    state = ready()
    other = replace(USER_A, **{component: "other-fiction"})
    assert begin_refresh(state, binding=other, epoch=state.epoch, generation=0)[1] is None
    decision = assess(state, replace(evidence(state), response_binding=other))
    assert decision.reason == Reason.IDENTITY_UNVERIFIED


def test_two_refreshes_have_one_winner_and_stale_generation_cannot_retry():
    initial = ready()
    waiting, attempt = begin(initial)
    assert attempt is not None
    assert begin(waiting) == (waiting, None)
    assert assess(waiting, evidence(waiting)).reason == Reason.REFRESH_IN_PROGRESS
    updated = finish_refresh(waiting, attempt, RenewalResult.COMMITTED)
    assert updated.generation == 1 and updated.pending is None
    assert begin_refresh(updated, binding=USER_A, epoch=initial.epoch, generation=0)[1] is None
    assert finish_refresh(updated, attempt, RenewalResult.UNCERTAIN) == updated
    assert finish_refresh(updated, attempt, RenewalResult.COMMITTED) == updated
    # A GET begun before rotation is discarded even if a complete response arrives.
    assert assess(updated, evidence(initial)).reason == Reason.STALE_RESPONSE
    assert assess(updated, evidence(updated)).status == "verified"


def test_uncertain_refresh_requires_interactive_reauthorization():
    # All these future adapter failures deliberately have the same safe transition.
    waiting, attempt = begin(ready())
    uncertain = finish_refresh(waiting, attempt, RenewalResult.UNCERTAIN)
    assert uncertain.health == Health.IN_DOUBT
    assert begin(uncertain) == (uncertain, None)
    assert assess(uncertain, evidence(uncertain)).reason == Reason.REFRESH_UNCERTAIN
    assert finish_refresh(uncertain, attempt, RenewalResult.COMMITTED) == uncertain
    new = record_verified_reauthorization(uncertain, USER_A)
    assert new.epoch > uncertain.epoch
    assert assess(new, evidence(new)).status == "verified"


@pytest.mark.parametrize("action", ["revoke_or_logout", "user_switch", "new_login"])
def test_old_refresh_completion_cannot_resurrect_logged_out_or_replaced_session(action):
    waiting, attempt = begin(ready())
    if action == "revoke_or_logout":
        current = revoke(waiting)
        assert assess(current, evidence(current)).reason == Reason.SESSION_REVOKED
    else:
        binding = replace(USER_A, user="person-b") if action == "user_switch" else USER_A
        current = record_verified_reauthorization(waiting, binding)
    for result in RenewalResult:
        assert finish_refresh(current, attempt, result) == current


def test_revocation_during_refresh_and_membership_loss_fail_closed():
    waiting, attempt = begin(ready())
    revoked = finish_refresh(waiting, attempt, RenewalResult.REVOKED)
    assert revoked.health == Health.REVOKED
    assert begin(revoked)[1] is None
    state = ready()
    assert assess(state, replace(evidence(state), membership_current=False)).reason == Reason.MEMBERSHIP_UNVERIFIED
    assert assess(state, replace(evidence(state), permission_current=False)).reason == Reason.PERMISSION_DENIED


def test_separate_computers_do_not_share_a_coordinator():
    first = ready()
    second = ready(replace(USER_A, session="login-pc-b", device="pc-b"))
    waiting, attempt = begin(first)
    assert finish_refresh(second, attempt, RenewalResult.COMMITTED) == second
    assert begin(second)[1] is not None
    assert waiting.pending == attempt


def test_late_result_cannot_complete_a_newer_pending_attempt():
    waiting, old_attempt = begin(ready())
    rotated = finish_refresh(waiting, old_attempt, RenewalResult.COMMITTED)
    next_waiting, next_attempt = begin(rotated)
    assert next_attempt != old_attempt
    for result in RenewalResult:
        assert finish_refresh(next_waiting, old_attempt, result) == next_waiting
    assert finish_refresh(next_waiting, next_attempt, RenewalResult.COMMITTED).generation == 2


def test_absent_session_and_unknown_refresh_outcome_cannot_be_used():
    assert begin_refresh(Session(), binding=USER_A, epoch=0, generation=0)[1] is None
    assert assess(Session(), evidence(ready())).reason == Reason.REAUTH_REQUIRED
    waiting, attempt = begin(ready())
    uncertain = finish_refresh(waiting, attempt, "untrusted-outcome")
    assert uncertain.health == Health.IN_DOUBT
    assert assess(uncertain, evidence(uncertain)).status == "unavailable"


def test_diagnostics_have_only_fixed_public_codes():
    assert [field.name for field in fields(assess(Session(), Observation()))] == ["status", "reason"]
    state = ready()
    decision = assess(state, replace(evidence(state), transport="untrusted-error-body"))
    assert decision.reason == Reason.TRANSPORT_FAILED
    assert "untrusted-error-body" not in repr(decision)
