# Alden reply queue skip audit — 2026-09-27

This is a read-only audit of the three configured room `reply-queue.sqlite3` files and their `reply-evidence.jsonl` ledgers. The fixed interval is **2026-09-26 14:00:00 ≤ created_at < 2026-09-27 14:00:00 KST**. It counts durable queue jobs, not unique conversational turns or independently confirmed KakaoTalk deliveries. No message text, sender name, chat ID, or reply body is included here.

| Terminal queue state | Reason/category | Jobs |
| --- | --- | ---: |
| `sent` | `geeknews_rss` / proactive | 9 |
| `sent` | `model_temporarily_unavailable` / reaction | 2 |
| `skipped` | `stale_backlog` / policy | 14 |
| `skipped` | `burst_superseded` / duplicate | 9 |
| `skipped` | `already_commented` / duplicate | 2 |
| **Total** | | **36** |

The 25 skipped jobs comprise 14 stale jobs (56%), nine superseded burst fragments (36%), and two already-commented duplicates (8%). All nine durable supersession edges point to successor jobs inside this interval: five directly to a stale terminal job and four to another superseded fragment. Collapsing those edges yields **27 terminal queue chains** from 36 jobs. This is still not a count of distinct human conversations, because it includes proactive and duplicate records. The 25 skipped jobs must not be described as 25 missed conversations.

For the 14 stale jobs, the first matching evidence record was `model_temporarily_unavailable` in **11** cases (78.6% of stale jobs), `geeknews_rss` scheduled in one, and already `stale_backlog` in two. Of those 11 model-unavailable evidence records, ten named Flash-Next and one named 27B. Their median elapsed time from queue creation to terminal stale state was **934.7 seconds** (range **870.3–1019.3 seconds**). This connects those skips to recorded model unavailability and retry expiry; it does not prove why each model was unavailable. One scheduled GeekNews job ended stale after roughly 729 seconds and needs separate pre-send investigation.

The 11 terminal `sent` rows are nine scheduled GeekNews jobs and two model-unavailable reaction rows. None is evidence of a normal generated conversation reply in this interval. Queue `sent` is the worker's durable state, not an independent KakaoTalk delivery receipt.

No job in this fixed interval was created after the 13:00 KST watcher diagnostic runtime cutover. A post-fix live conversation sample is therefore absent, and neither a skip-rate reduction nor a latency improvement is established. At the subsequent readback, the current configuration selected 27B, the local MLX `/v1/models` endpoint marked that exact model loaded and ready, and all three room supervisors and workers reported ready/healthy. These are current readiness observations, not retrospective explanations for the 24-hour failures.

Audit method: each room queue was opened through SQLite `mode=ro`, filtered on `reply_jobs.created_at` with the fixed half-open interval, and grouped by `status`, `reason`, and `category`. `reply_job_supersessions` was checked for edges whose source and destination both fell inside the interval. For stale rows, the first matching `reply-evidence.jsonl` record was selected by `event_id`; only its reason and model identifier were counted. Elapsed time was `reply_jobs.updated_at − reply_jobs.created_at`. No KakaoTalk database was opened or modified, and no send was attempted.
