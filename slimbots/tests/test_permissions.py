from slimbots.permissions import ADMINISTRATOR, MANAGE_SERVER, PermissionResolver
from slimbots.testing import FakeClient


def _roles_client():
    client = FakeClient()
    client.respond(
        "GET",
        "/roles",
        [
            {"id": "everyone", "name": "@everyone", "permissions": 0, "is_everyone": True},
            {"id": "mod", "name": "moderator", "permissions": MANAGE_SERVER, "is_everyone": False},
            {"id": "admin", "name": "admin", "permissions": ADMINISTRATOR, "is_everyone": False},
        ],
    )
    return client


def test_base_permissions_is_everyone_alone_for_a_plain_member():
    client = _roles_client()
    client.respond("GET", "/users/u1", {"id": "u1", "role_ids": []})
    resolver = PermissionResolver(client)
    assert resolver.base_permissions("u1") == 0


def test_base_permissions_unions_held_roles():
    client = _roles_client()
    client.respond("GET", "/users/u1", {"id": "u1", "role_ids": ["mod"]})
    resolver = PermissionResolver(client)
    assert resolver.base_permissions("u1") == MANAGE_SERVER


def test_has_permission_true_when_the_bit_is_held():
    client = _roles_client()
    client.respond("GET", "/users/u1", {"id": "u1", "role_ids": ["mod"]})
    resolver = PermissionResolver(client)
    assert resolver.has_permission("u1", MANAGE_SERVER) is True


def test_has_permission_false_without_the_bit():
    client = _roles_client()
    client.respond("GET", "/users/u1", {"id": "u1", "role_ids": []})
    resolver = PermissionResolver(client)
    assert resolver.has_permission("u1", MANAGE_SERVER) is False


def test_administrator_bypasses_every_check():
    client = _roles_client()
    client.respond("GET", "/users/u1", {"id": "u1", "role_ids": ["admin"]})
    resolver = PermissionResolver(client)
    assert resolver.has_permission("u1", MANAGE_SERVER) is True


def test_roles_are_cached_within_the_window():
    client = _roles_client()
    client.respond("GET", "/users/u1", {"id": "u1", "role_ids": []})
    resolver = PermissionResolver(client, cache_seconds=60)
    resolver.base_permissions("u1")
    resolver.base_permissions("u1")
    role_calls = [c for c in client.calls if c[1] == "/roles"]
    assert len(role_calls) == 1


def test_roles_refresh_after_the_cache_expires():
    client = _roles_client()
    client.respond("GET", "/users/u1", {"id": "u1", "role_ids": []})
    resolver = PermissionResolver(client, cache_seconds=0)
    resolver.base_permissions("u1")
    resolver.base_permissions("u1")
    role_calls = [c for c in client.calls if c[1] == "/roles"]
    assert len(role_calls) == 2
