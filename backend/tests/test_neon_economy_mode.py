from app import dependencies


def test_economy_mode_applies_low_connection_pool_and_long_public_cache(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeAccessControlService:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

    monkeypatch.setenv("NEON_ECONOMY_MODE", "true")
    monkeypatch.setenv("PUBLIC_PLANS_CACHE_TTL_SECONDS", "300")
    monkeypatch.setattr(dependencies, "AccessControlService", FakeAccessControlService)
    monkeypatch.setattr(dependencies, "_access_control_service", None)

    service = dependencies.get_access_control_service()

    assert isinstance(service, FakeAccessControlService)
    assert captured["db_pool_min_size"] == 0
    assert captured["db_pool_max_size"] == 1
    assert captured["db_pool_max_idle_seconds"] == 60.0
    assert captured["public_plans_cache_ttl_seconds"] == 86400
