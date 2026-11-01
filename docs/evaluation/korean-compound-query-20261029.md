# Korean compound-particle lexical queries

Queries such as `별무리에서는` previously retained that word and the legacy
`별무리에서` alternative, but could miss an original containing only `별무리`.
The lexical query builder now appends roots for six suffixes: `에서는`, `에서도`,
`에게는`, `에게도`, `에서`, and `에게`. It removes only one suffix and requires
at least two Unicode codepoints in the remaining root.

Every legacy term remains in the same order. New, deduplicated roots use only
spare positions within the existing 24-term limit. NFC normalization, numbers,
negation, original words, source identities, project permissions, evidence,
dense queries, and the graph page's literal filter retain their contracts.
This is a lexical alternative; it does not establish semantic or entity identity.

Validation before release of independent reference outcomes:

- New generated regressions: 28 methods and 173 subtests pass across the term
  builder, SQLite candidates, collection search, retrieval, and MCP.
- Existing Unicode tests: 11 pass. Related retrieval, original-text, authority,
  context-duplicate and voice-reference tests: 284 pass. The initial parent
  deny-all transport guard blocked one existing ephemeral socket fixture;
  rerunning only that test with its owned listener allowed passed. No product
  or existing test change was needed.
- 1,400 generated combinations retain the exact legacy term prefix, cap,
  deduplication and NFC/NFD behavior. This does not prove unchanged BM25 ranks.
- The new module is included in the explicit focused CI test list.

A separate source-only annotator froze 12 questions and 31 supporting quotes
from six retained originals before candidate development. Baseline outcomes
remain sealed until the candidate source is fixed. Exact-head CI and a fresh
retained-state copy are required before the candidate comparison. The old
exposed reference set is not reused. These author-selected references are not
human-adjudicated or representative semantic gold, and the phase directory
name could have cued the annotator's topic.

Alden remains off. Local source tests do not prove installed behavior, actual
model answer quality, voice-session behavior, performance or release readiness.
