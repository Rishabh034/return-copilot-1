# Adding a conversational agent

An agent is a directory. Copy `app/agents/example/`, rename it, and nothing
outside your own directory needs to change — not the runner, not `main.py`, not
the message router.

```
app/agents/<your-agent>/
  __init__.py        exports SPEC
  spec.py            the contract — ~20 lines
  prompts.py         your system prompt and context note
  knowledge/*.md     your reference material
  tools/             one module per area, each with build_tools(context)
  presentation.py    optional — how your results are described
```

Mounting is automatic. `app/core/registry.py` imports every package under
`app/agents/` that exposes a module-level `SPEC`, and `main.py` mounts each one
at `/api/<url_prefix>/`. A duplicate `slug` or `url_prefix`, or a package with
no `SPEC`, fails at import rather than at 3am.

What you get for free, because it is all platform: conversations and history,
SSE streaming, the agentic loop, the scratchpad, per-user token quotas,
enterprise budget caps, the usage ledger, auth, admin surfaces, and Langfuse
tracing.

---

## The contract

```python
SPEC = AgentSpec(
    slug="returns",                  # internal id, stored on the conversation row
    url_prefix="returns",            # mounts at /api/returns/
    display_name="Returns Copilot",
    build_tools=create_tools,
    build_system_prompt=build_system_prompt,
    build_context_note=build_context_note,
    runtime=RuntimeSettings.from_settings(),
)
```

Everything else is optional: `presentation`, `tool_groups`, `output_filter`,
`clients`, `enabled`.

`slug` and `url_prefix` are separate only because the allocation agent is
called `allocation` internally while keeping its published
`/api/rr-log-analyst/` URL. Set both the same.

---

## Five things that will bite you

**Tool order is part of the cached prompt prefix.** The tools array is sent with
every request and is matched byte for byte by Anthropic's prompt cache.
Reordering `_TOOL_MODULES` invalidates the cache for every caller — a pure cost
regression that changes no behaviour and that no functional test will notice.
Append; do not insert.

**The system prompt carries the cache breakpoint, so it takes no arguments.**
`build_system_prompt()` is the stable half and is cached. Anything
request-specific — the date, the enterprise, the user — belongs in
`build_context_note()`, which is sent after the breakpoint. Put a username in
the cached half and every caller gets their own cache entry instead of one
shared across the organization.

**Your knowledge base is yours alone.** `load_knowledge()` globs the directory
your `prompts.py` points at, and caches per directory. Dropping a file into
another agent's `knowledge/` puts it in that agent's prompt and moves its cache
breakpoint.

**Tools declare their own behaviour.** A tool whose result can change between
identical calls — a simulation, a "run it again" action — must declare
`ToolPolicy(reusable=False)`, or the loop will serve the second call from the
first one's stored result. A tool that reads the evidence store rather than
fetching should declare `scratchpad=False`.

**Errors are structured, not strings.** Tools return a dict; a key named `error`
marks a failure. Raise `ClickpostPermissionError` / `ClickpostAPIError` and the
loop maps them to a `ToolErrorCode`, which decides three separate things: what
the engineer sees in the logs, what the model is told to do next, and the
sentence the user reads. Never put an endpoint path, a status code or upstream
text into the message the model sees — that is what `detail` is for, and it
never leaves the server.

---

## Optional: behaviour you declare rather than fork the loop for

**`output_filter`** — for an agent handling personal data. Applied to streamed
text *and* to the text that is persisted. Implement `filter_final(text)` and
declare `stream_hold_chars` larger than the longest pattern you can match;
anything smaller lets a match straddling a chunk boundary through. Filtering
only the stream is the classic mistake: the browser shows masked text while the
database keeps the original.

**`tool_groups`** — when at most N of a family of tools should run per turn.
The platform enforces the budget, skips the losers and tells the model why;
your `choose` function decides which one wins, because "prefer the aggregate,
else match the user's wording" is domain judgement the shared loop cannot hold.

**`runtime`** — `RuntimeSettings.from_settings()` inherits today's model and
limits. Override any field to diverge; an agent that should run cheap sets
`model` and `secondary_model` to Haiku and changes nothing for anyone else.

**`clients`** — name the services your tools call. The platform builds them once
and closes them on shutdown, so no agent edits `main.py`'s lifespan. Read them
in a tool via `context.clients["<name>"]`.

---

## Checklist

- [ ] `slug` and `url_prefix` are unique across `app/agents/`
- [ ] `build_system_prompt()` takes no arguments
- [ ] `knowledge/` files are numbered so their order is explicit
- [ ] Every tool has a description that says *when* to use it, not just what it does
- [ ] Any non-deterministic tool declares `ToolPolicy(reusable=False)`
- [ ] Scratchpad agents include `app.core.runtime.tools.scratchpad` in `_TOOL_MODULES`
- [ ] No endpoint paths, status codes or upstream text in anything the model sees
- [ ] `enabled=True` only when you actually want the URL live

---

## Testing

The replay and snapshot harness is not in this repository yet. Until it lands,
verify by hand before shipping:

- the agent is discovered and mounted — `registry.enabled()` lists it, and
  `/api/<url_prefix>/messages/` answers
- `GET /api/<url_prefix>/conversations/` lists only your agent's threads
- one question that needs a tool, one that does not, and one that fails a tool
- the conversation row records your `agent_slug`

When the harness does land, the point of it is that a spec is all it needs:
the fake model and fake tools are parameterized by `AgentSpec`, so an agent
inherits the whole suite by existing.
