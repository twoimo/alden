# Keep failed or replaced terminal history out of the current view

When automatic following was paused, the terminal retained a new page as pending.
A subsequent failed, malformed or rejected history query cleared the displayed
rows but retained that page and the unread badge. Resuming following restored
those old rows beside an error in TAIL-F, while INGEST described them as recent
data. The source could not establish that the pending page was still authorized
or current.

The terminal now clears displayed and pending rows, stream identity and the
unread badge together on an unclassified query failure. It retains the explicit
unavailable state until a fresh successful response. No stored history or source
records are deleted. A successful paused page remains available until following
resumes, as before.

The history request scope also includes whether the view is collection knowledge
or conversation memory. Switching to conversation memory invalidates an earlier
collection request even when its filter fields are identical. Its late response
cannot repopulate the conversation view.

Four new behavioral cases fail on 0.3.215 source: denied, malformed and rejected
history followed by resume, and a collection reply arriving after a same-query
conversation switch. All four pass after the correction, alongside the existing
successful pause/resume and stream-generation checks. The original Pro thesis
raised the pending-page failure on historical source 60003be; the source defect
was independently reproduced locally. This is a synthetic boundary test, not an
observed production incident or the completed external three-stage audit.

This correction does not expand the recent 80-row history query, turn history
into live source acquisition, activate messenger ingestion, or establish
semantic quality or source-to-combat latency. Those remain separate goal gates.
Signed packaging, installation, preserved configuration, read-only native
inspection and exact-head CI have separate private receipts under
`outputs/history-fence216-20261023/`.
