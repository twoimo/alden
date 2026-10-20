# Graph pin boundaries and source-diverse labels — 2026-10-20

Alden0.3.195 integrates the completed, human-authorized repair from the separate cross-audit follow-up into the normal app delivery path. The five graph source/test files matched that follow-up's recorded SHA-256 values before integration. Baseline is `7c64bf4643feb880214451ba1d03b606a5d0e121`; no pstack files are included in this Alden change.

The dense fresh-layout branch now counts only visible, admitted pins. A stale/unknown pin ID cannot suppress the large-graph spread, including when a valid pin and an unknown ID occur together. On leaving a single off-center pin frame, cached disconnected anchors are projected back into the existing global bounds. Their component is not forced through a global solve; subsequent cached reads retain the bounded anchors and checkpoint.

Overview-label membership now considers one eligible top-tier subject per source/group before filling other source groups and remaining slots. Display order still follows the established subject/connection ranking. Eligibility, stored label text, entity IDs, source permissions, relation meaning and zoom collision rules are unchanged. This corrects source starvation without adding synthetic topics or relaxing person/noise filters.

## Reproduction and verification

The controlled stale-pin case changed256 anchors before the fix and0 after it. The controlled disconnected node's normalized radius after releasing an off-center pin changed from1.5444826253 to1.0000000000. These are engine fixtures, not measured on-screen density or GPU/frame-time improvements.

The native TypeScript suite passed91 files/1,018 tests after integration. Regressions cover unknown-only pins, valid/unknown combinations, bounded release and cache/checkpoint persistence, source-diversity selection at caps1/2/8, reverse input order, original object/text preservation and the Hologram label DOM path. The focus/unfocus integration exercises actual Hologram request/response methods, the driver, engine and restored worker checkpoints; worker transport and animation are test doubles. This is not physical native input or a WebGL capture.

The existing ChatGPT reverse report is bound to original source `137461c7c8c2794b31ff6ce2246bd6487a1e4bcd` and frozen current `9c115f9243086c1b26a938a5bccb801ffc0771c4`. Original URL: https://chatgpt.com/c/6ad7496d-da4c-83e8-a3e8-d05557d8755b; saved full report SHA-256 `f1257153528fd028a6762dc647c619cb7a11017b78c14b2aac5c2bd4948f54ac`. Its narrow decisions are applied here: source-diversity starvation is ACCEPT; raw/filtered pin-count consistency and pin-stranding remain conditional on the relevant boundary evidence. The current source is explicitly rechecked rather than equated with the original report's revision.

The preferred original Gemini Deep Research remains cancelled, and the reused adversarial report is labeled Deep Research off. This repair does not retroactively complete the historical preferred three-stage chain, the separate DB review, independent semantic gold or the full18-item objective.

## Installation evidence boundary

Canonical signed build, installation/hash/configuration readback, real read-only graph acquisition and PR/CI receipts are retained in the existing task's `outputs/graph-pin-label-delivery-20261020/` progress record when produced. A candidate build or unit fixture alone is not installed-app success. QA stays background/minimized; no floating native audit window, model inference or live send is required for this repair. Physical primary/tray/Retina/input and representative whole-app resource validation remain distinct full-goal gates.
