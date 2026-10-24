# Alden 0.3.55 실제 배치 통합 및 설치 검증

2026-10-09 KST에 설치한 런타임 소스는 `7f30db91d87570c34ec2492e9d919f12d8327dcd`다. 후속 문서 커밋과 설치 런타임을 구분한다. 요청한 `chatgpt-web/gpt-6-sol` / `max`의 두 기존 작업자가 저장 벡터 읽기와 배치 엔진을 병렬 구현했고, 부모가 dispatcher, Rust bridge, UI, 전용 WebWorker, 수명주기와 패키징을 연결했다.

## 실제로 달라진 동작

큰 overview는 저장된 관계 R과 별도로 검증한 의미 유사도 후보 S를 사용한다. 현재 ID·대상·버전·권한·원문·검색 텍스트·E5 프로필이 일치해야 S를 받는다. 실제 관계를 먼저 표시한 뒤 후보를 한 번에 한 요청씩 받아 배치를 갱신한다. 후보로 사실 관계를 만들거나 사람·회사·주장을 병합하지 않는다.

큰 배치는 전용 WebWorker에서 계산한다. 범위 변경·숨김 시 이전 작업을 종료하고 마지막 완료한 캐시만 복원하거나 폐기한다. 완료한 배치가 없는 첫 요청에는 임시 ID 시드를 과거에 정착한 위치로 전달하지 않는다. 표시한 2048개 중 기존 물리 계산 밖의 노드도 변경한 목표 위치로 보간한다. 선택·핀·카메라와 무관한 기존 구성요소의 위치를 보존한다.

## 설치 앱의 실제 읽기 및 화면

57개 설치 파일의 SHA와 모드가 후보 번들과 같고 strict/deep ad-hoc 서명이 유효했다. 추가 `__pycache__`는 없었다. 보호 설정 3개, 수집 대상 설정 7개, 원본 자동 확인 정책 2개를 기준선과 대조했다. 기본 Flash/high/manual 설정, 기존 모델과 큐를 유지했다.

설치 메뉴 경로의 읽기는 실제 1984개 저장 벡터와 1024개 후보를 반환했고 현재 그래프의 UI 파서가 거부 사유 없이 수용했다. 새 모델 추론·가중치 로딩·색인 재생성은 수행하지 않았다. 일반 SQLite 읽기의 WAL/SHM 메타데이터 관측과 원문·레코드·색인 쓰기는 구분한다. [읽기 계약과 실행 범위](alden-layout-affinity-20261009.md).

설치된 실행 파일의 독립 읽기 전용 native 감사는 종료 코드 0, `success=true`, `error=null`, 버전 0.3.55였다. 기본 1200×760 및 작은 640×680 창의 24개 화면을 직접 검토했다. 두 graph overview 모두 다음을 관측했다.

| 항목 | 기본 창 | 작은 창 |
| --- | ---: | ---: |
| 표시한 실제 노드 | 2048 | 2048 |
| 현재 수집 벡터 / 반환 후보 | 1984 / 1024 | 1984 / 1024 |
| 배치에 사용한 R / S 쌍 | 3761 / 226 | 3761 / 226 |
| S geometry 정책에서 제외한 후보 | 798 | 798 |
| 배치 군집 수 | 308 | 308 |
| worker 상태 / 해당 작업 시간 | ready / 90ms | ready / 약90ms |
| 남은 보간 목표 | 0 | 0 |
| rest / layout / affinity 관측 | 모두 true | 모두 true |
| 작은 물리 엔진 nodes / synapses | 120 / 9 | 120 / 9 |
| 물리 simulatedSeconds / moving | 4.839 / false | 4.528 / false |
| 충돌 검사 / 예산 소진 | 131072 / true | 131072 / true |

2048개는 수집 1984개와 기존 기억 64개다. worker의 용량 벤치마크에서 쓴 합성 64개와 다르다. 약90ms는 관측한 개별 worker 작업 시간이며 UI p95, FPS, GPU, 배터리 또는 개선율이 아니다. 물리 정지는 힘이 0이라는 뜻이 아니며 전체 2048노드를 물리 엔진으로 계산했다는 뜻도 아니다.

## 회귀 및 전달

UI 455개, affinity Python 47개, payload/launcher 60개가 통과했다. 기존 desktop Rust 전체는 직렬 실행에서 109개가 통과했고 새 bridge admission 회귀 1개도 통과했다. 타입 검사·Vite의 worker 번들·strict Clippy가 통과했다. 같은 런타임 소스의 [CI 37942664715](https://github.com/twoimo/alden/actions/runs/37942664715)는 release/runtime smoke, launchd/Python harness, macOS cargo test, Tauri/focused Python의 4개 작업 모두 성공했다.

최초 5초 native 관측 제한 실패와 잘못된 진단 getter로 관측값이 null이던 후속 실행을 보존했다. 제한을 25초로 늘린 뒤에도 실제 상태를 기다리며, `window.__knowledgeRenderDiagnostics`에서 상태를 읽는다. 실패했던 실행을 새 실행의 성공으로 바꾸지 않는다. 병행 IO에서 발생한 기존 Rust 시간 예산 실패와 같은 전체 테스트의 직렬 성공도 별도로 기록한다.

private 증거는 task root의 `outputs/semantic-integration-20261009/`에 있다. `RESULT.md`, `delivery-manifest.json`, `installed-verification.json`, `actual-parser.json`, `ci-observer-snapshot.json`, `native-installed-observer/workspace-readback.json` 및 24개 PNG를 연결한다. 원문·화면의 개인 대화 자료와 private 출력은 저장소에 커밋하지 않는다.

## 남은 품질 검증

같은 프로젝트에서 가능한 495796쌍 중 65536쌍만 검사했고 후보 4121개가 잘렸다. 검사한 모든 쌍이 .65를 통과했다는 편향을 그대로 기록한다. 프로젝트를 가로지르는 S 후보는 현재 제외한다. S 1024개 중 226개만 degree/mutual/hub 정책에 따라 geometry에 사용했다.

308개 군집은 배치 알고리즘의 결과이며 독립 의미 정답이 아니다. 충돌 예산을 소진했고 중심에 밀집한 노드·작은 창의 일부 라벨 겹침도 남아 있다. 중복 후보의 실제 판정·가역 병합, 의미 이상치, 동명이인·반박·최신성·단위의 독립 평가, cross-project 정책, 후보 보정과 대표 규모의 성능은 미완료다. 힘과 방향은 화면 배치의 물리량이며 의미 동일성이나 사실 관계를 증명하지 않는다. primary 물리 조작·Retina·전체 CPU/GPU/RAM/배터리와 정식 Developer ID/공증·릴리즈·프로덕션은 이 전달의 성공 범위에 포함하지 않는다.
