# Alden 목표별 현재 근거 — 2026-09-30

전체 목표는 **진행 중**이다. 최신 진행 기록은 [ALDEN_DELIVERY](../ALDEN_DELIVERY.md)다.
과거 설치·공유 작업자 기록은 해당 날짜와 판본의 근거로만 사용한다. 이번 소스 변경은
기존 MLX/E5 서비스, 카카오 세션, 전송 큐, 다른 세션의 앱·설정을 재시작하거나 교체하지 않는다.

| 요구사항 | 이번 작업에서 확인한 근거 | 남은 완료 근거 |
| --- | --- | --- |
| 최신 맥락·턴·취소·출처·중복 | 확정 사용자 입력 보존, conversation/turn/context ID, 최신 pending slot, 취소 epoch와 늦은 결과 폐기. 관련 97/97 검사 | 실제 설치 앱의 자연 대화, 마이크와 재생 중 끼어들기 |
| Tauri v2 / Three.js / Alden | 기존 구현 재사용, 0.1.6 metadata와 release CLI·frontend·native bundle build | 25/25 resource와28/28설치파일일치·단일PID50010실행확인. native화면/Retina는CUA타임아웃으로미검증 |
| 숨김/복원·GPU 자원 수명 | 191 frontend 검사, 실제 Chromium/WebGL2와 합성 Tauri bridge에서 숨김 750ms 추가 frame 0; 50회 복원 후 listener 1 | 설치 AppKit/WKWebView 숨김·잠금·Retina 관측, 앱 전체 GPU/배터리 측정 |
| 로컬 27B / Flash-Next | resident 27B에서 한국어 후속 질문 12/12 두 판본. 정확한 모델과 checkpoint 기록 유지 | Flash/iQ 현재 admission·생성 및 동일 조건 cold/warm 비교. 공유 서버 설정을 바꾸지 않음 |
| 웨이크 → STT → LLM → Qwen TTS | 체크포인트·로컬 runtime 존재, wake release gate 및 메모리 admission 유지 | 검증된 wake 모델, 실제 음성 턴과 에코·소음·침묵·짧은 발화·끼어들기 |
| 비상 중단·명시적 재개 | 취소 전 epoch의 작업 재개 방지, owned HTTP socket 중단. 실제 SSE client 3.082–5.383ms 종료, reply 없음 | 물리 단축키, 재생·외부 작업 전체 중단. backend idle은 2.279–2.464초, 즉시 추론 중단 미달 |
| Browser-use / macOS AX | 기존 로컬 browser runtime 및 배경 AX 경계 재사용, live send 없이 회귀 | 현재 설치 caller의 결과·포커스 영향. 네이티브 CUA 조회 3회 timeout |
| 사진·링크·파일 맥락 | 실제 ledger 사진 실패16events helper replay, 이미지 누락/읽기 실패의 HTTP 요청 차단 및 recipient/register 전달. 이미지10검사 | active worker는기존판본. 링크recent-tail/refresh,파일metadata/후속맥락,off-tailquote수정완료. 최종통합959 tests/2skips/실패0. 실제vision/file내용읽기는미완료 |
| GraphRAG / BM25 + Dense RRF | 13syntheticqueries dev6/heldout7, finalfilteredbundle4cc536. 실제E5Recall3/nDCG3=.9091/.9091,24.941/45.865ms,whole-contextleak0 | 독립누출tiny2→0. 과거valid_to없는aliascandidate1/7유지. 설치graph시각drill-down및productionquality미검증 |
| 읽기 전용 DB/WAL 스냅샷 | Python DB+WAL stable copy, private SHM 재구축, rollback journal fail-closed. 실제 plaintext mirror 1.30GiB quick_check=ok, 5.314초 | 암호화된 카카오 원본의 생산 전체 경로·최신성. plaintext mirror 관측을 encrypted DB 검증으로 확대하지 않음 |
| 동시 DB writer/checkpoint | 독립 synthetic WAL 시험: 80 snapshots, 95 commits, 19 checkpoints, 실패/혼합 transaction 0 | 실제 생산 암호화 DB의 접근·경합 조건 |
| DREAM-RSI / DPO | 공식 논문 분석과 기존 teacher-forced 실제 로그확률 scorer 근거 유지. alphaXiv PATH 없음, orx help 사용 가능 | 선호쌍 평가·독립 검증·실제 학습·모델 교체를 각각 검증. ln(2) 불변식은 품질 개선 근거가 아님 |
| 디자인·README·Archify | voice/snapshot/search 새 흐름 deliver9/9 각각, browser bounds 검사와 실제 이미지 검토. 기존 8개 구조 검사 통과 | 설치 화면의 짧은 수정/재검토 루프. 구조 검사와 화면 검토를 구분 |
| Git / CI / 산출물 / 설치 / 공개 배포 | source8ceeea8/graph11bfae7 push·해당 SHA CI 성공. Rust1,137passed/1ignored, 최종Python959/2skips. 0.1.6local설치및ZIP/sidecar/checksums준비 | 후속미디어통합commit·정상push·해당SHACI. 공개 서명/공증은 Developer ID 및 workflow 필수 ALDEN_APPLE_* 6개 부재로 차단 |

수치 비교에서 캐시와 다른 세션 부하를 통제하지 못한 표본은 인과적인 성능 개선으로 해석하지 않는다.
스트림 최종 timing은 12개 요청 중 2개만 완료한 부분 표본이며, 전체 앱 CPU/GPU/전력과 음성 end-to-end는 미측정이다.
공개 production release와 로컬 ad-hoc 설치는 별개의 전달 단계다. source8ceeea8/graph11bfae7은원격CI성공. 최종미디어/문서commit과해당SHACI가남아있다.
