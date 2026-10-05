# The execution boundary

Two modes, and the platform is explicit about which one it is in.

**`inprocess`** — the default. Tools are called directly, as in every earlier
version. Offline, no secret, no subprocess.

**`mcp`** — tools run in a separate process behind a real MCP server, and every
call must carry a cryptographically verifiable grant.

```bash
# default
python -m agent_platform.api

# tools in their own process
TOOL_TRANSPORT=mcp EXECUTION_GRANT_SECRET=<key> python -m agent_platform.api
```

Transport is **stdio only** in this version. HTTP is deliberately out of scope:
it would put tool arguments on a network, which is a different security
conversation from a co-located subprocess. See *Not implemented*.

## Why a grant exists

`require_gateway()` proves a tool is being called from inside the gateway —
*within this process*. That is the right guarantee while a tool is a function in
the same interpreter, and no guarantee at all once the tool lives elsewhere: a
`ContextVar` in the orchestrator says nothing to a separate tool server, and
anything that could reach that server would otherwise execute without the policy
engine ever being consulted.

A grant is the same idea, expressed so it can cross that boundary.

```
v1.<base64url(payload)>.<base64url(hmac-sha256)>

payload = { v, eid, tool, adg, iat, exp, nonce }
```

It binds an authorisation to the thing that was authorised: **which tool**,
**which arguments** (by digest), **for how long**, and **once**.

**Not JWT, on purpose.** JWT carries its algorithm in the token, which is how
`alg=none` became a recurring vulnerability. There is one algorithm here, it is
not negotiable, and it is not named in the token. The version is — in the prefix
for a cheap reject *and* inside the signed payload, so it cannot be rewritten.

## What crosses, and what does not

The grant carries a digest and identifiers. It does **not** carry the arguments,
any credential, any tool output, or any request context. Something that reads a
grant learns that *an* action was authorised, not what it was about. A test pins
the payload's exact field set.

The argument digest reuses `guardrails.rules.action_fingerprint()` — the same
canonicalisation the platform already uses to bind *human confirmations* to a
specific action. That reuse is the point: a second canonicalisation would mean
two definitions of "the same action", and the gap between them is where an
approval for one thing authorises another.

## What the server checks

In order, and every one a refusal rather than a repair:

1. a grant is present, and is not hiding among the business arguments;
2. the signature verifies, in constant time;
3. the version matches, in both the prefix and the signed payload;
4. `iat`/`exp` are sane and the window is open;
5. the grant's tool matches the tool being asked for;
6. the digest recomputed from the *received* arguments matches;
7. the nonce has not been consumed — atomically, so two replicas cannot both
   be first;
8. the tool exists in **this process's** registry;
9. the arguments validate against the tool's own model.

Only then does it open `gateway_execution()` and call the handler.

**Every refusal returns the same message.** A server that explains which check
failed helps a caller iterate towards a forgery, and the caller can do nothing
differently for one reason versus another. The platform records the real reason
in its own events; the caller is told "execution refused".

## Two layers, not a replacement

| Layer | Mechanism | Scope |
|---|---|---|
| Cross-process | signed `ExecutionGrant` | between processes |
| In-process | `gateway_execution()` / `require_gateway()` | inside the executing process |

The ContextVar is unchanged and still required — all nineteen tools still open with
`require_gateway()`. The grant proves authorisation crossed the boundary; the
ContextVar proves nothing inside the tool server called a handler around the
side. Removing either would leave a real gap.

## `search` stays in this process

`search` is excluded from MCP dispatch, and the reason is worth stating rather
than discovering later.

Hybrid retrieval is selected by a provider bound to the request through a
`ContextVar`. A separate tool-server process has no such binding, so the same
search would fall back to lexical BM25 and return a worse answer **with nothing
to indicate it had** — the silent-degradation failure 7G.0 exists to prevent.

The alternative — shipping the provider credential to the tool server so it
could embed for itself — is worse, and forbidden: that process runs simulated
tools and has no business holding a real key.

It is also the honest boundary. `search` is not an external system; it is the
platform's own knowledge. What belongs behind MCP are the tools standing in for
systems the platform does not own.

## The secret

`EXECUTION_GRANT_SECRET`, from the environment, minimum 16 bytes. Held with
`repr=False` like the API key and the Redis URL. It never appears in a log, a
URL, an exception, an error message or a command line — it reaches the
subprocess by environment, and tests assert each of those.

**Missing secret in `mcp` mode stops construction.** A platform that starts and
then refuses every tool call looks like a policy problem; refusing to start says
what is actually wrong, once.

## The subprocess environment

Built from an **allow-list**, not by inheritance:

```
EXECUTION_GRANT_SECRET, REDIS_URL, PATH, PYTHONPATH, SYSTEMROOT
```

`GEMINI_API_KEY` is not forwarded, and a test asserts it. A deny-list would
silently start forwarding every new variable someone adds, and the first time
that matters is the first time it forwards a credential.

## Sync stays sync

The MCP SDK is async throughout; the platform is synchronous and stays that way.
Async is confined to `execution/transport.py`, exactly as it is confined to the
ASGI edge in the HTTP layer: one background thread owns an event loop, the loop
owns one long-lived session over one stdio subprocess, and synchronous callers
hand work to it with `run_coroutine_threadsafe`.

The session is long-lived deliberately — a client per call would launch and tear
down a process for every tool invocation.

## Limitations

- **stdio only.** No HTTP transport, so the tool server must be co-located.
- **Tool schemas are uniform on the wire.** Each tool is exposed as
  `(grant, arguments)` because the SDK derives schemas from Python signatures;
  the real JSON Schema is published in the tool's description and arguments are
  validated against the tool's own model server-side. Introspection is poorer;
  the security properties are not.
- **One shared secret**, not per-caller keys. There is one orchestrator and one
  tool server; a key per identity would be meaningful only once there were
  several identities.
- **The platform authenticates its callers** as of V2.6 (see docs/api.md), and
  a grant remains a separate mechanism: authentication says who is asking, a
  grant says which single action the policy engine allowed. Neither substitutes
  for the other.
- **Single-tenant**, unchanged.

## Not implemented

HTTP transport, mutual TLS, per-caller identity, key rotation, and running the
tool server on a different host. Each is a real step; none is pretended here.
