# TokenCost — Context & Glossary

A glossary of the domain language used in this project. Definitions only — no
implementation detail. When a term here conflicts with how the code or a
conversation uses a word, that conflict gets resolved here first.

## Glossary

### Cost (notional / API-equivalent cost)
The dollar figure TokenCost reports is **notional API-equivalent cost**: what the
observed token usage *would* cost if billed at Anthropic's published per-token
**API list prices**. It is a usage-intensity and efficiency signal, **not** a
record of money actually spent.

This matters because TokenCost is a passive proxy in front of whatever client
sent the request. When that client authenticates with a **subscription** (see
*Auth mode*), the user's real marginal cost is zero — the API-equivalent figure
is still meaningful as an intensity proxy, but it is not their bill. The figure
is therefore always labelled as estimated/API-equivalent, never as "spend".

Consequence: cost is computed from uniform per-model list prices. It does **not**
branch on subscription tier (Pro/Max/Team) or auth mode — per-token API prices
do not vary by tier, and notional cost is defined in API-list terms regardless of
how the user is actually billed.

### Auth mode
How the upstream Claude client authenticates, which determines real billing:
- **Subscription** (Pro / Max / Team, via OAuth) — flat monthly fee plus usage
  *limits*; per-token marginal cost is effectively **$0**.
- **API key** — metered per-token at list price; the API-equivalent *Cost* equals
  real spend.

TokenCost does not reliably know which mode the client is in, which is why *Cost*
is defined notionally rather than as actual spend.

### Cache write TTL (5-minute vs 1-hour)
A cached prompt prefix can be written with one of two lifetimes. The two are
priced differently against base input price, so they are distinct cost events:
- **5-minute write** — 1.25× base input price.
- **1-hour write** — 2× base input price.
- **Cache read** (either TTL) — ~0.10× base input price.

The TTL is chosen by the client (e.g. Claude Code writes its system/tool prefix
at 1-hour TTL); TokenCost observes it, it does not set it.
