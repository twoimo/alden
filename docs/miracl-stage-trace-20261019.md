# 검색 융합의 순위 손실을 단계별로 확인한 결과

2026-10-19. 설치 Alden0.3.154/runtime `659d3b5cfea08fe26e856d55cdd1f5dac7fd67a8`의 consumer를 다시 실행해 **213질의의 기존 네 순위를 전부 정확히 재현했다.** 제품 코드·가중치·모델·색인은 바꾸지 않았다. 이 문서는 [공개 한국어 검색 기준선](miracl-retrieval-baseline-20261019.md)과 [Graphify 검토](graphify-adoption-audit-20261019.md)의 W-A06 후속 진단이다. Graphify 검색의 실행 결과나 새 holdout은 아니다.

## 실행 경계

기준 source는 `215992cf5889c59e03166ec037e4b1c7551d4db3`다. 원래 protocol·질의·query vector의 입력 hash·설치 모듈6개의 hash를 검사했다. 기존 실제 E5 query vector를 재사용하고 설치 `_rrf_merge`, 본문 근거 우선 함수의 입력/출력만 이 감사 프로세스 안에서 관찰했다. 제품 파일에는 instrumentation을 넣지 않았다.

처음에는 소켓을 먼저 막아 lazy `/models` 조회까지 거절했고, 제품은 BM25로 fallback했다. 이를 RRF 성공으로 세지 않고 첫 결과와 script를 보존했다. 수정 실행에서는 **실제 같은 모델을 먼저 조회·확인한 뒤 그 신원 메타데이터만 고정 재생**했다. 이후 단계에서 네트워크·임베딩 시도는0개다. 실행 중 계속 ready였다는 주장은 하지 않는다. 추가 인코딩·새 서버·모델 교체는 없다.

원래 감사 폴더의 전체 파일 hash가 전후 일치했다. pause된 감사 target과 원문·벡터·원래 순위도 그대로다. 새 trace는 별도 폴더에만 기록했다. 종료·예외 시 process-local wrapper를 복원한다. 유지 도구에 추가한 `-B` 필수 및 원래 폴더 아래 출력 거절은 측정 이후의 사전 검사 보강이며, 실제 실행 snapshot/hash를 따로 남겼다.

## 단계별 결과

분모는 모든213질의다. corpus는 원래와 같은 **2,835개 공개 판정 문서 합집합**이며,148만 전체 corpus나 개인 지식 정확성을 나타내지 않는다. 미판정 문서는 계속 unknown이다. token BM25와 제품 lexical은 서로 다른 단계다.

| 단계 | nDCG@10 | Recall@10 | MRR@10 |
|---|---:|---:|---:|
| token BM25 | .525161 | .653485 | .557286 |
| E5 Dense pooled | .721301 | .851156 | .752483 |
| token BM25 + Dense RRF, ID 동점 순서 | .682766 | .839214 | .709779 |
| 같은 RRF, 수집 시각 동점 순서 | .680100 | .839214 | .704289 |
| 제품 lexical 단독 | .527186 | .659119 | .557957 |
| 제품 lexical + Dense RRF, ID 동점 순서 | .681432 | .838275 | .708834 |
| 제품 lexical + Dense RRF, 수집 시각 동점 순서 | .678877 | .838275 | .703292 |
| 본문 근거 우선 처리 후 | .680211 | .838275 | .704700 |
| 실제 consumer 반환 | .680211 | .838275 | .704700 |

네 기존 방법의 **전체 순위 목록이 원래 영수증과 동일**하다. 감사용 ID 동점 순서는 반사실 비교이며 제품 정책 변경이 아니다. 추가5단계의213질의별/전체 nDCG·Recall·MRR 총 **3,210값**을 기존 고정 NIST `trec_eval`로 검산했다. 출력4자리 오차 한도.000051 안에서 전부 일치했다.

token raw RRF→현재 제품 consumer 차이 `-.00255532`는 이번 순서에 따라 다음처럼 분해된다. 인과 기여가 모든 순서에서 독립·가산적이라는 뜻은 아니다.

| 한 단계 변화 | nDCG@10 차이 | 상위10 순서/구성이 달라진 질의 |
|---|---:|---:|
| token RRF의 ID 동점→수집 시각 동점 | -.00266628 |26|
| 같은 수집 시각 동점에서 제품 lexical 사용 | -.00122334 |23|
| 본문 근거 우선 처리 | +.00133430 |5|
| 그 이후 consumer admission | .00000000 |0|

제품 lexical 고정 비교에서도 ID→수집 시각 동점 차이는 `-.00255480`/23질의였다. 감사 store의 수집 시각은 ingest 시각이며 Wikipedia 발행 시각으로 해석하지 않는다. 개인 corpus의 최신성 정책을 이 결과로 바꿀 수 없다.

본문 검사 함수는160질의에서 호출됐다. preferred가 있는6질의는 `247,303,691,907,927,1273`이고, 실제 순위가 바뀐 것은247을 제외한5개다. 따라서 **본문 우선 처리를 현재 aggregate 손실 원인이라고 설명하는 것은 이 결과와 맞지 않는다.** 모든213질의에서 post-body 상위10과 실제 반환 순서가 일치했다. 개인 장문·URL-only·권한 변경의 admission은 별도 검증 대상이다.

## 새 외부 보고서와 모델 신원 충돌

별도 [원래 연구 대화](https://chatgpt.com/c/6ad537a0-37b4-83e8-8942-d6818953987a)에서 보고서44,791bytes를 정식 Markdown export로 저장했다. SHA256 `c028be9f272d1c7d08530ad9868dfea7ee4181a7f6b511a26c7c17c02385d5a6`이며 다운로드 원본과 정확히 같다. 입력 master SHA `bb3f8e1e860d79409496684048cf106b6234dd18e6fed61b1cbec2bb1c6db2dc`, bundle SHA `3930e1000603abd7e04eff21ebeb91ee4f5251241627f50fcf12b0798aed346a`에 결속한다. 완료 UI는8m/8citations/135searches였다. 저장 직후 소유 슬롯을 정상 해제했다.

**화면에서 GPT-6/Pro5of5와 Deep Research를 확인했지만, 보고서15행은 자신을 GPT-5.5 Thinking이라고 명시한다.** 둘은 서로 다른 관측이다. 보고서의 자기소개만으로 실제 backend를 확정하지 않으며, 이 보고서를 요청된 strict GPT-6 Pro/Astra 감사 충족으로 세지도 않는다. 원문·원래 수락 turn1회·별도 DR-start meter는 보존했다. 재전송·하위 모델 선택·추가 유료 용량은 없다.

[OpenAI 모델 선택 문서](https://developers.openai.com/api/docs/guides/model-selection)와 [공식 검색/Deep Research 안내](https://learn.chatgpt.com/docs/web-search)를 확인했으나 이 실행의 backend나 모드 결합을 증명하는 자료는 확보하지 못했다. 실제 요청 신원은 **UNVERIFIED**다. 기술적 지적은 이 표의 코드·실행·수학 근거로 별도 판정한다. 보고서의 자체 A/B/C/D 표시는 아래 공식7등급을 대신하지 않는다.

| 보고서 지적 | 로컬 판정·근거 등급 | 조치와 남은 조건 |
|---|---|---|
| W06-POOL-001 | ACCEPT / SOURCE_OBSERVED + BOUNDARY_INTEGRATION_TESTED |2,835 closed-pool 범위를 유지. 전체 corpus·개인 gold 주장 금지|
| W06-UNJ-002 | ACCEPT / SOURCE_OBSERVED |unknown 노출을 계속 별도 보고. gain0을 사람의 비관련 판정으로 바꾸지 않음|
| W06-JONLY-003 | REJECT 대체안 / SOURCE_OBSERVED |NIST `-J`는 unjudged를 제거하므로 기존 평가 대체에 부적합. 원래 qrels/분모 유지|
| W06-STAGE-004 | ACCEPT / BOUNDARY_INTEGRATION_TESTED |이 trace로 실제 lexical·fusion·body·consumer를 분리, 순위/버전/hash 보존|
| W06-TIME-005 | ACCEPT 측정, DEFER 정책 / BOUNDARY_INTEGRATION_TESTED |동점 효과를 측정. temporal personal gold 전 제품 정책 변경 보류|
| W06-FUSION-006 | CONDITIONAL / BOUNDARY_INTEGRATION_TESTED + PROPOSED |현 공개 set의 이득/손실 사례 유지. 새 weight는 미사용 실제 질의 평가 전 채택하지 않음|
| W06-LEX-007 | ACCEPT / SOURCE_OBSERVED + BOUNDARY_INTEGRATION_TESTED |token baseline과 실제 product lexical을 분리 표시|
| W06-F32-008 | REJECT 현재 손실 설명 / CONTRADICTED |기존2,835벡터 byte대사와 이번 전체 dense 순위 일치. 직렬화 변경 없음|
| W06-POOLING-009 | DEFER 최적성 / UNVERIFIED |실제 pooling 측정은 있으나 장문·다중주제 최적성은 미검증. 기존 profile 유지|
| W06-CTX-010 | REJECT 이번 손실 설명 / CONTRADICTED |이번213건은 post-body와 반환 상위10이 모두 일치. 개인/장문 admission은 열려 있음|
| W06-BODY-011 | ACCEPT 진단 완료, 정책은 CONDITIONAL / BOUNDARY_INTEGRATION_TESTED |6 preferred/5 reorder와 정확한 전후 순위 확보. 효과는+.00133430. 독립 literal slice 전 변경 없음|
| W06-CAND-012 | DEFER / PROPOSED |candidate40 유지. 미사용 자료의 recall ceiling·동등 예산·호스트 지연 검증 필요|
| W06-RELEASE-013 | DEFER / UNVERIFIED |요청 모델 계약·Gemini 반대감사·최종 역검증·사람 gold·whole-app gate 미완료. 기본값/배포 승격 없음|

이 표의 실제 파일/DB 소비와 NIST 실행은 **BOUNDARY_INTEGRATION_TESTED**이며, query encoding과 model metadata는 저장 관측의 재생이다. 현재 live encoder inference, 물리 UI, HOST_TESTED 전체 성능 또는 독립 외부 합의로 승격하지 않는다. 원문 보고서의 의사코드는 그대로 적용하지 않았고, 실제 Store 인자를 사용하는 설치 consumer를 관찰했다.

## 재실행과 전달 근거

새 도구 `docs/audits/miracl_stage_trace.py`는 기존 output 덮어쓰기·범위 이탈·원래 폴더 내부 출력·입력 hash 손상4개 CLI 거절 검사를 통과했다. 이미 완료한 trace를 새 연구처럼 재실행하지 않는다. 새로운 frozen baseline을 진단할 때만 다음을 사용한다.

```sh
"/Users/twoimo/Library/Application Support/openkakao/runtimes/menubar/bin/python3.11" -B docs/audits/miracl_stage_trace.py --baseline-dir outputs/FROZEN-BASELINE --output-dir outputs/NEW-STAGE-TRACE
```

worktree `outputs/retrieval-stage-trace-20261019-r2/`에 protocol,213개 query trace·순위 hash·현재 source version, 결과, NIST 검산, 거절 검사, 실제 실행 snapshot을 보관한다. 외부 전문/27개 원래 UI text 영수증/완료·해제와 모델 모순은 `outputs/rrf-delta-20261019/`에 결속한다. 원래 연구와 별도 계정의 세션을 보존한다.

Graphify 반대감사 request `4a2990ac990ad93c20b539cc`와 알고리즘 반대감사 request `729d4f33b6e17ee1fac681e4`는 계속 queued다. 공유 Gemini 실행3개/빈 슬롯0인 상태에서 업로드·전송하지 않았다. 원래137461c 연구는 전체 보고서가 확인되지 않았다. 마지막 ChatGPT 역검증도 미완료이며 전체18목표는 active다.
