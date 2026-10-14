# Source tag candidate navigation

Alden 0.3.111 adds navigation from a retained-tag inspection to every admitted difference group and every member version. A group records exact declared-set differences on the same projected retained body; it remains a review candidate. No tag correction, identity decision or source merge is performed.

The first inspection scans the admitted sources once and stores a bounded private metadata-only review: project, body digest, difference field names and document/version IDs. Source text and tag strings are excluded. The first five groups describe that inspection snapshot. Later group pages check the current scoped metadata fingerprint; source pages additionally verify current membership permissions, expected version/projection, retained byte hash and full projected-body digest before returning at most five 4,000-character bodies with their original declaration states. The current store path/device/inode prevents replay through a copied root.

Pagination has an explicit total, offset and next offset, with five groups or records per page. Scope, store replacement, permissions, availability or current-version changes invalidate the cached review. Expiry is 30 minutes. Cache files have immutable hash names, owner/private-mode checks, a byte limit and a bounded file count; only exact-hash expired generated metadata is reclaimed. The collection tables, source blobs, identities, relationships and operating history remain unchanged. Cache writes are derived inspection metadata, not graph activity or learned facts.

The UI clears visible old raw records while checking a page and rejects a mismatched review ID, scope, cursor, response kind or partial page. Record content and tags are rendered as literal text, with an explicit unverified semantic notice. A group with identical first two declarations still permits the user to reach the third and later records. Existing general-quality and source-tag inspection timestamps remain separate.

Independent Web GPT-6/xhigh work wrote nine synthetic contract regressions in one test file. It found copied-root replay and system Python 3.9 compatibility failures; the parent added store binding and portable os.stat. Pinned-runtime and system-Python checks are separate from actual retained-store reads and owned native views.

An initial admitted review exposed 20 groups and 66 member versions. Its complete traversal made 28 requests with no group/member omission or duplicate in the observed store. This is current retained-source readback, not independent semantic gold or evidence that the declared tags are correct. Initial pre-binding evidence is retained separately; final bound review, installed API, native render, preservation and CI receipts determine delivery.

Evidence: task `outputs/annotation-candidates-20261014/`. Upstream tag generation, human meaning/identity/temporal evaluation, primary physical interactions, representative resource measurements and production signing/deployment remain open.

## Bounded source scan in 0.3.112

The 0.3.111 installed API and final native inspection both reached the existing 20-second tag scan budget under ambient load. A profile identified per-record parent-path checks and individual version queries. The 0.3.112 scan opens each ancestor with directory-relative `O_NOFOLLOW`, pins one directory descriptor for the request and rechecks its device/inode on successful completion. Each leaf still checks its name, `O_NOFOLLOW`/`O_NONBLOCK`, regular-file shape, byte limit, stable file metadata and SHA256. Exception paths close descriptors. The version rows are fetched by one scoped SQL join in the same read transaction, preserving missing-version findings, ordering, permissions and budgets.

One sequential profiled pair used the same pinned Python, admitted projects, 26,625 current versions and 43,586,907 examined bytes. Source findings, tag review, scope digest and all candidate/member metadata matched exactly. Wall time was 23.35s before and 10.67s after; process CPU time was 15.65s and 6.29s. SQL execute calls were 26,634 and 9; `is_symlink` calls were 266,260 and 10. Ambient machine load was uncontrolled and n=1 per condition. These are bounded scan measurements, not representative whole-app latency, GPU, battery, semantic quality or quota savings.

Optimization evidence and final 0.3.112 delivery gates are in task `outputs/annotation-scan-20261014/`; earlier failures and successful 0.3.111 observations remain intact in the candidate evidence.
