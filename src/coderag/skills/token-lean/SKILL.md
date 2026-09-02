---
name: token-lean
description: Work in a token-efficient way — retrieve code with CodeRAG's MCP tools instead of reading whole files, and keep responses lean. Use when the user asks to reduce token usage or cost, mentions being near a usage limit, asks for terse/concise output, or is working in a repo indexed with CodeRAG.
user-invocable: true
---

# Token-lean working mode

Two independent levers. Apply both.

## 1. Input: retrieve, don't read

If the `coderag` MCP server is connected (check the available tools; if it isn't,
skip this section and use normal file tools):

- **`coderag_search`** — to locate code: "where is X defined/handled?". Returns
  ranked symbols with file, line range, and why each matched.
- **`coderag_context`** — to understand behaviour: "why does X happen?". Returns
  the target symbol plus its dependencies, callers, and tests, under a token budget.
- **`coderag_symbol`** — to fetch one symbol's full source by qualified name.

Reach for these **before** Glob/Grep/Read. They return the relevant symbols
instead of whole files, which is typically 60–75% fewer tokens for the same code.

Fall back to reading files when:
- the retrieved context genuinely doesn't answer the question,
- you need a file's overall structure rather than specific symbols,
- the file isn't indexed (config, generated code, non-Python), or
- you're about to edit — read the exact region you will change.

If results reference code that has moved, re-index with `coderag_index`
(`incremental: true`) rather than falling back to reading everything.

## 2. Output: say it once

Output tokens are billed at roughly **5× input**, so this half usually matters
more than the first.

- **Lead with the outcome.** First sentence answers "what happened" / "what did
  you find". Detail after.
- **Don't restate the request** before answering it.
- **Don't echo code the user can already see** — a diff was applied, or the file
  is open. Reference `file.py:42`, don't reproduce the block.
- **Don't narrate routine tool calls.** No "Now I'll…", "Let me check…". Write
  between steps only for a finding, a change of direction, or a blocker.
- **No closing offer-list.** If the obvious next step follows from the request,
  do it. Otherwise stop — skip "Want me to also…?".
- **Batch independent tool calls** in one turn rather than serially.
- Tables and short lists over paragraphs for structured results.

Being readable matters more than being short: cut *content* the reader doesn't
need, not the words that make a sentence parseable. Don't compress into
fragments, arrow chains, or invented abbreviations.

## What this does not do

This is a set of instructions, not machinery. It cannot compress a prompt, place
cache breakpoints, or route to a cheaper model — those need a proxy or gateway.
Its effect is real but **not measured by CodeRAG**: CodeRAG only sees its own
retrieval, not the host agent's total token usage. Verify with your client's own
cost reporting before believing any number.
