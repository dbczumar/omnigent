r"""E2E: AskUserQuestion renders as a web form (mock, no real Claude Code).

A background thread POSTs directly to the server's
``POST /v1/sessions/{session_id}/hooks/permission-request`` endpoint with an
``AskUserQuestion`` payload. The server parks the elicitation (long-poll), the
SPA renders it as an ``AskUserQuestionForm`` inside an ``ApprovalCard`` — radio
inputs for a single-select question and a Submit button. The test selects an
answer and submits it, confirming the parked HTTP call drains and the verdict
flowed back through the PermissionRequest round-trip.

This approach replaces the original native Claude Code session (real LLM turn)
with a seeded session and a synthetic hook POST — no real Claude Code needed,
test completes in seconds rather than minutes.

This is the structured-form counterpart to the binary approval card
(``test_approval_card.py``): the binary card covers a policy ASK, this covers
Claude's own question tool. It is the sibling of ``test_exit_plan_mode.py``:
both cover a Claude built-in tool that surfaces a structured card rather than
the binary policy ASK.

``test_answered_question_stays_outside_the_worked_fold`` covers the second
half of the story: where the ANSWERED card lands in the transcript once the
turn moves on.
"""

from __future__ import annotations

import logging
import threading
import time

import httpx
import pytest
from playwright.sync_api import Page, expect

_log = logging.getLogger(__name__)

_APPROVAL_CARD = '[data-testid="approval-card"]'
_FORM = '[data-testid="ask-user-question-form"]'
_SUBMIT = '[data-testid="ask-user-question-submit"]'
_WORKED_FOLD = '[data-testid="turn-worked-fold"]'
_COMPOSER = "Ask the agent anything…"

_MOCK_ELICITATION_TIMEOUT_MS = 15_000
# A mock-LLM turn is fast, but this one is paused mid-flight on the gate
# and only settles after the answer + release round-trip.
_TURN_TIMEOUT_MS = 60_000

# The exact option labels used in the hook payload and form assertions.
_OPTION_ONE = "Alpha"
_OPTION_TWO = "Bravo"


def _pending_elicitations(base_url: str, session_id: str) -> list[dict]:
    """Return the session snapshot's pending elicitation events (owner view)."""
    resp = httpx.get(f"{base_url}/v1/sessions/{session_id}", timeout=10.0)
    resp.raise_for_status()
    return resp.json().get("pending_elicitations") or []


def _wait_for(predicate, *, timeout_s: float = 30.0, interval_s: float = 0.5) -> None:
    """Poll *predicate* until truthy or the deadline passes."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(interval_s)
    raise AssertionError("condition not met within timeout")


def _post_ask_user_question(base_url: str, session_id: str, holder: dict) -> threading.Thread:
    """Park an ``AskUserQuestion`` permission request on *session_id*.

    The call blocks server-side until the web verdict lands, so it runs on
    its own thread; join it after answering the card.

    :param base_url: Server base URL.
    :param session_id: Session to raise the prompt on.
    :param holder: Dict the thread writes ``response`` / ``error`` into.
    :returns: The started thread.
    """

    def _post() -> None:
        try:
            resp = httpx.post(
                f"{base_url}/v1/sessions/{session_id}/hooks/permission-request",
                json={
                    "tool_name": "AskUserQuestion",
                    "tool_input": {
                        "questions": [
                            {
                                "question": "Which option do you prefer?",
                                "options": [_OPTION_ONE, _OPTION_TWO],
                            }
                        ]
                    },
                },
                timeout=60.0,
            )
            resp.raise_for_status()
            holder["response"] = resp.json()
        except Exception as exc:
            holder["error"] = exc

    thread = threading.Thread(target=_post, daemon=True)
    thread.start()
    return thread


@pytest.mark.timeout(90)
def test_ask_user_question_form_renders_and_submits(
    page: Page,
    seeded_session: tuple[str, str],
) -> None:
    """Mock AskUserQuestion permission-request → web form renders → answer → prompt drains."""
    base_url, session_id = seeded_session
    _log.info("seeded session ready: base_url=%s session_id=%s", base_url, session_id)

    result_holder: dict = {}
    hook_thread = _post_ask_user_question(base_url, session_id, result_holder)

    # Let the server park the elicitation before the SPA tries to render it.
    page.wait_for_timeout(500)

    page.goto(f"{base_url}/c/{session_id}")

    card = (
        page.locator(f'{_APPROVAL_CARD}[data-state="pending"]')
        .filter(has=page.locator(_FORM))
        .first
    )
    expect(card).to_be_visible(timeout=_MOCK_ELICITATION_TIMEOUT_MS)
    form = card.locator(_FORM)
    expect(form).to_be_visible(timeout=_MOCK_ELICITATION_TIMEOUT_MS)
    expect(form.get_by_text(_OPTION_ONE, exact=True)).to_be_visible()
    expect(form.get_by_text(_OPTION_TWO, exact=True)).to_be_visible()
    assert _pending_elicitations(base_url, session_id), "server has no parked elicitation"

    form.get_by_role("radio", name=_OPTION_ONE).check()
    form.locator(_SUBMIT).click()

    responded = page.locator(f'{_APPROVAL_CARD}[data-state="responded"]').first
    expect(responded).to_be_visible(timeout=_MOCK_ELICITATION_TIMEOUT_MS)

    hook_thread.join(timeout=30)
    if "error" in result_holder:
        raise AssertionError(f"hook thread failed: {result_holder['error']}") from result_holder[
            "error"
        ]

    _wait_for(lambda: not _pending_elicitations(base_url, session_id))


@pytest.mark.timeout(180)
def test_answered_question_stays_outside_the_worked_fold(
    page: Page,
    paused_mid_turn_session: tuple[str, str, str],
) -> None:
    """An answered question card is a turn boundary, not a step of the work.

    The turn pauses between its two tool calls (mock-LLM gate), an
    AskUserQuestion prompt is raised into that window, and the test answers
    it before releasing the turn to finish. The answer is the USER's
    contribution, so the settled transcript must read:

        [Worked for … ]   ← the work before the question
        [answered card]   ← visible, outside any fold
        [Worked for … ]   ← the work after the answer
        Workspace inspected.

    The card used to be grouped with the turn that asked, which folded it
    into the single "Worked for" row — hiding what the user answered behind
    a disclosure triangle, under a label claiming the agent did it.
    """
    base_url, session_id, mock_url = paused_mid_turn_session

    page.goto(f"{base_url}/c/{session_id}")
    composer = page.get_by_placeholder(_COMPOSER)
    expect(composer).to_be_visible(timeout=30_000)
    composer.fill("Inspect the workspace.")
    page.get_by_role("button", name="Send", exact=True).click()

    # The turn is now blocked on the mock gate, its first tool call already
    # rendered — the window the question lands in.
    _wait_for(lambda: httpx.get(f"{mock_url}/gate/pending", timeout=5.0).json()["pending"])

    result_holder: dict = {}
    hook_thread = _post_ask_user_question(base_url, session_id, result_holder)

    form = page.locator(f'{_APPROVAL_CARD}[data-state="pending"]').locator(_FORM).first
    expect(form).to_be_visible(timeout=_MOCK_ELICITATION_TIMEOUT_MS)
    form.get_by_role("radio", name=_OPTION_ONE).check()
    form.locator(_SUBMIT).click()

    answered = page.locator(f'{_APPROVAL_CARD}[data-state="responded"]').first
    expect(answered).to_be_visible(timeout=_MOCK_ELICITATION_TIMEOUT_MS)

    hook_thread.join(timeout=30)
    if "error" in result_holder:
        raise AssertionError(f"hook thread failed: {result_holder['error']}") from result_holder[
            "error"
        ]

    # Let the same turn run its second tool call and wrap-up text.
    assert httpx.post(f"{mock_url}/gate/release", timeout=5.0).json()["released"]
    expect(page.get_by_text("Workspace inspected.")).to_be_visible(timeout=_TURN_TIMEOUT_MS)

    # Both stretches of work fold; the answer between them does not.
    expect(page.locator(_WORKED_FOLD)).to_have_count(2, timeout=_TURN_TIMEOUT_MS)
    expect(answered).to_be_visible()
    expect(page.locator(f"{_WORKED_FOLD} {_APPROVAL_CARD}")).to_have_count(0)
