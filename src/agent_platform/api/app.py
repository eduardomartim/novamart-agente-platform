"""The HTTP boundary.

This module adds a transport. It does not add a second way to do anything.

Every route ends in a call to :class:`~agent_platform.platform.AgentPlatform`,
which is the same object the CLI and the dashboard drive. There is no route that
reaches a tool, the registry, or the gateway directly, and the API holds no
authority of its own: it cannot allow an action the policy engine denied, cannot
skip a confirmation, and cannot spend past a budget. It parses, it calls, it
serialises the result.

**Where async begins and ends.** Reading a request body is asynchronous in ASGI
and cannot be done from a synchronous handler, so the route functions are
``async def``. That is the entire extent of it: each one awaits the body, then
hands every decision to a plain synchronous function through
``run_in_threadpool``. The core stays exactly as it was. Turning 12,000 lines of
working synchronous code inside out to satisfy a transport would be the tail
wagging the dog.

That threadpool is safe here for a reason worth stating rather than assuming:
every piece of shared mutable state these handlers reach -- the rate limiter,
the circuit breaker, the budget ledger, the pending-confirmation registry -- is
already guarded by a ``threading.Lock`` in the V1 code.
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Final, TypeVar

from pydantic import BaseModel, ValidationError
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Route

from .. import __version__
from ..config import Settings
from ..observability import logging as structured
from ..observability.exposition import Counters, Histogram, render
from ..platform import AgentPlatform
from ..security.api_auth import (
    SCOPE_CONFIRM_WRITE,
    SCOPE_METRICS_READ,
    SCOPE_RUNS_WRITE,
    AuthConfigError,
    AuthError,
    Keyring,
    Principal,
    current_principal,
    parse_credentials,
    principal_from_header,
    principal_scope,
)
from .schemas import (
    ConfirmRequest,
    ErrorResponse,
    HealthResponse,
    ReadyResponse,
    RunRequest,
    http_status_for,
    run_response_from,
)

logger = logging.getLogger("agent_platform.api")

M = TypeVar("M", bound=BaseModel)


def _json(
    model: BaseModel, status_code: int = 200, headers: dict[str, str] | None = None
) -> JSONResponse:
    return JSONResponse(
        model.model_dump(mode="json"), status_code=status_code, headers=headers
    )


def _error(
    error: str, detail: str, status_code: int, headers: dict[str, str] | None = None
) -> JSONResponse:
    return _json(ErrorResponse(error=error, detail=detail), status_code, headers)


class _BadRequest(Exception):
    """A body that never became something the platform could be asked about."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


def _parse(raw: bytes, model: type[M]) -> M:
    """Validate a JSON body, or raise :class:`_BadRequest`.

    Malformed JSON and a body that violates the schema are the same class of
    problem from the caller's side -- neither produced a request the platform
    could act on -- so both are 400.
    """
    try:
        payload: Any = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise _BadRequest(f"body is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise _BadRequest("body must be a JSON object")
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        raise _BadRequest(_first_validation_message(exc)) from exc


def _first_validation_message(exc: ValidationError) -> str:
    """One readable sentence naming the offending field.

    The caller learns which field is wrong and why, and nothing about the
    internals that decided it.
    """
    errors = exc.errors()
    if not errors:  # pragma: no cover - pydantic always populates this
        return "request body failed validation"
    first = errors[0]
    location = ".".join(str(p) for p in first.get("loc", ())) or "body"
    if location == "actor" and first.get("type") == "extra_forbidden":
        # Worth a sentence of its own. "Extra inputs are not permitted" tells a
        # caller that something changed but not what to do, and this field was
        # part of the published contract until it turned out to be a hole.
        return (
            "actor: no longer accepted. The approver is taken from the "
            "authenticated credential, not from the request body."
        )
    return f"{location}: {first.get('msg', 'is invalid')}"


def _guarded(work: Callable[[], Response]) -> Response:
    """Run handler logic, converting anything unexpected into a safe 500.

    The platform sanitises the error text it produces. A bug in *this* module
    carries no such guarantee, and a traceback would disclose paths and internal
    structure, so nothing but a type name escapes.
    """
    try:
        return work()
    except _BadRequest as exc:
        return _error("bad_request", exc.detail, 400)
    except Exception as exc:
        logger.exception("unhandled error in API handler")
        return _error("internal_error", f"the request failed ({type(exc).__name__})", 500)


# ------------------------------------------------------------ authentication

#: The scope value marking a route that needs no credential.
#:
#: Safe as a plain string because ``parse_credentials`` refuses any scope
#: outside ``KNOWN_SCOPES``, and this is not one of them: no principal can ever
#: hold "public", so it cannot be satisfied by a credential.
PUBLIC: Final[str] = "public"

#: Returned for every authentication failure, whatever caused it.
#:
#: A verifier that says *which* check failed helps somebody iterate towards a
#: valid credential, and the caller can do nothing differently for one reason
#: versus another. The reason is logged and counted for the operator; it does
#: not cross the network. Same rule as ``GrantError("grant refused")``.
_UNAUTHORIZED_DETAIL: Final[str] = "a valid credential is required"
_FORBIDDEN_DETAIL: Final[str] = "this credential may not perform that action"


@dataclass(frozen=True, slots=True)
class RouteSpec:
    """One route, and the authority needed to reach it.

    ``scope`` has no default. That is the point of this type: a route cannot be
    added without deciding what it requires, because leaving it out is a
    ``TypeError`` at import rather than an omission somebody has to notice in
    review.

    The Starlette route table and the middleware's path matcher are both built
    from these, so they cannot come to disagree about which paths exist -- which
    matters because middleware runs *before* routing and cannot ask Starlette
    what it is about to match.
    """

    path: str
    methods: tuple[str, ...]
    endpoint: Any
    scope: str


def _compile_path(path: str) -> re.Pattern[str]:
    """Turn ``/runs/{request_id}/confirm`` into a matcher for the same paths.

    Derived from the same string Starlette compiles, and applied to the same
    ``scope["path"]`` Starlette routes on -- already percent-decoded, so
    ``/runs/a%2Fb/confirm`` arrives as ``/runs/a/b/confirm`` and is rejected by
    both for the same reason.
    """
    parts = re.split(r"(\{[^}]+\})", path)
    body = "".join(r"[^/]+" if part.startswith("{") else re.escape(part) for part in parts)
    return re.compile(f"^{body}$")


def _resolve(path: str, table: tuple[tuple[re.Pattern[str], RouteSpec], ...]) -> RouteSpec | None:
    """Which route a path belongs to, or ``None``.

    Trailing slashes are stripped before the second attempt because Starlette
    answers ``/runs/x/confirm/`` with a 307 to the canonical path, and that
    redirect re-enters this middleware. Matching both forms means the scope
    check happens on the first pass instead of relying on the redirect to bring
    the request back -- and being *more* permissive than the router is the safe
    direction: at worst a scope is demanded for a path that then 404s.

    ``None`` means deny. It never means "no scope required": an unresolved path
    treated as unprotected is precisely the bypass the trailing-slash redirect
    would deliver.
    """
    candidate = path.rstrip("/") or "/"
    for pattern, spec in table:
        if pattern.match(path) or pattern.match(candidate):
            return spec
    return None


def build_keyring(settings: Settings) -> Keyring | None:
    """Resolve the configured credentials, or refuse to start.

    ``None`` means authentication is off, which requires ``API_AUTH_MODE`` to
    say so out loud. Absent configuration is **not** a way to get there: the
    process fails to start instead, for the same reason an unreachable
    ``REDIS_URL`` raises rather than falling back to local state. A security
    control that switches itself off when its configuration is missing is a
    control that is off on the day it matters, with nothing in the logs.
    """
    mode = settings.api_auth_mode
    if mode not in {"enforced", "disabled"}:
        raise AuthConfigError(
            f"API_AUTH_MODE must be 'enforced' or 'disabled', got {mode!r}"
        )

    inline = settings.api_auth_keys
    path = settings.api_auth_keys_file
    if inline and path:
        raise AuthConfigError(
            "API_AUTH_KEYS and API_AUTH_KEYS_FILE are both set. Ambiguous "
            "configuration about credentials is the kind that resolves wrongly "
            "in silence; set exactly one."
        )

    if mode == "disabled":
        if inline or path:
            raise AuthConfigError(
                "API_AUTH_MODE=disabled but credentials are configured. Either "
                "the credentials or the mode is a mistake, and guessing which "
                "is not this code's job."
            )
        logger.warning(
            "API authentication is DISABLED: anything that reaches this port can "
            "submit requests and approve suspended high-risk actions",
            extra={"event": "auth_disabled"},
        )
        return None

    if inline:
        text = inline
    elif path is not None:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            # The path, not the contents: a file that cannot be read has no
            # contents to leak, and the operator needs to know which path.
            raise AuthConfigError(
                f"could not read API_AUTH_KEYS_FILE at {path}: {type(exc).__name__}"
            ) from exc
    else:
        raise AuthConfigError(
            "no API credentials are configured. Set API_AUTH_KEYS_FILE (or "
            "API_AUTH_KEYS), or set API_AUTH_MODE=disabled to run without "
            "authentication deliberately. Refusing to serve an unauthenticated "
            "API by default."
        )

    return Keyring(parse_credentials(text))



# ------------------------------------------------------------------ handlers


async def _health(request: Request) -> Response:
    """Liveness: is this process running?

    It touches nothing -- no database, no provider, no registry. A liveness
    probe that checks dependencies turns a dependency blip into a simultaneous
    restart of every replica, converting a degradation into an outage.
    Dependencies belong in readiness.
    """
    return _json(HealthResponse())


def _readiness_sync(
    platform: AgentPlatform, *, detailed: bool, auth_mode: str
) -> Response:
    """Readiness: can this instance take work right now?

    Checks only what exists in V2.0 and is genuinely instance-local -- the
    repository answers, and the tool registry is populated.

    Provider health is **reported but not gated on**, deliberately. Every
    replica shares one provider, so failing readiness when the circuit opens
    would pull every replica out at once and replace informative, governed
    responses with an unreachable service. The budget is the same kind of fact:
    an exhausted budget still serves stub traffic and still returns correct
    refusals, so it is worth reporting and not a reason to leave the rotation.
    """
    checks: dict[str, bool] = {}
    detail: str | None = None

    try:
        platform.repository.metrics_summary()
        checks["repository"] = True
    except Exception as exc:
        checks["repository"] = False
        detail = f"repository unavailable: {type(exc).__name__}"

    try:
        checks["registry"] = len(platform.registry) > 0
    except Exception as exc:  # pragma: no cover - registry is a plain container
        checks["registry"] = False
        detail = detail or f"registry unavailable: {type(exc).__name__}"

    # Shared state, when there is any. A replica whose Redis is unreachable can
    # still answer /health -- the process is fine -- but it cannot honour a
    # shared rate limit, resume a confirmation raised elsewhere, or refuse a
    # replayed grant. Serving traffic in that state would mean silently
    # enforcing weaker limits than the operator configured, so the pod leaves
    # the Service until the backend answers again.
    if platform.shared_state.is_shared:
        try:
            checks["shared_state"] = bool(platform.shared_state.backend.ping())
        except Exception as exc:
            checks["shared_state"] = False
            detail = detail or f"shared state unavailable: {type(exc).__name__}"

    ready = all(checks.values())
    # The verdict is public; the operational detail is not. A kubelet probe
    # sends no credential and needs only the status code, whereas the provider
    # name and the circuit state are free reconnaissance for anyone who can
    # reach the port. So the route stays public and the body narrows.
    body = (
        ReadyResponse(
            ready=ready,
            auth_mode=auth_mode,
            checks=checks,
            provider=platform.provider.name,
            circuit=platform.circuit.state,
            detail=detail,
        )
        if detailed
        else ReadyResponse(ready=ready, auth_mode=auth_mode)
    )
    return _json(body, 200 if ready else 503)


def _metrics_sync(platform: AgentPlatform, counters: Counters, duration: Histogram) -> Response:
    """Prometheus exposition.

    The repository summary is read here rather than cached, because it is a
    per-replica view: SQLite lives in a pod-local volume, so each pod reports
    what it handled and Prometheus aggregates. No single pod's numbers are the
    whole picture, which is the correct model but worth knowing when reading a
    dashboard.
    """
    summary: dict[str, Any] | None
    try:
        summary = dict(platform.repository.metrics_summary())
    except Exception:
        summary = None

    body = render(
        counters,
        duration,
        summary=summary,
        build={
            "version": __version__,
            "provider": platform.provider.name,
            "transport": platform.settings.tool_transport,
            "state_backend": platform.shared_state.kind,
        },
    )
    return PlainTextResponse(body, media_type="text/plain; version=0.0.4")


def _create_run_sync(
    platform: AgentPlatform, raw: bytes, quota_key: str | None = None
) -> Response:
    """Hand a request to the platform and return what it produced.

    The platform is asked exactly the way the CLI asks it. Rate limiting, input
    security, routing, policy, the gateway, confirmations and output security
    all happen inside ``run()``. None of them is reimplemented here, and none of
    them can be skipped from here.
    """
    body = _parse(raw, RunRequest)
    result = platform.run(body.input, quota_key=quota_key)
    return _json(run_response_from(result), http_status_for(result.status))


def _confirm_sync(
    platform: AgentPlatform, request_id: str, raw: bytes, actor: str
) -> Response:
    """Resolve a suspended action.

    This endpoint exists because suspension is a first-class outcome of the
    platform, not an edge case. Without it the API could *create* pending
    confirmations and never resolve them, leaving every high-risk action
    permanently unreachable over HTTP and making the API strictly less capable
    than the CLI.

    The body carries only the decision. Which action it applies to is rebuilt by
    the platform from its own state, so approving one action cannot execute
    another.

    **``actor`` is supplied by the caller of this function, never by the body.**
    It comes from the verified credential. The audit record already carries
    ``source``, so a reader can tell an attested identity (``api``) from an
    asserted one (``cli``); that distinction only became worth anything once
    this value stopped being whatever the request said it was.
    """
    body = _parse(raw, ConfirmRequest)

    if not platform.has_pending_confirmation(request_id):
        # Separate "aged out" from "never existed" so the caller learns which
        # happened. An already-consumed confirmation is indistinguishable from
        # one that never existed, which is correct: both mean there is nothing
        # to approve, and neither may execute anything.
        if platform.confirmation_expired(request_id):
            return _error(
                "confirmation_expired",
                "That action expired before it was confirmed and was not carried out.",
                409,
            )
        return _error(
            "not_found", "There is no suspended action with that request id.", 404
        )

    result = platform.confirm(
        request_id, approved=body.approved, actor=actor, source="api"
    )
    return _json(run_response_from(result), http_status_for(result.status))


# ------------------------------------------------------------------ assembly


def create_app(
    platform: AgentPlatform | None = None,
    *,
    settings: Settings | None = None,
) -> Starlette:
    """Build the ASGI application around a platform.

    Accepting a platform is what lets the tests drive the real object -- with an
    in-memory repository and a stub provider -- over the real HTTP stack, rather
    than mocking the thing under test.
    """
    owned = platform is None
    resolved = settings or (platform.settings if platform is not None else None)
    if resolved is None:
        resolved = Settings.from_env()
    app_platform = platform or AgentPlatform(resolved)

    # Before anything is served. A misconfiguration here must stop the process
    # rather than produce an application that answers requests without checking
    # who is asking.
    keyring = build_keyring(resolved)
    auth_mode = "enforced" if keyring is not None else "disabled"

    # Per-app, not module-level. Two apps in one process -- which is what the
    # tests do -- must not accumulate into each other's counters.
    counters = Counters()
    duration = Histogram()

    async def ready(request: Request) -> Response:
        principal = current_principal()
        detailed = principal is not None and principal.may(SCOPE_METRICS_READ)
        return await run_in_threadpool(
            _guarded,
            lambda: _readiness_sync(
                app_platform, detailed=detailed, auth_mode=auth_mode
            ),
        )

    async def metrics(request: Request) -> Response:
        return await run_in_threadpool(
            _guarded, lambda: _metrics_sync(app_platform, counters, duration)
        )

    async def create_run(request: Request) -> Response:
        raw = await request.body()
        started = time.perf_counter()
        # Quota is charged to the principal, not to the credential: issuing
        # yourself a second key must not double your allowance.
        principal = current_principal()
        quota_key = principal.name if principal is not None else None
        response = await run_in_threadpool(
            _guarded, lambda: _create_run_sync(app_platform, raw, quota_key)
        )
        duration.observe(time.perf_counter() - started)
        # The label is the HTTP class, not the body's status: a closed set of
        # five values. Labelling by anything a caller influences would let a
        # caller mint unbounded series.
        counters.increment("agent_requests_total", status=f"{response.status_code // 100}xx")
        return response

    async def confirm_run(request: Request) -> Response:
        raw = await request.body()
        request_id = request.path_params["request_id"]
        principal = current_principal()
        # "unauthenticated" rather than a friendly default: when the platform
        # runs with authentication off, the audit record should say so in the
        # field a reader looks at, not imply an operator who never existed.
        actor = principal.name if principal is not None else "unauthenticated"
        counters.increment("agent_confirmations_total")
        return await run_in_threadpool(
            _guarded, lambda: _confirm_sync(app_platform, request_id, raw, actor)
        )

    @asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncIterator[None]:
        # Only a platform this factory built is a platform this factory may
        # close. One passed in belongs to the caller -- closing it would shut
        # down a gateway and a repository the caller is still using.
        try:
            yield
        finally:
            if owned:
                app_platform.close()

    async def correlate(request: Request, call_next: Any) -> Response:
        """Give every request an id, and hand it back.

        An inbound ``X-Request-ID`` is honoured so a caller can stitch its own
        logs to ours, but it is bounded and stripped of anything that is not a
        safe identifier character -- it ends up in log output, and an
        unvalidated header that reaches a log is a log-injection vector.

        With several replicas behind one Service this id is what makes a request
        followable: the client sees it, and every line about that request
        carries it, whichever pod produced it.
        """
        supplied = request.headers.get("x-request-id", "")
        safe = "".join(c for c in supplied if c.isalnum() or c in "-_")[:64]
        correlation = safe or f"req-{uuid.uuid4().hex[:12]}"

        structured.bind_request(correlation)
        try:
            response: Response = await call_next(request)
        finally:
            structured.bind_request(None)
        response.headers["X-Request-ID"] = correlation
        return response

    # One literal. The Starlette route table and the middleware matcher are
    # both derived from it, so there is no second place to keep in sync and no
    # way to add a route without deciding what authority it needs.
    specs: tuple[RouteSpec, ...] = (
        RouteSpec("/health", ("GET",), _health, PUBLIC),
        RouteSpec("/ready", ("GET",), ready, PUBLIC),
        RouteSpec("/metrics", ("GET",), metrics, SCOPE_METRICS_READ),
        RouteSpec("/runs", ("POST",), create_run, SCOPE_RUNS_WRITE),
        RouteSpec(
            "/runs/{request_id}/confirm", ("POST",), confirm_run, SCOPE_CONFIRM_WRITE
        ),
    )
    table = tuple((_compile_path(spec.path), spec) for spec in specs)

    def _refused(reason: str, status: int) -> Response:
        counters.increment("agent_auth_refusals_total", reason=reason)
        logger.warning(
            "credential refused", extra={"event": "auth_refused", "reason": reason}
        )
        if status == 401:
            return _error(
                "unauthorized",
                _UNAUTHORIZED_DETAIL,
                401,
                {"WWW-Authenticate": "Bearer"},
            )
        return _error("forbidden", _FORBIDDEN_DETAIL, 403)

    async def authenticate(request: Request, call_next: Any) -> Response:
        """Establish who is calling, and whether they may ask for this.

        Middleware rather than a decorator per route. Opt-in authentication
        fails by omission -- a route added later without it -- and opt-out
        fails by requiring somebody to write down that a route is public. Only
        one of those failures is visible in a diff.

        This runs *before* routing: ``request.scope["route"]`` does not exist
        yet and ``path_params`` is empty, which is why the matcher above has to
        exist at all.
        """
        if keyring is None:
            unchecked: Response = await call_next(request)
            return unchecked

        spec = _resolve(request.scope.get("path", ""), table)
        header = request.headers.get("authorization", "")

        principal: Principal | None = None
        if header:
            # Offering a credential means it has to be a good one, on every
            # route including the public ones. Public means no credential is
            # required, not that a bad one is overlooked.
            try:
                principal = principal_from_header(header, keyring)
            except AuthError as exc:
                return _refused(exc.reason, 401)

        if spec is None:
            # Default-deny. An unresolved path is never "no scope required", so
            # an anonymous caller cannot map the API by watching which paths
            # answer 404 and which answer something else.
            return _refused("unknown_path", 401 if principal is None else 403)

        if spec.scope != PUBLIC:
            if principal is None:
                return _refused("no_header", 401)
            if not principal.may(spec.scope):
                return _refused("missing_scope", 403)

        with principal_scope(principal):
            response: Response = await call_next(request)
        return response

    app = Starlette(
        routes=[
            Route(spec.path, spec.endpoint, methods=list(spec.methods))
            for spec in specs
        ],
        lifespan=lifespan,
        # Correlation on the outside, so a refused request still carries an
        # X-Request-ID. A burst of 401s is not diagnosable without one.
        middleware=[
            Middleware(BaseHTTPMiddleware, dispatch=correlate),
            Middleware(BaseHTTPMiddleware, dispatch=authenticate),
        ],
    )
    app.state.platform = app_platform
    app.state.counters = counters
    app.state.duration = duration
    app.state.route_specs = specs
    app.state.auth_mode = auth_mode
    return app


__all__ = ["PUBLIC", "RouteSpec", "build_keyring", "create_app"]
