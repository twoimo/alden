# Graphify를 Alden에 적용할지에 대한 검토

2026-10-19. **로컬 재현을 마친 조건부 방향이며 외부 교차감사는 아직 미완료다.** Graphify 전체 도입이나 Alden 알고리즘 교체를 승인한 문서가 아니다. 감사 입력은 Alden 0.3.153/runtime `c3871f3aa203bebb9a8b0d5bba6b9f3fc908b0b8`에 결속한다. 현재 설치본은 별도 [Threads 갱신 호환 수정](threads-source-refresh-0.3.154.md)을 포함한0.3.154/runtime `659d3b5cfea08fe26e856d55cdd1f5dac7fd67a8`이며, 그래프 알고리즘과 정규화 정책은 이번 호환 수정으로 바꾸지 않았다. 기존 [알고리즘 감사](graph-algorithm-audit-0.3.153.md)와 [후속 실험](graph-algorithm-audit-0.3.153-followup.md)을 잇는다.

현재 단계는 **ChatGPT thesis 전문 저장·로컬 판정 완료 / Gemini 반대감사 대기 / ChatGPT 최종 역검증 미시작**이다. 원래 전송·모드·첨부·연구 시작·완료·export UI 도구 결과8개를 해당 대화ID가 일치하는 원본 transcript에서 추출해 `ui-evidence-index.json`에 결속했다. 부모의 독립 byte/hash 대조 영수증도 공통 completion manifest에 연결했다. 이는 독립 제품 수리나 최종 외부 합의의 증거가 아니다.

## 조사 대상과 의도

사용자가 원하는 결과는 유용한 주제 라벨, 덜 겹치는 탐색, 출처를 잃지 않는 정규화와 중복 판정, 신뢰할 수 있는 의미 관계다. Graphify의 예쁜 화면이나 저장 노드 수는 이 결과의 증거가 아니다.

[Graphify 공식 저장소](https://github.com/Graphify-Labs/graphify)는 `v8`이 기본 branch다. 확인한 최신 공식 release는 [v0.9.92](https://github.com/Graphify-Labs/graphify/releases/tag/v0.9.92), 2026-10-16 게시이며 tag와 조사 HEAD가 모두 `86c57be8204673c2a1b9849a3292a7dd3ab373d8`로 일치했다. 제품 의존성·에이전트 설정·MCP는 설치하지 않았다. 공식 소스를 별도 audit 디렉터리에 받고, 그 `uv.lock`에 있는 NetworkX 3.6.1, NumPy 2.4.6, RapidFuzz 3.14.5 등만 격리 venv에 설치했다.

소스의 `pyproject.toml`은 Apache-2.0을 표시하고 `LICENSE`, `LICENSE-MIT`, `NOTICE`를 함께 제공한다. NOTICE는 과거 MIT 기여분을 설명한다. 실제 코드 복사나 배포 시 해당 파일·개별 출처·의존물 고지를 확인한다. 이 검토에서는 upstream 코드를 Alden runtime에 복사하지 않았다.

## 코드에서 확인한 차이

| 영역 | Graphify의 실제 경로 | Alden에 대한 판단 |
|---|---|---|
| 관계 추출 | tree-sitter AST + 선택적 LLM 문서/미디어 semantic pass. 구조·의미 출처를 `_origin`으로 구분 | 코드 자료를 위한 별도 producer 후보. 현재 수집·원문·권한의 대체물이 아님 |
| 근거 | `EXTRACTED`, `INFERRED`, `AMBIGUOUS`, 별도 confidence score, source file/location | 증거 종류와 추론 여부를 노출하는 패턴은 유용. 확률 보정이나 내용의 참을 보증하지 않음 |
| 군집 | native Leiden→graspologic wrapper→NetworkX Louvain 순서. global modularity, seed42, 크기/밀도에 따른 재분할 | 설치 의존성에 따라 실효 backend가 달라짐. backend/objective/seed/입력 해시를 반드시 기록하고 같은 입력 비교 |
| 대표 라벨 | 기본 `label_communities_by_hub`: 전체 그래프에서 degree가 가장 큰 멤버의 저장 이름. 선택적 LLM 라벨은 별도 | 일반 동영상·채널명 같은 현재 문제를 해결하지 못하는 baseline. Alden의 현재 라벨 제외 규칙을 되돌리지 않음 |
| 화면 | vis-network forceAtlas2 기반. degree에 따른 0/12px 라벨, 검색·hover·메타 그래프 | 현재 Alden의 연속 zoom·충돌 회피를 교체할 근거 부족. 그래프 공간과 저장 지식의 계층을 구분 |
| 중복 | NFKC/casefold/구두점 접기→동일 source의 exact 병합→MinHash/LSH+문자열 검증→union-find | 후보 생성 기법만 검토. 동일 문자열을 실체 동일성·주장 동등성으로 자동 승격하지 않음 |
| 증분 | cache/mtime·graph schema/version·보호된 untouched ID·community membership signature | source content hash/version과 함께 적용할 수 있는 패턴. 파일 변경·수집 실패·철회·원본 삭제는 서로 다른 사건 |
| 검색 | 로컬 graph traversal/query와 별도 benchmark harness | E5/FTS를 제거할 근거 없음. 동일 corpus/query/budget에서 lexical+dense+graph를 분리 평가 |

README의 ‘임베딩 없음’은 CLI graph 표현 설명이다. `BENCHMARKS.md`의 memory harness는 로컬 BGE-m3와 hybrid retrieval을 명시한다. 80% token 절감·최고 성능·정형 검증 같은 상단 Cloud 설명을 Alden의 실측치나 이 OSS CLI의 보증으로 옮기지 않는다. LOCOMO 300·LongMemEval-S 50·code 질의6의 저자 보고는 한국어 개인 지식에 대한 독립 실험이 아니다.

## 실제 로컬 반례

[재현 도구](audits/graphify_probe.py)는 공식 revision과 변경 여부를 확인하고 upstream 함수를 직접 호출한다. 개인 자료·API 키·LLM 호출을 사용하지 않는다. 근거는 task root의 `outputs/graphify-audit-20261019/`에 보관한다.

| 입력 | 실제 결과 | 적용 한계 |
|---|---|---|
| 같은 파일의 서로 다른 김민수 두 명, 다른 ID·위치·실체 표시 | 노드2→1 | 같은 문서라는 사실이 동일인 증명이 될 수 없음 |
| 개념 C++ / C# | 2→1 | 구두점 제거가 식별 의미를 지울 수 있음 |
| `10 mg` / `-10 mg` | 2→1 | 부호를 보존해야 함 |
| `3.14` / `3/14` | 2→1 | 수량·날짜의 타입/문맥을 보존해야 함 |
| 같은 개념의 대소문자·공백 반복 | 2→1 | 의도한 정리 사례 |
| GPT-6 / GPT-7 | 2 유지 | upstream 숫자 형제 보호가 동작한 대조군 |
| 출처 없는 같은 label / 서로 다른 repo / protected incremental | 각각 유지 / 거절 / 유지 | 세 보호 대조군 모두 통과 |
| 별형 그래프의 중심을 ‘일반 동영상’으로 지정 | 기본 대표 라벨도 ‘일반 동영상’ | degree는 대표 의미의 정답이 아님 |
| 삼각형 두 개+브리지의 A에 무관 K20 연결 요소를 추가 | A의 군집이2→1로 바뀜. `_partition`과 최종 `cluster` 모두 동일 | 이번 Louvain 경로의 global 목적함수 영향. Leiden 경로의 실제 실행 결과로 확대하지 않음 |

여섯 중복 사례 중 네 개는 **Alden의 실체/주장 보존 계약과 충돌했다.** 의도적으로 고른 반례 수이며 실제 오병합률·한국어 전체 품질·Graphify 전체 결함률이 아니다. 처음의 다른 5노드 A+K5 probe에서는 분할 변화가 없었다. 그 결과도 `probe-results-first-small-A.json`에 보존하고, 이후 삼각형 반례로 바꾼 탐색 실험임을 명시한다. 이들 모두 이미 본 regression이며 holdout으로 다시 세지 않는다.

upstream의 군집·중복·hub label·survivor 관련173검사는 통과, Leiden binding 관련2검사는 skip됐다. 첫 실행은 audit 환경의 tree-sitter 의존성 누락과 subprocess의 다른 cwd로20failure/1error였다. 원본 log를 보존하고 공식 lock의 tree-sitter0.25.2를 격리 환경에 추가한 뒤 upstream cwd에서 같은 검사만 재실행했다. 이 환경 오류를 제품 결함으로 세지 않았다.

외부 연구 dispatch 후 별도 AST 경로도 확인했다. 직접 만든 Python2파일에서 동명 Validator 클래스 두 개가 다른 ID·source span으로 남았고, 전체8노드/10간선을 추출했다. 첫 실행과 cache replay의 정렬된 nodes/edges SHA256은 `988b45d2e11e50d19c16214cd8bb0f6a985c9748943cbdffe6e3ef1306176372`로 같았다. 해당 추출 중 socket 연결/DNS를 거절하는 계측의 네트워크 시도는0이다. 가상 작은 코드 fixture의 결과이며 Alden adapter·다른 언어·실제 저장/UI 통합은 수행하지 않았다. 이 후속 결과는 처음 제출한 source bundle에 들어 있지 않으며 다음 반대감사의 delta로 다룬다.

여러 관계의 보존도 직접 확인했다. 같은 두 노드 사이의 정방향 `calls/uses`와 역방향 `calls` 세 관계를 넣으면 기본 simple Graph는1간선, DiGraph는2간선을 보유한다. 기본 export도 각각1/2개다. 공식 `to_json(... original_links=...)` 경로를 사용하면 두 경우 모두3개 원본 관계와 `multigraph=true`가 저장됐다. 따라서 모든 경로에서 관계가 손실된다고 판정하지 않는다. adapter가 원본 관계 ledger와 방향/버전/근거를 별도로 보존하고 이 명시 경로를 사용하는 조건이다. 근거 `edge-roundtrip-results.json`과 네 export 파일을 보존했다.

## 판정과 작업 순서

대안은 ①전체 엔진 대체, ②코드 source adapter만 추가, ③유용한 패턴을 현 구조에 반영하고 shadow 비교하는 세 가지다. 현재는 **③을 우선하고 ②를 조건부 후보로 남긴다.** 전체 대체는 출처·동일성·증분 상태·권한·설치 의존성 위험을 늘리며 측정된 제품 이익이 없다.

| ID | 판정 | 구현/개선 후보와 위험 | 검증·전달 gate |
|---|---|---|---|
| GF01 | CONDITIONAL | 코드 전용 tree-sitter producer. lexical owner·언어·repo·source version·정확한 span 보존. 결과는 adapter 경계로 수용 | tiny code corpus에서 함수/상속/호출·동명 심볼을 독립 수작업 판정; parser-only 네트워크0; 기존 수집 정책/원문 불변. 검증 전 제품에 등록하지 않음 |
| GF02 | ACCEPT: 방향 | 관계의 직접 추출/추론/모호함을 구분하고 원문 위치·적용 시점·철회 근거를 조회 가능하게 함. 기존 provenance와 중복 규칙을 먼저 대사 | 같은 UI→저장→MCP 경로에서 근거 roundtrip, 잘못된 버전·권한·철회·반대 주장 regression. confidence를 calibrated probability로 표시하지 않음 |
| GF03 | REJECT: 그대로 자동 병합 | MinHash/문자 유사는 후보 검색으로만 사용. 원문·수치/부호/타입/시간·speaker·외부 ID와 alias overlay를 유지 | 위 반례+새 source/entity family, predicted merge positive 독립 이중 판정. 기존 A02의 사전 표본/정밀도 계약·실제 merge/revert 복구 통과 전 쓰기 금지 |
| GF04 | CONDITIONAL | Graphify backend를 명시한 reference 하나로 비교. 현 component objective·Leiden·CPM baseline과 입력/가중치/시간 예산 통일 | 이미 본 반례는 regression. 새 가족·크기·희소/허브·반대/철회·semantic/mixed 채널과 ID/편집/증분 안정성을 별도 평가. CPU/GPU/RSS/p50/p95는 동일 조건에서 측정 |
| GF05 | ACCEPT: 방향 | degree hub를 대표 주제 정답으로 쓰지 않음. 원문에서 뽑은 주제의 coverage·차별성·일반 라벨 penalty·중복 label·변경 안정성을 평가 | zoom별 label overlap/잘림, hover/focus 원문 접근, 실제 사용자의 탐색 성공·잘못된 출처 선택을 측정. 지금의 zoom 정책을 semantic hierarchy로 부르지 않음 |
| GF06 | CONDITIONAL | content hash+version+schema 기반 증분 snapshot. cluster membership signature는 stale label 탐지에 사용하고 영구 실체 ID와 구분 | 같은 입력 replay, rewrite/same-mtime·부분 수집·중단·삭제/철회·복원·동시 writer 테스트. mutation log/이전 snapshot으로 복구 가능해야 함 |
| GF07 | DEFER | graph expansion을 hybrid retrieval 후보로 추가 | lexical/dense/hybrid/graph ablation, 원문 근거 recall·사실 정확도·반대 주장 회수·latency/cost를 동일 frozen query/source split에서 측정. 저자 benchmark나 작은 fixture를 제품 개선으로 보고하지 않음 |

`ACCEPT: 방향`은 방향에 대한 로컬 판정이다. 해당 제품 기능의 구현·검증·외부 합의가 끝났다는 뜻이 아니다. GF01→GF02/06→GF03/04/05 shadow→GF07 retrieval→승격 순서를 사용하되, 기존 A01~A07과 전체18목표를 중복 생성하지 않고 연결한다. GF03은 A02, GF04는 A01/A03, GF05는 A05, GF07은 A04/A06, 승격은 A07에 귀속한다.

독립 실제 정답은 사람이 source/entity family 단위로 판정하고 dev/test를 분리한다. 수치 gate는 기존 감사의 pilot/통계 한계를 유지한다. 기존44 private 후보·개인 원문·원시 실행 상태를 cloud 연구에 올리지 않는다. 원문 삭제·자동 병합·기본 모델 교체·새 paid capacity·정식 배포는 이번 조사에 포함하지 않는다.

### 후보를 제품에 승격할 때의 종료 기준

아래는 미래 후보를 판정할 사전 gate다. 이번 작은 반례·173검사가 통과했다고 충족되는 기준이 아니다.

| Gate | 측정과 통과 기준 | 실패·복구 |
|---|---|---|
| source/identity | 고정 parser fixture의 예상 심볼·owner·파일·span 전수 일치, 동명이인/부호/소수/날짜 regression의 오병합0. 원본 hash·권한·버전·철회 ledger 전수 보존 | adapter shadow만 유지. 원문/기존 색인 변경 금지 |
| 관계 roundtrip | raw relation의 source/target/방향/관계/근거/버전 tuple이 재조회·export·MCP에서100% 일치; 오류/중단/replay에서도 손실·중복0 | 원래 raw ledger에서 재구축. simple graph를 canonical 원본으로 승격하지 않음 |
| merge precision | 기존 A02의 독립 predicted-positive 이중 판정과 사전 family split. 목표99.5%의 단측95%하한을 family-aware 검정으로 충족해야 하며 표본/방법은 test 공개 전에 고정 | 숫자 gate만으로 identity/truth 권한을 얻지 않음. 미검토·상충은 별도 상태, 가역 overlay만 허용 |
| 군집 | 이미 본 component/허브/3브리지 반례와 incremental replay 통과. 무관 component 추가가 기존 component를 바꾸는 candidate는 현재 Alden 경계에 부적합. ARI/AMI·연결성·과분할·seed/ID/편집 안정성의 분포를 함께 공개 | global로 단순 회귀하지 않음. 품질/계산 예산의 사전 우선순위 없이 ARI 최고값만 선택하지 않음 |
| label/zoom | 고정 viewport·zoom·focus corpus에서 label끼리 겹침/잘림0, 숨겨진 노드의 hover/focus·원문 접근100% 유지. 블라인드 실제 탐색 성공률·원문 선택 오류·소요시간을 baseline과 paired 평가 | label count를 줄인 것만으로 의미 품질 개선을 선언하지 않음. 기존152 정책으로 복구 |
| 성능/운영 | 같은 장치/자료/전원·warm/cold 조건에서 source→UI p50/p95, 전체 process-tree RSS·CPU/GPU·전력을 측정. 허용 회귀와 최소 의미 개선량을 독립 pilot 후 test 전에 고정. 기존 RAM admission·취소·권한·큐 보호 회귀0 | 현재 runtime/model로 복구. 아직 기준선이 없으므로 현재 성능 gate 통과/절감률은 미확인 |

이 gate와 공개/외부 재실행 가능한 protocol, 독립 실제 gold, 동등 예산 비교를 확보해야 ‘최고 수준’을 논의할 수 있다. 현재 결과는 그 검증 방향을 구체화한 것이며 순위나 제품 품질 인증이 아니다.

## 외부 감사 상태

Graphify는 이전 ChatGPT 논문에 없던 material delta이므로 공개 upstream 코드·선택된 Alden 코드·본 계획·가상 반례만 묶어 GPT-6 Pro + Deep Research thesis를 요청한다. 전체 보고서를 원문/URL/hash로 저장하고, 공유 coordinator로 Gemini Argon 최고/Deep Research 반대감사와 ChatGPT 역검증을 이어야 한다. 기존 원래 연구·queue·owned tab을 대체하지 않는다.

현재 Gemini는 공유 슬롯 부족으로 추가 dispatch되지 않았다. 해당 단계를 ‘대기’로 남기며 thesis 완료도 3단계 검증 완료로 합산하지 않는다. 최종 결정 행에는 외부 지적→판정→현재 source→실제 조치/조건→검증을 결속한다. 모든 gate가 충족되기 전 세계 최고 수준이나 교체 방향 검증 완료를 선언하지 않는다.

실제 새 thesis URL은 [Graphify 적용 심층 연구](https://chatgpt.com/c/6ad519f8-d304-83ee-9a11-dc34d6b9152c), 공유 예약은 `ba8b26d99d8e87908cb15b8c`다. 제출 master SHA256 `d8804394043fe71da492effe35c068f9aab30be93e58300c4d0ff8ceb42df757`, bundle SHA256 `407de68f601765dba13b9f2067d90316cc113f0496f7636607e06dbcddf6d692`/286,994bytes. 원래 userturn1개를 Pro ledger에, 실제 research start는 별도 meter에 기록했다. 계정 숫자 할당량·reset과 연구 내부 backend 신원은 노출되지 않아 미확인이다. `research-start.json`은 실제 시작 영수증이며 전문 완료 영수증이 아니다.

### 완료한 thesis와 후속 로컬 판정

이후 원래 연구가8m 완료 카드로 전환됐다. 정식 Markdown export63,019bytes를 수정 없이 `outputs/graphify-audit-20261019/chatgpt_6_pro_full_review.md`에 저장하고 다운로드 원본과 byte-identical임을 확인했다. SHA256은 `489f235f507b5976834b59f5e9fdda0c7f7505884ecfb41747f0f670f6da117e`다. 저장 직후 소유 예약을 completed로 정상 해제했다. 최초 제출본과 이 후속 판정을 혼동하지 않는다.

보고서는 전체 대체REJECT, 코드 producerCONDITIONAL, 선별 패턴ACCEPT, 자동 병합REJECT, 군집CONDITIONAL, graph expansionDEFER로 판정했다. 이를 제품 승격 승인이나 외부3단계 완료로 해석하지 않는다. `adjudication.json`은 GF01~07을 근거·조건·기존A01~07·native 검증·복구에 매핑한다. 다음 추가 조건을 반영했다.

- GF01의 제안 `startByte/endByte`는 실제 tiny AST 결과의 `source_location=L…`와 다르다. byte 범위를 만들어 넣지 않는다. 현재 줄 위치 수준을 명시하고, UTF-8 원문과 parser node 범위를 검증한 경우에만 byte-precise라고 표시한다.
- GF02의 근거 tag도 producer가 낸 주장이다. 현재 source version과 실제 위치/관계 구문을 대사한 범위에서 직접 관찰이라고 표시하며, enum만으로 resolver의 대상 결정을 신뢰하지 않는다.
- GF03의 `cannot_link`는 node 종류별 동일성 기준으로 제한한다. 같은 사람의 직무·주장·수량·시간이 달라지는 것은 동일인을 갈라야 할 일반 규칙이 아니다. entity identity와 서로 다른 claim occurrence를 분리한다. 보고서의 모든 facet을 모든 entity에 기계 적용하는 schema는 채택하지 않는다.
- GF04의 첫 무변화 probe는 **다른5노드 A5+K5**, 변화 probe는 **6노드 A6+K20**이다. 같은 A에서 K 크기만 바꾼 실험이 아니다. 원본 두 결과를 유지한다. 삼각형 A6의 두 후보 modularity 차이를 exact Fraction과 NetworkX로 검산했으며 m7에서5/14, m197에서-345/77618로 일치했다. 같은 알려진 A6를 component별 Graphify Louvain으로 호출하면 K20 추가 후에도 두 삼각형이 분리됐다. 이 한 regression의 제한된 prototype이며 제품 패치·새 holdout·Leiden 결과가 아니다.
- GF06의 cache 본문은 최초 외부 bundle에 없었다. 후속으로 실제 함수를 점검했고, 아래의 preserved-mtime 반례를 별도 근거로 추가했다. 논문이 독립 실행했다고 주장하지 않은 점과 저자 benchmark/Cloud/OSS 경계는 유지한다.

### 추가 GF06 반례: 과거 mtime을 복원한 같은 길이 수정

같은 공식 revision의 `file_hash`와 `extract`를 실제 audit 파일시스템에서 호출했다. `def alpha()`를 저장하고 mtime을 과거로 고정해 캐시한 뒤, 같은 길이의 `def omega()`로 바꾸고 원래 mtime을 복원했다. 원문 SHA256과 ctime은 바뀌었는데 cache key와 추출 결과는 `alpha()` 그대로였다. 일반 mtime 변경 대조에서는 새 key와 `omega()`로 갱신됐다. 개인 자료·Alden store를 사용하지 않았다.

이는 같은 프로세스의 명시적 추출/cache API에서 재현한 것이다. 전체 CLI의 detection 경로·다른 프로세스·Graphify Cloud의 동작으로 확대하지 않는다. 원인은 해당 stat fastpath가 size/mtime/indexed-at로 freshness를 판정하는 경계다. 현재 producer에 그 캐시 판정을 그대로 위임하는 것은 **REJECT**다. Alden source version의 현재 바이트를 검증한 manifest/격리 snapshot을 먼저 사용하거나 검증 불가능한 cache reuse를 거절해야 한다. 단순 ctime 추가만으로 모든 race를 해결했다고 주장하지 않는다.

재현 소스 `cache-preserved-mtime-probe.py`, `cache-preserved-mtime-results.json`, 일반 변화 대조와 원본 code/grammar pin은 audit 폴더에 있다. component/math 검산은 `component-experiment.py/.json`이다. 이 두 보강 실험은 이미 완료된 ChatGPT thesis 제출 이후의 delta이며, 새 Gemini 반대감사와 최종 ChatGPT 역검증에서 반드시 별도 확인해야 한다.

새 Gemini 입력은 원문 thesis를 그대로 포함한 master88,540bytes/SHA256 `c8f6c20f9ac2700af0a58846fb4570311c8253d1ff14ff58d564b99e500bf1bd`, source bundle325,956bytes/SHA256 `d2d0d663f82405be06fd6a8462af7d92c906ab4b20996ec4aed46e2cd8671dca`다. 최초 보고서에 없던 AST/export/component/cache 실험과 scoped 공식 source excerpt를 별도 post-thesis delta로 담았다. job `alden-c387-graphify-0992-antithesis-20261019`, request `4a2990ac990ad93c20b539cc`가 공유 FIFO에 queued됐으며 **업로드/전송/연구 시작은 없다.** 빈 슬롯0·기존3실행을 보존한다. 실제 dispatch 전 계정·Argon 최고·Deep Research·할당량을 새로 확인해야 한다. 원래137461c와 maturity-r2를 철회하거나 재전송하지 않았다.
