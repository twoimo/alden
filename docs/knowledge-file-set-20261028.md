# Explicit original-file collection

The `file-set` adapter reads original documents named by a finite manifest. It
keeps each file's stable identity, parsed structural positions, source version
and exact original bytes. The existing `spark-index` adapter continues to read
derived metadata; an index row is not evidence that its original file was read.

This is a source implementation. The installed desktop app and the earlier
`c0b5d88` candidate do not contain it. Alden remains off during this phase.

## Declare the source

Create a UTF-8 JSON manifest. Paths are canonical POSIX paths relative to the
separately configured root. IDs stay constant when a file's contents change.

```json
{
  "schema": 1,
  "files": [
    {"id": "operations-notes", "path": "notes/operations.md", "label": "Operations notes"},
    {"id": "inventory", "path": "sheets/inventory.xlsx"},
    {"id": "reference", "path": "references/guide.pdf"}
  ]
}
```

Register that manifest through the existing collection registry:

```json
{
  "targets": [{
    "platform": "files",
    "original_id": "spark-declared-originals",
    "kind": "file-set",
    "label": "Declared project originals",
    "projects": ["sparkuniverse"],
    "config": {
      "adapter": "file-set",
      "root": "/absolute/project/originals",
      "path": "/absolute/project/original-files.json"
    }
  }]
}
```

Root, manifest and state paths must be explicit absolute paths. Directory
components and files are opened without following symlinks. There is no directory
crawl, link download or inferred relationship generation. Duplicate IDs, duplicate
paths, traversal, absolute manifest entries and noncanonical relative paths fail.
Register separate targets with explicit project lists for different collections;
registration does not grant permission to read an unrelated corpus.

From a development checkout, use its pinned Python environment:

```bash
"$ALDEN_PYTHON" -B scripts/alden_collect.py --state-root "$STATE_ROOT" --config registry.json
"$ALDEN_PYTHON" -B scripts/alden_collect.py --state-root "$STATE_ROOT" --target "$TARGET_ID"
"$ALDEN_PYTHON" -B scripts/alden_collect.py --state-root "$STATE_ROOT" --capture-source "$TARGET_ID"
"$ALDEN_PYTHON" -B scripts/alden_collect.py --state-root "$STATE_ROOT" --query inventory --project sparkuniverse
```

`ALDEN_PYTHON` names that environment's Python executable; `TARGET_ID` is returned
by registration. Use an owned private state directory, with the existing abort
controller's required permissions. These commands do not start the native app,
activate a scheduler or prove that an installed CLI has been updated.

## Read, publish and retain

All listed files must be acquired and parsed before collection publication. A
missing, changing, unsupported or unreadable file fails the snapshot rather than
turning the other files into a successful replacement set. Only removal from a
valid explicit manifest retracts that file's membership; historical versions
remain retained.

Reads use a descriptor bound to the verified root inode. Each file is checked
before/after its bounded read. Root identity, manifest/file stamps, parser hash
and current target binding are rechecked before the collection transaction can
publish. The existing caller's publication guard is retained. These checks do
not create an atomic filesystem snapshot or guarantee future freshness after
publication. Filesystem I/O and the cooperative target deadline are not a hard
wall-clock guarantee for every mounted filesystem.

Collection and source capture remain separate operations. Capture rereads the
declared set, requires the committed source revision and raw-record bindings,
then retains a packet containing the exact manifest, per-file parsed records and
SHA-256-keyed original-byte blobs. Equal-byte copies share a blob while retaining
different file IDs and independent revision histories. Exact-byte equality does
not authorize semantic identity merging.

Realtime observation includes each listed file's stat signature, so a body change
can be noticed even when the manifest is unchanged. This integration does not
start or change any user's watcher or scheduler.

## Interpret locations and completeness

Each parser result contains `text`, its hash, `segments`, `truncated` and `scope`.
Each segment retains its own text/hash and a real structural location: source
line, CSV/TSV record and physical line range, DOCX XML part and paragraph, XLSX
sheet/cell, PPTX slide/part/paragraph, or PDF page. Blank structural positions are
preserved. DOCX paragraphs are not presented as inferred page numbers. Spreadsheet
formulas and cached values are disclosed without evaluating formulas. Textutil
conversion reports converted lines rather than original positions.

`manifest_complete` says the explicitly listed membership was acquired. It does
not claim discovery of every file in a project. `body_complete` requires both an
explicit positive parser scope result and an untruncated extraction. A missing or
negative completeness result stays false. Exact original-byte capture is a
separate claim: it can succeed while text extraction is incomplete. Inspect the
format's `scope` for omitted structures, conversion and OCR limitations.

Small decoded text and CSV/TSV can report a complete textual body. Office,
PDFKit page text and textutil conversion conservatively report incomplete body
coverage, including when no extraction budget was reached. PPTX linked speaker
notes are read in presentation order and retain their real XML part and slide;
unlinked notes, charts, diagrams and other unparsed parts remain disclosed.

Version-bound source details expose `source_extraction` for this adapter's stored
processing version. It independently states `body_complete`, `parser_truncated`
and `status` (`complete`, `partial` or `unverified`), with original input and
extracted-text digests/counts. The bounded packet binds the document, target,
stored version, archive hash and exact displayed-body digest. It does not reread
the original file or assert that a separate capture stage succeeded.

The existing detail `truncated` flag describes only its 12,000-character response
excerpt. A partially parsed original may fit that excerpt and therefore have
`truncated=false`; a complete parser result can also have a shortened response.
The reader shows extraction coverage separately. Missing or malformed file
coverage stays unknown. Its async hash result cannot replace a later selection.
Search fact provenance carries the shared `original_body_coverage` under its
existing source-version/archive binding, separately from `fact_truncated`.

| Boundary | Limit |
| --- | --- |
| Manifest | 1 MiB, 64 files |
| Original file | 5 MiB |
| Total original bytes | 16 MiB |
| Retained capture packet | 32 MiB |
| Joined parsed text | 16,000 UTF-8 bytes |
| Parsed segments | 256 |
| PDF pages / Office sheets or slides | 50 |
| Archive expansion / entries | 20 MiB / 256 |
| XML nodes | 250,000 |
| Parser child / native command deadline | 12 seconds / 8 seconds |
| Snapshot cooperative deadline | 120 seconds |

Larger documents require a future versioned pagination path to index the entire
body. Retaining their complete original bytes does not close that requirement.
No OCR is performed. Source MCP detail also has its own disclosed text projection
and excerpt bound; it is not an original-file download API.

## Verification scope

Evidence for this phase lives in the task's `outputs/spark-original-files-20261028`
directory, outside this document. The initial parser suite passed 43 tests and the
affected collection/realtime/file/chat suite passed 237 tests. Those initial
results predate three independently reproduced review findings. Their failing
counterexamples and corrected regressions are retained separately; final source
hashes and rerun results supersede the initial counts.

After the fixes, 296 affected tests pass, including 53 parser and 25 file-set
integration methods. The independent parser follow-up preserves the original 43
test method bodies. Another 48 packaging/build-receipt tests pass. Both modules
are declared in desktop resources; the prepared runtime's explicit copy list
also includes their complete local import closure.

The initial CLI journey generated seven document formats, executed actual PDFKit
and textutil adapters, retained byte-identical originals and read seven scoped
FTS results. It used generated files and isolated state. Guard-removal experiments
in copied source made the source-change and target-change tests fail, while the
unmodified controls passed. This supports those bounded publication invariants,
not an implementation-wide safety or liveness proof.

The final seven-format CLI journey also captures linked PPTX notes. It retains
all seven exact originals while marking only the small Markdown and CSV textual
bodies complete. The readonly MCP stdio journey returns seven search results,
seven graph nodes and seven version-bound source details. Stale versions and an
outside project are rejected, fixture state hashes remain equal, and guarded
network/embedding calls stay at zero. This is an owned subprocess journey rather
than connected-host or installed-desktop acceptance.

Actual private Spark corpus coverage, larger-document full-body indexing, dense
retrieval/model behavior, desktop UI delivery, installation and release remain
separate acceptance stages. The reserved formal external cross-audit is not
replaced by these local tests or independent native review.
