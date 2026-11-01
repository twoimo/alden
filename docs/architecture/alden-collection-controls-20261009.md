# Native collection schedule controls — 2026-10-09

The history workspace now displays declared collection targets, configured intervals, next due/retry times, recent durable attempt outcomes, and the observed fixed macOS schedule state. Display is bounded at 200 and reports the full registry count separately. Permission-denied and excluded targets cannot be resumed through this view. A configured due time is not proof that the job is installed or running.

`collection-scheduler-status` is read-only. It does not initialize the source store/scheduler database, change schedules, start workers/models, infer activity, or send messages. The observer checks only the fixed app-owned launchd definition and service in the logged-in user's GUI domain. Unknown/mismatched/unloaded definitions stay unverified.

`collection-scheduler-control` admits pause/resume with an optional known target ID and deliberate opt-in. Rust and Python validate the boundary; the existing scheduler lock and durable controls handle changes. Target resume preserves its checkpoint and clears its retry/conflict block according to the existing CLI contract. Schedule resume cannot clear the independent emergency latch. Native audit admits status reads and rejects control writes.

The UI owns one request and one 15-second timer while history is visible. Hiding/page changes cancel polling and fence late results. An ambiguous write disables controls until a successful status read; no write is automatically resent. Unchanged polls retain target buttons and keyboard focus. The status projection omits source configuration, bodies, raw result payloads and exception text.

Validation: 401 frontend tests, 109 Rust tests, 18 scheduler tests and Clippy. Actual current seven-target status was read through the pinned product dispatcher. Pause/resume was executed on an isolated copy of one current target's registry/project metadata, preserving the entire target row and emergency-latch bytes. Live controls were not changed. Native default/compact screenshots and data readback are separate from physical native-button input proof.

Editing intervals in this UI, manual immediate execution, full multi-host history, physical UI input and broader active-objective requirements remain separate work. Existing collection intervals, checkpoints, model defaults, permissions and schedule definition are preserved.
