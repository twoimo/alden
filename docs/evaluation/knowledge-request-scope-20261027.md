# Requested knowledge scope, 2026-10-27

A known denied project's ID or alias could disappear during scope resolution:
`_voice_collection_projects` filtered denied memberships before matching names.
The caller then omitted the empty `projects` argument. The existing router
selected OSK and could return an unrelated permitted note. This was scope loss;
the reproduced cases never returned the denied original content.

Resolve all known requested projects before checking source permissions. The
collection retrieval layer continues to own visibility and source admission.
A denied-only request now retains a nonempty explicit scope and returns empty
collection results. Mixed scopes retain the requested project set and return
only permitted material. No permission is granted or changed. Unregistered
words keep existing unscoped behavior; no new project-name inference is added.

For a query q, the bounded invariant is: if its known requested set R(q) is
nonempty, route retrieval with R(q); return only facts admitted within R(q).
Denial may reduce visible facts to zero, but must not replace R(q) with another
store's default scope. This describes the tested implementation property,
not a general theorem or a proof of semantic alias disambiguation.

The focused test drives actual temporary CollectionStore, corpus and OSK
executors, with original-content canaries and provenance readback. Only
embedding configuration/model calls and network are replaced/blocked. It
checks denied ID/label/alias/follow-up, allowed and mixed scopes, shared labels,
NFC/NFD and fullwidth distinctions, explicit no-search/storage, and unchanged
default lookup. Source hashes remain equal; network and embedding calls are
zero. Baseline 9530b70 had nine tests: five passing methods and five failure
entries across four methods. The final focused regression has eleven tests.

```sh
python3 -B -m unittest tests.test_alden_knowledge_request_scope tests.test_alden_voice_retrieval tests.test_alden_collection_retrieval -v
python3 -B tests/test_alden_knowledge_request_scope.py --fixture
```

Project evidence lives in `outputs/local-runtime-tool-frontier-20261027/`.
Native Astra identified this as a prerequisite for a real local-model knowledge
tool loop. Supplemental OpenCodex Argon reviewed the exact SQL candidate,
reported no concrete defect, and identified partial-source coverage limits;
the maintained real-executor regressions cover those bounded paths. Neither
review is an independently executed test or the required external web audit.

This change fixes the prerequisite scope boundary. It does not implement the
proposed two-hop model/tool loop, establish real model tool choice, or prove
installed voice/UI behavior. The full Alden goal and existing model, source
currentness, app-OFF, external audit and normal release holds remain open.
