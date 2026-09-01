"""Unit tests for secret detection and redaction (no DB)."""

from __future__ import annotations

from coderag.security.secrets import is_secret_file, redact


def test_is_secret_file():
    assert is_secret_file(".env")
    assert is_secret_file("config/.env.production")
    assert is_secret_file("keys/server.pem")
    assert is_secret_file("id_rsa")
    assert not is_secret_file("payments/payment_service.py")
    assert not is_secret_file("README.md")


def test_redacts_quoted_secrets():
    src = 'API_KEY = "AKIAIOSFODNN7EXAMPLE"\npassword = "hunter2wordlongenough"\n'
    r = redact(src)
    assert r.count >= 2
    assert "AKIAIOSFODNN7EXAMPLE" not in r.text
    assert "hunter2wordlongenough" not in r.text


def test_redacts_private_key_block():
    src = "-----BEGIN RSA PRIVATE KEY-----\nabc\ndef\n-----END RSA PRIVATE KEY-----\n"
    r = redact(src)
    assert r.count == 1
    assert "abc" not in r.text


def test_no_false_positive_across_lines():
    # regression: `if user.api_key:` must not link to the next line's identifier
    src = (
        "if user.api_key:\n"
        "    self._by_key[user.api_key] = user\n"
    )
    r = redact(src)
    assert r.count == 0
    assert "self._by_key" in r.text


def test_no_false_positive_type_annotation():
    src = "def f(self, api_key: str) -> None:\n    return None\n"
    assert redact(src).count == 0
