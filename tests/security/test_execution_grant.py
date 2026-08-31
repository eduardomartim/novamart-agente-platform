"""What a grant does, and everything it refuses.

A grant is the only thing standing between "a process can reach the tool
server" and "a process can run tools". Its whole value is in the refusals, so
this file is mostly refusals: a grant for one tool presented at another, a
grant whose arguments were swapped underneath it, an expired one, a replayed
one, a forged one, one whose version was rewritten.

The positive case is here too, and it matters just as much -- a verifier that
refuses everything is not secure, it is broken, and every negative test below
would still pass.
"""

from __future__ import annotations

import base64
import json
import threading
import time

import pytest

from agent_platform.execution import grant as g
from agent_platform.execution.nonce import NonceReplayed, consume
from agent_platform.state import LocalBackend

SECRET = "unit-test-signing-secret-not-a-real-one"
OTHER_SECRET = "a-different-secret-of-sufficient-length"


def mint(tool="get_order", arguments=None, secret=SECRET, **kw):
    return g.issue(
        secret=secret,
        execution_id="req-abc:1",
        tool=tool,
        arguments={"order_id": "ORD-1001"} if arguments is None else arguments,
        **kw,
    )


def tamper(token: str, **changes) -> str:
    """Rewrite payload fields, keeping the original signature.

    This is the forgery an attacker can actually attempt: they hold a valid
    token and want it to say something else.
    """
    prefix, body, signature = token.split(".")
    payload = json.loads(g._unb64(body))
    payload.update(changes)
    rebuilt = g._b64(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
    return f"{prefix}.{rebuilt}.{signature}"


# ================================================================== it works


def test_a_valid_grant_verifies():
    token, issued = mint()
    verified = g.verify(
        token, secret=SECRET, expected_tool="get_order",
        arguments={"order_id": "ORD-1001"},
    )
    assert verified.tool == "get_order"
    assert verified.nonce == issued.nonce
    assert verified.version == g.GRANT_VERSION


def test_argument_order_does_not_matter():
    """Canonicalisation sorts keys, so the same call is the same digest."""
    token, _ = mint(tool="update_record", arguments={"a": 1, "b": 2})
    assert g.verify(
        token, secret=SECRET, expected_tool="update_record", arguments={"b": 2, "a": 1}
    )


# =============================================================== tool binding


def test_a_grant_for_one_tool_does_not_authorise_another():
    """Tool confusion: the headline reason a grant names its tool."""
    token, _ = mint(tool="get_order")
    with pytest.raises(g.GrantError):
        g.verify(
            token, secret=SECRET, expected_tool="delete_record",
            arguments={"order_id": "ORD-1001"},
        )


def test_rewriting_the_tool_in_the_payload_is_refused():
    token, _ = mint(tool="get_order")
    forged = tamper(token, tool="delete_record")
    with pytest.raises(g.GrantError):
        g.verify(
            forged, secret=SECRET, expected_tool="delete_record",
            arguments={"order_id": "ORD-1001"},
        )


# =========================================================== argument binding


@pytest.mark.parametrize(
    "swapped",
    [
        {"order_id": "ORD-9999"},
        {"order_id": "ORD-1001", "extra": 1},
        {},
        {"order_id": 1001},
        {"order_ID": "ORD-1001"},
    ],
)
def test_a_grant_does_not_authorise_different_arguments(swapped):
    """Argument confusion: a grant for one record must not move to another."""
    token, _ = mint(arguments={"order_id": "ORD-1001"})
    with pytest.raises(g.GrantError):
        g.verify(token, secret=SECRET, expected_tool="get_order", arguments=swapped)


def test_rewriting_the_digest_in_the_payload_is_refused():
    token, _ = mint(arguments={"order_id": "ORD-1001"})
    forged = tamper(token, adg=g.arguments_digest("get_order", {"order_id": "ORD-9999"}))
    with pytest.raises(g.GrantError):
        g.verify(
            forged, secret=SECRET, expected_tool="get_order",
            arguments={"order_id": "ORD-9999"},
        )


def test_the_digest_distinguishes_types_and_nesting_and_unicode():
    """The canonicalisation's edges, asserted rather than assumed.

    ``action_fingerprint`` serialises with ``default=str``, which is where two
    different values could collapse into one digest. These are the cases worth
    pinning.
    """
    d = g.arguments_digest
    assert d("t", {"x": 1}) != d("t", {"x": "1"}), "int and str collapsed"
    assert d("t", {"x": True}) != d("t", {"x": 1}), "bool and int collapsed"
    assert d("t", {"x": None}) != d("t", {"x": "None"}), "None and 'None' collapsed"
    assert d("t", {"x": [1, 2]}) != d("t", {"x": [2, 1]}), "list order ignored"
    assert d("t", {"x": {"y": 1}}) != d("t", {"x": {"y": 2}}), "nesting not covered"
    assert d("t", {"x": "café"}) != d("t", {"x": "cafe"}), "unicode collapsed"
    assert d("t", {"x": "café"}) == d("t", {"x": "café"}), "same string differs"
    assert d("a", {"x": 1}) != d("b", {"x": 1}), "tool name not covered"


# ================================================================== signature


def test_a_grant_signed_with_another_secret_is_refused():
    token, _ = mint(secret=OTHER_SECRET)
    with pytest.raises(g.GrantError):
        g.verify(
            token, secret=SECRET, expected_tool="get_order",
            arguments={"order_id": "ORD-1001"},
        )


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "not-a-token",
        "v1.only-two.parts.extra",
        "v1..",
        "v1.!!!.!!!",
    ],
)
def test_malformed_tokens_are_refused(bad):
    with pytest.raises(g.GrantError):
        g.verify(bad, secret=SECRET, expected_tool="get_order", arguments={})


def test_an_invented_signature_is_refused():
    token, _ = mint()
    prefix, body, _ = token.split(".")
    forged = f"{prefix}.{body}.{base64.urlsafe_b64encode(b'x' * 32).decode().rstrip('=')}"
    with pytest.raises(g.GrantError):
        g.verify(
            forged, secret=SECRET, expected_tool="get_order",
            arguments={"order_id": "ORD-1001"},
        )


def test_an_empty_signature_is_refused():
    prefix, body, _ = mint()[0].split(".")
    with pytest.raises(g.GrantError):
        g.verify(
            f"{prefix}.{body}.", secret=SECRET, expected_tool="get_order",
            arguments={"order_id": "ORD-1001"},
        )


# ==================================================================== version


def test_an_unknown_version_prefix_is_refused():
    token, _ = mint()
    _, body, signature = token.split(".")
    with pytest.raises(g.GrantError):
        g.verify(
            f"v99.{body}.{signature}", secret=SECRET, expected_tool="get_order",
            arguments={"order_id": "ORD-1001"},
        )


def test_a_payload_version_that_disagrees_with_the_prefix_is_refused():
    """The version is signed as well as prefixed, so it cannot be rewritten."""
    forged = tamper(mint()[0], v=99)
    with pytest.raises(g.GrantError):
        g.verify(
            forged, secret=SECRET, expected_tool="get_order",
            arguments={"order_id": "ORD-1001"},
        )


# ================================================================= timestamps


def test_an_expired_grant_is_refused():
    token, _ = mint(ttl_seconds=10.0, now=1_000.0)
    with pytest.raises(g.GrantError):
        g.verify(
            token, secret=SECRET, expected_tool="get_order",
            arguments={"order_id": "ORD-1001"}, now=1_100.0,
        )


def test_a_grant_is_valid_inside_its_window():
    token, _ = mint(ttl_seconds=30.0, now=1_000.0)
    assert g.verify(
        token, secret=SECRET, expected_tool="get_order",
        arguments={"order_id": "ORD-1001"}, now=1_020.0,
    )


def test_a_grant_from_the_future_is_refused():
    token, _ = mint(ttl_seconds=30.0, now=5_000.0)
    with pytest.raises(g.GrantError):
        g.verify(
            token, secret=SECRET, expected_tool="get_order",
            arguments={"order_id": "ORD-1001"}, now=1_000.0,
        )


def test_an_expiry_at_or_before_issue_is_refused():
    """Not a short grant -- a malformed one."""
    for exp_offset in (0.0, -5.0):
        forged = tamper(mint(now=1_000.0)[0], iat=1_000.0, exp=1_000.0 + exp_offset)
        with pytest.raises(g.GrantError):
            g.verify(
                forged, secret=SECRET, expected_tool="get_order",
                arguments={"order_id": "ORD-1001"}, now=1_000.0,
            )


def test_extending_the_expiry_is_refused():
    """The obvious attack on a short TTL."""
    token, _ = mint(ttl_seconds=10.0, now=1_000.0)
    forged = tamper(token, exp=9_999_999_999.0)
    with pytest.raises(g.GrantError):
        g.verify(
            forged, secret=SECRET, expected_tool="get_order",
            arguments={"order_id": "ORD-1001"}, now=1_100.0,
        )


# ====================================================================== reuse


def test_a_nonce_can_only_be_consumed_once():
    backend = LocalBackend()
    now = time.time()
    consume(backend, "nonce-a", expires_at=now + 30, now=now)
    with pytest.raises(NonceReplayed):
        consume(backend, "nonce-a", expires_at=now + 30, now=now)


def test_distinct_nonces_do_not_interfere():
    backend = LocalBackend()
    now = time.time()
    consume(backend, "nonce-a", expires_at=now + 30, now=now)
    consume(backend, "nonce-b", expires_at=now + 30, now=now)


def test_concurrent_consumers_of_one_nonce_produce_exactly_one_winner():
    """Replay under contention, in one process.

    The cross-replica version of this lives in the MCP integration tests; this
    one pins the primitive itself.
    """
    backend = LocalBackend()
    now = time.time()
    winners: list[int] = []
    lock = threading.Lock()
    start = threading.Barrier(24)

    def attempt() -> None:
        start.wait(timeout=30)
        try:
            consume(backend, "contended", expires_at=now + 30, now=now)
        except NonceReplayed:
            return
        with lock:
            winners.append(1)

    threads = [threading.Thread(target=attempt) for _ in range(24)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert len(winners) == 1, f"{len(winners)} callers consumed the same grant"


# ==================================================================== secrets


def test_no_error_message_reveals_anything_about_the_secret():
    """A verifier that explains itself helps someone iterate toward a forgery."""
    messages: list[str] = []
    for token in ("garbage", tamper(mint()[0], tool="delete_record"), mint(secret=OTHER_SECRET)[0]):
        try:
            g.verify(
                token, secret=SECRET, expected_tool="get_order",
                arguments={"order_id": "ORD-1001"},
            )
        except g.GrantError as exc:
            messages.append(str(exc))

    assert len(messages) == 3
    assert len(set(messages)) == 1, f"refusals are distinguishable: {set(messages)}"
    for message in messages:
        assert SECRET not in message
        assert "signature" not in message.lower()
        assert "hmac" not in message.lower()


def test_the_token_carries_no_arguments_and_no_secret():
    token, _ = mint(arguments={"order_id": "ORD-1001", "note": "sensitive-value"})
    _, body, _ = token.split(".")
    decoded = g._unb64(body).decode()

    assert "sensitive-value" not in decoded, "arguments travelled inside the grant"
    assert "ORD-1001" not in decoded
    assert SECRET not in decoded
    assert set(json.loads(decoded)) == {"v", "eid", "tool", "adg", "iat", "exp", "nonce"}


def test_a_missing_secret_fails_closed_and_names_no_value():
    with pytest.raises(g.GrantConfigurationError) as caught:
        g.issue(secret=None, execution_id="e", tool="get_order", arguments={})
    assert "EXECUTION_GRANT_SECRET" in str(caught.value)

    with pytest.raises(g.GrantConfigurationError):
        g.issue(secret="tooshort", execution_id="e", tool="get_order", arguments={})
