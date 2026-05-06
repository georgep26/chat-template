"""Optional smoke test for deployed Lambda Function URL.

This test is skipped unless both env vars are set:
- RAG_FUNCTION_URL
- RAG_SMOKE_BEARER_TOKEN
"""

import json
import os
import uuid
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pytest

RAG_FUNCTION_URL = os.getenv("RAG_FUNCTION_URL")
RAG_SMOKE_BEARER_TOKEN = os.getenv("RAG_SMOKE_BEARER_TOKEN")


@pytest.mark.skipif(
    not RAG_FUNCTION_URL or not RAG_SMOKE_BEARER_TOKEN,
    reason="Set RAG_FUNCTION_URL and RAG_SMOKE_BEARER_TOKEN to run smoke test.",
)
def test_function_url_smoke():
    """Call deployed Function URL and assert a successful chat response."""
    payload = {
        "request_id": str(uuid.uuid4()),
        "message": "Reply with a short hello.",
    }

    request = Request(
        RAG_FUNCTION_URL,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {RAG_SMOKE_BEARER_TOKEN}",
        },
    )

    try:
        with urlopen(request, timeout=60) as response:
            body = json.loads(response.read().decode("utf-8"))
            status_code = response.status
    except HTTPError as exc:
        error_body = exc.read().decode("utf-8") if exc.fp else ""
        pytest.fail(f"Smoke test failed with HTTP {exc.code}: {error_body}")
    except URLError as exc:
        pytest.fail(f"Smoke test could not reach Function URL: {exc}")

    assert status_code == 200
    assert uuid.UUID(body["conversation_id"])
    assert isinstance(body.get("answer"), str)
    assert isinstance(body.get("steps"), list)
