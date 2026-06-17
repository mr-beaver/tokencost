# 0001 — The proxy forwards the request body verbatim, except routing normalization

- Status: Accepted
- Date: 2026-06-17

## Context

TokenCost is a transparent proxy: the client (Claude Code, VS Code, OpenAI-compat
SDKs, …) builds a request, TokenCost forwards it to the upstream API, logs the
cost, and returns the response. Its one active intervention is **smart routing** —
rewriting `model` to a cheaper model for low-complexity prompts.

Historically the request path also mutated the body in several other ways: it
stripped `effort`, `thinking`, and `betas`, stripped `output_config.effort`,
injected `effort: "low"` for Haiku, and injected a top-level `cache_control`.
These were written against an older API revision. Against current Claude Code they
caused a series of 400s:

- stripping `thinking` orphaned the `clear_thinking_*` context-management strategy;
- stripping `thinking.budget_tokens` removed a now-required field;
- injecting a top-level `cache_control` collided with the client's block-level 1h TTL;
- forwarding the `context-1m` beta header to a downgraded (smaller-context) model
  was rejected as "long context beta not available".

Each was patched individually, but they shared one root cause: **the proxy mutated
parts of the request it had no reason to touch.** A naive "stop mutating anything"
rule is also wrong — some normalization is genuinely required by routing (the
cheaper target rejects attributes the original model accepted).

## Decision

The proxy forwards the client's request **body and headers verbatim**, with two
scoped exceptions:

1. **Routing normalization.** When routing rewrites `model` to a cheaper target,
   the proxy also strips attributes that target cannot accept:
   - the `context-1m` beta header (smaller-context models reject it);
   - `output_config.effort` / top-level `effort` **when routing to Haiku 4.5**,
     which returns 400 on the effort parameter. (Sonnet 4.6 accepts effort, so a
     Sonnet route keeps it.)
2. **Opt-in cache optimization.** A top-level `cache_control` is injected **only
   when the client set no `cache_control` anywhere** (top-level, system, tools, or
   message blocks). If the client manages its own caching, the proxy does not touch it.

Everything else the client sends — `thinking`, `budget_tokens`, `effort` on
non-downgraded requests, `betas`, sampling params — is forwarded unchanged.

## Consequences

- New API fields the client adopts pass through automatically; the proxy can't
  desync from the upstream API by stripping something that became required.
- Routing normalization is keyed to *what the target model rejects*, so adding a
  routing target means re-checking only its capability gaps (effort, context window).
- The cache-injection optimization still benefits clients that don't cache, without
  ever conflicting with clients that do.
- Cost figures remain notional API-list-price estimates (see `CONTEXT.md`); this
  ADR governs request mutation only, not cost accounting.
