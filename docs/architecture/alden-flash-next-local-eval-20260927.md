# Flash-Next local model check — 2026-09-27 KST

This is a bounded local inference check on the M5 Max MacBook Pro with 128 GiB unified memory. It does not exercise Alden's model selection, a real KakaoTalk turn, voice, or a persistent second server.

The existing mixed 4/8-bit Flash-Next pack did not pass the earlier memory preflight while the 27B service was in use. A smaller checkpoint, [`ddalcu/Qwen3.8-Flash-Next-MLX-Serve-iQ-MLX-3.3bpw`](https://huggingface.co/ddalcu/Qwen3.8-Flash-Next-MLX-Serve-iQ-MLX-3.3bpw), was downloaded at immutable Hugging Face revision `b5ee278c61ad445890f1fb718e48c6bb7b780535`. The model publisher reports approximately 52 GB resident weights and a separately memory-mapped n-gram table; its quality comparison is publisher evidence rather than a local measurement.

The pinned download completed with `hf download` exit 0. A same-revision dry run listed **113/113 local files**, including **101 model safetensors** and `ngram_table.bin` of **32,000,153,976 bytes**. The safetensors index mapped **3,167 tensors** to all 101 nonempty shards, with zero missing references. The dry-run inventory totals approximately **86.35 GB of disk payload**; that is different from resident model memory. The local MLX Core `mlx-serve` binary reported version **26.9.5** with MLX **0.32.2**.

An isolated `mlx-serve` process used the local directory on `127.0.0.1:11235`, an 8,192-token context, disabled vision, and the default memory preflight. It reported **50.60 GB estimated weights** and **66.77 GB available** before loading, then reached `Model ready`. The established 27B server on `127.0.0.1:11234` remained loaded and ready throughout this check, and the KakaoTalk worker host remained healthy.

A synthetic localhost chat completion with `enable_thinking=false` and at most eight output tokens returned HTTP **200**, exact `OK`, and **17 prompt + 1 completion tokens** in **6.176 s** on the first request. Three repeats of the same cached prompt returned `OK` in **0.989 s**, **0.073 s**, and **0.076 s** (median **0.076 s**). Prefix-cache reuse makes these timings unsuitable as general answer-latency estimates. No cloud inference was used.

The isolated server was gracefully shut down after the check. Port 11235 was then closed; port 11234 still returned HTTP 200 and the worker host remained healthy. Flash-Next is installed on disk and has passed a local API smoke, but Alden's deep-analysis selector has not been routed to this 3.3bpw checkpoint and there is no ongoing Flash-Next service.
