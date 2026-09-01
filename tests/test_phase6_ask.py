"""Phase 6: run_ask pipeline + usage accounting (mock provider, no network)."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from coderag.db.models import LLMRequest, QueryRecord
from coderag.llm.base import LLMProvider, LLMResponse, Usage
from coderag.service import run_ask

pytestmark = pytest.mark.db


class MockProvider(LLMProvider):
    name = "mock"

    def __init__(self):
        self.last_prompt = None
        self._last_usage = Usage()

    def generate(self, request):
        self.last_prompt = request.prompt
        self._last_usage = Usage(
            input_tokens=len(request.prompt) // 4, output_tokens=7,
            model="mock-1", latency_ms=1.0, success=True,
        )
        return LLMResponse(text="ANSWER: invoice not reconciled after retries.",
                           usage=self._last_usage)


def test_run_ask_end_to_end(db_session, demo_repo):
    provider = MockProvider()
    repo, package, response, outcome = run_ask(
        db_session, "why can retry_payment leave an invoice pending?", "payments",
        provider=provider,
    )
    db_session.commit()

    # LLM received the built context prompt
    assert "TARGET SYMBOL" in provider.last_prompt
    assert provider.last_prompt == package.prompt_text
    assert response.text.startswith("ANSWER:")

    # query recorded as 'ask'
    q = db_session.scalar(select(QueryRecord).where(QueryRecord.mode == "ask"))
    assert q is not None
    # llm usage persisted and linked
    llm = db_session.scalar(select(LLMRequest).where(LLMRequest.query_id == q.id))
    assert llm is not None
    assert llm.provider == "mock" and llm.output_tokens == 7
    assert llm.model == "mock-1"


def test_run_ask_records_failure(db_session, demo_repo):
    class Boom(LLMProvider):
        name = "boom"

        def __init__(self):
            self._last_usage = Usage(model="boom", success=False, error="kaboom")

        def generate(self, request):
            raise RuntimeError("kaboom")

    with pytest.raises(RuntimeError):
        run_ask(db_session, "q", "payments", provider=Boom())
    db_session.commit()
    llm = db_session.scalar(select(LLMRequest).where(LLMRequest.provider == "boom"))
    assert llm is not None and llm.success is False
