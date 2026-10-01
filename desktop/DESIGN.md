# Alden desktop design contract

## 2026-10-01 — wide settings workspace

The user requested a full settings redesign, a wide rectangular window, and explicitly allowed a palette beyond champagne gold. The selected reference is [Fieldwork · A workspace that remembers](https://style.gallery/components/21st-49e34572c05d/), reviewed in its running example on Style Gallery. Its independently authored MIT study supplies the layout direction: a quiet fixed sidebar, an independent scroll area, selected-view feedback, thin separators and a restrained linen/sage surface. The product's existing controls and real backend remain authoritative; example accounts, project metrics and dates are not copied.

Default settings window: **1200×760**, minimum **640×680**. The default view is the knowledge graph, filling the main area beside the sidebar. The sidebar has knowledge graph, KakaoTalk history, voice history, DB updates, and Settings. Settings is a page, with no gear launcher or modal. A selected node opens a floating detail panel; overview closes it. Labels are local DOM text projected onto the bounded Three.js scene. OSK automatically maintains the private knowledge vault while Alden runs. Keyboard arrows/Home/End move selection and focus together. Each view retains its scroll position. The memory renderer runs only when both its view and the native window are visible; switching views never invents activity.

Settings light tokens: canvas `#FAFBF6`, sidebar `#F0F3E9`, surface `#FFFFFF`, text `#273329`, muted `#697361`, sage `#657E50`, accent ink `#3F5630`, selected surface `#DFE8D0`, border `#E1E5D9`. Muted text contrast on canvas is **4.78:1**; selected text contrast is **6.42:1**. Dark surfaces use charcoal greens and muted sage with the same hierarchy. The left-click panel now uses the same real 3D graph at 560×420, with orbit controls and a bounded node budget.

The wordmark uses the system Georgia serif and controls use existing local system sans fonts. No external font request is required. [Lucide1.49.0](https://lucide.dev/) supplies icons; ISC/Feather notices are included in the source and preserved in the built JavaScript. UI assets are local, and no neon effects or added shader background is introduced.


The Settings page opens with the automation registry. New/edit opens a contextual editor beside the list; narrow windows stack it. Two compact answer choices and voice controls follow. Current operating status is a disclosure. This replaces the duplicated registration controls and the settings drawer. Save/delete require persisted catalog readback; uncertain writes are not repeated. Catalog edits apply when automation restarts; this UI does not adopt or kill existing foreground workers.

KakaoTalk history uses room-isolated keyset pages, fixed anchors, string IDs and untruncated text. A measured virtual list bounds live DOM rows at 80. Voice history stores confirmed user/assistant text by session; final cancellation fences precede assistant journaling. DB history retains older pages through polling and pauses reads when hidden. Raw DB+WAL collection counts and the existing search corpus counts are distinct. OSK nodes preserve their IDs through real API moves into KakaoTalk / rooms / people / topics; equal names never merge identities.

The new [record and knowledge flow](../docs/architecture/alden-history-osk-20261002.html) was generated with Archify. Authored labels are Korean; its fixed viewer controls and HTML language fall back to English.

The older contracts below describe the preceding settings layout and remain historical references for the panel, runtime and motion rules. The new wide settings geometry/view grouping supersedes their 960px card split.

## Experience

Alden should feel like a quiet instrument panel: warm, legible, and composed. A new user should understand each setting without knowing model, retrieval, or runtime terminology. Status copy gives one useful next step; diagnostic detail stays in developer documentation. The trade-off is deliberate: expert users see fewer live counters in the settings window.

## Decision table

| Constraint | Decision | Review check |
| --- | --- | --- |
| First-use comprehension | Plain Korean labels and one short status per task | User-facing settings contain no model IDs or diagnostic acronyms. |
| Visual character | Ivory and warm black surfaces; champagne marks selection; amber marks caution | No neon, bloom, glow, or decorative shadow. |
| Reading width | 960px window, 912px content area, golden-ratio 61.8:38.2 columns with a 16px gap; one column below 800px | The 38.2% side retains its 300px minimum at the two-column breakpoint; long labels wrap without forcing horizontal scrolling. |
| Live panel geometry | 276×260 panel, 12 inset, 236 core | Main panel constants are tested. |
| Main-panel hierarchy | Spherical Alden core only; settings open from a right-click on the menu-bar tray icon | No health/jobs/bulk/permission chrome. |
| Settings | One unified 960×880 window; two-column desktop grid and a single narrow-screen column | Rooms and AI answers, then voice and conversation status, then conversation search beside recent replies. |
| Motion | Per-source angular velocity, voice-aware global load, phase-integrated pulse, bounded spring substeps | Idle ≤15fps, active ≤30fps, frame dt ≤250ms, hidden/close/lock cancels RAF. |
| Color | Warm neutral canvas/surface with restrained gold | Champagne/gold is reserved for core and selection; amber is warning. |
| Retrieval language | Knowledge GraphRAG shell only | Hash-cosine is never labeled RRF. |

The decision → token → component → rendered-review sequence follows the contract-first approach in [oh-my-design](https://github.com/kwakseongjae/oh-my-design). The layout and motion review also draws on the previously reviewed [style.gallery](https://style.gallery/) reference. These sources guide the method; no third-party visual style is copied verbatim.

## Tokens

Light: canvas `#F7F4EE`, surface `#FFFCF7`, text `#241F1A`, muted `#6D655B`, champagne `#B88A45`, accessible champagne text `#73501F`, warning `#92540E`.

Dark: canvas `#12100D`, surface `#191611`, text `#F1EBE1`, muted `#B7AEA1`, gold and its accessible text color `#D5B36E`, amber `#E5A04B`.

Small champagne-colored text uses the accessible text token while borders, fills, and motion marks keep the brighter accent. On the light selection surface `#F7EFE3`, the accessible text contrast is **6.36:1** (the previous `#B88A45` text was **2.72:1**).

Spacing uses 4/8/12/16/24px steps. Settings cards use an 11px radius, a 1px warm border, and no decorative shadow.

The desktop settings split uses the golden ratio `φ = (1 + √5) / 2 ≈ 1.618`: the main column receives `1/φ ≈ 61.8%` of the available tracks and the secondary column receives `1/φ² ≈ 38.2%`. The secondary column keeps a 300px minimum for readable controls, so the split yields to that minimum when the window is near the 800px breakpoint.

## Component contract

- `AldenPanel`: opaque 276×260 root. The Three.js canvas is transparent and exactly 236×236. The panel has no interactive controls; settings open from a right-click on the menu-bar tray icon.
- `AldenCore`: three independently damped gimbal rings, 96 neuron points, three synapses per neuron, 30 particles, a spring nucleus, and an acoustic wire lattice. GPU buffers are allocated once and updated in place.
- `UnifiedSettings`: target rooms, two plain-language AI choices, voice start, one conversation status, holographic conversation search, and recent replies. There are no bulk-verification, feature-checklist, permission, model-owner, hardware, index, or training-status controls in this window.
- AI models: the existing ready Qwen3.8 27B service is the verified local voice choice. The exact Flash-Next iQ option is admitted only with 60 GiB of reclaimable memory and a matching ready catalog entry. The current host does not meet that budget; the UI must not imply that Flash is resident or available. A second resident model is not started just to populate the chooser.

## Motion model

Every activity input is clamped to `L ∈ [0, 1]`. For ring `i`,
`ωᵢ* = bᵢ (1 + gᵢ ℓᵢ) (1 + 0.35 L)`, where
`b = [0.17, -0.12, 0.09] rad/s`, `g = [1.4, 1.65, 1.9]`, and
`ℓ = [reply, GeekNews, DB sync]`. `L` is the maximum of job, background, and
voice activity; voice increases global motion but does not claim a ring. Ring
velocity follows the exact exponential damper
`ωᵢ ← ωᵢ + (ωᵢ* - ωᵢ)(1 - e⁻ᵏⁱᵈᵗ)`, with `k = [1.8, 2.35, 2.9] s⁻¹`.
Rotation integrates the preserved elapsed frame time, bounded at 250ms; nucleus
spring integration subdivides that time into steps of at most 50ms.

The lattice frequency is `f = 1 + 0.8L Hz`, amplitude is `0.05 + 0.05L`,
and opacity is `0.06 + 0.12L`. Its phase accumulates `φ ← (φ + 2πf·dt) mod 2π`
so changing load does not reset the waveform. A single 1,344-vertex sphere
buffer is packed into draw ranges of 448, 896, and 1,344 vertices; normalized
load thresholds 0.22 and 0.62 select the density. Frame updates only change the
draw range when the tier changes; they do not construct geometry.

## Bundle contract

The primary bundle is `Alden.app` with identifier
`com.openkakao.alden.desktop`; `LSUIElement=true` keeps it menu-bar-only.
Signing identity is intentionally not hardcoded in source. The packaging
script accepts an explicitly supplied `OPENKAKAO_SIGN_IDENTITY`; local builds
remain unsigned when it is absent. The former `com.openkakao.auto-reply.menu`
Swift Extra is a separately named legacy path and is stopped before the Tauri
LaunchAgent is bootstrapped.

## Render review

- Opaque warm panel, transparent WebGL canvas only; no transparent window.
- No cyberpunk neon, bloom, glowing text, or Gemma recommendation.
- Core motion is legible at low frame rates and resumes without a jump.
- Settings use the golden-ratio 61.8:38.2 split with a 16px gap in the 960px desktop window and return to one column below 800px, preserving the 300px minimum for the right column.
- The conversation graph shares its row with recent replies; narrow layouts keep the graph readable and stack the sections.
- Knowledge copy uses everyday Korean and does not expose retrieval implementation details.

## Corpus-backed graph and timeline

0.3.0 uses a resumable account-scoped corpus populated from a consistent encrypted DB+WAL snapshot. Working and published SQLite databases are separate. A complete publication invalidates the graph cache; actual numeric author IDs identify people across rooms. Same names never establish identity. Snapshot evidence records observed source rows and is separate from decision-ledger evidence. Focus lookup returns scoped quoted history, with unclassified outgoing history marked explicitly. Dense/model inference coverage is not implied by raw full-text coverage.

DB history resets its oldest-page cursor when a newly fetched page does not overlap the prior window after a long hidden interval. All loaded rows remain available while the live DOM is bounded. Row keys preserve reading position through append, prepend and height measurement. Hidden views cancel their animation requests and observers.
