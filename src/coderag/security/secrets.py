"""Secret detection and redaction.

Two jobs:
  * decide whether a *file* is likely to contain secrets and must not be indexed
    (``is_secret_file``), and
  * redact secret-shaped *content* that slips into text we do keep (``redact``).

Conservative by design: better to skip/redact a false positive than to leak a
credential into the index or a prompt.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass

# Exact filenames / glob patterns that should never be indexed.
SECRET_FILE_PATTERNS: tuple[str, ...] = (
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "*.keystore",
    "*.jks",
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "*.crt",
    "*.cer",
    "*.der",
    "credentials",
    "credentials.*",
    ".npmrc",
    ".pypirc",
    ".netrc",
    "*.secret",
    "*secrets*.y*ml",
    "*.pkcs8",
)

# Content patterns for redaction. Each is (label, compiled regex).
_SECRET_CONTENT_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    (
        "private_key_block",
        re.compile(
            r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----"
            r".*?-----END (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----",
            re.DOTALL,
        ),
    ),
    ("slack_token", re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[0-9A-Za-z]{20,}\b")),
    (
        "assigned_secret",
        re.compile(
            r"""(?ix)
            \b(?:api[_-]?key|secret|password|passwd|token|access[_-]?key)\b
            [ \t]*[:=][ \t]*          # key/sep/value must stay on one line
            ['"]([A-Za-z0-9_\-./+]{12,})['"]   # value must be quoted
            """
        ),
    ),
]

REDACTION = "«REDACTED-SECRET»"


def is_secret_file(path: str) -> bool:
    """True if the file name/path matches a known secret pattern."""
    name = path.replace("\\", "/").rsplit("/", 1)[-1].lower()
    return any(fnmatch.fnmatch(name, pat.lower()) for pat in SECRET_FILE_PATTERNS)


@dataclass
class RedactionResult:
    text: str
    count: int
    labels: list[str]


def redact(text: str) -> RedactionResult:
    """Replace secret-shaped substrings with a placeholder.

    For ``assigned_secret`` we redact only the captured value, keeping the key
    name so the code remains readable.
    """
    labels: list[str] = []
    count = 0
    out = text
    for label, pattern in _SECRET_CONTENT_PATTERNS:
        if label == "assigned_secret":

            def _sub(m: re.Match[str]) -> str:
                nonlocal count
                count += 1
                return m.group(0).replace(m.group(1), REDACTION)

            new = pattern.sub(_sub, out)
        else:
            new, n = pattern.subn(REDACTION, out)
            count += n
        if new != out:
            labels.append(label)
        out = new
    return RedactionResult(text=out, count=count, labels=labels)
