# 알고리즘 감사 후속 판정과 reference 비교

2026-10-19 KST. 설치 runtime은 `c3871f3aa203bebb9a8b0d5bba6b9f3fc908b0b8`/0.3.153 그대로다. 이번 수정은 감사 도구와 방향·평가 계약이며 제품 알고리즘 교체가 아니다. [최초 계획](graph-algorithm-audit-0.3.153.md)과 기존 전체18목표를 유지한다.

## 완료 보고서와 보존

[현재-source ChatGPT 연구 원본](https://chatgpt.com/c/6ad50755-1acc-83e8-9254-144d7c2a0e7d)은 완료 카드13m/15인용/82검색을 표시했다. 원래 Markdown download와 `outputs/algorithm-audit-20261019/chatgpt_6_pro_full_review.md`는 byte-identical 55,699bytes, SHA256 `89f7dd4f5c998c81f506d5cd99901e7b46da801fc57a2fec90d812c341b9972c`다. 원본 계획 `bb77e7c3b4151a4c4c968bb768830593732b3718b092f8fe577fadf36b8e1ec7`와 bundle `4c14fcca5fc24c1e6e526ed8bce53e358c379f8fae1c2b789d6ba551808e471b`에 결속한다. 전문 저장/해시 대사 후 소유자가 `3e0b605faea2571e8bf90912` 슬롯을 정상 해제했다. 기록은 `report-completion.json`에 있다. 내부 연구 backend 모델 신원은 미확인이다.

이 연구는 기존137461c Gemini 반대감사나 최종 ChatGPT reverse가 아니다. 기존 Gemini 원본·예약을 보존한다. 새 연구의 완료를 외부3단계 왕복 완료로 합산하지 않는다. 보고서가 주장한 독립 실행도 로컬 재현과 구분한다.

## 원인과 실제로 적용한 정정

- 3브리지 cliff의 직접 원인은 연결 요소별 null-model 분모와 implicit gamma=1의 목적함수다. native source를 읽기 전용으로 컴파일한 probe에서도 gamma/분모만 바꿔 원인을 재현했다. 8회 상한이나 계층 부재를 이번 cliff의 직접 원인으로 설명하지 않는다.
- clean은3~7회, bridge는4~5회에서 이동이 멈췄다. 상한8에 도달하지 않았다는 실제 진단이 위 정정을 지지한다. 상한 자체를 늘리는 패치를 하지 않았다.
- 이미 본 3브리지와 기존27검색 질의는 regression이다. 최초 계획의 '고정 holdout' 표현을 수정했다.
- 모든 predicted auto-merge positive의 독립 이중 판정, source/entity family 표본 단위,600/200/300/30의 pilot 범위를 계획에 반영했다. 정밀도 gate를 통과해도 원문 삭제나 새 자동 병합 권한이 생기지 않는다.
- 설치 source·원문·ID·벡터·권한·큐·모델을 보존했다. 이 단계에 의미 정답이나 실제 사용자 판정은 추가하지 않았다.

## 실제 reference 실험

공식 PyPI에서 현재 stable `leidenalg==0.12.0`, `igraph==1.0.0`, 의존 `texttable==1.7.2`의 wheel을 격리된 audit venv에 설치했다. 제품/voice/menu 런타임에 설치하지 않았다. 버전·registry release 파일 해시·pip install report는 `outputs/algorithm-reference-20261019/`에 있다. 참고: [공식 release](https://pypi.org/project/leidenalg/0.12.0/), [Leiden 공식 구현](https://github.com/vtraag/leidenalg), [objective 문서](https://leidenalg.readthedocs.io/en/latest/reference.html).

`audit_alden_graph_communities.ts --include-inputs`는 실제 엔진 checkpoint의 정규화 관계 가중치를 내보낸다. 현재 실험은 완전한 양의 구조 간선만 포함하며 raw degree-imbalance community gate를 재구성한다. 간선/노드가 잘리거나 semantic 간선이 있으면 exporter가 거절한다. 이 경계 밖에서 '정확히 같은 입력'을 주장하지 않는다.

실험 전에 `preregistered.json`으로 generator/seed/CPM grid/선택 규칙/코드 해시를 고정했다. 개발 seed1..5의30조건에서 gamma `.001,.002,.005,.01,.02,.05,.1` 중 disconnected 수→macro ARI→작은 gamma 순으로 `.01`을 선택했다. selection SHA256은 `c083bb73af95e6321cc15305ebd26d02fb4c56a568770368f921ca616f49b5a8`이다. 그 뒤 처음 seed101..120의120조건을 생성·평가했다. 선택 이후 코드를 바꾸거나 holdout에서 gamma를 다시 골라 쓰지 않았다.

| 방식 | 개발30 macro ARI | 새 seed120 macro ARI | 새 seed의 index 치환 ARI 최소/중앙값 | 분할이 달라진 조건/120 |
| --- | ---: | ---: | --- | ---: |
| 연결 요소 baseline | .2933 | .2945 | 1 / 1 | 0 |
| 현행 | .6819 | .6754 | .3362 / .9119 | 63 |
| Leiden, 연결 요소별 modularity | .7335 | .7295 | .3770 / 1 | 56 |
| Leiden-CPM, dev선택 gamma=.01 | .8760 | .8922 | .5689 / 1 | 36 |
| Leiden, global modularity | .8952 | .9111 | .5064 / 1 | 23 |

모든 방식에서 이번 derived community graph 위의 disconnected 군집은0이었다. 중앙값1만 보고 이름 치환 안정성이 보장됐다고 해석하면 안 된다. 희소 입력에서 CPM도 군집 수 중앙값23, global21로4개의 planted label을 완전히 복원하지 못했다. 같은 계열의 새 seed 평가이며 LFR/새 생성기 가족·실제 독립 의미 gold가 아니다. ARI 수식은 Python exact Fraction 구현으로 TypeScript 결과와 대사했다.

희소 조건의 파생 입력 자체가 연결 요소 중앙값20.5개다. 그러므로 connected-community 방법이 네 개의 생성 블록을 정확히 복원할 수 없는 경우를 포함한다. 낮은 ARI 전부를 partition optimiser의 결함으로 돌리지 않고, 원시 관계→degree-imbalance gate→파생 연결성/후보 recall과 partition 품질을 나눠 평가한다. 생성 블록과 파생 그래프의 목적함수가 같은 정답을 표현한다고 가정하지 않는다.

같은 입력 가중치로 비교했지만 global/component/CPM의 목적함수는 다르다. reference는 seed0/n_iterations2이며 현행8회와 계산 예산 동등성을 입증하지 않았다. partition 시간은 source 검증·후보 계산·레이아웃/렌더 시간을 포함하지 않으므로 속도 향상으로 보고하지 않는다. global의 높은 ARI만으로 제품 승격을 선택하지 않는다.

보고서의 순수 partition 함수 diff도 그대로 적용하지 않았다. 제안 함수는 singleton label로 시작하지만 실제 엔진은 reflow가 아니면 기존 `this.labels`를 이어 쓰고 영향받은 노드만 방문한다. cold run의 objective가 같다는 이유만으로 warm checkpoint·active mask·핀·worker authority의 기존 동작까지 보존됐다고 볼 수 없다. 추출/승격 전 기존 증분·동일 snapshot 무작업·연결 요소/핀/취소·권한 변경 회귀와 label/anchor parity를 요구한다. 현재 probe는 앱 밖에서만 실행해 이 경계를 바꾸지 않았다.

### global 분모의 반대 사례

`objective_probe.mjs`는 실제 `semantic-layout.ts`의 정확한 score anchor가 한 번 존재함을 확인하고, 원본을 수정하지 않은 채 audit 출력에만 세 임시 변형을 컴파일한다. 이 도구는 Leiden 구현이 아니다. production gamma나 옵션을 추가하지 않는다.

5노드 A의 관계는 `01,02,03,04,13,14,24,34`다. A와 완전히 분리된 K5를 추가하면:

| objective probe | A 단독 | 무관 K5 추가 후 A |
| --- | --- | --- |
| 현재 component/gamma1 | {0,2,4}, {1,3} | 동일 |
| 임시 global/gamma1 | {0,2,4}, {1,3} | {0,1,2,3,4} |

즉 global은 쉬운4블록 점수는 좋아도 무관한 데이터 추가에 영향을 받는다. CPM·hierarchy를 비교할 근거이지 특정 gamma나 새 알고리즘이 실제 지식의 정답이라는 근거는 아니다. 원본 source SHA와 변형 diff/compiled SHA는 `objective-probes.json`에 결속한다.

## 외부18개 지적의 로컬 판정

기존19개 finding 및 ALG-001..006과 아래 ID를 서로 덮어쓰지 않는다. `REJECT` 대상은 표의 잘못된 주장/단순 패치이며 외부 보고서 전체를 기각한다는 뜻이 아니다.

| ID | 로컬 판정 | 적용/조건 및 현재 근거 | 작업·필수 검증·남은 조건 |
| --- | --- | --- | --- |
| ALG153-COMP-001 | ACCEPT | 실제 source objective probe와30/120조건이 분모·해상도 민감성을 재현. 계획의 원인을 정정 | A01/A03: 구조·의미 채널별 objective/해상도 비교; 실제 의미 gold 남음 |
| ALG153-PASS-002 | REJECT: '이번 원인은8회 상한' | 실제 진단 clean3~7/bridge4~5. 상한 증대 패치 없음 | A03: 다른 stress의 상한 영향은 별도 평가 |
| ALG153-HIER-003 | ACCEPT | hierarchy는 기능 갭이며 cliff의 필요 원인이 아니라는 구분을 계획에 적용 | A03/A05: 검증된 계층 및 zoom 소비 경로 구현/실기 남음 |
| ALG153-ID-004 | ACCEPT | index 치환의 ARI 분포와 최솟값까지 공개. source의 in-place 방문 순서 확인 | A01/A03: 전 후보 seed·ID·편집 안정성 분포; 불변 partition 보장 금지 |
| ALG153-GLOBAL-005 | REJECT: 단순 global 복귀 | native-source A+K5 counter-proof로 무관 component 영향 재현 | A03: global은 reference로만; 기존 경계 유지 |
| ALG153-LMOD-006 | CONDITIONAL | 공식 구현으로120조건 실행 완료. 같은 component objective에서도 결과가 달라짐 | A03: 실제 의미·채널·계산 예산·호스트 경로 검증 전 미채택 |
| ALG153-LCPM-007 | CONDITIONAL | dev30에서gamma .01 고정 후 새 seed120 실행 | A03: 새 가족·가중치 scale·semantic graph·실제 gold/호스트 전 미채택 |
| SEM153-E5-008 | DEFER | producer 소스 위치를 추가 확인. 원 보고서의 입력 부족과 제품 부재를 구분 | A04: collection/OSK pooling+E5 server 전문을 다음 감사 입력에 포함; 같은 모델의 실제 장문 비교 남음 |
| SEM153-SPARSE-009 | ACCEPT | raw encoder→sampled pairs→producer cap→consumer cap→cluster를 분리 평가하는 계약 추가 | A04: 각 단계 pair/neighbor recall·실제 유효 비교 범위 측정 남음 |
| ID153-DUP-010 | ACCEPT | 원문 중복/검색 텍스트/동일 주장/동일 실체의 gold 구분과 명시 overlay 유지 | A02: 독립 사람 판정·가족별 split·실제 merge/revert 전 범위 남음 |
| UX153-ZOOM-011 | ACCEPT | 현재는 screen-space policy라는 표현으로 정정. extractive 대표 라벨 baseline 우선 | A05: 대표성/차별성/편집 안정성·물리 탐색 성공 검증 남음 |
| RET153-SYN-012 | REJECT: '.9583/1.0이 E5 품질' | evaluator와 실제 offline 결과에서 fixture dense를 확인. 계획·PR의 범위 제한 유지 | A06: 같은 모델 실제 encoder와 독립 실제 질의 gold 남음 |
| STAT153-HOLD-013 | REJECT: '알려진3브리지 final holdout' | 계획을 regression으로 수정. 신규 seed는 selection 고정 후 처음 생성 | A01/A02: 새 생성기/외부 담당자·실제 독립 정답은 별도 필요 |
| STAT153-PAIR-014 | CONDITIONAL | exact beta 검산598/947/1258/1549.600을pilot로, merge positive 전수 이중 판정으로 수정 | A02: IID 미충족 시 단순binomial claim 금지; 오류 후 사전 규칙/별도 frozen test 필요 |
| STAT153-NORM-015 | ACCEPT |200무손실의95%오류상한1.4867%검산.200은회귀로한정 | A04: 실제span·mutation/metamorphic suite; 모집단0오류 주장 금지 |
| STAT153-RET-016 | CONDITIONAL | paired SD.3→385/.4→683 계산 검산.300의 충분성 보장 삭제 | A06: 별도dev분산·source-family bootstrap·final test freeze 필요 |
| PERF153-017 | DEFER |59전수통과가95% pass-probability 단측95%하한이라는 수학만검산.30은pilot로수정 | A07: 실제60+/100+run·block trace·물리입력/프레임·CPU/GPU/RSS/전력 남음 |
| SOTA153-018 | DEFER |세계최고/의미군집/설치성능인증을 하지 않음 | A02~A07: 독립gold·동등budget·공개/외부재실행·실제호스트와외부왕복 필요 |

`outputs/algorithm-reference-20261019/statistical-checks.json`의 SciPy exact beta 검산은 실제 자료 수집이나 통계 gate 달성 증거가 아니다. 외부 보고서의 수치를 복사해 통과로 처리하지 않았다. 600개 중0오류의 정밀도 하한은99.50196%,1오류면99.21182%다. 같은 가족의 쌍을 독립 표본으로 취급하는 것은 여전히 허용되지 않는다.

## W-A01~W-A07 전달 상태

- **A01 ACCEPT/부분 완료:** 도구·입력 해시, 알려진 반례 regression, native objective probe 완료. 새 가족/의미 반례 확장 남음.
- **A02 CONDITIONAL/미완료:** 판정 정책·표본 계약을 수정했다. 기존44후보는 미검토다. 사람이 판정한 독립 gold를 생성 모델로 채우지 않는다.
- **A03 CONDITIONAL/부분 완료:** 공식 reference와 새 seed120조건의 실행·코드/selection 고정 완료. semantic/mixed 채널, LFR·새 크기·가중치·증분/호스트 검증은 미완료.
- **A04 CONDITIONAL/미완료:** pooling producer가 실제로 있는 경로를 확인했다. 단계별 recall과 같은 모델의 shadow 표현 평가는 아직 없다. 기존 모델 유지.
- **A05 CONDITIONAL/미완료:** 현재 zoom/라벨과 semantic hierarchy의 구분을 명시했다. 구현·물리 검증 남음.
- **A06 CONDITIONAL/미완료:** 회귀27질의·실제 질의·공개 benchmark를 구분했다. 독립 실제 질의·live encoder·비용 평가 남음.
- **A07 DEFER/미완료:** 기존 설치153·서명/원문 보존을 유지한다. 새 후보 제품 승격·물리/자원 검증·Gemini+최종reverse·운영 릴리스는 열려 있다.

새 연구 전문·판정·실행 결과와 실제 pooling producer를 결속한 다음 반대감사/역검증 입력을 준비한다. 원래 Gemini를 취소하거나 복제하지 않고 공유 admission을 따르며, 실제 외부 실행 전 계정/모드/할당량을 재확인한다. 준비·대기·완료 상태는 각각 기록한다.
