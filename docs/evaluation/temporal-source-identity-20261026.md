# Preserve source identity when selecting latest observations

A `Mercury latest` search could discard records from one publisher because
another publisher used the same alias and had a later publication date.
Publisher names had origin scopes, but document titles and metadata aliases did
not. A second boundary also included memberships pointing to another version
of the same document, which could create a false shared origin.

Collection retrieval now binds the title and the first 32 aliases (the existing
selector limit) to authorized targets holding the selected version. The existing
ambiguous-origin rule retains the evidence when those scopes do not overlap.
Same-source latest selection, history, pinned records, permission filtering,
publication-date basis and source integrity checks retain their contracts.
A common source is a comparison context, not proof of entity identity or truth.

Four public primary-source excerpts from two distinct Mercury publisher origins
supply six offline reference cases. Visible publication dates and literal
excerpts were checked against captured HTML; the fixture retains URLs and
capture hashes. The unchanged baseline passes four cases and the correction
passes all six. Three generic title/alias/version regressions also cover the
underlying failure without depending on the public wording.

The initial reference oracle incorrectly expected two insurance articles dated
October20 and October22 to have a strict order. Their dates do not attest times
or timezones: the possible intervals can overlap across UTC-14 and UTC+14. Both
are correctly retained. The original failed expectation and the corrected
baseline are preserved separately; the product was not changed for this case.

The affected suite passes 178 tests with no skips. Isolated original-source
replay reproduces the failure; four executed mutations (missing title/alias
binding, missing version binding, ignored origin ambiguity, and retaining
everything) are detected. The real checkout was never mutated by these controls.
The public replay leaves its isolated collection database unchanged and creates
no embeddings. It does not read or write the operational store.

Run from the repository with Python3.11 and SQLite FTS5:

```sh
PYTHONPATH=scripts python3 -B -m unittest tests.test_alden_collection_retrieval.CollectionRetrievalTests.test_public_source_temporal_identity_reference
```

These author-selected source-backed cases are not independent human adjudication
or representative private semantic/time/contradiction/entity gold. Source
publication is not proof of current claim validity. OSK canonical-note identity,
installed/native behavior, current-source external review and release remain
open. Alden stays off; no installation, permission, service or model setting was
changed. This source delta does not make an earlier candidate current.

Primary references: [Mercury financing announcement](https://mercury.com/blog/series-c-announcement),
[Mercury charter application announcement](https://mercury.com/blog/occ-national-bank-charter-application),
[Mercury Insurance October20 announcement](https://newsroom.mercuryinsurance.com/2026-10-20-First-Rain,-Hidden-Danger-Mercury-Insurance-Warns-Drivers-About-Slippery-Roads-as-El-Nino-Strengthens),
[Mercury Insurance October22 announcement](https://newsroom.mercuryinsurance.com/2026-10-22-Before-You-Hand-Down-That-Old-Phone,-Make-Sure-Youre-Not-Giving-Away-Your-Personal-Information).

Machine-readable scope and evidence: `temporal-source-identity-20261026.json`.
