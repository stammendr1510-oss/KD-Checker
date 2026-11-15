"""Pure, fail-closed access policy prototype for future multi-user KD Checker.

Not integrated into FastAPI. A caller MUST authenticate the user, validate
session freshness, and obtain tenant and object identities from trusted storage.
Never construct Principal from client-provided role or organization fields.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Role(str, Enum):
    VIEWER = "viewer"
    ENGINEER = "engineer"
    ADMIN = "admin"


class Action(str, Enum):
    READ = "read"
    CHECK = "check"
    EDIT_FINDING = "edit_finding"
    MANAGE_CONFIG = "manage_config"


@dataclass(frozen=True)
class Principal:
    """Identities below are assumed verified by a future auth boundary."""

    subject: str
    organization_id: str
    role: Role
    session_verified: bool


@dataclass(frozen=True)
class Resource:
    """Identity must come from a trusted database record, never request input."""

    organization_id: str


_ALLOWED: dict[Action, frozenset[Role]] = {
    Action.READ: frozenset(Role),
    Action.CHECK: frozenset((Role.ENGINEER, Role.ADMIN)),
    Action.EDIT_FINDING: frozenset((Role.ENGINEER, Role.ADMIN)),
    Action.MANAGE_CONFIG: frozenset((Role.ADMIN,)),
}


def is_allowed(
    principal: Principal | None,
    action: Action,
    resource: Resource | None,
) -> bool:
    """Deny unless verified session, known action/role and matching tenant.

    No network, storage, secret or side effects. No implicit owner override.
    An unknown action, malformed principal, or missing resource fails closed.
    """
    if not isinstance(principal, Principal) or not isinstance(resource, Resource):
        return False
    if not isinstance(action, Action) or not isinstance(principal.role, Role):
        return False
    if principal.session_verified is not True:
        return False
    if not isinstance(principal.subject, str) or not principal.subject.strip():
        return False
    if not isinstance(principal.organization_id, str) or not principal.organization_id.strip():
        return False
    if not isinstance(resource.organization_id, str) or not resource.organization_id.strip():
        return False
    if principal.organization_id != resource.organization_id:
        return False
    return principal.role in _ALLOWED.get(action, frozenset())
