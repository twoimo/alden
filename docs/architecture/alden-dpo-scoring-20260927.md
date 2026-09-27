# Local DPO scoring gap — 2026-09-27

The parent ran the existing `probe_response_scoring()` against the loaded local
`ddalcu/Qwen3.8-27B-MLX-Serve-4bit` model on `127.0.0.1:11234/v1`.
The bounded, synthetic `completions_echo` probe completed in **0.931584 s**.
The server returned the newly generated token ` epsilon`, rather than echoing
the supplied marker. The probe recorded `echo_unsupported` and
`supports_response_scoring=false`; no string-similarity substitute was used.
See the [raw receipt](alden-dpo-scoring-probe-20260927.json).

For a fixed prompt x and fixed preferred/rejected responses y+ and y-, standard
DPO needs the policy and frozen reference model probabilities of those same
response sequences:

```text
delta = beta * ((log pi_policy(y+ | x) - log pi_policy(y- | x))
              - (log pi_ref(y+ | x) - log pi_ref(y- | x)))
loss = log1p(exp(-abs(delta))) + max(-delta, 0)
```

The existing arithmetic helper implements this numerically stable softplus
form and can reject missing reference probabilities. Generated-token logprobs
alone do not provide scores for arbitrary previously stored response pairs.
This observation is limited to the existing echo-based transport; it does not
prove that every native MLX scoring interface is unavailable.

Remaining implementation: a local teacher-forced response scorer with an exact
tokenizer/model contract and fixed reference probabilities, integrated with
the offline preference-pair loop. Continuous standard DPO evaluation, adapter
training, promotion and measured quality improvement remain unverified. No
KakaoTalk conversation or message was sent for this probe.
