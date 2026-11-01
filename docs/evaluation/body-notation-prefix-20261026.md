# Source body quantity boundaries

Product `0dd1a1951c7ca537b78393d5949f632332331bea` prevents a denominator in an unsupported numeric expression from appearing as an independent normalized quantity. Before the change, `1/2kg` produced no annotation while `1 / 2kg`, `1⁄2kg` and `1 ÷ 2kg` could produce `2kg → 2000 g`. The canonical source detail reader passed that substring to the frontend, which correctly bound its hash/version/offsets but could still display the incorrect syntax boundary.

The prefix guard now follows horizontal Unicode spaces and tab, recognizes a bare numeric left operand, and preserves word/identity boundaries. It does not cross line breaks. `품목1: 2kg`, `SKU1: 2kg`, NFD identity labels and ordinary `무게: 2kg` remain valid label/quantity pairs. Explicitly typed numerator rates such as `10km / 2시간` retain their independent quantity spans. Unsupported sign spacing cannot silently discard a sign and admit its positive tail. This does not parse a quotient or assert that an annotation is a semantic fact. The source text, raw hash, selected source version, codepoint/UTF16 offsets, reader authorization and `semantic_validity: not_assessed` contract remain unchanged.

## Validation

- Five related Python modules: 75 methods pass, including the actual `read_action`/source-detail boundary on owned synthetic records. New bounded controls include 960 fraction/ratio tails, 120 numbered-label cases, 28 unsupported sign-spacing cases and eight label/rate/line controls.
- Three frontend source/detail-binding test files: 53 methods pass. These are local DOM/contract checks, not installed UI or physical interaction acceptance.
- Native Astra independently wrote eight pure-parser controls covering 5,720 fixed synthetic inputs. Those controls caught a numbered-label regression in the initial candidate. The final source passes the unchanged eight methods and all 5,720 inputs when rerun by the parent. The worker's original failed review remains preserved; it is not relabeled as a final source approval or an external audit.
- The bounded mutation verifier accepts the current/restored parser and rejects all ten deliberately broken variants. It covers sign, scale, grouping, URL/date boundaries, offsets, fraction/ratio prefixes, horizontal spacing and identity-label boundaries. This is mutation sensitivity on finite controls, not a theorem, generic expression parser or semantic gold evaluation.
- [Exact product CI](https://github.com/twoimo/alden/actions/runs/40331568480) passes all four jobs.

The initial baseline red control, excluded candidate failures and an output-log collision are retained in local evidence. Accepted mutation results use a new output directory and do not overwrite those earlier logs.

## Actual original-source readback after CI

The canonical query-only detail reader inspected the same five declared projects after exact CI success. It verified seven eligible annotated details across four projects, against a budget of two per project. One project has only one listed record; another project's first 16 records are technical metadata and remain excluded by the existing content classifier. No source classification or permission was changed to obtain coverage.

For each accepted detail, the current expected source version, target/raw/body hash binding, body excerpt boundary and exact codepoint/UTF16 spans passed. Shared annotations equal the prior parser's fields. This bounded sample observed zero added or removed annotation candidates, so it is not an original-data reproduction of the synthetic fraction bug or a representative precision/recall test. There were 259 query-only collection connection contexts, zero original operational writes, zero native engine calls and zero GUI launches.

Separate complete comparisons of three historical canonical export epochs retain 43 targets, 26,651 nodes and 48,355 relation groups. The two adjacent comparisons change nine and six nodes respectively, only in `source_version` and `source_contexts[*].version`. Equal display labels do not prove equal raw source/provenance. Source versions, complete row checks, currentness and visibility were not relaxed. The existing GBrain checkpoint remains private as-of, inactive and not ready; no source-current cache was published by this change.

## Build-only candidate

The new detached source tree has 2,036 tracked files matching the product commit. The app contains 96 files and 82 Python resources. Its packaged body parser exactly matches the source used for post-CI original-source readback. Canonical/compatibility CLI copies remain regular, independent files with equal bytes/signatures. Canonical build receipt, identity-only, strict/deep codesign and ZIP extraction byte/file/directory-mode checks pass.

ZIP: 22,491,762 bytes; SHA-256 `8cfb5dec3a01413e7c18cf0da62a6d0a0567f9ee4626cb18b7a8f35d9c52d7f8`. The first normal build generated new-path dep-info but could not record a receipt because its initial cloned pre-key was unavailable. After confirming all tracked bytes unchanged and a valid current pre-key, one ordinary second build and receipt check both exited zero. No force/evaluation/receipt bypass was used.

Twenty-four older app/extraction artifacts and 396 prior output files were preserved. Five configuration files match this build's start; three match the original baseline, with prior external differences retained. No task-time configuration drift was observed. The app uses Apple Development and the CLI linker ad-hoc; Developer ID Application remains unmet. The candidate was not installed, launched, notarized or released. Alden stayed off and the original collector/model remained running.

The full original 18 requirements, follow-ups, ST01–16 and Cursor goal remain active. Latest-source active serving, installed UI/TCC/native-account/SDK readiness, representative semantic/identity/time gold, 3D/resources/voice/inference/tool runtime, formal/external review and release gates remain incomplete. Native Astra is additional evidence and does not replace the held independent web-Astra/ChatGPT–Gemini–reverse review. Machine-readable scope: [body-notation-prefix-20261026.json](body-notation-prefix-20261026.json). Raw source bodies, labels, authority records and cursors remain local.
