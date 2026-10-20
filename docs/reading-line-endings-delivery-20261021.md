# Retained graph-note reading boundaries

A retained graph note ending with the existing numeric-tail pattern could be folded for reading when its lines used LF, but the same note using CRLF or CR stayed unfolded. The splitter discarded the separators, and rebuilding the prefix with LF made the exact retained-prefix check fail.

The reading presentation now keeps each original separator while locating the boundary. The numeric-tail policy, uncertainty label and source binding remain the same. It does not alter stored bodies, source bytes, IDs, versions, indexing, duplicate decisions or entity merges. The complete original stays available through the existing expanded view.

The new regression failed in four CRLF/CR subcases before the change. Six LF/CRLF/CR and trailing-blank-line cases now preserve the exact reading/tail concatenation. A collection detail integration test also preserves the database, source blobs, indexed rows and current version. The affected source/collection/retrieval/quality suites passed 175 tests.

A read-only snapshot verified 25,157 permitted graph raw-version references (19,025,727 bytes), including 12,538 retained text bodies. None contained CRLF or lone CR. All 42 existing reading folds were identical before and after the patch. This is a reproducible boundary defect, not evidence that it caused the user's current screenshot or that the live corpus had that defect.

The installed 0.3.204 module reproduced the LF-success/CRLF-and-CR-failure boundary with an isolated collection. Release target 0.3.205 will be checked separately after the normal signed build and installation. Native interaction, visual QA, broader semantic/identity/temporal gold, external cross-audit and production release remain separate gates. The full 18-item goal and ST01–13 remain active.

Evidence is in `outputs/reading-line-endings-20261021/`. The first retained-source audit used an incorrect `blobs` directory; its failure log is retained. The corrected audit uses the product's `sources` directory. Neither audit wrote live source or database content.
