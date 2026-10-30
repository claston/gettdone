from datetime import datetime, timedelta, timezone

from app.application.access_control import AccessControlService
from app.application.admin_dashboard_service import AdminDashboardService
from app.application.conversion_feedback import save_feedback


def test_feedback_is_idempotent_persistent_and_filters_by_offer_period_and_identity(tmp_path):
    clock = {"now": datetime(2026, 10, 29, 15, tzinfo=timezone.utc)}
    service = AccessControlService(state_file=tmp_path / "state.json", token_secret="test", now_provider=lambda: clock["now"])
    anonymous = service.resolve_identity(anonymous_fingerprint="browser-one", user_token=None)
    registered = service.register_user(name="Test", email="test@example.com", password="test-pass")
    user = service.resolve_identity(anonymous_fingerprint=None, user_token=registered.token)
    save_feedback(service, identity=anonymous, processing_id="old", answer=None)
    clock["now"] += timedelta(days=10)
    for conversion in ("yes", "no", "unanswered"):
        save_feedback(service, identity=anonymous, processing_id=conversion, answer=None)
    save_feedback(service, identity=anonymous, processing_id="yes", answer="yes")
    save_feedback(service, identity=anonymous, processing_id="yes", answer="yes")
    save_feedback(service, identity=anonymous, processing_id="yes", answer=None)
    save_feedback(service, identity=anonymous, processing_id="no", answer="no")
    save_feedback(service, identity=user, processing_id="user", answer="yes")
    result = AdminDashboardService(service).get_dashboard(days=7)["conversion_feedback"]
    assert result["offered_count"] == 4
    assert (result["yes_count"], result["no_count"], result["unanswered_count"]) == (2, 1, 1)
    assert (result["yes_people"], result["no_people"], result["unanswered_people"]) == (2, 1, 1)
    filtered = AdminDashboardService(service).get_dashboard(days=7, identity_type="registered")["conversion_feedback"]
    assert (filtered["yes_count"], filtered["no_count"], filtered["unanswered_count"]) == (1, 0, 0)
    service.close()
    reopened = AccessControlService(state_file=tmp_path / "state.json", token_secret="test", now_provider=lambda: clock["now"])
    assert save_feedback(reopened, identity=anonymous, processing_id="yes", answer=None)["answer"] == "yes"
    reopened.close()


def test_feedback_can_correct_answer_without_adding_a_vote(tmp_path):
    service = AccessControlService(state_file=tmp_path / "state.json", token_secret="test")
    identity = service.resolve_identity(anonymous_fingerprint="browser", user_token=None)
    save_feedback(service, identity=identity, processing_id="conversion", answer="no")
    save_feedback(service, identity=identity, processing_id="conversion", answer="yes")
    result = AdminDashboardService(service).get_dashboard()["conversion_feedback"]
    assert (result["offered_count"], result["yes_count"], result["no_count"]) == (1, 1, 0)
    save_feedback(service, identity=identity, processing_id="second-conversion", answer="yes")
    result = AdminDashboardService(service).get_dashboard()["conversion_feedback"]
    assert (result["yes_count"], result["yes_people"]) == (2, 1)
    service.close()
