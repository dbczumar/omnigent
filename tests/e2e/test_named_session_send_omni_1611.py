"""Regression test for OMNI-1611 / GH-2539.

Named ``sys_session_send`` 404 after first child from a bundled session-scoped agent.

After the first successful named-mode ``POST /v1/sessions`` (with
``agent_id=<bundle_agent_id>``, ``parent_session_id=<parent>``, and
``sub_agent_name=<name>``) from a session created via an uploaded agent bundle
(session-scoped agent), every subsequent named create from the same parent
session failed with::

    404 {"error": {"code": "not_found", "message": "Conversation not found"}}

Root cause: ``SqlAlchemyAgentStore._session_id_for_agent()`` uses an unordered
``LIMIT 1`` to derive the "owning" conversation for a session-scoped agent.
Once the first child session is created, the query can return that child's
conversation id instead of the parent's.  The subsequent auth check in
``validate_session_agent`` then calls ``require_access`` on the child's id; on
deployments where the child conversation row is not yet replicated to the
read replica (or when any row other than the owning parent is returned), the
check surfaces as 404 "Conversation not found".

This test creates a parent session from an uploaded bundle (archer, which
carries ``fact_checker`` and ``summarizer`` inline sub-agents), then creates
three sequential and two concurrent named children and asserts all succeed.
A fix must ensure that ``_session_id_for_agent`` deterministically returns the
*first* conversation bound to the agent (ORDER BY created_at, id), or skips
the ambiguous owning-session auth check for named-child creates where the
caller already supplies ``parent_session_id``.
"""

from __future__ import annotations

import concurrent.futures

import httpx
import pytest

from omnigent.runner.identity import OMNIGENT_INTERNAL_WS_ORIGIN
from tests.e2e.conftest import lookup_agent_id


def _named_create(
    http_client: httpx.Client,
    *,
    agent_id: str,
    parent_session_id: str,
    sub_agent_name: str,
    title: str,
) -> httpx.Response:
    """POST a named-mode child session create and return the raw response.

    :param http_client: HTTP client pointed at the live server.
    :param agent_id: The parent's (session-scoped) agent id.
    :param parent_session_id: The parent session to attach the child to.
    :param sub_agent_name: Name of the declared sub-agent, e.g.
        ``"fact_checker"``.
    :param title: Unique title for the child session.
    :returns: The raw HTTP response (caller asserts status).
    """
    return http_client.post(
        "/v1/sessions",
        json={
            "agent_id": agent_id,
            "parent_session_id": parent_session_id,
            "sub_agent_name": sub_agent_name,
            "title": f"{sub_agent_name}:{title}",
        },
        headers={"Origin": OMNIGENT_INTERNAL_WS_ORIGIN},
    )


def test_named_session_send_second_child_does_not_404(
    live_server: str,
    http_client: httpx.Client,
    archer_agent: str,
    live_runner_id: str,
) -> None:
    """Named-mode child creates #2 and #3 must not 404 after child #1 succeeds.

    Regression for OMNI-1611: after the first successful named child is created
    from a bundled (session-scoped) agent, every subsequent named create from
    the same parent failed with 404 "Conversation not found" because
    ``_session_id_for_agent`` returned the first child's conversation id (via
    an unordered LIMIT 1) and the auth check on that id could fail.

    :param live_server: Base URL of the live test server.
    :param http_client: HTTP client pointed at the live server.
    :param archer_agent: Agent name of the uploaded archer bundle.
    :param live_runner_id: Runner id for runner-bound session creation.
    """
    agent_id = lookup_agent_id(http_client, archer_agent)

    # Create the parent session from the bundled (session-scoped) agent.
    # The parent's agent is session-scoped — kind == "session" — so its
    # agent_id can appear in multiple conversation rows once children are
    # created, which is exactly the state that triggers the bug.
    parent_resp = http_client.post(
        "/v1/sessions",
        json={"agent_id": agent_id},
        headers={"Origin": OMNIGENT_INTERNAL_WS_ORIGIN},
    )
    parent_resp.raise_for_status()
    parent_session_id = str(parent_resp.json()["id"])

    # ── Sequential creates ──────────────────────────────────────────────────
    # Child #1: expect success (this always worked even before the fix).
    child1_resp = _named_create(
        http_client,
        agent_id=agent_id,
        parent_session_id=parent_session_id,
        sub_agent_name="fact_checker",
        title="task-alpha",
    )
    assert child1_resp.status_code in (200, 201), (
        f"child #1 (fact_checker:task-alpha) failed unexpectedly: "
        f"{child1_resp.status_code} {child1_resp.text[:300]}"
    )
    child1_id = str(child1_resp.json()["id"])

    # Child #2: the bug manifests here — should succeed but returned 404.
    child2_resp = _named_create(
        http_client,
        agent_id=agent_id,
        parent_session_id=parent_session_id,
        sub_agent_name="summarizer",
        title="task-beta",
    )
    assert child2_resp.status_code in (200, 201), (
        f"child #2 (summarizer:task-beta) 404'd after child #1 was created — "
        f"OMNI-1611 regression: {child2_resp.status_code} {child2_resp.text[:300]}"
    )

    # Child #3: same sub-agent as #1 but a different title (new session).
    child3_resp = _named_create(
        http_client,
        agent_id=agent_id,
        parent_session_id=parent_session_id,
        sub_agent_name="fact_checker",
        title="task-gamma",
    )
    assert child3_resp.status_code in (200, 201), (
        f"child #3 (fact_checker:task-gamma) failed: "
        f"{child3_resp.status_code} {child3_resp.text[:300]}"
    )

    # ── Concurrent creates ─────────────────────────────────────────────────
    # Two parallel creates from the same parent.  The bug also reproduced
    # immediately with parallel sends: the first wins and the second 404s.
    def _parallel(sub_agent: str, title: str) -> httpx.Response:
        return _named_create(
            http_client,
            agent_id=agent_id,
            parent_session_id=parent_session_id,
            sub_agent_name=sub_agent,
            title=title,
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        f_a = pool.submit(_parallel, "fact_checker", "concurrent-1")
        f_b = pool.submit(_parallel, "summarizer", "concurrent-2")
        concurrent_resp_a = f_a.result(timeout=30)
        concurrent_resp_b = f_b.result(timeout=30)

    assert concurrent_resp_a.status_code in (200, 201), (
        f"concurrent child A (fact_checker:concurrent-1) failed: "
        f"{concurrent_resp_a.status_code} {concurrent_resp_a.text[:300]}"
    )
    assert concurrent_resp_b.status_code in (200, 201), (
        f"concurrent child B (summarizer:concurrent-2) failed: "
        f"{concurrent_resp_b.status_code} {concurrent_resp_b.text[:300]}"
    )

    # ── Verify all children are listed under the parent ────────────────────
    children_resp = http_client.get(f"/v1/sessions/{parent_session_id}/child_sessions")
    children_resp.raise_for_status()
    child_ids = {row["id"] for row in children_resp.json()["data"]}
    created_ids = {
        child1_id,
        str(child2_resp.json()["id"]),
        str(child3_resp.json()["id"]),
        str(concurrent_resp_a.json()["id"]),
        str(concurrent_resp_b.json()["id"]),
    }
    assert created_ids <= child_ids, (
        f"not all created children appear under parent: missing={created_ids - child_ids}"
    )


@pytest.mark.parametrize(
    "builtin_agent",
    ["polly", "debby"],
)
def test_named_session_send_builtin_unaffected(
    live_server: str,
    http_client: httpx.Client,
    builtin_agent: str,
) -> None:
    """Builtin/template agents (polly, debby) are NOT affected by this bug.

    The root cause only fires for session-scoped agents (``kind=session``).
    Template agents have ``agent.session_id = NULL``, so the
    ``validate_session_agent`` auth branch that calls ``_session_id_for_agent``
    is skipped.  This test asserts that the builtin path keeps working as a
    guard against regressions from the fix.

    :param live_server: Base URL of the live test server.
    :param http_client: HTTP client pointed at the live server.
    :param builtin_agent: Name of the builtin agent under test.
    """
    resp = http_client.get("/v1/agents")
    resp.raise_for_status()
    agents = resp.json().get("data", [])
    builtin = next((a for a in agents if a["name"] == builtin_agent), None)
    if builtin is None:
        pytest.skip(f"builtin agent {builtin_agent!r} not registered on this server")
    agent_id = builtin["id"]

    # Create a top-level session from the builtin.
    parent_resp = http_client.post(
        "/v1/sessions",
        json={"agent_id": agent_id},
        headers={"Origin": OMNIGENT_INTERNAL_WS_ORIGIN},
    )
    parent_resp.raise_for_status()
    assert parent_resp.status_code in (200, 201), (
        f"builtin {builtin_agent!r} top-level create failed: "
        f"{parent_resp.status_code} {parent_resp.text[:300]}"
    )
