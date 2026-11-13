import pytest

from app.routers.checkout import _is_valid_checkout_email


@pytest.mark.parametrize("email", [
    "cliente@example.com", "cliente+pedido@example.com.br", "cliente.nome@example.com",
    "a@b.co", "cliente!pedido@example.com",
])
def test_checkout_accepts_supported_email_addresses(email):
    assert _is_valid_checkout_email(email)


@pytest.mark.parametrize("email", [
    "", "cliente", "@example.com", "cliente@", "cliente@example", "cliente@.com", "cliente@example.",
    "cliente@@example.com", "cliente @example.com", "cliente@example.com\n", "cliente@exam\tple.com",
    "cliente\u2003@example.com", "!" * 320, "cliente@" + "!" * 312, "a" * 321 + "@example.com",
])
def test_checkout_rejects_incomplete_whitespace_and_repeated_character_inputs(email):
    assert not _is_valid_checkout_email(email)
