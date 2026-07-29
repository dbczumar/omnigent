"""Stale-stream banner: kill the runner mid-stream, verify the UI reacts.

The frontend polls ``GET /health?session_id=`` every 10 s while a
response is streaming. When the runner crashes, the tunnel drops and
the health endpoint returns ``runner_online: false``. The next poll
flips ``streamStale`` in the chat store, which swaps the "Working…"
shimmer for an "Agent is unresponsive" banner.

This test exercises the full chain: SPA → SSE stream → health poll →
banner render. It kills the runner subprocess with SIGKILL while the
LLM is processing, so the tunnel drops instantly — no graceful
shutdown, no terminal SSE event.
"""

from __future__ import annotations

import os
import re
import signal
import time
from collections.abc import Callable

import httpx
from playwright.sync_api import Page, expect


def test_stale_banner_on_runner_crash(
    page: Page,
    seeded_session: tuple[str, str],
    owned_runner_pids: Callable[[], list[int]],
) -> None:
    """
    Open a pre-created session, send a message, kill the runner while
    the LLM is thinking, and assert the "unresponsive" banner replaces
    "Working…".

    Starts from ``/c/<id>`` instead of ``/`` because the home route no
    longer renders a composer — see :func:`seeded_session`.

    :param page: Playwright page fixture.
    :param seeded_session: ``(base_url, session_id)`` of a pre-created
        session bound to the running runner.
    :param owned_runner_pids: Callable returning the fixture-owned
        runner PID — the only process this test may kill.
    """
    live_server, session_id = seeded_session
    page.goto(f"{live_server}/c/{session_id}")

    composer = page.get_by_placeholder("Ask the agent anything…")
    expect(composer).to_be_visible()
    composer.fill("Write a 500-word essay about the history of computing.")
    page.get_by_role("button", name="Send", exact=True).click()

    # URL should still match /c/<session_id>.
    expect(page).to_have_url(re.compile(rf"/c/{re.escape(session_id)}"), timeout=15_000)

    # Wait for the "Working…" shimmer — proves the SSE stream opened
    # and the agent started processing.
    working = page.locator('[data-testid="working-indicator"]')
    expect(working).to_be_visible(timeout=15_000)

    # Verify the health endpoint reports online before the kill.
    health_before = httpx.get(
        f"{live_server}/health?session_id={session_id}",
        timeout=5,
    ).json()  # /health — no auth needed
    assert health_before.get("session", {}).get("runner_online") is True, (
        f"Health endpoint should report runner_online=true before kill, got: {health_before}"
    )

    # Kill the fixture-owned runner. Only the tracked PID — pattern
    # discovery would also match unrelated runners on a dev machine.
    runner_pids = owned_runner_pids()
    assert runner_pids, "conftest did not record a runner_pid for this server"
    for pid in runner_pids:
        os.kill(pid, signal.SIGKILL)

    # Poll until the health endpoint reports offline (tunnel teardown
    # is async — the server's WS route needs to notice the close and
    # deregister). 10 retries × 0.5 s = 5 s budget.
    health_after: dict[str, object] = {}
    for _attempt in range(10):
        time.sleep(0.5)
        health_after = httpx.get(
            f"{live_server}/health?session_id={session_id}",
            timeout=5,
        ).json()
        if health_after.get("session", {}).get("runner_online") is False:
            break
    assert health_after.get("session", {}).get("runner_online") is False, (
        f"Health endpoint should report runner_online=false after kill, got: {health_after}"
    )

    # The health poller fires every 10 s. No grace period — the
    # indicator flips on the next poll. Budget 20 s from the kill.
    indicator = page.locator('[data-testid="disconnected-indicator"]')
    expect(indicator).to_be_visible(timeout=20_000)
    expect(indicator).to_contain_text("disconnected")
