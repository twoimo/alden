# 제품 layout affinity 읽기 경계 — 2026-10-09

`alden_layout_affinity.read_action(state_root, raw_query)`를 부모의
`collection-affinity` action에 연결할 수 있도록 구현했다. backend가 현재
`CollectionStore.graph_page`의 참조를 얻으며 클라이언트 노드 배열·본문·벡터는
받지 않는다. dispatcher/Rust/resource/controller/parser 및 실제 UI·설치는
부모 범위이며 이 worker의 검증 결과에 포함하지 않는다.

## 계약

query는 UTF-8 **4096바이트** 이내의 JSON object다. 기존 graph query의
`projects, limit, offset, target_id, platform, node_type, relation, search,
since, until, focus, hops, overview, expected_version`을 적용한다. `details`
는 false만 허용한다. activity·client nodes·다른 profile과 알 수 없는 key는
거절한다. query 한도를 자동으로 늘리지 않는다. overview는 최대1984,
일반/focus page는 기존120 제한이다. 표시 전체2048/`memory:` overlay를
affinity 노드나 사실 관계로 확장하지 않는다.

```json
{"ok":true,"layout_affinity":{
  "schema":"alden-layout-affinity-v1","state":"bounded_partial",
  "input_nodes":[{"id":"graph:…","source_version":"version:…","source_target":"target:…"}],
  "input_projects":["career"],"profile":{},"profile_sha256":"…",
  "nodes":[],"candidates":[],"coverage":{},"binding":{}
},"revision":0}
```

`input_nodes`는 graph_page 표시 순서 그대로의 전체 collection 참조이며
`nodes`는 검증된 부분집합이다. 노드 근거는 프로젝트·대상·현재 버전과
raw/projection/base/text/vector 해시·처리 버전·body_source다. model/revision,
endpoint identity, encoding, 384차원은 전역 profile에 결속하며 각 벡터에서
검사한다. 본문·표시명·원본 ID·사실 edges는 반환하지 않는다. 후보는
`source/target/cosine/projects`이며 레이아웃 근거에만 쓴다.

부모 parser 계약에 맞춰 노드별 `dimension/profile_sha256/text_sha256/raw_sha256/
vector_sha256/projects/permissions/raw_path/projection_sha256/base_text_sha256/
vector_norm/body_source`를 유지한다. raw_path는 raw hash+'.json'인 보관 사본
이름이다. `kind`는 후보에서 생략하며 schema/purpose가 별도 layout 채널을
표시한다. 전체 proof를 유지한 compact 응답도 아래4MiB 예산을 지킨다.

`binding.input_sha256`은 ordered input_nodes/input_projects/profile_sha256을,
query hash는 실제 요청 필터를 결속한다. graph revision·journal ID·허용한
노드 근거 digest도 제공한다. 부모 parser는 현재 collection 노드의 전체
id/version/target와 input_nodes를 대조하고 memory overlay를 제외한 뒤 적용해야
한다. 오래된 응답으로 위치·핀·선택·카메라를 바꾸지 않는 UI fence는 부모 범위다.

query/graph 읽기 실패는 `ok:false`; vector store 누락·부적합·예산/취소 실패는
`ok:true`, `state:unavailable`, 빈 근거/후보다. 일부 제외 또는 pair/후보 상한은
`bounded_partial`; 해당 page 범위의 완전한 검사는 `bounded_ready`다. 분모 0의
coverage는 null이다. 빈 page는 빈 화면의 범위이지 전체 그래프가 비었다는 뜻이 아니다.

## 읽기 트랜잭션과 보존

`read_transactions(...)`가 정확한 파생 `knowledge/collection/collection.sqlite3`
와 `retrieval.sqlite3`를 mode=ro/query_only/BEGIN으로 연다. caller는 해당 scope를
소유한다. 필요한 경우 `read_action(..., transactions=reads)`로 재사용하며
root/thread/active transaction/query_only/main DB 경로를 검증한다. raw writer나
닫힌 owner를 받아 읽기 연결처럼 바꾸지 않는다.

instance-local borrowed CollectionStore의 nested graph_page/projects/targets는
같은 collection 연결을 사용한다. 공유 evaluator는 새 연결·commit·close를
하지 않는다. retrieval은 별도 read snapshot이며 current target/version/raw/
base/text/profile 결속으로 검증한다. 두 DB의 원자적 공동 snapshot을 주장하지
않는다. 이전 snapshot 이후의 permission/version/cache 변경은 다음 요청에서
재검증한다. 동일 프로젝트가 없는 노드쌍은 비교하지 않는다.

SQL authorizer는 record/index DML·DDL·attach·journal_mode·checkpoint·query_only
해제를 막는다. 원본 OSK 파일·Kakao DB·전송 큐·모델·네트워크는 열지 않는다.
원문 검증은 파생 collection의 hashed canonical JSON 사본만 읽으며 원본 위치
메타데이터를 따라가지 않는다. 새 embedding/inference/index를 실행하지 않는다.

**제품 경로에서는 SQLite 엔진의 정상 파생 WAL/SHM 읽기 메타데이터 사용을
명시적으로 허용한다.** before/during/after 존재·크기·mtime과 거절된 SQL 수를
기록한다. 이는 record/index 변경 허가나 SHM byte 불변 주장이 아니다.
700MB 사본을 매번 만들지 않으며 immutable/nolock으로 live WAL을 무시하지 않는다.
기존 개발 `semantic_affinity` CLI의 “sidecar가 없으면 사전 거절” guard는 유지했다.
이 경로 차이는 별도 제품 caller adapter에 명시돼 있고 CLI 우회가 아니다.

## 예산과 검증

기본 상한은 벡터8MiB·누적 텍스트32MiB·파생 원문64MiB·record2MiB,
65,536쌍/25,165,824 dimension products, 이웃3/후보1024,
CPU8초/wall20초/프로세스 peak RSS128MiB, compact 응답4MiB다.
그래프 선택부터 계산·응답 확인까지 동일 취소/guard를 사용한다. RSS는
프로세스 lifetime peak이므로 전체 앱 메모리·추가 메모리 또는 절감치가 아니다.
hard stop은 부분 후보를 성공으로 발행하지 않는다.

고정 Python 경로의 실제 버전3.11.9, `-B`로 **47 pass / 0 fail / 0 error /
0 skipped**를 확인했다. 기존23개를 포함한 semantic25/product22 회귀는
권한/target/current version, stale graph/cache/provenance, profile/dimension/
malformed, 예산·취소·byte cap, 기존 graph 필터/FTS/시간/focus/관계/paging,
owner 재사용, 동시 permission/version/cache 변경과 live WAL frame을 다룬다.
초기45회귀 중 검색2개 실패는 FTS의 읽기 `PRAGMA data_version`을 막은 guard
때문이었다. 해당 read-only 조회만 허용해 해결했고 초기 실패 기록을 보존했다.

## 현재 파생 저장소의 별도 새 실행

부모 parser가 필요한 proof 필드를 추가한 최종 제품 `read_action`으로 5개
허용 프로젝트의 overview 최대1984를 읽었다.
전체 허용 filtered graph 26,623노드 중 해당 page만 검사했다.

| 측정 | 결과 |
|---|---:|
| 현재 참조/유효384차원 E5 벡터 | 1984/1984 |
| 같은 프로젝트 가능한 쌍/실제 검사 | 495,796/65,536 (13.2183%) |
| 프로젝트 혼입 제외쌍 | 1,471,340 |
| 이웃 union/반환/잘림 | 5145/1024/4121 |
| .65 통과 | 검사한65,536쌍 전부 |
| query/compact 응답 | 119/2,911,881바이트 |
| endpoint wall/계산 포함 CPU | 5.323730초/3.780222초 |
| 프로세스 peak RSS | 119,635,968바이트 |
| 상태/observer 종료 | bounded_partial/0 |

전후 파생 DB main 바이트 해시는 같았고 journal header도 그대로다. 기존
collection/retrieval WAL은0바이트 그대로였다. 최종 읽기에서 두 SHM mtime이
변했다. 앞선 compact proof 실행에서는 SQLite가 retrieval의0바이트 WAL과
32,768바이트 SHM을 생성했다. 두 실행 모두 SQL write 거절 시도는0개였다.
이 메타데이터 변화와 record/index 본문 보존을 구분하며
원본 전체 파일 또는 모든 SQLite 메타데이터 불변으로 보고하지 않는다.

**.65 검사쌍 전부 통과·상위 후보·허브와 ID 순서 budget 편향**을 유지한다.
이 수치는 의미 정답·실용적 cluster 품질·전체 nearest neighbors·UI 성능 또는
2048 전체 물리를 입증하지 않는다. snapshot 이후 상태·실제 설치 UI는 별도
검증 대상이다. 이전86bfad/1ffcbf 작업 영수증과92508 결과/종료 unknown은
그대로 보존하며 이번 제품 실행으로 이전 조회를 성공 처리하지 않는다.

부모가 보고한 bridge query4096/timeout25초, ready60초·partial15초의
one-in-flight controller 주기는 integration 조건으로 기록한다. 최종 단일
실행은 이 timeout 안에서 끝났으나 반복 주기의 CPU·권한 변경 반영 지연이나
실제 UI 흐름의 측정은 아니다. backend는 요청마다 새 read snapshot으로 권한을
검사한다. 같은 owner의 이전 snapshot과 새 요청의 revocation/cache 갱신 구분은
fake WAL DB 회귀로 검증했으며 운영 permission을 바꿔 시험하지 않았다.

## 전달

`outputs/layout-affinity-20261009/parser-compatible-receipt.json`은 최종 실행의 profile,
input/snapshot binding·노드별 근거·DB/sidecar 관측과 소스 SHA를 결속한다.
`tests-parser-contract.json`은 최종47회귀의 소스·테스트 SHA를,
`delivery-manifest.json`은 전체 artifact 해시를 제공한다. 초기 compact proof의
`product-receipt.json`과 이후 snapshot 회귀 기록은 별도로 보존한다. 부모가
요구한 proof 계약 변경 후에만 실제 읽기를 다시 실행했다.

부모는 `collection-affinity`에 이 endpoint를 연결하면 된다. 이 worker는
dispatcher/Rust/UI/resource를 수정하거나 commit/push/build/install, 운영
프로세스 변경, 추가 agent/가중치/추론/네트워크를 실행하지 않았다. 전달은
제품 integration 준비와 독립 실제 읽기 검증이며 설치·UI 완료가 아니다.
