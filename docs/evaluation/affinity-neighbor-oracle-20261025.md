# Numerical neighbor oracle and remaining layout validation

The current sampler compares every admitted career node66–67 times, but uniform
access does not establish nearest-vector quality. A read-only export of the
current1,984-node page admitted the existing E5 vectors and source versions. An
offline float64 cosine oracle checked all1,967,136 permitted pairs. Current
product scores and the oracle agreed within1e-12. No inference or graph/source
writes were performed.

## Reproduction

Use an already provisioned Python with NumPy and an explicitly exported private
snapshot. This developer tool is outside the app resource allowlist. It never
installs dependencies or falls back to a different backend.

```sh
python3 -B scripts/evaluate_alden_affinity_neighbors.py \
  --backend numpy --snapshot PRIVATE/snapshot.json \
  --candidate partition=PRIVATE/projection_partition-candidates.json
```

The sibling `vectors.f32le`, snapshot hash, per-vector hashes, contiguous offsets,
dimension, unit norms, profile, IDs, project memberships, duplicate pairs and
candidate cosine values are checked. Input files must be stable, regular,
caller-owned files without symlink components. Bounds are2,048 nodes,8MiB of
vector bytes,4,096 candidate records per set and eight extra sets. Output contains aggregate measurements,
snapshot hash and backend version. It exposes no IDs or bodies and does not
re-perform live source admission. Keep the original exporter receipt.

The fresh-process affinity CI group runs seven new independent small tests for
exact rankings, ties, missing endpoints, absent eligible neighbors, disjoint
projects, duplicated/reversed or invalid pairs, malformed JSON and bound vector
bytes/norms/profile. Three owned broken variants are detected: removing duplicate
rejection, admitting disjoint projects into the denominator, and omitting
unreturned endpoints. This is bounded diagnostic validation, not a project-wide
mathematical proof.

## Current career page

| Candidate method | Scored pairs | Selected top3 incidences /5,952 | Selected incident nodes |
| --- | ---: | ---: | ---: |
| Current project tournament |65,536|144 (2.42%)|1,767|
| Experimental two-hop refinement |65,536|208 (3.49%)|1,747|
| Experimental projection order sweeps |64,874|281 (4.72%)|1,792|
| Experimental projection partitions |63,221|629 (10.57%)|1,802|
| Experimental partition + two-hop |63,221|642 (10.79%)|1,731|

All retain the existing1,024-pair return cap and endpoint-degree4 bound. Projection
arithmetic counts against the same25,165,824 scalar-product budget; the partition
proposal uses888,832 projection products and25,165,696 total. The last proposal
fails the predeclared incident-node criterion despite its higher numerical
recall. None has replaced the product sampler. The prototypes use precomputed
exact scores; their timings are not product generation or pair-scoring performance.

Current traversal sees193/5,952 top3 incidences before final allocation; the
returned144 show a second, intentional budget constraint. With5,952 eligible
directed incidences and at most1,024 undirected pairs, recall cannot exceed
2,048/5,952=34.4%, even if every selected endpoint is a true top3 hit. These are
nearest vectors, not independently judged related knowledge or corroborating
sources. Ties use a declared1e-12 cosine tolerance.

On the same current1,984-node/4,096-edge R graph and source engine, partition S
candidates changed world pairs below.07 from10→6 and fitted four-pixel pairs
285→274. Common-camera central-quarter fraction changed55.49%→54.99%, and shared
exact-neighbor endpoint top16 alignment2.59%→15.28%. However, the same-camera
central100px population increased69→73; fitted nearest-pixel median also fell
6.277→6.184. Central crowding is not solved. This cold-start comparison is not
producer/consumer wire acceptance, native rendering, physical input, semantic
gold or whole-app performance.

## Other current projects and next gates

The current `jangsin` export admitted26 vectors with zero ID or vector hash overlap
against career. Its325 permitted pairs are fully compared;42 selected edges
recover65/78 top3 incidences. This supports a small complete-comparison control,
not generalization of the large-page proposal.

The `sparkuniverse` check requested921 nodes but admitted no content vectors:
908 metadata descriptions,6 metadata-only records and7 source-metadata records
were explicitly excluded. The store is not empty. Do not promote descriptions
into verified content to manufacture an independent benchmark.

Next gates remain: a bounded implementation with real dot products and measured
projection/sort/proposal overhead, cancellation/RSS/CPU checks; larger independent
content pages and adversarial scopes; truthful sampling metadata and consumer
compatibility; installed rendering and incremental pins/camera; and independent
semantic/time/identity gold. Metadata-only projects need authorized original-body
acquisition. Current-source external Pro→Gemini→Pro review and normal release
remain incomplete under the earlier readiness hold.

## Research boundaries

The proposal borrows ideas from [RP trees](https://cseweb.ucsd.edu/~dasgupta/papers/rptree-stoc.pdf)
and [NN-Descent](https://www.cs.princeton.edu/cass/papers/www11.pdf). Sparse
deterministic partitions and bounded two-hop lists are experimental heuristics;
their measurements do not inherit those papers' algorithms or theorems.

High cosine values alone are not an embedding failure: the
[E5 model card FAQ](https://huggingface.co/intfloat/multilingual-e5-small/raw/main/README.md)
describes its compressed score range and emphasizes ordering. It recommends
`query:` for symmetric similarity/clustering. Alden reuses its declared retrieval
profile. Changing prefixes requires a separately versioned and evaluated profile;
no prefix, model or cache was silently changed.
