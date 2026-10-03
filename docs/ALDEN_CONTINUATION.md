# Alden 작업 이어받기 — 2026-10-03 KST

전체 목표는 **진행 중**이다. 현재 목표 원문은
`/Users/twoimo/.codex/attachments/1a69ccc3-7339-4916-9776-ce3c4d2fc983/goal-objective.md`이며,
이전 채팅 `01a0f115-35c9-7df2-95d8-561697e115eb`의 작업을 이어받는다.

## 복구와 현재 상태

- 기존 작업 폴더 `alden-settings-wide`는 삭제되어 있다. 보관된 Git 스냅샷
  `c9cd870`과 원격 PR27의 `63267e9`를 비교해 이전 세션의 변경 9개를 복구했다.
- 현재 작업 폴더: `/Users/twoimo/.codex/worktrees/a0c2/openkakao-bot`.
  분기: `codex/alden-continuation-20261003`. 관련 없는 변경은 없었다.
- 장치: Apple M5 Max, 통합 메모리 128GB, macOS 26.6.2. 전원 연결 상태.
- 설치본은 이미 Alden 0.3.10이다. 상주 27B PID58780, 전용 E5 PID11016,
  기존 카카오 세션과 음성 PID25865를 발견했다. 실행 중인 작업은 이어받기에서 재시작하지 않았다.
- 이전 수정의 Python 필수 검사 1149개/28 skip 및 추가 집중 검사 29개는 통과했다.
  경로가 사라진 이전 빌드 자체를 현재 checkout의 빌드라고 보고하지 않는다.
- 현재 Computer Use의 `getApp('Alden')`도 `timeoutReached(-10005)`로 실패했다.
  별도 네이티브 검사, 개발 화면, 상시 설치본의 직접 조작은 다른 검증 범위다.

## 단계

| 항목 | 단계 | 남은 작업 |
| --- | --- | --- |
| 맥락·턴·출처·취소 | 실제 실행 검증 | 자연 발화·물리 끼어들기·에코 |
| Tauri v2·Three.js·Alden | 전달 완료 | 최신 0.3.10 원격 산출물 대조 |
| 정확한 로컬 모델 | 실제 실행 검증 | Flash-Next 메모리 admission, 운영 이미지 경로 |
| 음성·자동화·비상 중단 | 구현/부분 실제 실행 검증 | 배포 한국어 wake, 물리 마이크·재생·단축키 |
| 디자인·오브·노드 근거 | 전달 완료 | 0.3.10 설치본·현재 primary/Retina |
| 렌더·전력·메모리 | 구현/부분 실제 실행 검증 | E5 수정 전달, 실제 UI/전력·지연 |
| GraphRAG·DB | 실제 실행 검증 | 원본 앱 데이터 접근 승인·최신 수집 |
| DREAM-RSI·평가·DPO | 구현/부분 실제 실행 검증 | 독립 최종 test·제품 품질 평가·promotion |
| 성능 | 부분 실제 실행 검증 | 재현 가능한 E5 비교·tokens/s·제품 음성 p95 |
| 문서·도식 | 전달 완료 | 이번 변경·수치·범위를 갱신 |
| commit·push·release·설치·production | 구현 | 정상 push/CI·산출물·자격 증명·안전한 전환 |

과거 상세 근거는 [목표별 이력](architecture/alden-goal-coverage-20260930.md),
[오브와 노드 설명](architecture/alden-orbs-20261003.md),
[전달 이력](ALDEN_DELIVERY.md)을 사용한다. 지나간 판본의 성공을 현재 판본의 성공으로 대체하지 않는다.

## 다음 작업

1. 완료: E5 변형별 새 프로세스3회·44요청/165벡터, 동일 출력, 14.453→1.517GB·89.50% 감소.
   전체 요청 시간 0.521→0.726초·39.29% 증가도 공개했다. 처리 시간10% 목표는 미달이다.
2. 완료: 복구 checkout의 release build·0.3.10 재설치·35파일·ad-hoc deep/strict·기존5프로세스 보존.
   재설치 후 별도 native 팝업10회/설정 검사 통과, 숨김 추가 렌더0.
3. 완료: UI231·desktop Rust94·CLI Rust1150/1ignored·Clippy.
   필수 Python1181/28skip의 첫 실행은 설치 fixture timeout1건, 해당 단독 재실행 통과.
4. 완료: 메모리 측정 스크립트·32집중 검사·측정 원자료·Archify9checks와 실제 Chrome/이미지 검토.
5. 다음: 0.3.10 commit/push·정확 SHA CI·릴리즈 자산/다운로드/설치 판본 대조.
6. 모델·음성·운영 적용의 남은 독립 작업을 계속하고 준비된 권한/서명 차단만 구체적으로 요청한다.

기존 공유 모델 설정·설정 파일·enrollment·전송 큐를 보존한다. 확인되지 않은 외부 쓰기는 재시도 전에 읽어 확인한다.
