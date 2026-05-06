from app.application.access_control import AccessControlService
from scripts.set_user_admin_role import main


def test_script_grants_admin_role_for_existing_user(tmp_path, capsys) -> None:
    service = AccessControlService(
        state_file=tmp_path / "access-control-state.json",
        token_secret="test-secret",
    )
    user = service.register_user(
        name="Admin Candidate",
        email="admin@ofxsimples.com.br",
        password="test-pass",
    )
    assert service.is_user_admin(user_id=user.user_id) is False

    exit_code = main(
        ["--email", "admin@ofxsimples.com.br"],
        service=service,
    )

    assert exit_code == 0
    assert service.is_user_admin(user_id=user.user_id) is True
    payload = capsys.readouterr().out
    assert '"status": "ok"' in payload
    assert '"is_admin": true' in payload


def test_script_returns_error_when_user_is_missing(tmp_path, capsys) -> None:
    service = AccessControlService(
        state_file=tmp_path / "access-control-state.json",
        token_secret="test-secret",
    )

    exit_code = main(
        ["--email", "missing@ofxsimples.com.br"],
        service=service,
    )

    assert exit_code == 1
    assert "User not found" in capsys.readouterr().err
