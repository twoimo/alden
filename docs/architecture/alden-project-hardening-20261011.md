# Alden 0.3.59–0.3.60 고도화 전달

0.3.60 실행 소스는 `5d030759ed2d38133f2296b2b10135ce4cb0bd06`이다. `/Applications/Alden.app`에 설치했으며 [PR27](https://github.com/twoimo/alden/pull/27)로 두 기존 작업 브랜치에 정상 반영했다. 이후 문서만 바뀌어도 이 실행 소스와 문서 HEAD를 구분한다.

## 수정한 문제

0.3.59 이전에는 수집 본문의 공백 정리가 들여쓰기·탭·빈 줄을 지울 수 있었다. 현재 본문은 NFC와 줄바꿈만 정규화하며 원문 보관과 이력을 유지한다. 이름·식별자·URL 정규화와 본문 구조를 같은 규칙으로 처리하지 않는다. 정책 salt 변경으로 이후 수집이 기존 snapshot을 한 번 재확인한다. 운영 원본의 일괄 재작성은 하지 않았다.

자동 snapshot이 A→B→A로 돌아오면 새로운 전환을 기록한다. 이미 완료된 명시적 요청의 replay와 중단된 요청의 retry는 기존 identity를 유지한다. 실제 후보 Resources의 임시 `collect_target→capture_source` 실행에서 발견한 보관 근거 참조 누락도 수정했다. capture run은 잠긴 현재 snapshot 전환에 연결하고, 동일 content-addressed manifest·버전은 재사용한다. 취소·오래된 cursor·완료 참조 복구가 기존 run/event를 중복 생성하지 않는지 검사했다.

0.3.59의 native 감사에서는 대화 화면이 65.648초 후에도 messages pending으로 남아 실패했다. 보이는 창의 blur가 이력 조회를 숨김 처리하는 연결을 두 UI 회귀로 재현했다. 0.3.60의 데이터 화면은 `pauseOnBlur:false`를 사용하며 실제 native/document 숨김·복원·닫힘을 계속 따른다. 그래프 등 렌더러의 기본 정책은 유지한다. 같은 설치 감사는 제한 시간을 늘리거나 readiness를 강제로 바꾸지 않고 통과했다.

미디어 생성이 취소될 때 실패 기록이 모델 health를 덮어 자동 선택 대상에서 제외하는 문제는 fake HTTP 회귀 두 개로 재현했다. 취소된 요청은 제공자 실패 관측으로 저장하지 않는다. 실제 transport/response 장애 기록은 유지한다. 이 검증은 서버 측 취소 확인이나 실제 모델 추론 결과가 아니다.

## 검증과 전달

| 단계 | 확인한 범위 |
| --- | --- |
| 0.3.59 소스 | pinned Python3.11의 관련106개 회귀 통과 |
| 0.3.59 제품 | 후보16개 Resources 회귀, 설치57파일/서명/설정 대사, 실제 임시 collect→capture 복원 확인 |
| 0.3.60 소스 | UI465개·타입 검사·Vite build, 관련 Python89개 통과 |
| 0.3.60 번들 | 후보와 설치 Resources 각각62개 통과; 모든 제품 모듈 경로를 실제 Resources로 확인 |
| 빌드 | canonical build와 현재 입력 영수증 대사. 첫 빌드의 오래된 Cargo asset 참조로 인한 영수증 실패도 보존 |
| 설치 보존 | 57파일 SHA/모드, strict/deep ad-hoc 서명, bytecode 없음, 모델/등록/비상 설정3개·수집 대상7개·정책2개 보존 |
| 복구 | 설치 전0.3.59 앱의 독립 복구본 및 설정 백업을 해시·모드·서명으로 확인 |
| native | 설치 실행 파일의 격리된 읽기 전용 WKWebView: 기본1200×728·작은640×648,7페이지/14상태의 pending0·표시 오류0·제목 포함 |
| 시각 검토 | 24개 원본 PNG를 모두 직접 확인. 기본·작은 대화/이력/설정, 모델 목록, 원문, 일정/취득 컨트롤 포함 |
| CI | [실행 소스 CI38119062209](https://github.com/twoimo/alden/actions/runs/38119062209)의4개 작업 성공 |

이미지 검토는 별도 영수증으로 기록했고, 캡처 생성 검사 자체의 `visual_review: pending` 값을 성공으로 덮지 않았다. 감사 이후 설치57파일·설정·복구본을 다시 대사했다. 검증 명령은 `-B`로 실행하여 서명된 Resources에 bytecode를 만들지 않았다. 테스트에는 private 파일·fake HTTP/DB/프로세스를 사용했으며 메시지 전송이나 새로운 모델 로딩을 수행하지 않았다.

증거 루트는 `/Users/twoimo/Documents/Codex/2026-10-07/macos-ai-alden-ai-1-cpu/outputs/project-hardening-20261011/`이다. `source-delivery.json`, `candidate-runtime-verification.json`, `installed-runtime-verification.json`, `installed-verification-post-audit.json`, `installed-native-verification.json`, `installed-visual-review.json`, `ci-current.json`과 실패 로그를 보존했다. 0.3.59의 capture 재현과 실패한 native 감사는 별도 `outputs/project-hardening-20261010/delivery-0359-20261011T062236Z/`에 남겼다. 원문이 포함된 캡처는 로컬 private 증거로 유지한다.

## 남은 종료 조건

이번 화면 검증에서 그래프 중앙 밀집과 일부 라벨 겹침은 여전히 보였다. 저장 벡터의 유사도와 공간 근접성을 의미 정답이나 사실 관계로 승인하지 않는다. 의미 중복·동명이인·이상치 원인·가역 병합·독립 군집/GraphRAG gold 평가와 개선이 남아 있다.

고난도 병렬 검토는 당시 지원된 최고 `chatgpt-web/gpt-6-sol/xhigh` 두 작업으로 수행했다. 미디어 검토 결과는 부모가 실패를 재현하고 검증한 뒤 반영했다. 군집 검토는 ChatGPT 응답 중단으로 오류 종료했으므로 승인 근거가 없다. 추가 Astra·모델 대체·유료 용량을 사용하지 않았다.

Gemini Flash High 수동 기본값과 로컬 readiness/RAM 가드를 보존했다. 카탈로그·연결 상태는 모든 모델의 추론 성공을 뜻하지 않는다. 실제 동시 미디어 입력, 여러 호스트 전체 경로, 사람 음성/끼어들기/에코, 물리 조작·잠금·절전, 전체 CPU/GPU/RAM/배터리·latency·비용, 실제 DPO 승격과 정식 릴리즈/프로덕션은 원래18개 TODO에 유지한다. 유효한 감독자 증빙이 없는 운영 worker 활성화나 Developer ID/공증 게이트를 우회하지 않았다. 이번 로컬 ad-hoc 설치는 정식 프로덕션 배포와 구분한다.
