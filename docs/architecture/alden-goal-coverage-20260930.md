# Alden 목표별 현재 근거 — 2026-09-30

전체 목표는 **진행 중**이다. 최신 진행 기록은 [ALDEN_DELIVERY](../ALDEN_DELIVERY.md)다.
과거 설치·공유 작업자 기록은 해당 날짜와 판본의 근거로만 사용한다. 이번 소스 변경은
기존 MLX/E5 서비스, 카카오 세션, 전송 큐, 다른 세션의 앱·설정을 재시작하거나 교체하지 않는다.

| 요구사항 | 이번 작업에서 확인한 근거 | 남은 완료 근거 |
| --- | --- | --- |
| 최신 맥락·턴·취소·출처·중복 | conversation/turn/context ID, 최신 pending slot, 취소 epoch와 늦은 결과 폐기. native processed input의 연속 3프레임으로 이전 재생/턴 취소·앞부분 보존, 집중 CI971/2skip/실패0 | 실제 설치 앱의 자연 대화와 사람의 음성 끼어들기·에코 품질 |
| Tauri v2 / Three.js / Alden | 기존 구현 재사용, 0.1.6 release CLI·frontend·native 오디오 번들. 26/26 resource·29/29 설치파일·단일PID22050·ad-hoc 서명 확인 | native 화면/Retina는 CUA 타임아웃으로 미검증 |
| 숨김/복원·GPU 자원 수명 | 191 frontend 검사, 실제 Chromium/WebGL2와 합성 Tauri bridge에서 숨김 750ms 추가 frame 0; 50회 복원 후 listener 1 | 설치 AppKit/WKWebView 숨김·잠금·Retina 관측, 앱 전체 GPU/배터리 측정 |
| 로컬 27B / Flash-Next | resident 27B에서 한국어 후속 질문 12/12 두 판본. 정확한 모델과 checkpoint 기록 유지 | Flash/iQ 현재 admission·생성 및 동일 조건 cold/warm 비교. 공유 서버 설정을 바꾸지 않음 |
| 웨이크 → STT → LLM → Qwen TTS | 체크포인트·로컬 runtime 존재, wake release gate 및 메모리 admission 유지. native 입출력·녹음된 기준음 재생/취소 일부 실제 검증, 설치 waiter 종료1회12.418ms | 입력은 RMS0. 검증된 wake 모델, 실제 음성 턴과 사람 발화·에코·소음·침묵·짧은 발화·끼어들기 |
| 비상 중단·명시적 재개 | 취소 전 epoch의 작업 재개 방지, owned HTTP socket 중단. 실제 SSE client 3.082–5.383ms 종료, reply 없음 | 물리 단축키, 재생·외부 작업 전체 중단. backend idle은 2.279–2.464초, 즉시 추론 중단 미달 |
| Browser-use / macOS AX | 설치된 Python browser entrypoint가 local27B로 공개 title1건48.556초 성공, 독립 title 일치. live send 없이 AX 회귀 | 실제 Tauri UI caller·macOS AX·포커스 영향. 네이티브 CUA app/inventory 조회는 timeout |
| 사진·링크·파일 맥락 | 실제 ledger 사진 실패16events helper replay, 이미지 누락/읽기 실패의 HTTP 요청 차단 및 recipient/register 전달. 이미지10검사 | active worker는기존판본. 링크recent-tail/refresh,파일metadata/후속맥락,off-tailquote수정완료. 최종통합959 tests/2skips/실패0. 실제vision/file내용읽기는미완료 |
| GraphRAG / BM25 + Dense RRF | 13syntheticqueries dev6/heldout7, finalfilteredbundle4cc536. 실제E5Recall3/nDCG3=.9091/.9091,24.941/45.865ms,whole-contextleak0 | 독립누출tiny2→0. 과거valid_to없는aliascandidate1/7유지. 설치graph시각drill-down및productionquality미검증 |
| 읽기 전용 DB/WAL 스냅샷 | Python DB+WAL stable copy, private SHM 재구축, rollback journal fail-closed. 실제 plaintext mirror 1.30GiB quick_check=ok, 5.314초 | 암호화된 카카오 원본의 생산 전체 경로·최신성. plaintext mirror 관측을 encrypted DB 검증으로 확대하지 않음 |
| 동시 DB writer/checkpoint | 독립 synthetic WAL 시험: 80 snapshots, 95 commits, 19 checkpoints, 실패/혼합 transaction 0 | 실제 생산 암호화 DB의 접근·경합 조건 |
| DREAM-RSI / DPO | 공식 논문 분석과 기존 teacher-forced 실제 로그확률 scorer 근거 유지. alphaXiv PATH 없음, orx help 사용 가능 | 선호쌍 평가·독립 검증·실제 학습·모델 교체를 각각 검증. ln(2) 불변식은 품질 개선 근거가 아님 |
| 디자인·README·Archify | voice/snapshot/search 새 흐름 deliver9/9 각각, browser bounds 검사와 실제 이미지 검토. 기존 8개 구조 검사 통과 | 설치 화면의 짧은 수정/재검토 루프. 구조 검사와 화면 검토를 구분 |
| Git / CI / 산출물 / 설치 / 공개 배포 | native codeeffa5c6 normalpush, 해당 원격CI4/4작업 성공. 로컬Python971/2skip·hosted971/73skip·실패0. 0.1.6설치29/29·resource26/26·ZIP29/29byte대조, 이전패키지보존 | PR #27 draft/main 미병합, 최종worker 미활성화. 공개 서명/공증은 Developer ID 및 workflow 필수 ALDEN_APPLE_* 6개 부재로 차단 |

수치 비교에서 캐시와 다른 세션 부하를 통제하지 못한 표본은 인과적인 성능 개선으로 해석하지 않는다.
스트림 최종 timing은 12개 요청 중 2개만 완료한 부분 표본이며, 전체 앱 CPU/GPU/전력과 음성 end-to-end는 미측정이다.
공개 production release와 로컬 ad-hoc 설치는 별개의 전달 단계다. 최신 native codeeffa5c6와 [원격 CI](alden-native-audio-remote-ci-20260930.json)를 기준으로 읽고 e03a678 worker 후보 및 b987ad1 fixture 이력은 별도로 보존한다. Hosted 건너뜀 수는 로컬 검사와 다르다. 전체 목표는 실제 음성·설치 창 검증과 생산 worker·공개 릴리즈 전달까지 계속 진행 중이다.
