"""Synthetic access-policy contract tests; no live sessions or database."""
import pytest

from design.access_policy import Action, Principal, Resource, Role, is_allowed


@pytest.mark.parametrize("role,action,expected", [
    (Role.VIEWER, Action.READ, True),
    (Role.VIEWER, Action.CHECK, False),
    (Role.VIEWER, Action.EDIT_FINDING, False),
    (Role.VIEWER, Action.MANAGE_CONFIG, False),
    (Role.ENGINEER, Action.READ, True),
    (Role.ENGINEER, Action.CHECK, True),
    (Role.ENGINEER, Action.EDIT_FINDING, True),
    (Role.ENGINEER, Action.MANAGE_CONFIG, False),
    (Role.ADMIN, Action.READ, True),
    (Role.ADMIN, Action.CHECK, True),
    (Role.ADMIN, Action.EDIT_FINDING, True),
    (Role.ADMIN, Action.MANAGE_CONFIG, True),
])
def test_explicit_matrix(role, action, expected):
    principal = Principal("user-1", "org-A", role, True)
    assert is_allowed(principal, action, Resource("org-A")) is expected


@pytest.mark.parametrize("principal,action,resource", [
    (None, Action.READ, Resource("org-A")),
    (Principal("u", "org-A", Role.ADMIN, False), Action.READ, Resource("org-A")),
    (Principal("u", "org-A", Role.ADMIN, True), Action.READ, Resource("org-B")),
    (Principal("u", "org-A", Role.ADMIN, True), Action.READ, None),
    (Principal("", "org-A", Role.ADMIN, True), Action.READ, Resource("org-A")),
    (Principal("u", "", Role.ADMIN, True), Action.READ, Resource("")),
    (Principal("u", "org-A", Role.ADMIN, True), Action.READ, Resource("")),
    (Principal("u", "org-A", "admin", True), Action.READ, Resource("org-A")),
    (Principal("u", "org-A", Role.ADMIN, True), "read", Resource("org-A")),
    (Principal("u", "org-A", Role.ADMIN, True), "delete_everything", Resource("org-A")),
    (Principal("u", "org-A", Role.ADMIN, 1), Action.READ, Resource("org-A")),
])
def test_denies_missing_unverified_or_unrecognized_inputs(principal, action, resource):
    assert is_allowed(principal, action, resource) is False


def test_cross_tenant_denied_even_to_administrator():
    user = Principal("administrator", "tenant-1", Role.ADMIN, True)
    for action in Action:
        assert not is_allowed(user, action, Resource("tenant-2"))


def test_no_client_identity_override_is_part_of_policy_signature():
    # Pure decision requires an externally verified Principal and stored Resource.
    # HTTP session binding and database owner lookup must be implemented separately.
    principal = Principal("engineer", "company-1", Role.ENGINEER, True)
    assert is_allowed(principal, Action.EDIT_FINDING, Resource("company-1"))
    assert not is_allowed(principal, Action.EDIT_FINDING, Resource("company-2"))
