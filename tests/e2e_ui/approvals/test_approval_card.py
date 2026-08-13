"""E2E: an in-chat approval prompt renders and resolves.

When a tool call trips a policy that returns ASK, the runner forwards the
gate to the server, which publishes a ``response.elicitation_request`` the
chat renders as an ``ApprovalCard`` (``web/src/components/blocks/
ApprovalCard.tsx``). This test drives the full loop on the openai-agents
harness: send a turn that makes the agent attempt a gated ``git push``,
wait for the pending card, click **Approve**, and assert the card flips to
its "Approved" responded state and the server clears the pending prompt.

The ``approval_session`` fixture (conftest) supplies an agent whose
``blast_radius`` guardrail gates pushes; the gate fires on the *tool call*,
so the push never has to succeed. Real LLM in the loop → nightly + a
generous timeout, matching the other agent-driven UI suites.

``test_native_permission_card_names_the_harness`` covers the same card's
header for harness-native prompts instead of policy asks. It drives a
synthetic permission-request hook against a seeded session (no LLM, no
native CLI — seconds, like ``test_persistent_approval.py``), so it runs on
every PR.
"""

from __future__ import annotations

import threading
import time

import httpx
import pytest
from playwright.sync_api import Page, expect

_COMPOSER = "Ask the agent anything…"
_APPROVAL_CARD = '[data-testid="approval-card"]'
_MOCK_ELICITATION_TIMEOUT_MS = 15_000

# The agent must boot, take a turn, and emit the gated tool call before the
# card appears — cold-start can be slow, so allow well past the streaming
# default but under the test's 600s ceiling.
_AGENT_TURN_TIMEOUT_MS = 120_000


def _pending_elicitations(base_url: str, session_id: str) -> list[dict]:
    """Return the session snapshot's pending elicitation events (owner view)."""
    resp = httpx.get(f"{base_url}/v1/sessions/{session_id}", timeout=10.0)
    resp.raise_for_status()
    return resp.json().get("pending_elicitations") or []


@pytest.mark.nightly
@pytest.mark.timeout(600)
def test_approval_card_renders_and_approves(
    page: Page,
    approval_session: tuple[str, str],
) -> None:
    """Gated tool call → pending approval card → Approve → resolved."""
    base_url, session_id = approval_session
    page.goto(f"{base_url}/c/{session_id}")

    composer = page.get_by_placeholder(_COMPOSER)
    expect(composer).to_be_visible(timeout=30_000)
    composer.fill("Run the command now.")
    page.get_by_role("button", name="Send", exact=True).click()

    # The agent calls the gated push; the policy ASK surfaces a pending card.
    card = page.locator(f'{_APPROVAL_CARD}[data-state="pending"]').first
    expect(card).to_be_visible(timeout=_AGENT_TURN_TIMEOUT_MS)
    expect(card.get_by_text("Approval required")).to_be_visible()
    # The server is genuinely parked on this prompt, not just an optimistic UI.
    assert _pending_elicitations(base_url, session_id), "server has no parked elicitation"

    card.get_by_role("button", name="Approve").click()

    # Card transitions to the responded "Approved" state and the parked
    # server-side prompt drains.
    responded = page.locator(f'{_APPROVAL_CARD}[data-state="responded"]').first
    expect(responded).to_be_visible(timeout=30_000)
    expect(responded.get_by_text("Approved", exact=False).first).to_be_visible()
    _wait_for(lambda: not _pending_elicitations(base_url, session_id))


@pytest.mark.nightly
@pytest.mark.timeout(600)
def test_approval_card_reject(
    page: Page,
    approval_session: tuple[str, str],
) -> None:
    """Rejecting a pending approval flips the card to the rejected state."""
    base_url, session_id = approval_session
    page.goto(f"{base_url}/c/{session_id}")

    composer = page.get_by_placeholder(_COMPOSER)
    expect(composer).to_be_visible(timeout=30_000)
    composer.fill("Run the command now.")
    page.get_by_role("button", name="Send", exact=True).click()

    card = page.locator(f'{_APPROVAL_CARD}[data-state="pending"]').first
    expect(card).to_be_visible(timeout=_AGENT_TURN_TIMEOUT_MS)
    card.get_by_role("button", name="Reject").click()

    responded = page.locator(f'{_APPROVAL_CARD}[data-state="responded"]').first
    expect(responded).to_be_visible(timeout=30_000)
    expect(responded.get_by_text("Rejected", exact=False).first).to_be_visible()
    _wait_for(lambda: not _pending_elicitations(base_url, session_id))


@pytest.mark.timeout(90)
def test_native_permission_card_names_the_harness(
    page: Page,
    seeded_session: tuple[str, str],
) -> None:
    """A claude-native prompt is labeled "Claude Code", not by its internal stamp."""
    base_url, session_id = seeded_session
    result_holder: dict = {}

    def _post_hook() -> None:
        try:
            resp = httpx.post(
                f"{base_url}/v1/sessions/{session_id}/hooks/permission-request",
                json={"tool_name": "Bash", "tool_input": {"command": "ls -la"}},
                timeout=60.0,
            )
            resp.raise_for_status()
            result_holder["response"] = resp.json()
        except Exception as exc:
            result_holder["error"] = exc

    hook_thread = threading.Thread(target=_post_hook, daemon=True)
    hook_thread.start()
    # Let the server park the elicitation before the SPA tries to render it.
    page.wait_for_timeout(500)

    page.goto(f"{base_url}/c/{session_id}")

    # The server stamps policy_name="claude_native_permission" and
    # phase="pre_tool_use" as provenance. Neither is a user-authored policy, so
    # the card must name the product that asked and drop the constant phase.
    card = page.locator(f'{_APPROVAL_CARD}[data-state="pending"]').first
    expect(card).to_be_visible(timeout=_MOCK_ELICITATION_TIMEOUT_MS)
    expect(card).to_contain_text("Claude Code")
    expect(card).not_to_contain_text("claude_native_permission")
    expect(card).not_to_contain_text("pre_tool_use")
    assert _pending_elicitations(base_url, session_id), "server has no parked elicitation"

    card.get_by_role("button", name="Approve", exact=True).click()

    # The responded pill is what lingers in the transcript — it must not leak
    # the stamp either.
    responded = page.locator(f'{_APPROVAL_CARD}[data-state="responded"]').first
    expect(responded).to_be_visible(timeout=_MOCK_ELICITATION_TIMEOUT_MS)
    expect(responded).to_contain_text("Claude Code")
    expect(responded).not_to_contain_text("claude_native_permission")

    hook_thread.join(timeout=30)
    if "error" in result_holder:
        raise AssertionError(f"hook thread failed: {result_holder['error']}") from result_holder[
            "error"
        ]
    _wait_for(lambda: not _pending_elicitations(base_url, session_id), timeout_s=30.0)


def _wait_for(predicate, *, timeout_s: float = 15.0, interval_s: float = 0.25) -> None:
    """Poll *predicate* until truthy or the deadline passes."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(interval_s)
    raise AssertionError("condition not met within timeout")
