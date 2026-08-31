"""The credential, and what it is allowed to reach.

The question here is not "does a good token get a 200". It is whether the
boundary can be reached *around*: with no credential, with a credential for
somebody else, with a credential that lacks the authority, or by a path the
matcher does not recognise but the router does.

The last of those is the interesting one and it is not hypothetical. Starlette
runs middleware **before** routing, so the middleware cannot ask which route is
about to handle the request -- it has to resolve the path itself. And Starlette
answers ``/runs/x/confirm/`` with a 307 to the canonical path, which re-enters
the middleware. A matcher that treated an unrecognised path as "no scope
required" would hand the second pass a request that had already skipped the
check.
"""

from __future__ import annotations

import time

import pytest

pytest.importorskip("starlette")
pytest.importorskip("httpx")

from starlette.testclient import TestClient

from agent_platform.api.app import PUBLIC, build_keyring, create_app
from agent_platform.config import Settings
from agent_platform.persistence.memory import InMemoryRepository
from agent_platform.platform import AgentPlatform
from agent_platform.security.api_auth import (
    KNOWN_SCOPES,
    MIN_SECRET_CHARS,
    AuthConfigError,
    AuthError,
    Keyring,
    issue_token,
    parse_credentials,
    principal_from_header,
    secret_hash,
)
from tests.conftest import bearer


@pytest.fixture
def settings(tmp_path, monkeypatch, api_credentials) -> Settings:
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "auth.db"))
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("API_AUTH_KEYS", api_credentials.keys)
    monkeypatch.delenv("API_AUTH_KEYS_FILE", raising=False)
    monkeypatch.delenv("API_AUTH_MODE", raising=False)
    return Settings.from_env(load_dotenv_file=False)


@pytest.fixture
def app(settings):
    platform = AgentPlatform(settings, repository=InMemoryRepository())
    try:
        yield create_app(platform, settings=settings)
    finally:
        platform.close()


@pytest.fixture
def anonymous(app):
    with TestClient(app) as c:
        yield c


@pytest.fixture
def keyring(api_credentials) -> Keyring:
    return Keyring(parse_credentials(api_credentials.keys))


# ================================================================== the token


def test_a_generated_token_verifies_against_its_own_digest():
    key_id, token, digest = issue_token()
    ring = Keyring(parse_credentials(f"{key_id} svc runs:write {digest}"))
    principal = ring.verify(token)
    assert principal.name == "svc"
    assert principal.key_id == key_id
    assert principal.scopes == {"runs:write"}


def test_the_secret_is_never_recoverable_from_what_is_stored():
    """Configuration holds a digest. Nothing there reconstructs the token."""
    key_id, token, digest = issue_token()
    line = f"{key_id} svc runs:write {digest}"
    secret = token.split("_", 2)[2]
    assert secret not in line
    assert digest != secret
    assert len(digest) == 64


def test_a_secret_containing_an_underscore_still_verifies():
    """urlsafe secrets contain ``_``; splitting without a bound would tear them.

    Not a hypothetical: ``token_urlsafe`` draws from an alphabet that includes
    the separator, so an unbounded split rejects a fraction of genuine
    credentials -- intermittently, and only in production.
    """
    key_id = "aabbccdd"
    secret = "x_y_z" + "q" * 40
    ring = Keyring(parse_credentials(f"{key_id} svc runs:write {secret_hash(secret)}"))
    assert ring.verify(f"ap_{key_id}_{secret}").name == "svc"


@pytest.mark.parametrize(
    ("label", "token"),
    [
        ("empty", ""),
        ("no structure", "hunter2"),
        ("wrong prefix", "xx_aabbccdd_" + "q" * 43),
        ("key id not hex", "ap_zzzzzzzz_" + "q" * 43),
        ("key id wrong length", "ap_aabb_" + "q" * 43),
        ("secret too short", "ap_aabbccdd_short"),
        ("only two segments", "ap_aabbccdd"),
    ],
)
def test_a_malformed_token_is_refused(keyring, label, token):
    with pytest.raises(AuthError):
        keyring.verify(token)


def test_an_unknown_key_id_and_a_wrong_secret_are_both_refused(keyring, api_credentials):
    with pytest.raises(AuthError) as unknown:
        keyring.verify("ap_00000000_" + "q" * 43)
    assert unknown.value.reason == "unknown_key"

    good = api_credentials.runner
    with pytest.raises(AuthError) as wrong:
        keyring.verify(good[:-1] + ("a" if good[-1] != "a" else "b"))
    assert wrong.value.reason == "bad_secret"


def test_an_unknown_key_id_costs_what_a_wrong_secret_costs(keyring, api_credentials):
    """The hash is computed either way, so existence is not timed out of the ring.

    This is a coarse check on a coarse property. Python offers no constant-time
    guarantee across a dict lookup and a garbage collector, and the claim being
    made is only that the *obvious* difference -- skipping the hash entirely for
    an unknown key -- is not there.
    """
    good = api_credentials.runner
    unknown = "ap_00000000_" + good.split("_", 2)[2]
    wrong = good[:-1] + ("a" if good[-1] != "a" else "b")

    def measure(token: str) -> float:
        start = time.perf_counter()
        for _ in range(300):
            # SIM105 is suppressed deliberately: contextlib.suppress builds
            # and enters a context manager on every iteration, and this loop is
            # the measurement. That overhead lands on both arms and pulls the
            # ratio towards 1, making the assertion easier to pass rather than
            # more honest.
            try:  # noqa: SIM105
                keyring.verify(token)
            except AuthError:
                pass
        return time.perf_counter() - start

    a, b = measure(unknown), measure(wrong)
    slower, faster = max(a, b), min(a, b)
    assert slower < faster * 5, f"unknown={a:.4f}s wrong={b:.4f}s"


@pytest.mark.parametrize(
    ("label", "header"),
    [
        ("missing", ""),
        ("no scheme", "ap_aabbccdd_" + "q" * 43),
        ("wrong scheme", "Basic ap_aabbccdd_" + "q" * 43),
        ("scheme only", "Bearer"),
        ("empty token", "Bearer   "),
    ],
)
def test_a_bad_authorization_header_is_refused(keyring, label, header):
    with pytest.raises(AuthError):
        principal_from_header(header, keyring)


def test_the_scheme_is_case_insensitive(keyring, api_credentials):
    assert principal_from_header(f"bearer {api_credentials.runner}", keyring).name == "runner"


# ========================================================= the credential file


def test_comments_and_blank_lines_are_ignored():
    key_id, _token, digest = issue_token()
    text = f"""
    # the on-call rotation
    {key_id} ops runs:write {digest}   # inline comment

    """
    assert len(parse_credentials(text)) == 1


@pytest.mark.parametrize(
    ("label", "line"),
    [
        ("too few fields", "aabbccdd svc runs:write"),
        ("too many fields", "aabbccdd svc runs:write " + "a" * 64 + " extra"),
        ("key id not hex", "zzzzzzzz svc runs:write " + "a" * 64),
        ("key id wrong length", "aabb svc runs:write " + "a" * 64),
        ("principal with a space", "aabbccdd 'two words' runs:write " + "a" * 64),
        ("no scopes", "aabbccdd svc , " + "a" * 64),
        ("unknown scope", "aabbccdd svc admin " + "a" * 64),
        ("digest too short", "aabbccdd svc runs:write abc"),
        ("digest not hex", "aabbccdd svc runs:write " + "z" * 64),
    ],
)
def test_a_malformed_credential_line_refuses_to_load(label, line):
    with pytest.raises(AuthConfigError):
        parse_credentials(line)


def test_a_duplicate_key_id_refuses_to_load():
    digest = "a" * 64
    with pytest.raises(AuthConfigError, match="duplicate"):
        parse_credentials(f"aabbccdd one runs:write {digest}\naabbccdd two runs:write {digest}")


def test_an_empty_table_refuses_to_load():
    with pytest.raises(AuthConfigError, match="no credentials"):
        parse_credentials("# nothing here\n\n")


def test_a_secret_pasted_where_the_digest_belongs_is_refused():
    """The most likely operator mistake, and the most expensive one.

    A secret in the credential table would be a working credential stored in
    plaintext in configuration -- and it would work, so nothing would say so.
    """
    key_id, token, _digest = issue_token()
    with pytest.raises(AuthConfigError, match="not a secret"):
        parse_credentials(f"{key_id} svc runs:write {token}")


def test_a_weak_secret_cannot_be_registered():
    """The SHA-256 argument holds for high-entropy secrets and nothing else.

    A short secret is refused at the door rather than accepted and then
    reasoned about as though the entropy assumption applied to it.
    """
    key_id = "aabbccdd"
    weak = "hunter2"
    assert len(weak) < MIN_SECRET_CHARS
    ring = Keyring(parse_credentials(f"{key_id} svc runs:write {secret_hash(weak)}"))
    with pytest.raises(AuthError):
        ring.verify(f"ap_{key_id}_{weak}")


# ================================================================== start-up


def _settings_with(monkeypatch, tmp_path, **env) -> Settings:
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "boot.db"))
    for name in ("API_AUTH_KEYS", "API_AUTH_KEYS_FILE", "API_AUTH_MODE"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return Settings.from_env(load_dotenv_file=False)


def test_with_no_credentials_the_api_refuses_to_start(monkeypatch, tmp_path):
    """Fail closed. Absent configuration is not a way to switch authentication off.

    The same rule an unreachable REDIS_URL follows: a control that disables
    itself when its configuration is missing is disabled on the day it matters,
    and the logs say nothing.
    """
    with pytest.raises(AuthConfigError, match="Refusing to serve"):
        build_keyring(_settings_with(monkeypatch, tmp_path))


def test_running_without_authentication_has_to_be_typed_out(monkeypatch, tmp_path, caplog):
    with caplog.at_level("WARNING"):
        off = _settings_with(monkeypatch, tmp_path, API_AUTH_MODE="disabled")
        assert build_keyring(off) is None
    assert any("DISABLED" in r.message for r in caplog.records)


def test_two_credential_sources_at_once_refuse_to_start(monkeypatch, tmp_path, api_credentials):
    path = tmp_path / "keys"
    path.write_text(api_credentials.keys, encoding="utf-8")
    with pytest.raises(AuthConfigError, match="both set"):
        build_keyring(
            _settings_with(
                monkeypatch,
                tmp_path,
                API_AUTH_KEYS=api_credentials.keys,
                API_AUTH_KEYS_FILE=str(path),
            )
        )


def test_disabled_while_credentials_are_configured_refuses_to_start(
    monkeypatch, tmp_path, api_credentials
):
    with pytest.raises(AuthConfigError, match="disabled but credentials"):
        build_keyring(
            _settings_with(
                monkeypatch, tmp_path, API_AUTH_MODE="disabled", API_AUTH_KEYS=api_credentials.keys
            )
        )


def test_an_unreadable_key_file_refuses_to_start(monkeypatch, tmp_path):
    with pytest.raises(AuthConfigError, match="could not read"):
        build_keyring(
            _settings_with(
                monkeypatch, tmp_path, API_AUTH_KEYS_FILE=str(tmp_path / "does-not-exist")
            )
        )


def test_credentials_load_from_a_file(monkeypatch, tmp_path, api_credentials):
    path = tmp_path / "keys"
    path.write_text(api_credentials.keys, encoding="utf-8")
    ring = build_keyring(_settings_with(monkeypatch, tmp_path, API_AUTH_KEYS_FILE=str(path)))
    assert ring is not None
    assert ring.principals == ("approver", "operator", "runner", "scraper")


def test_an_unknown_auth_mode_refuses_to_start(monkeypatch, tmp_path):
    with pytest.raises(AuthConfigError, match="API_AUTH_MODE"):
        build_keyring(_settings_with(monkeypatch, tmp_path, API_AUTH_MODE="maybe"))


# ============================================================ over the wire


def test_public_routes_need_no_credential(anonymous):
    assert anonymous.get("/health").status_code == 200
    assert anonymous.get("/ready").status_code == 200


def test_a_bad_credential_is_refused_even_on_a_public_route(anonymous):
    """Public means no credential is required, not that a bad one is overlooked."""
    r = anonymous.get("/health", headers=bearer("ap_00000000_" + "q" * 43))
    assert r.status_code == 401


@pytest.mark.parametrize(
    ("method", "path"),
    [("GET", "/metrics"), ("POST", "/runs"), ("POST", "/runs/abc/confirm")],
)
def test_protected_routes_refuse_an_anonymous_caller(anonymous, method, path):
    r = anonymous.request(method, path, json={"input": "x", "approved": True})
    assert r.status_code == 401
    assert r.headers["WWW-Authenticate"] == "Bearer"


def test_every_refusal_says_exactly_the_same_thing(anonymous, api_credentials):
    """Four different causes, one indistinguishable answer.

    A verifier that explains which check failed is a verifier that helps
    somebody iterate towards a valid credential, and the caller can do nothing
    differently for one cause versus another. Same rule as the execution grant.
    """
    good = api_credentials.runner
    bodies = {
        anonymous.post("/runs", json={"input": "x"}).text,
        anonymous.post("/runs", json={"input": "x"}, headers=bearer("garbage")).text,
        anonymous.post(
            "/runs", json={"input": "x"}, headers=bearer("ap_00000000_" + "q" * 43)
        ).text,
        anonymous.post(
            "/runs",
            json={"input": "x"},
            headers=bearer(good[:-1] + ("a" if good[-1] != "a" else "b")),
        ).text,
    }
    assert len(bodies) == 1, f"the refusals are distinguishable: {bodies}"


def test_a_refused_request_never_echoes_the_credential(anonymous):
    token = "ap_11111111_" + "z" * 43
    r = anonymous.post("/runs", json={"input": "x"}, headers=bearer(token))
    assert token not in r.text
    assert "z" * 43 not in r.text
    assert "11111111" not in r.text


def test_a_refused_request_still_carries_a_correlation_id(anonymous):
    """A burst of 401s is not diagnosable without one."""
    r = anonymous.post("/runs", json={"input": "x"})
    assert r.status_code == 401
    assert r.headers["X-Request-ID"]


def test_a_credential_in_a_query_string_is_not_a_credential(anonymous, api_credentials):
    """URLs reach access logs and proxies. There is no query-parameter form."""
    r = anonymous.post(f"/runs?api_key={api_credentials.runner}", json={"input": "x"})
    assert r.status_code == 401
    r = anonymous.post(f"/runs?token={api_credentials.runner}", json={"input": "x"})
    assert r.status_code == 401


# ====================================================== separation of duties


def test_submitting_does_not_confer_approving(anonymous, api_credentials):
    r = anonymous.post(
        "/runs/whatever/confirm",
        json={"approved": True},
        headers=bearer(api_credentials.runner),
    )
    assert r.status_code == 403


def test_approving_does_not_confer_submitting(anonymous, api_credentials):
    r = anonymous.post(
        "/runs", json={"input": "x"}, headers=bearer(api_credentials.approver)
    )
    assert r.status_code == 403


def test_reading_metrics_confers_neither(anonymous, api_credentials):
    scraper = bearer(api_credentials.scraper)
    assert anonymous.get("/metrics", headers=scraper).status_code == 200
    assert anonymous.post("/runs", json={"input": "x"}, headers=scraper).status_code == 403
    assert (
        anonymous.post(
            "/runs/x/confirm", json={"approved": True}, headers=scraper
        ).status_code
        == 403
    )


def test_no_scope_subsumes_another():
    """There is no admin scope, and this is what stops one appearing by accident."""
    assert {"runs:write", "confirm:write", "metrics:read"} == KNOWN_SCOPES


# ============================================================== the matcher


def test_an_anonymous_caller_cannot_map_the_api(anonymous):
    """A path that exists and one that does not answer identically without a credential."""
    assert anonymous.get("/metrics").status_code == 401
    assert anonymous.get("/admin").status_code == 401
    assert anonymous.get("/metrics").text == anonymous.get("/admin").text


def test_an_unresolved_path_is_denied_rather_than_waved_through(anonymous, api_credentials):
    r = anonymous.get("/nope/at/all", headers=bearer(api_credentials.scraper))
    assert r.status_code == 403


@pytest.mark.parametrize(
    "path",
    [
        "/runs/abc/confirm/",
        "/runs/abc/confirm//",
        "/METRICS",
        "/runs//confirm",
        "/runs/a%2Fb/confirm",
        "//runs",
    ],
)
def test_no_spelling_of_a_path_skips_the_scope_check(anonymous, api_credentials, path):
    """Every variant either resolves and is checked, or does not resolve and is denied.

    The trailing-slash forms are the reason this test exists. Starlette answers
    them with a 307 to the canonical path, and that redirect re-enters the
    middleware -- so a matcher that treated "unrecognised" as "unprotected"
    would let the first pass through unchecked.
    """
    r = anonymous.post(
        path,
        json={"approved": True, "input": "x"},
        headers=bearer(api_credentials.runner),
        follow_redirects=True,
    )
    assert r.status_code in (403, 404, 405), f"{path} answered {r.status_code}"


def test_the_canonical_confirm_path_is_reached_with_the_right_scope(anonymous, api_credentials):
    """The complement: the matcher is not simply refusing everything."""
    r = anonymous.post(
        "/runs/does-not-exist/confirm",
        json={"approved": True},
        headers=bearer(api_credentials.approver),
    )
    assert r.status_code == 404


# ================================================================ disclosure


def test_ready_tells_an_anonymous_caller_nothing_operational(anonymous):
    body = anonymous.get("/ready").json()
    assert body["ready"] is True
    assert body["provider"] == ""
    assert body["circuit"] == ""
    assert body["checks"] == {}


def test_ready_tells_an_authorised_caller_everything(anonymous, api_credentials):
    body = anonymous.get("/ready", headers=bearer(api_credentials.scraper)).json()
    assert body["provider"] == "stub"
    assert body["checks"]["repository"] is True


def test_ready_names_the_auth_mode_to_everyone(anonymous):
    """Not a secret from somebody who just reached it without a credential."""
    assert anonymous.get("/ready").json()["auth_mode"] == "enforced"


def test_the_disabled_mode_is_visible_in_readiness(monkeypatch, tmp_path):
    settings = _settings_with(monkeypatch, tmp_path, API_AUTH_MODE="disabled")
    platform = AgentPlatform(settings, repository=InMemoryRepository())
    try:
        with TestClient(create_app(platform, settings=settings)) as c:
            assert c.get("/ready").json()["auth_mode"] == "disabled"
            # And everything is reachable, which is what "disabled" means.
            assert c.post("/runs", json={"input": "What is the refund policy?"}).status_code == 200
    finally:
        platform.close()


def test_refusals_are_counted_without_naming_the_credential(anonymous, api_credentials):
    anonymous.post("/runs", json={"input": "x"})
    anonymous.post("/runs", json={"input": "x"}, headers=bearer(api_credentials.approver))
    body = anonymous.get("/metrics", headers=bearer(api_credentials.scraper)).text

    assert 'agent_auth_refusals_total{reason="no_header"} 1' in body
    assert 'agent_auth_refusals_total{reason="missing_scope"} 1' in body
    for key_id in api_credentials.ids.values():
        assert key_id not in body
    for token in (api_credentials.runner, api_credentials.approver):
        assert token not in body


def test_the_route_table_and_the_scope_table_are_one_thing(app):
    specs = app.state.route_specs
    assert {s.path for s in specs} == {r.path for r in app.routes}
    assert [s for s in specs if s.scope == PUBLIC and s.path not in ("/health", "/ready")] == []
