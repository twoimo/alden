# Alden 2026-10-11: 독립 소스 무결성 수정과 전달 경계

이 기록은 로컬 Mac 설치 검증 또는 전체 목표 완료의 증거가 아니다. 사용자가 지정한 전용 복제본
`/Users/twoimo/Documents/Codex/2026-10-07/macos-ai-alden-ai-1-cpu/work/alden`에 접근하는
Chat On Steroids Core 실행 세션의 `turn_token`이 유효하지 않아, GitHub에서 소스만 읽고
PR #27 HEAD `0509551b08b71a72778d7e24fa852ccb235e3f7e`로부터
`codex/alden-integrity-recovery-20261011` 독립 브랜치를 생성했다.
원래 PR27의 `codex/alden-local-runtime-20260927` 및 원래 Mac 복제본은 이 기록에서 변경하지 않았다.
이 브랜치는 PR27에 병합하기 전 CI/설치 검증을 거쳐야 한다.

## 구현

1. `scripts/alden_osk_delta.py`: 완전한 게시 스냅샷에서 사라진 방/작성자
   메타데이터를 `external_source_removed` 불변 Raw 기록으로 남긴다.
   이전에 추적한 현재 계정의 `room`/`author` 키만 삭제 대사하고,
   다른 계정이나 다른 범주의 캐시 키는 보존한다. `metadata_removed`는
   메타데이터 삭제 건수이며 `removed`(메시지 삭제 건수)와 구분된다.
   체크포인트·캐시 갱신은 기존 단일 트랜잭션 후에만 확정한다.
   이를 downstream 그래프의 삭제 반영이 검증된 것으로 간주하지 않는다.
2. `scripts/alden_source_acquisition.py`: native Aside 응답에서 `NaN`뿐 아니라
   지수 오버플로(`1e400`)로 생기는 무한대 수치도 하위 객체까지 거부한다.
   기존 소스 읽기·대상 ID·취소·수신 계약은 유지한다.
3. `scripts/verify_local_models.py`: localhost MLX 진단이 JSON 내용만 보고
   성공하는 문제를 보완한다. 선택한 모델 ID, `assistant` 역할,
   `finish_reason=stop`, 정확한 한국어 확인 응답을 각각 검사한다.
   불일치·불완료·비정상 역할·진단 답변 오류를 별도 실패 코드로 보고한다.
   이것은 로컬 모델 실행 여부를 *검사하는 CLI의 계약*을 개선하는 변경이며
   모델 가중치 다운로드·로드·제품 모델 선택 변경은 없다.

`tests/test_alden_osk_delta.py`, `tests/test_alden_source_acquisition.py`,
`tests/test_verify_local_models.py`에는 삭제→재등록·타 계정 격리,
중첩 JSON 오버플로, 다른 모델·중단·역할·응답 오류 회귀를 추가했다.

## 이번 회차의 검증 상태

| 검증 범위 | 상태 | 근거 및 한계 |
| --- | --- | --- |
| GitHub PR #27 HEAD와 전용 브랜치 생성 | 실제 실행 검증됨 | PR27 최신 조회 시 open/draft/unmerged, 고정 HEAD에서 브랜치 생성 |
| 변경 파일 저장/즉시 재조회 | 실제 실행 검증됨 | GitHub write 후 각 파일 전체 문자열과 blob readback 일치 |
| 완전 스냅샷 메타데이터 삭제 범위 SQL | 실제 실행 검증됨 (독립 Linux) | 서로 다른 계정 보존, 현재 계정 삭제 범위 가상 SQLite 검사 |
| 중첩 JSON 무한대 거부 | 실제 실행 검증됨 (독립 Linux) | `json.loads(parse_float=...)`의 중첩 오버플로 검사 |
| OSK 삭제·근거 보류, Aside, MLX macOS 러너 테스트 | 실제 실행 검증됨 | GitHub Actions macos-14, Python 3.11, 40 tests OK, run 38066589911. 원 사용자 설치 Mac과 구별 |
| 지정 Mac 복제본, `outputs/STATUS.md`, 설치 앱 및 운영 데이터 검사 | 차단됨 | Mac-native 도구에 유효한 `turn_token` 미제공; Linux 런타임에 Mac 경로 없음 |
| 설치 앱에서 수집→MCP→검색→이력→3D 실제 데이터 최종 검증 | 미착수 (이번 변경) | 이전 버전의 증거와 이번 브랜치의 배포 검증을 구분 |
| 새 앱 설치·서명/공증·정식 배포 | 미착수 (이번 변경) | 원본 사용자 상태 변경이나 불명확한 릴리즈 대상 전환을 하지 않음 |

관련 회귀 명령은 기존 `.github/workflows/ci.yml`에 이미 포함되어 있다:

```sh
python3 -B -m unittest \
  tests.test_alden_osk_delta \
  tests.test_alden_source_acquisition \
  tests.test_verify_local_models -v
```

초기 단계에서는 기존 데이터/스케줄/설정/모델 및 사용자의 전송 큐에
쓰기나 전송을 하지 않았다. PR27에 합치기 전에 해당 브랜치의 diff,
위 3개 모듈 및 상위 CI, macOS 고정 CPython·현행 의존성에서의 회귀 결과를 확인한다.
그 다음 전용 복제본의 `outputs/STATUS.md`에 동일 항목과 정확한 결과를 누적한다.

## GitHub 검증 및 코드 검토 전달

2026-10-11 별도 검증 브랜치 `codex/alden-integrity-ci-20261011`의
정확한 SHA `6c2f9f2098c93cad48a086c9611cebeac91a7807`에서 macOS-14 /
Python 3.11 CI [run 38066178487](https://github.com/twoimo/alden/actions/runs/38066178487)
(jobs 114254172499)이 **30개 검사, 0건 실패, 0건 오류, 종료 성공**을 보고했다.
테스트된 구현/테스트 6개 파일은 PR27 HEAD 기반 독립 구현 브랜치의
내용과 동일하다. 검증 전용 브랜치의 CI YAML 1개는 코드 검토 PR에 포함되지 않는다.
이 결과는 테스트 실행 성공으로만 해석하고 운영자의 Mac 전용 설치/실기기 성능
및 로컬 모델 가중치 로딩 성공으로 확장하지 않는다.

원본 PR #27의 당시 HEAD와 독립 구현 브랜치 사이를 GitHub에서 비교했으며
기록한 8개 파일만 수정되었고 기존 이력 대비 8커밋 앞서 있다.
[검토용 초안 PR #28](https://github.com/twoimo/alden/pull/28)은
원본 PR27의 `codex/alden-local-runtime-20260927`에 **스택**으로 걸어,
기존 1,169개 PR 차이를 다시 제출하지 않았다. PR #28 병합/릴리즈와
프로덕션 스케줄/데이터 변경은 수행하지 않았다.

추가 확인: `tests/test_alden_osk_sources.py`에서 실제 현재 스냅샷에
방 메타데이터가 존재하면 근거 노드·연결이 표출되고, 같은 원본이
삭제되면 해당 노드 및 연결이 보류되며 재등록 뒤 복원되는 테스트를 추가했다.
기존 저장 좌표와 원본 raw 파일은 테스트 과정에서 보존된다.
이 테스트는 OSK 엔진의 현재 근거 재조회 경로이며 운영 MCP의
삭제 전파나 설치된 Three.js 화면에서 물리적으로 이를 본 증거는 아니다.

후속 [CI run 38066589911](https://github.com/twoimo/alden/actions/runs/38066589911),
검증 브랜치 SHA `a6c872c2de362f71540e20dbe6219d90839ada04`,
macOS-14/Python3.11, 총 **40개 테스트 성공** 및 OSK 삭제·재등록
신규 시나리오 성공을 실제 job 114255363711 로그에서 확인했다.
이 비교의 40개 구현·테스트 부분은 검토 PR에서 해당 파일들과 동일하다.

## 2026-10-11 모델 배포처 교차 확인

공식 `Qwen/Qwen3.8-27B`는 Apache-2.0 모델로 게시되어 있으며,
MLX 커뮤니티 변환 `mlx-community/Qwen3.8-27B-4bit`는 별도 변환 배포다.
공식 `Qwen/Qwen3.8-Flash-Next`는 `qwen-community-1.0` 조건의
대형 MoE이며, 공식 모델 카드가 MLX 런타임의 실제 성공을 보증하지 않는다.
세부 조건은 [공식 27B](https://huggingface.co/Qwen/Qwen3.8-27B),
[공식 Flash-Next](https://huggingface.co/Qwen/Qwen3.8-Flash-Next),
[Qwen Community License](https://huggingface.co/Qwen/Qwen3.8-Flash-Next/blob/main/LICENSE),
[MLX 커뮤니티 변환](https://huggingface.co/mlx-community/Qwen3.8-27B-4bit)에서 확인한다.

Alden의 기존 고정 `ddalcu/` MLX-Serve 모델 세 개, 리비전과 해시,
Flash-Next 메모리 admission 차단은
[기존 모델 출처 기록](alden-model-provenance-20261004.md)에 유지한다.
새로 확인한 공식 ID를 가리킨다는 이유로 사용자의 기존 변환 모델,
서버, Gemini 라우팅, 로컬/클라우드 설정을 자동으로 바꾸지 않는다.
Mac M5 Max 128GB에서 실제 로딩, 한국어 생성, tool calling,
메모리/TTFT/p95 및 네트워크 미우회는 새 실행 증거가 필요하다.
