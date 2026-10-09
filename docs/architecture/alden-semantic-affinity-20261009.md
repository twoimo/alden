# 기존 현재 벡터의 읽기 전용 semantic affinity — 2026-10-09

`scripts/alden_semantic_affinity.py`는 실제 `CollectionStore.graph_page` 노드의
`id`, `source_version`, `source_target`을 입력받아 기존 벡터의 cosine 후보를
계산한다. 후보의 용도는 `layout_evidence_only`이다. 엔터티 병합, 사실 관계,
저장된 그래프 edge, 학습 결과로 사용하지 않는다. UI·graphpage·vector 생산자
통합과 위치 보존은 부모 작업이며 이 파일은 운영 UI를 수정하지 않는다.

구현과 최종 회귀 23개는 검증됐다. 아래 실제 512노드 계산은 sidecar 보완 전
소스에서 수행했으며, 계산 결과와 파일 보존 guard의 종료 코드 2를 함께
보존했다. 최종 소스의 현재 DB 실행은 WAL sidecar가 없어 연결 전에 차단됐다.
최종 소스가 실제 현재 DB에서 512개를 다시 계산했다고 보고하지 않는다.

부모의 후속 검증에서는 닫히고 checkpoint된 두 파생 DB를 별도 private 복사본으로 확보했다. 원본 SQL 연결은0이며 복사 전후 main/WAL/SHM fingerprint가 같고, 복사본의 두 quick_check가 통과했다. 필요한512개 canonical JSON도 해시로 대사했다. WAL/SHM 초기화는 소유한 복사본에만 허용한 뒤 같은 최종1ffcbf reader로512개 벡터·32,385쌍·1024후보/184잘림을 확인했다. affinity wall1.326초/CPU0.642초/프로세스 peak RSS58,179,584바이트였다. 자료가 변경되지 않은 복사 구간을 확인한 것이며 여러 DB의 원자적 공동 snapshot·live UI·의미 gold의 증거가 아니다. 논리 DB 복사 크기는698,138,624바이트이고 APFS 공유 물리 블록/공간 절감은 미측정이다. 증거는 부모 outputs/semantic-layout-20261009/frozen-affinity-verification.json에 보존한다.

첫 CI37930697004는 기존 전체 Python 검사와 같은 프로세스에서 실행하면서 이미 사용한 peak RSS가128MiB를 넘어서 실패했다. 실제 reader의 상한은 유지하고 CI의 affinity23검사를 새 Python 프로세스로 분리했다. 첫 CI 실패 로그와 새 프로세스23pass를 보존한다. 이는 CLI 실행 조건의 검사 분리이며 메모리 상한을 무력화한 것이 아니다.

## 입력·격리·근거 계약

Python API는 `affinity(state_root, projects, nodes, ...)`이다. 프로젝트는 명시적
1~16개이며, 노드는 중복 없는 최대 2048개다. 입력은 일반 텍스트나 임의의
embedding 배열이 아닌 현재 CollectionStore v3 그래프의 참조다. CLI에서는
최대 2MiB의 JSON 노드 배열 또는 `{"nodes": [...]}` 객체를 전달한다.

각 노드의 정확한 target/current membership과 허용 프로젝트를 재조회한다.
문서와 멤버십 모두 `available`이어야 하며 `permission='denied'`를 제외한다.
다른 프로젝트의 더 최신 `documents.current_version`으로 교체하지 않는다.
입력 버전과 현재 대상 버전이 다르면 `stale_graph_version`으로 제외한다.
두 노드가 공유하는 허용 프로젝트가 없으면 그 쌍을 비교하지 않는다.

문서 안정 ID, version 소유권, metadata projection 해시와 version ID를 대조한다.
검색 캐시의 label/base/raw/extraction/body_source와 실제 검색 텍스트 해시를
대조한다. 원문은 크기·regular file·no-follow·읽기 중 변경·SHA256을 확인한
보관 canonical record JSON이다. `retained_record.localOriginalText`는 이 JSON의
실제 문자열과 일치해야 한다. 원본 전체 export, PDF·영상 바이트나 사실의
진실성을 이 검사로 확인한 것은 아니다.

허용되는 기존 profile은 다음 하나다. 서비스 조회나 가중치 로딩으로 profile을
자동 선택하지 않는다. 다른 profile, endpoint, encoding, 차원은 제외한다.

| 항목 | 값 |
|---|---|
| model/revision | `mlx-community/multilingual-e5-small-mlx@5030c7625865046d350eeea28f427d80353d0ac0` |
| endpoint identity | `5956b9388ba26ec18062d7a95c02b4d02eb01a467e383f7ddd505ecba2508844` |
| endpoint identity의 선언 근거 | 기존 `http://127.0.0.1:11236/v1/embeddings` URL의 SHA256 |
| encoding | `e5-char256-stride192-weighted-unit-pool-v1` |
| dimension/storage | 384, little-endian float32 1536바이트 |
| 허용 norm | 1과의 차이 .001 이하, 전 요소 유한값 |

endpoint hash는 벡터 생산 당시 저장된 profile 결속 근거이며 현재 서비스의
접속·가중치 파일·실행 상태 증명이 아니다. NaN/Inf, 0 또는 비단위 벡터,
다른 차원, 손상 바이트, 현재 버전 벡터·텍스트 누락을 혼합하거나 생성하지
않는다. 새 embedding/inference/index/write는 호출하지 않는다.

출력은 `nodes`의 프로젝트·권한·대상·버전·raw/base/projection/text/vector
해시·차원·profile 해시와 `candidates`의 source/target/cosine/공유 프로젝트를
연결한다. 본문·표시명·실제 원본 ID는 출력하지 않는다. 입력 참조 digest와
독립적인 두 DB 읽기 트랜잭션의 한계를 기록한다. 소비 시 버전·허용 범위를
다시 검증해야 하며, 두 DB의 원자적인 공동 스냅샷을 주장하지 않는다.

## 자원 예산·coverage

| 자원 | 기본 상한 | API에서 허용하는 절대 상한 |
|---|---:|---:|
| 노드 | 2048 | 2048 |
| 읽는 벡터 바이트 | 8MiB | 8MiB |
| 누적 텍스트 바이트 | 32MiB | 32MiB |
| 보관 JSON 바이트 | 64MiB | 64MiB |
| 단일 원문·텍스트 record | 2MiB | 2MiB |
| 실제 dot-product 쌍 | 65,536 | 131,072 |
| dimension products | 25,165,824 | 50,331,648 |
| 노드별 선택 이웃 | 3 | 8 |
| 반환 후보 | 1024 | 4096 |
| CPU 시간 | 8초 | 20초 |
| wall 시간 | 20초 | 45초 |
| 프로세스 peak RSS | 128MiB | 256MiB |

기본 계산쌍 상한은 `min(max_pairs, max_products // 384)`이다. 노드 ID 순서로
같은 프로젝트의 쌍을 탐색하고, 검사한 쌍에서 노드별 상위 이웃의 undirected
union을 만든 뒤 총 후보 상한을 적용한다. union으로 인해 incoming degree는
노드별 선택 이웃 수보다 클 수 있다. 제한에 닿으면 global nearest neighbors나
전체 그래프 군집이라고 해석하지 않는다. 원본 graph_page overview의 degree와
source interleave는 허브 선택 편향이 있고, pair budget은 ID 순서 편향이 있다.

`coverage`에 입력/사용/제외 노드 수, 제외 사유, 프로젝트별 수, 프로젝트 혼입
제외쌍, 가능한 쌍과 같은 프로젝트 쌍, 실제 검사쌍, dimension products,
임계값 통과쌍, 이웃 union 수, 반환/잘림 수, node/pair coverage를 명시한다.
분모 0은 null이다. 누락·pair 제한·후보 잘림은 `bounded_partial`이다.
CPU/wall/RSS/누적 바이트 상한·취소는 실패이며 부분 후보를 발행하지 않는다.
RSS는 같은 Python 프로세스의 과거 peak를 포함하므로 호출 자체의 추가 메모리
또는 전체 Alden 메모리라고 해석하지 않는다. Python 표준 라이브러리만 사용하며
새 모델·추론·GPU 계산·추가 agent를 사용하지 않는다.

## 실제 기존 벡터 실행과 소스 보존 결과

독립 실행은 기존 세션 92508의 재조회가 아니다. `CollectionStore.graph_page`
overview에서 실제 노드 512개를 받아 새 affinity 구현을 실행했다. 선택 범위는
`career`, `sparkuniverse`, `jangsin`, `youtube-tzuyang`, `alden-model-research`다.
당시 허용된 filtered graph는 26,623노드/48,355관계였다. 512개는 노드의
1.923149%이며 전체 그래프나 의미 정답 평가셋의 대표 표본이 아니다.

| 측정 항목 | 실제 값 |
|---|---:|
| 입력/유효 현재 벡터 | 512/512 |
| 프로젝트별 유효 노드 | career127, sparkuniverse128, jangsin128, youtube-tzuyang128, alden-model-research1 |
| 본문 출처 | stored_body406, retained_record.localOriginalText106 |
| 전체 가능한 벡터쌍 | 130,816 |
| 공유 프로젝트가 없어 제외한 쌍 | 98,431 |
| 동일 프로젝트 실제 검사쌍 | 32,385/32,385 |
| 기본 cosine .65 통과쌍 | **32,385개, 검사쌍 전부** |
| dimension products | 12,435,840 |
| 노드별 이웃 union | 1208 |
| 반환/후보 상한에 의해 잘림 | 1024/184 |
| node/pair coverage | 1.0/1.0, 주어진 노드와 동일 프로젝트쌍에 한함 |
| 결과 상태 | bounded_partial, 후보 상한 잘림 |
| 읽은 벡터/텍스트/원문 바이트 | 786,432/902,682/439,897 |
| affinity wall/CPU | 1.174983초/0.772796초 |
| 프로세스 peak RSS | 90,062,848바이트 |
| graph_page 선택 wall | 1.977600초 |
| 반환 후보 cosine 범위 | .8974128025083746~1.0 |

**검사쌍 전부가 .65를 통과했다. 임계값의 분리 능력·의미 정답·실용적 cluster
품질을 이 실행으로 입증하지 않는다.** 위 cosine 범위는 상위 반환 후보에
한하며 검사한 전체 쌍의 최소값이 아니다. 자동 병합·사실 관계·독립 출처·
UI 재배치 성과를 주장하지 않는다. 허브/ID 순서 편향, 많은 공통 텍스트,
E5 profile의 해당 자료에서의 분리 능력은 독립 평가가 남아 있다.

당시 실행은 모든 SQLite 연결이 `mode=ro`임을 검사하고 SQL authorizer로
DB 변경·DDL·attach·journal_mode·checkpoint를 금지했다. 금지된 write 시도는
0개였으며 두 DB 본문 SHA256은 같았다. 하지만 첫 실행 후 이전에 없던
**0바이트 `retrieval.sqlite3-wal`**이 관측됐다. wrapper의 파일 보존 검사가
종료 코드 **2**를 반환했으며 이를 성공으로 덮어쓰지 않는다. 기존 source
sidecar를 삭제하거나 journal mode를 바꿔 복구하지 않았다.

원본 record/index 내용과 SQLite 운영 메타데이터 보존을 구분한다. 두 DB main
파일의 동일 바이트 해시, 기존 collection WAL의 동일 해시, 새 retrieval WAL의
0바이트 상태는 이 관측 중 record/index 변경을 확인하지 않았다는 근거다.
반면 WAL 파일의 존재 상태는 바뀌었다. SHM은 첫 관측에서 해시 검사 대상이
아니었으므로 SHM 바이트·read mark 불변을 주장하지 않는다. “원본 전체 파일
불변” 또는 “모든 메타데이터 불변”으로 승격하지 않는다.

SQLite는 WAL DB의 `mode=ro` 연결에서도 쓰기 가능한 디렉터리에 sidecar를
만들 수 있다. 최종 코드는 파일 header를 읽고 WAL이면 기존 `-wal`/`-shm`이
둘 다 있어야만 DB를 연다. 없으면 `affinity_wal_sidecars_required:<db>`로
거절하며 `immutable=1`로 live WAL을 무시하지 않는다. 사전 검사와 연결 사이의
동시 sidecar 삭제를 OS 수준에서 봉쇄하는 보장은 없다. 현재 원본이 닫힌 WAL
상태이면 소유자의 정상 읽기 환경이나 이미 확보된 일관된 snapshot이 필요하다.
[SQLite 공식 WAL 읽기 전용 조건](https://sqlite.org/wal.html#read_only_databases)을
2026-10-09 확인했다. 원본에 sidecar를 만들기 위한 별도 쓰기·checkpoint는 하지
않았다.

최종 코드의 실제 preflight는 `affinity_wal_sidecars_required:collection.sqlite3`
로 연결 전에 중단했다. SQLite open 시도 0, 관측 파일 변경 0이다. 관측 harness는
먼저 retrieval에서 거절될 것으로 예상했으나 실제 collection에서 먼저 거절돼
종료 코드 1을 기록했다. 이는 예상 DB의 차이이며 그대로 보존했다. 최종 소스의
현재 벡터 재계산은 차단 상태다. 첫 계산의 소스와 최종 소스를 구분한다.
여기서 전후 fingerprint는 `final-current-preflight.json`에 열거한 retrieval
main/WAL/SHM 범위이며 모든 원본 파일을 재감사한 결과가 아니다.

## 검증과 SHA 결속

고정 실행 경로는
`/Users/twoimo/Library/Application Support/openkakao/runtimes/menubar/bin/python3.11`
이고 실제 `sys.version`은 **3.11.9**였다. 소스 패키징 스크립트의 3.11.16
선언을 설치된 버전으로 보고하지 않는다. 모든 Python 실행에 `-B`를 적용했고
설치 Resources를 import하거나 수정하지 않았다.

최종 회귀는 **23 pass / 0 fail / 0 error / 0 skipped**다. 허용/거부/삭제/
범위 밖 격리, 프로젝트별 현재 버전과 전역 최신 버전의 분리, archived vector
재사용 거절, stale base/raw/extraction/label/text 해시, 실제 원문 대조,
profile/dimension/NaN/Inf/zero/손상 벡터, 크기·CPU·wall·RSS·취소·SQL interrupt,
pair/product/candidate 제한, 2048 입력 상한, readonly와 CLI 실패 출력을 검사한다.
2048 입력 검사는 누락 참조로 상한을 검사한 것이며 2048 실제 벡터·전체 물리
성능 또는 UI 통합 검증이 아니다. WAL 회귀는 없는 sidecar의 선행 거절과
실제 WAL frame에 남은 stale text hash의 조회를 확인한다.

| 증거 단계 | 소스 SHA256 | 테스트 SHA256 |
|---|---|---|
| 실제 512노드 계산, 초기 21회귀 | `86bfad099195dc6ad90f11e02d326ee63de7eb1b7e117bea5394a4163d232462` | `5b20760f6c045b0474179d2e85e7f222bdc5779d6e2d4b3e18a9ae1b39c22419` |
| sidecar guard 보완, 최종 23회귀 | `1ffcbf7a52cb08d1dad99cba9950c68f399dbaa00642162ccc3b19a28e274e68` | `6adf82865d1b44cf0541be73b6eaebaced387c6220b23c2998239940f92366cf` |

전체 증거·코드 snapshot·manifest는
`outputs/semantic-affinity-20261009/`에 있다. `current-affinity.json`에 512개
노드별 근거와 후보, `current-run-summary.json`에 profile·coverage·DB fingerprint와
검증 소스 해시, `tests-final.json`에 최종 해시와 회귀 수를 보존했다.
`final-current-preflight.json`은 최종 소스의 실제 차단 증거다.

두 영수증은 `actual-86bfad-receipt.json`과 `final-1ffcbf-receipt.json`으로
분리했다. 첫 영수증의 live 계산을 최종 소스 영수증으로 재사용하지 않는다.
`delivery-manifest.json`이 각 영수증·소스·테스트·관측 산출물의 SHA256을
결속한다. 최종 CLI는 현재 닫힌 WAL 조건에서는 사용할 수 없으며 명시적인
실패 JSON/종료 코드 2를 반환한다. 이 턴에서 sidecar 자동 생성, immutable
우회, 안전 계약 완화 또는 원본 sidecar 생성 작업을 추가하지 않는다.

```sh
'/Users/twoimo/Library/Application Support/openkakao/runtimes/menubar/bin/python3.11' -B \
  -m unittest tests.test_alden_semantic_affinity -v

# 기존 정상 WAL sidecar가 있는 DB 또는 이미 확보된 일관된 snapshot에 한함.
# CLI는 stdout만 반환한다. 결과 저장은 outputs의 새 이름으로 수행한다.
'/Users/twoimo/Library/Application Support/openkakao/runtimes/menubar/bin/python3.11' -B \
  scripts/alden_semantic_affinity.py \
  --state-root '/Users/twoimo/Library/Application Support/openkakao/bujamentor' \
  --project career --project sparkuniverse --project jangsin \
  --project youtube-tzuyang --project alden-model-research \
  --nodes-json outputs/semantic-affinity-20261009/current-nodes.json
```

## 전달 범위와 남은 작업

현재 HEAD 관측은 부모가 canonical build를 완료했다고 보고한
`b7ce26efd5d7c5c652ae1e1351d0a842c525739a`다. 이 worker는 commit/push/build/install,
설정·원본·큐·모델·프로필 변경, 추가 agent/LLM/유료 용량, 가중치 로딩 또는
실전 메시지를 실행하지 않았다. 첫 조회 92508은 `Unknown process id 92508`이며
결과·종료 lifecycle unknown을 유지한다. 이 독립 실행으로 기존 조회가 성공했거나
종료됐다고 주장하지 않는다. 역사적인 빌드 보류는 부모 coordinator의 요청이며
직접적인 사람 발화로 기록하지 않는다.

위 SHA는 실행 준비 때의 관측이다. 전달 직전 별도 read-only HEAD 관측은
`055de7d01120dd171ca3dafc5b2c8d6d116b01fd`였고, 이 worker의 소스·테스트는
위 최종 SHA256과 일치했다. 부모의 이후 commit을 이 worker의 commit으로
표시하지 않는다.

노드의 현재 버전·profile에 결속된 bounded cosine 후보 구현과 회귀 및 첫
실제 계산의 근거를 전달한다. 최종 reader의 현재 DB 실행은 위 WAL 조건으로
차단됐다. 실제 graphpage/vector UI 연결, 기존 위치·핀·선택·카메라 보존,
2048 전체 물리, cluster gold와 안정성·품질 평가, 설치/배포 및 전체 Alden
목표 완료는 부모와 후속 검증의 범위이며 이 작업에서 완료됐다고 하지 않는다.
