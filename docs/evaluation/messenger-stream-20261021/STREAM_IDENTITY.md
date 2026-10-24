# Stable activity identity and current content refresh

The ordinary production reader used its strong current-content revision as
the activity namespace. Adding a message changed that revision and therefore
the stream ID. A consumer polling from its earlier cursor received `reset=true`
and no receipts, even when the exact new live message had been committed and
released. The integration counterexample reproduces that lost notification.

The production authority now supplies a separate `stream_revision`: enrollment
generation plus the existing account/room authorization, project permissions and
quality-policy scope. It remains stable when message contents grow within that
scope. Account/scope or journal replacement/rewind still requires a new baseline.
The existing `revision` remains a fresh batch of original content hashes; it
continues to guard every protected record and verify the read before completion.
Both callbacks are bound to the same declared state root. An unavailable or
malformed stream authority withholds protected records.

The graph/activity checkpoint additionally carries an opaque `content_revision`
when protected sources participate. A changed body, withdrawal or source failure
can change this token without replacing the journal. The UI requests a fresh
graph but preserves valid pending receipts. It displays a pulse only after the
current graph's token, document version and target match the receipt. Old
snapshots and partial responses cannot drop a declared content-token contract;
first adoption fences an older in-flight response. Hidden, stale, failed,
duplicate and expired activity keeps its existing handling and resource bounds.

| Change | Journal identity | Content token | Consumer action |
|---|---|---|---|
| New committed message in existing scope | Stable | Changes | Read receipt; refresh graph; match before effect |
| Original body changes before collection | Stable | Changes | Refresh/withhold stale content; no invented event |
| Account, permitted scope or journal changes | Changes | May change | Clear effects and establish baseline |
| No source/collection change | Stable | Stable | Preserve cursor; no activity |

Explicit legacy callbacks retain their old contract unless a separate stream
callback is supplied. Ordinary unprotected sources need not declare a content
token. New source fields are additive; this is not an atomic transaction across
the original database, frontier, index and collection store. Room-scope changes
may conservatively replace a baseline. Timing, source availability, refresh and
the existing effect TTL remain liveness conditions.

Tests cover delivery of a committed live addition, body changes without a new
journal event, account changes, foreign/invalid stream callbacks, strong content
withdrawal, pending snapshots, delayed polls and protocol adoption/downgrade.
Process-local variants that restore the data-bound namespace or weaken the
content guard fail their respective original counterexamples and pass after
restoration. These are bounded implementation checks, not a general proof.

## Correction to earlier activity evidence

An initial activity read without `after` is always a baseline: it intentionally
returns no items. Earlier private native receipts reporting `activity_items=0`
and the scheduling comparison's initial activity response were such baseline
reads. They do not prove that no readiness records existed, that backfill was
filtered, or that real combat had been exercised. Their raw receipts remain
historical evidence; their interpretation is narrowed here.

First-time backfill and live ingestion retain their actual `origin` values.
They must be distinguished when reporting source behavior; a first successful
stored change is not an idle/decorative event. Policy reprocessing stays a refresh
signal rather than a pulse. Current native incremental validation must establish
a baseline before collection, poll with the exact cursor/stream, and inspect
committed origins afterward. Original installed UI/combat and external review
remain separate gates.
