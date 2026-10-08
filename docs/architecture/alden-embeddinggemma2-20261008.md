# EmbeddingGemma2 후보 실행 확인

사용자가 공유한 Threads 글의 모델을 Google 공식 소개·모델 카드와 대조했다. 공개 Apache2.0 모델 `google/embeddinggemma-2@914f7f89142e33e77833254d9c9b90c3cef7303b`를 별도 후보로 받았다. 기존 E5·MLX·음성 런타임의 패키지와 색인은 바꾸지 않았다.

기존 Transformers4.57.3에는 새 모델 구조가 없어 별도 환경에 공식 stable Transformers5.19.0, SentenceTransformers6.1.0, Torch2.14.0와 image extra를 설치했다. text-only config에서 vision/audio encoder를 제외하고 CPU/bfloat16, offline/no remote code로 실행했다. 초기 PIL/torchvision 미설치 실패를 보존하고 격리 환경에서 해결했다.

한국어 문서3개와 질문2개의 synthetic smoke에서768차원·L2 정규화 벡터를 만들고 기대 문서0·2를 찾았다. 로드1.493초, 프로세스 peak RSS1.23GiB, n=1이다. 이 수치는 전체 앱 메모리·양자화된 Pixel 수치·E5보다 나은 검색 품질을 의미하지 않는다. 이미지·음성·영상과 실제 독립 검색 평가,256d 재정규화·저장 예산과 증분 색인 연결은 진행 항목이다. 기존 생산 색인은 E5+BM25/RRF를 유지한다.

출처: [Google 공식 소개](https://blog.google/innovation-and-ai/technology/developers-tools/embeddinggemma-2/), [모델 카드](https://ai.google.dev/gemma/docs/embeddinggemma/model_card_2), [공식 가중치](https://huggingface.co/google/embeddinggemma-2). 비공개 실행 영수증은 task outputs/model-routing-20261008/embeddinggemma2-smoke.json에 보존했다.

## Fixed-fixture text comparison

The already existing20-entity/13-query fixture was fixed before these runs, with6 development and7 held-out queries(5 positive). Real E5 and pinned offline CPU/bfloat16 EmbeddingGemma2 embeddings were used through the same temporary product retrieval/RRF pipeline. Production indices, global model choice and runtime pins were preserved.

| Encoder | Held-out Recall@1 | Held-out nDCG@3 |
| --- | ---: | ---: |
| E5 |0.9|1.0|
| EmbeddingGemma2 768d |0.7|0.9262|
| EmbeddingGemma2 256d |0.9|0.9912|

Each encoder run is n=1. This small synthetic holdout does not establish independent human-rated real-source quality or comparable device latency. Candidate CPU peak RSS was about1.24GiB. No demonstrated superiority warrants replacing E5. Both encoders had the same latest-intent freshness failure: the previous synthetic pricing entity also entered current-price context. It is a quality failure requiring explicit temporal/supersession handling, not evidence of cross-user/project permission leakage. The fixture/judgments were not altered to make a result pass. Multimodal evaluation and product integration remain open.
