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

### Event Candidate Extraction

The Match Unification work that turns raw bookmaker events, normalized odds, and
normalized outcome offers into event candidates before grouping. It owns source
matching, raw source metadata, candidate precedence, football raw event candidate
enrichment, and extraction benchmark counters.

### Event Pairing

The Match Unification work that compares bookmaker-specific event candidates
within a temporal bucket to decide whether they describe the same real-world
event. It owns sport-specific pairing rules, team text similarity, orientation
scoring, pair ranking, large-bucket fan-out diagnostics, and reusable similarity
state that prevents repeated fuzzy scoring inside one scrape cycle.
