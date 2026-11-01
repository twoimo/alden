# 설치된 임베딩 경로의 실제 shadow 점검

2026-10-19 KST. 설치 Alden0.3.153, runtime `c3871f3aa203bebb9a8b0d5bba6b9f3fc908b0b8`. `docs/audits/embedding_shadow.py`는 제품 설정·원문·색인·모델·큐를 변경하지 않고 가상 자료와 별도 audit store만 사용한다. [알고리즘 후속 판정](graph-algorithm-audit-0.3.153-followup.md)의 W-A04를 위한 실행 근거이며, 전체 의미 품질 인증이나 새 표현 방식의 제품 승격이 아니다.

## 실제 경로와 준비 조건

- 기존 managed E5 listener11236의 같은 프로세스를 launchd 관리 도구와 listener 조회로 확인했다. 구체 PID는 비공개 로컬 영수증에 유지한다. 새 모델·서비스를 시작하거나 프로세스를 채택/중지/재시작하지 않았다.
- 요청/응답 모델 ID는 `mlx-community/multilingual-e5-small-mlx@5030c7625865046d350eeea28f427d80353d0ac0`,384차원이다. 실제 호출은 설치 Resources의 `auto_reply_knowledge_graph.py` 어댑터를 사용했다. redirect·응답 모델/shape 검사는 기존 어댑터 그대로다.
- 관리 도구는 warm readiness를 확인했지만, 기존 고정 스냅샷 디스크 경로가 없어 전체 `ok=false`였다. **실행 중 기능 응답과 디스크 가중치 재검증/재시작 가능성은 별개**다. 현재 가중치 파일이나 RAM 속 가중치 신원까지 검증했다고 주장하지 않는다. 어떤 관리 install/start/stop 가드도 해제하지 않았다.
- 실제 결과는 `outputs/embedding-shadow-20261019/`에 있다. 고정 모델 프로파일·입력·스크립트·raw response cache 해시를 보존한다. 실제 사용자 원문이나 카카오 대화를 평가 자료로 쓰지 않았다.

## 고정한 실험과 결과

가상 문서64개, paired probe32개다. 주제4종×위치3종×배경 반복2종의24쌍과 숫자·단위·날짜·부정·범위·동명이인 등의8쌍을 준비했다. 목적과 임계값/방법은 최초 실제 요청 전에 protocol에 고정했다. 방법을 결과에 맞춰 선택하지 않았다.

모든 방법이 같은 실제 vector를 사용한다. 현재 방식은 원래 label+body를 char256/stride192로 나누고 각 벡터를 단위화해 원래 조각 길이로 가중 합한 뒤 재단위화한다. 비교 방식은 max-chunk, top2-chunk 평균, pooled/max의 고정 .5/.5 결합이다.

| 방법 | 지정 주제 문장 존재 쪽 선호/24 | 지정 assertion 일치 쪽 선호/8 |
| --- | ---: | ---: |
| 현행 pooled | 24 | 7 |
| max-chunk | 24 | 7 |
| top2 평균 | 24 | 7 |
| .5 pooled + .5 max | 24 | 7 |

**이 표는 답변 정답률이 아니다.** 예/아니오 질문에는 일치 assertion과 반대 assertion이 모두 근거가 될 수 있다. 날짜 쌍에서 반대 assertion을 약 .00129 높은 query cosine으로 선호한 것을 곧바로 잘못된 사실 답변이나 제품 결함으로 분류하지 않는다. 결과를 이용해 상충 근거를 숨기는 reranking 규칙을 추가하지 않았다.

짧은8쌍은 각각 단일 chunk여서 네 방식이 같은 점수를 갖는다. 따라서 이 부분은 새 pooling 방식 우열 비교 자료가 아니다.24주제 쌍에서도 이 자료의 preference 기준으로는 상대적 우위가 없었다. 실제 corpus의 장문 품질·독립 gold 또는 더 어려운 혼합 주제에 일반화하지 않는다.

실제 producer에 저장된 passage vector끼리의 cosine은 다음과 같았다.

| 다른 내용 | passage cosine |
| --- | ---: |
| 5개 / 50개 | .99252 |
| 5 mg / 5 g | .99420 |
| 2026-10-20 / 2026-10-21 | .99865 |
| 지원 / 지원하지 않음 | .98242 |
| 기본 꺼짐 / 켜짐 | .98525 |
| 최대5 / 최소5 | .99531 |
| 같은 이름, 파랑 팀 / 초록 팀 | .98088 |
| .5 m / 5 m | .99682 |

주제적으로 가까운 문장은 수치·시각·극성·실체가 달라도 가까울 수 있다. source/claim/identity를 분리하고 원문 span·구조화 값·상충을 보존하는 기존 조건을 유지한다. 이 수치를 새로운 동일성 임계값이나 자동 병합 권한으로 사용하지 않는다.

## 설치 producer와의 실제 일치 검증

고유 입력103개의 실제 임베딩을14개 bounded 요청으로 얻었다. 관리 readiness의 상태 점검1요청과 별도의 설치 어댑터 warm probe1요청은 이 실험 수와 구분한다. 이 세 실행에서 추적한 embedding POST 합계는16개/105입력이며 다른 앱/세션의 사용량은 포함하지 않는다. quota 절감이나 전체 계정 사용량으로 환산하지 않는다.

설치된 `alden_collection.py`와 `alden_collection_retrieval.py`로 별도 audit store에 가상64자료를 ingest/index했다. embed callback은 같은 입력·프로파일에 결속된 실제 응답을 재사용했다. 가짜 벡터를 넣거나 producer 안에서 transport를 다시 호출하지 않았다.64개의 실제 SQLite float32 blob이 기준 pooling의 packed float32와 **64/64 byte-identical**이었다. 이는 현재 pooling 수학과 저장 경로의 일치 근거이며, pooling 품질이 최적이라는 근거는 아니다.

최초 실행에서는 fixture target을 paused로 등록해 `collection_target_paused` 정상 가드에 걸렸다. 제품 가드를 변경하지 않고 audit target을 활성 상태로 ingest한 뒤 완료 후 pause하는 순서로 고쳤다. 최초 스크립트/protocol/log와 raw vector cache를 보존했고, 입력·pooling·scoring 블록이 전후 동일함을 확인했다. 수정 실행의 새 임베딩 요청은0개다. cache의 모델/endpoint/PID/전체 입력 해시 집합·벡터 shape를 확인한 뒤 사용했다.

완료 후 target paused, 문서64개와 vector64개가 남아 있음을 다시 읽었다. 이미 완료된 실행의 반복, outputs 밖의 경로, 준비 후 입력 변경은 세 실제 경계 검사에서 거절됐다. 모델별14요청은64요청 한도,103입력은512입력 한도 안이다. 원시 cache가 producer 실패 뒤에도 보존돼 동일 모델 요청을 반복할 필요가 없었다.

재사용 가능한 현재 도구는 manager 실패를 일반적으로 무시하지 않는다. 실제로 관측한 단일 missing-snapshot warm 경우만 별도로 허용하고, script integrity 오류·다른 모델·loading 상태는 세 추가 검사에서 거절했다. 이 guard 변경 뒤에도 같은 실제 응답으로 전체 producer 경로를 별도 audit store에서 replay해32개 score가 같고64개 packed vector가 다시 일치함을 확인했다. 추가 embedding 호출은0개이며 표본 수를 늘린 것으로 세지 않는다. 최초 성공 코드와 현재 guard 코드의 해시·결과를 모두 보존했다.

## 재현·복구·coverage

새 audit 출력 폴더에서만 실행한다. 이미 완료된 폴더는 재실행을 거절하며 결과와 raw 응답을 먼저 읽는다. 아래 manager 명령은 metadata뿐 아니라 기존 모델에 상태 점검 embedding1개를 요청한다.

```sh
"/Users/twoimo/Library/Application Support/openkakao/runtimes/menubar/bin/python3.11" -B docs/audits/embedding_shadow.py --phase prepare --output-dir outputs/embedding-shadow-20261019
"/Users/twoimo/Library/Application Support/openkakao/runtimes/menubar/bin/python3.11" -B scripts/manage-alden-local-embedding-launchd.py status > outputs/embedding-shadow-20261019/manager-status.json
"/Users/twoimo/Library/Application Support/openkakao/runtimes/menubar/bin/python3.11" -B docs/audits/embedding_shadow.py --phase run --output-dir outputs/embedding-shadow-20261019
```

manager의 원래 JSON은 같은 출력 폴더의 `manager-status.json`으로 보존해야 한다. current source/설치 adapter/profile 또는 listener가 바뀌면 이 결과를 새 버전의 성공 근거로 재사용하지 않는다. missing disk snapshot은 별도 자산 복구·검증 조건이다. 임의 다른 모델·endpoint로 대체하거나 기존 warm 프로세스를 재시작하지 않는다.

검증한 경계는 실제 local HTTP/설치 어댑터, fixture source/version, 실제 설치 producer의 별도 SQLite, 원래 응답 cache/packed 벡터, 완료 후 readback이다. 실제 사용자 자료·1차 앱 UI/물리 입력·두 knowledge host/MCP·multimodal indexing·새 알고리즘 채택·성능 p95·운영 배포는 이 실험의 coverage에 없다. latencies는 matched 전후 비교가 아니어서 속도 향상 수치로 보고하지 않는다.

다음 결정은 현행 pooling 유지, 독립 실사용 gold와 더 어려운 문서/상충 과제 준비, encoder→후보 sampler→consumer→cluster 단계별 recall 평가다. Gemini 반대감사와 최종 역검증에는 이 실제 근거와 명확한 한계를 결속한다.

## 실험 후 같은 revision의 디스크 자산 복구

실험 당시 missing snapshot이라는 관측은 그대로 보존한다. 이후 공식 Hugging Face client0.36.2로 **같은 고정 revision**의7파일만 받아252,568,869bytes를 복구했다. 공식 tree의 LFS SHA256/Git blob SHA1과 크기, 별도 SHA256, owner·쓰기 권한·blob cache 내부 경로를 대사했다. 새 다운로드나 모델 전환 없이 복구 영수증의7파일 해시를 다시 읽어 일치시켰다.

복구 후 manager는 `ok=true`, issues0, exact model/revision·384차원·ready를 반환했다. listener와 launchd PID는 기존1121 그대로다. 이 상태 검사는 실제 embedding1개를 추가 요청했다. 따라서 이전 shadow+warm+초기 상태 점검의16요청/105입력과 별도로 **복구 후 상태 점검1요청/1입력**이 있다. 다른 앱의 요청이나 계정 전체 사용량을 포함하는 집계가 아니다.

`asset-recovery.json`, `asset-recovery-independent-readback.json`, `manager-status-after-recovery.json`은 이 세 경계를 구분한다: 디스크7파일 검증 완료, 기존 warm 서비스 기능 확인, **cold restart/loading은 미검증**. 기존 프로세스를 재시작하지 않았으므로 RAM에 이미 있던 가중치와 새 파일의 동일성이나 재부팅 성공을 소급해 주장하지 않는다. f2adee7의 CI39156314890은 네 작업 모두 통과했으며 제품 runtime은c3871f3/153 그대로다.
