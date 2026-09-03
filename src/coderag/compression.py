"""Cache-safe request compression for the proxy (opt-in, tool results only).

Design constraints, in order of importance:

1. **Deterministic.** The client never sees the compressed form — it resends the
   original history every turn and the proxy recompresses it. Prompt caching is
   a prefix match on *content*, so the same input must always produce the same
   output, or every turn would invalidate the provider's cache and cost more
   than it saves. Every transform here is a pure function; no timestamps,
   randomness, or counters appear in output.
2. **Tool results only.** System prompts, user text, assistant turns, and tool
   definitions are never touched. Tool output (logs, test runs, build noise) is
   where the redundancy lives and where a lossy-but-recoverable cut is safe.
3. **Recoverable.** Before any elision, the original text is stored locally,
   content-addressed. The agent can fetch it back with the ``coderag_expand``
   MCP tool named in the marker.
4. **Guarded.** If the body doesn't parse, nothing shrinks, or anything at all
   goes wrong, the original bytes are forwarded untouched.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

RECOVERY_DIR = Path.home() / ".coderag" / "proxy-cache"

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07")
_MIN_RUN = 3          # consecutive identical lines before dedupe kicks in
_MIN_BLOCK_SAVING = 64  # chars a block must shrink by to accept the rewrite

# Lines that must survive middle-elision: the whole point of a log is usually
# in these, and they are exactly what an agent needs to act without expanding.
_IMPORTANT_LINE_RE = re.compile(
    r"error|warn|fail|fatal|exception|traceback|panic|assert", re.IGNORECASE
)
_MAX_IMPORTANT_KEPT = 40  # cap so a pathological log can't defeat elision

# Diff-shaped content is exempt from elision: every changed line is signal.
_DIFF_RE = re.compile(r"^diff --git |^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@", re.MULTILINE)

# Long base64/hex runs (inline images, wheels, blobs) carry nothing an LLM
# can use at this size; elide recoverably.
_BASE64_RUN_RE = re.compile(r"[A-Za-z0-9+/]{2048,}={0,2}")


@dataclass
class CompressionStats:
    blocks_seen: int = 0
    blocks_compressed: int = 0
    chars_in: int = 0
    chars_out: int = 0

    @property
    def chars_saved(self) -> int:
        return self.chars_in - self.chars_out


# ---- pure text transforms (deterministic by construction) -------------------

def strip_ansi(text: str) -> str:
    """Remove ANSI colour/control sequences — zero information for an LLM."""
    return _ANSI_RE.sub("", text)


def dedupe_consecutive_lines(text: str) -> str:
    """Collapse runs of >= _MIN_RUN identical lines into one line + a count."""
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        j = i
        while j < len(lines) and lines[j] == lines[i]:
            j += 1
        run = j - i
        if run >= _MIN_RUN and lines[i].strip():
            out.append(lines[i])
            out.append(f"[coderag: previous line repeated {run - 1} more times]")
        else:
            out.extend(lines[i:j])
        i = j
    return "\n".join(out)


def squeeze_blank_lines(text: str) -> str:
    """3+ consecutive blank lines -> a single blank line."""
    return re.sub(r"\n[ \t]*\n[ \t]*\n(?:[ \t]*\n)*", "\n\n", text)


def _snap_to_line(text: str, pos: int, forward: bool) -> int:
    """Move a cut point to the nearest newline so we never split a line."""
    if forward:
        nl = text.find("\n", pos)
        return nl + 1 if nl != -1 else pos
    nl = text.rfind("\n", 0, pos)
    return nl + 1 if nl != -1 else pos


def store_original(text: str, directory: Path | None = None) -> str:
    """Content-addressed local store; returns the recovery key."""
    directory = directory or RECOVERY_DIR
    key = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{key}.txt"
    if not path.exists():  # content-addressed -> idempotent
        path.write_text(text)
    return key


def load_original(key: str, directory: Path | None = None) -> str | None:
    """Fetch a stored original. Key is validated — it is model-supplied input."""
    if not re.fullmatch(r"[0-9a-f]{8,64}", key or ""):
        return None
    path = (directory or RECOVERY_DIR) / f"{key}.txt"
    try:
        return path.read_text()
    except OSError:
        return None


def elide_middle(
    text: str, threshold: int, keep: int, directory: Path | None = None,
    original: str | None = None,
) -> str:
    """Keep head + tail of an oversized block; store the original for recovery.

    ``original`` is what gets stored — pass the raw pre-transform text so
    recovery returns exactly what the tool produced, not our cleaned version.
    """
    if len(text) <= threshold:
        return text
    key = store_original(original if original is not None else text, directory)
    head_len = _snap_to_line(text, (keep * 2) // 3, forward=False)
    tail_start = _snap_to_line(text, len(text) - keep // 3, forward=True)
    elided = tail_start - head_len

    # Errors/warnings in the elided middle survive, in original order.
    important = [
        line for line in text[head_len:tail_start].split("\n")
        if _IMPORTANT_LINE_RE.search(line)
    ][:_MAX_IMPORTANT_KEPT]
    kept_note = f"; kept {len(important)} error/warning lines" if important else ""
    marker = (
        f"\n[coderag: elided {elided} chars of tool output{kept_note}; retrieve "
        f'the full original with the MCP tool coderag_expand("{key}")]\n'
    )
    middle = ("\n".join(important) + "\n") if important else ""
    return text[:head_len] + marker + middle + text[tail_start:]


def elide_base64_runs(text: str, directory: Path | None = None) -> str:
    """Replace huge base64/hex runs with a recoverable marker."""
    def repl(m: re.Match[str]) -> str:
        key = store_original(m.group(0), directory)
        return (f'[coderag: elided {len(m.group(0))} chars of encoded data; '
                f'coderag_expand("{key}")]')

    return _BASE64_RUN_RE.sub(repl, text)


# ---- JSON content-shape compressor ------------------------------------------

_ERRORISH_KEY_RE = re.compile(
    r"error|errors|warning|warnings|message|failure|failed|traceback|stderr|"
    r"status|detail", re.IGNORECASE
)
_JSON_MAX_STRING = 400   # chars a leaf string may keep outside error subtrees
_JSON_ARRAY_HEAD = 5     # array items kept from the front
_JSON_ARRAY_TAIL = 2     # array items kept from the back


def _shrink_json(node: object, protected: bool) -> object:
    """Structure-preserving shrink: keys and error subtrees stay intact."""
    if isinstance(node, dict):
        return {
            k: _shrink_json(
                v, protected or bool(_ERRORISH_KEY_RE.search(str(k))))
            for k, v in node.items()
        }
    if isinstance(node, list):
        limit = _JSON_ARRAY_HEAD + _JSON_ARRAY_TAIL
        if not protected and len(node) > limit + 1:
            elided = len(node) - limit
            return (
                [_shrink_json(v, protected) for v in node[:_JSON_ARRAY_HEAD]]
                + [f"[coderag: {elided} of {len(node)} items elided]"]
                + [_shrink_json(v, protected) for v in node[-_JSON_ARRAY_TAIL:]]
            )
        return [_shrink_json(v, protected) for v in node]
    if isinstance(node, str) and not protected and len(node) > _JSON_MAX_STRING:
        return (node[:_JSON_MAX_STRING]
                + f"…[coderag: {len(node) - _JSON_MAX_STRING} chars elided]")
    return node


def compress_json_text(
    text: str, threshold: int, directory: Path | None = None
) -> str | None:
    """Shrink an oversized JSON tool result, keeping structure and errors.

    Object keys always survive; subtrees under error-ish keys are never
    shrunk; long arrays keep their edges; long strings are truncated. The
    raw original stays recoverable. Returns None when the text isn't JSON.
    """
    stripped = text.strip()
    if not stripped or stripped[0] not in "{[":
        return None
    try:
        data = json.loads(stripped)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(data, (dict, list)) or len(text) <= threshold:
        return None
    key = store_original(text, directory)
    shrunk = json.dumps(_shrink_json(data, protected=False),
                        ensure_ascii=False, separators=(",", ":"))
    return (shrunk + f'\n[coderag: JSON compressed; full original via '
            f'coderag_expand("{key}")]')


def compress_text(
    text: str, threshold: int, keep: int, directory: Path | None = None
) -> str:
    if _DIFF_RE.search(text):
        # Diffs are dense signal: every changed line matters. Only strip ANSI.
        return strip_ansi(text)
    as_json = compress_json_text(text, threshold, directory)
    if as_json is not None:
        return as_json
    cleaned = squeeze_blank_lines(dedupe_consecutive_lines(strip_ansi(text)))
    cleaned = elide_base64_runs(cleaned, directory)
    return elide_middle(cleaned, threshold, keep, directory, original=text)


def tool_schema_chars_in_body(raw: bytes) -> int:
    """Characters of tool-definition JSON in a request body (count only)."""
    try:
        data = json.loads(raw)
        tools = data.get("tools")
        if not isinstance(tools, list) or not tools:
            return 0
        return len(json.dumps(tools, ensure_ascii=False))
    except Exception:  # noqa: BLE001 - observability only
        return 0


# ---- request-body rewriting -------------------------------------------------

def compress_messages_body(
    raw: bytes, threshold: int, keep: int, directory: Path | None = None
) -> tuple[bytes, CompressionStats] | None:
    """Rewrite tool_result text blocks in a Messages API request body.

    Returns (new_bytes, stats), or None when the original must be forwarded
    untouched (parse failure, nothing to do, or no worthwhile saving).
    """
    stats = CompressionStats()
    try:
        data = json.loads(raw)
        messages = data.get("messages")
        if not isinstance(messages, list):
            return None

        changed = False
        for message in messages:
            if not isinstance(message, dict) or message.get("role") != "user":
                continue
            content = message.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "tool_result":
                    continue
                inner = block.get("content")
                if isinstance(inner, str):
                    new = _maybe(inner, threshold, keep, directory, stats)
                    if new is not None:
                        block["content"] = new
                        changed = True
                elif isinstance(inner, list):
                    for sub in inner:
                        if isinstance(sub, dict) and sub.get("type") == "text" \
                                and isinstance(sub.get("text"), str):
                            new = _maybe(sub["text"], threshold, keep, directory, stats)
                            if new is not None:
                                sub["text"] = new
                                changed = True
        if not changed:
            return None
        new_raw = json.dumps(data, ensure_ascii=False,
                             separators=(",", ":")).encode("utf-8")
        return new_raw, stats
    except Exception:  # noqa: BLE001 - any failure means: forward untouched
        return None


def _maybe(
    text: str, threshold: int, keep: int, directory: Path | None,
    stats: CompressionStats,
) -> str | None:
    """Compress one text value; return None unless it shrinks meaningfully."""
    stats.blocks_seen += 1
    new = compress_text(text, threshold, keep, directory)
    if len(new) + _MIN_BLOCK_SAVING >= len(text):
        return None
    stats.blocks_compressed += 1
    stats.chars_in += len(text)
    stats.chars_out += len(new)
    return new


def tool_result_chars_in_body(raw: bytes) -> int:
    """Characters of tool_result content in a Messages request body (count only)."""
    try:
        data = json.loads(raw)
        total = 0
        for message in data.get("messages", []):
            if not isinstance(message, dict) or message.get("role") != "user":
                continue
            content = message.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "tool_result":
                    continue
                inner = block.get("content")
                if isinstance(inner, str):
                    total += len(inner)
                elif isinstance(inner, list):
                    for sub in inner:
                        if isinstance(sub, dict) and isinstance(sub.get("text"), str):
                            total += len(sub["text"])
        return total
    except Exception:  # noqa: BLE001 - observability only
        return 0
