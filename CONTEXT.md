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
