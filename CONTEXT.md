# KvotoLovac Context

## Domain terms

### Match Unification

The backend work that turns bookmaker-specific raw events and outcome offers into
canonical resolved events, oriented market outcomes, diagnostics, and review cases.
It includes raw event bucketing, team similarity, event resolution, orientation,
outcome attribution, and persistence of the resolved event graph.
If Match Unification fails after a scrape snapshot is persisted, the scrape cycle
may continue with match_id-only opportunity analysis, but the fallback must be
visible in cycle result, status, and benchmark data.
Benchmark fields for this work should use Match Unification language rather than
legacy event-resolver naming.

### Player Identity Resolution

The Match Unification work that resolves bookmaker-specific player labels inside
a resolved event. It produces event-scoped player identities, binds player prop
odds to those identities, and reports odds that cannot be event-scoped because
the resolved event membership is missing or the row is not a supported player
market.

The previous standalone event-player resolver naming is retired; player identity
belongs to Match Unification because it depends on the resolved event graph.
