"""Issue #21: pure, non-production model; contains no credentials or I/O.

Evidence flags stand for trusted checks NOT implemented here. These functions
neither authenticate users nor verify JWTs, RLS, membership or real storage.
Atomic transitions model a future serialized, durable coordinator; they do not
provide a process lock, vault, SDK, network client or production implementation.
"""
from dataclasses import dataclass, replace
from enum import Enum


class Health(str, Enum):
    ABSENT = "absent"
    READY = "ready"
    IN_DOUBT = "in_doubt"
    REVOKED = "revoked"


@dataclass(frozen=True)
class Binding:
    """Abstract identifiers only; no actual user/session identifiers in fixtures."""

    project: str
    user: str
    session: str
    device: str


@dataclass(frozen=True)
class Attempt:
    binding: Binding
    epoch: int
    generation: int
    sequence: int


@dataclass(frozen=True)
class Session:
    binding: Binding | None = None
    epoch: int = 0
    generation: int = 0
    sequence: int = 0
    pending: Attempt | None = None
    health: Health = Health.ABSENT


class RenewalResult(str, Enum):
    # Includes verified same identity AND atomic durable storage of rotated pair.
    COMMITTED = "committed"
    REVOKED = "revoked"
    UNCERTAIN = "uncertain"


def record_verified_reauthorization(state: Session, binding: Binding) -> Session:
    """Model an EXTERNALLY verified interactive login, never a retry of refresh.

    New epoch fences all old requests, including login by the same principal.
    Production must allocate a durable, non-reused epoch across restarts.
    """
    return Session(binding=binding, epoch=state.epoch + 1, health=Health.READY)


def revoke(state: Session) -> Session:
    return replace(state, epoch=state.epoch + 1, pending=None, health=Health.REVOKED)


def begin_refresh(
    state: Session, *, binding: Binding, epoch: int, generation: int
) -> tuple[Session, Attempt | None]:
    """One atomic compare-and-set in the abstract coordinator, no token reuse."""
    if (state.health != Health.READY or state.pending is not None
            or state.binding != binding or state.epoch != epoch
            or state.generation != generation):
        return state, None
    attempt = Attempt(binding, epoch, generation, state.sequence + 1)
    return replace(state, pending=attempt, sequence=attempt.sequence), attempt


def finish_refresh(state: Session, attempt: Attempt, result: RenewalResult) -> Session:
    """Ignore delayed results; every unproven completion quarantines the session.

    UNCERTAIN includes timeout, lost response, crash recovery and vault failure.
    Even a previously unexpired access credential cannot bypass quarantine.
    """
    if (state.pending != attempt or state.binding != attempt.binding
            or state.epoch != attempt.epoch or state.generation != attempt.generation):
        return state
    if result == RenewalResult.COMMITTED:
        return replace(state, pending=None, generation=state.generation + 1)
    if result == RenewalResult.REVOKED:
        return revoke(state)
    return replace(state, pending=None, health=Health.IN_DOUBT)


class Transport(str, Enum):
    OK = "ok"
    TIMEOUT = "timeout"
    REDIRECT = "redirect"
    DENIED = "denied"
    FAILED = "failed"


class Reason(str, Enum):
    NOT_CONFIGURED = "source_not_configured"
    CONFIG_INVALID = "configuration_invalid"
    REAUTH_REQUIRED = "reauth_required"
    REFRESH_IN_PROGRESS = "refresh_in_progress"
    REFRESH_UNCERTAIN = "refresh_uncertain"
    SESSION_REVOKED = "session_revoked"
    IDENTITY_UNVERIFIED = "identity_unverified"
    ACCESS_EXPIRED = "access_expired"
    SESSION_UNVERIFIED = "session_unverified"
    MEMBERSHIP_UNVERIFIED = "membership_unverified"
    PERMISSION_DENIED = "permission_denied"
    TIMEOUT = "network_timeout"
    REDIRECT = "redirect_rejected"
    TRANSPORT_FAILED = "transport_failed"
    STALE_RESPONSE = "stale_response_rejected"
    INCOMPLETE = "response_incomplete"
    VERIFIED = "fresh_snapshot_verified"


@dataclass(frozen=True)
class Observation:
    """Trusted evidence is an assumption, not something booleans can establish."""

    configured: bool = False
    configuration_valid: bool = False
    identity_verified: bool = False
    access_current: bool = False
    session_current: bool = False
    membership_current: bool = False
    permission_current: bool = False
    transport: Transport = Transport.FAILED
    request_id: int = 0
    response_request_id: int = 0
    request_epoch: int = 0
    request_generation: int = 0
    response_binding: Binding | None = None
    snapshot_valid_and_complete: bool = False


@dataclass(frozen=True)
class Diagnostic:
    status: str
    reason: Reason


def assess(state: Session, evidence: Observation) -> Diagnostic:
    """Allowlist-only diagnostics; no raw exceptions, headers, bodies or secrets.

    A previous snapshot is deliberately not an input. This model does not change
    existing runtime status/response schemas; it proposes future reason codes.
    """
    if not evidence.configured:
        return Diagnostic("not_configured", Reason.NOT_CONFIGURED)
    failures = (
        (not evidence.configuration_valid, Reason.CONFIG_INVALID),
        (state.health == Health.REVOKED, Reason.SESSION_REVOKED),
        (state.health == Health.IN_DOUBT, Reason.REFRESH_UNCERTAIN),
        (state.health != Health.READY or state.binding is None, Reason.REAUTH_REQUIRED),
        (state.pending is not None, Reason.REFRESH_IN_PROGRESS),
        (not evidence.identity_verified or evidence.response_binding != state.binding,
         Reason.IDENTITY_UNVERIFIED),
        (not evidence.access_current, Reason.ACCESS_EXPIRED),
        (not evidence.session_current, Reason.SESSION_UNVERIFIED),
        (not evidence.membership_current, Reason.MEMBERSHIP_UNVERIFIED),
        (not evidence.permission_current, Reason.PERMISSION_DENIED),
        (evidence.transport == Transport.DENIED, Reason.PERMISSION_DENIED),
        (evidence.transport == Transport.TIMEOUT, Reason.TIMEOUT),
        (evidence.transport == Transport.REDIRECT, Reason.REDIRECT),
        (evidence.transport != Transport.OK, Reason.TRANSPORT_FAILED),
        (evidence.request_id <= 0 or evidence.request_id != evidence.response_request_id
         or evidence.request_epoch != state.epoch
         or evidence.request_generation != state.generation, Reason.STALE_RESPONSE),
        (not evidence.snapshot_valid_and_complete, Reason.INCOMPLETE),
    )
    for failed, reason in failures:
        if failed:
            return Diagnostic("unavailable", reason)
    return Diagnostic("verified", Reason.VERIFIED)
