"""API access inventory: an explicit review gate, NOT an authorization test.

The local MVP currently has no server-side authorization. These tests ensure
new API routes cannot silently escape the access-design inventory. They do not
claim that any role restriction is implemented.
"""
from fastapi.routing import APIRoute

from app import app


# Proposed future authorization policy; none of these restrictions is active.
# 'engineer' includes authorized administrators; 'viewer' is read-only.
EXPECTED_API_ACCESS = {
    ("GET", "/api/config"): "viewer",
    ("PUT", "/api/config"): "admin",
    ("POST", "/api/check"): "engineer",
    ("GET", "/api/runs"): "viewer",
    ("GET", "/api/runs/{run_id}"): "viewer",
    ("GET", "/api/runs/{run_id}/compare/{previous_run_id}"): "viewer",
    ("GET", "/api/runs/{run_id}/checklist-compare/{previous_run_id}"): "viewer",
    ("GET", "/api/runs/{run_id}/checklist-compare/{previous_run_id}/csv"): "viewer",
    ("GET", "/api/runs/{run_id}/checklist-compare/{previous_run_id}/xlsx"): "viewer",
    ("GET", "/api/runs/{run_id}/csv"): "viewer",
    ("PATCH", "/api/runs/{run_id}/findings/{finding_id}"): "engineer",
    ("GET", "/api/runs/{run_id}/findings/{finding_id}/history"): "viewer",
    ("GET", "/api/runs/{run_id}/xlsx"): "viewer",
}


def test_every_api_route_has_explicit_future_access_classification():
    actual = {
        (method, route.path)
        for route in app.routes
        if isinstance(route, APIRoute) and route.path.startswith("/api/")
        for method in route.methods
    }
    assert actual == set(EXPECTED_API_ACCESS), (
        "An API route was added, removed, or changed. Review its future role, "
        "object ownership, and denial tests; then update this inventory."
    )


def test_all_inventory_roles_are_known():
    assert set(EXPECTED_API_ACCESS.values()) <= {"viewer", "engineer", "admin"}
    assert ("PUT", "/api/config") in EXPECTED_API_ACCESS
    assert EXPECTED_API_ACCESS[("PUT", "/api/config")] == "admin"
    assert EXPECTED_API_ACCESS[("PATCH", "/api/runs/{run_id}/findings/{finding_id}")] == "engineer"
