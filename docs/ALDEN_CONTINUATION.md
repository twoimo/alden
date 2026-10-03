# Alden 작업 이어받기 — 2026-10-03 KST

전체 목표는 **진행 중**이다. 현재 목표 원문은
`/Users/twoimo/.codex/attachments/0eef40c0-ffdb-45f1-93cd-e9497437da8a/goal-objective.md`이며,
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

## 추가된 사용자 범위 — 2026-10-03

- 사이드바5페이지의 사용 경험·컨셉을 검토하고 실제 앱에서 수정한다. 불필요한 여백·간격·문구를 줄이고 정보 밀도를 높인다.
- OSK v4.1.2 실제 엔진·설치 리소스 해시 일치 확인. 현재 sync managed151, pending0/conflicts0. 브라우저는 synthetic 개발 예시다.
- MCP의 직접 API 대비 시작/호출/메모리/토큰 비용을 실측하고 적절한 경로를 구현한다.
- twoimoui-MacBookPro.local은 현재 Mac이다. Alden0.3.10과 ChatGPT26.930.21537 실행 중. Alden-dot 연동을 구현·검증한다.
- ChatGPT 앱 자체의 CUA 조작은 도구 안전 정책으로 차단됐다. 우회하지 않고 공식 connected-computer/plugin 경로를 준비한다.
- 자기 평가와 수학적 성능 최적화: 비교 조건·식·표본·미달을 공개한다. 세계 최고라는 검증되지 않은 순위를 주장하지 않는다.
- 세션 OSK 포착은 착지 미정으로 저장되지 않았고 검토 대기/계수는 유지된다. 사용자가 한 번 안내받았다. 제품 vault와 세션 포착 대상을 혼동하지 않는다.

현재 UI 수정: 공통 잉크색 토큰, 중복 topbar 제거, 좁은 sidebar/간격, 최근 기록 자동 선택, 빈/실패 상태, 음성 페이지에 마이크 동선, DB→기억 정리, 저장 결과 표시, 숨김 상태 보존.
수정 후 build 통과. UI231 첫 재검사에서 텍스트 기대값2건만 실패했고 기대값 수정 중. Label overlay 경계는 resize에서 캐시한다.
브라우저 screenshot 일부가 잘리거나 blank여서 거부했다. 현재 app에 적용 완료/시각 검증 완료라고 보고하지 않는다.

0.3.10 전달은 완료: 6e7022c·CI37099249366 4/4, 설치35경로·release6개 다운로드/ZIP대조.
릴리즈초안 https://github.com/twoimo/openkakao-bot/releases/tag/untagged-19d2ec0cadd04f2cfb13 .
LLM actual12회 사실12/12·오류0, TTFTp50 .194794/p95 3.530862초, 답변p50 .361727/p95 3.688151초, 관측decode p50 60.673454 tokens/s.
같은 설치 voice adapter SHA756f8ab7; socket11234 only. 첫 요청 tail이 목표미달이며 모델 변경/전후개선으로 계산하지 않는다.
측정추가 code/scripts/measure_alden_voice_llm.py·tests2·CI모듈은 아직 작업변경이다.

다음: UI 기능회귀·5페이지/최소창 실제 캡처 → 0.3.11 native 설치/CI/전달, MCP benchmark와 local bridge, Dot 최종연결/확인.

협업 승인: 사용자가 ‘공유하고 작업을 조정해줘’라고 답했다. durable task01a1006a-47ce-7609-bec2-7af9dc651098(‘Alden 연동과 성능 최적화’)에 상태·분담을 전송했다.
중앙작업은 UI/native0.3.11/릴리즈, 해당분리candidate는 MCP·Dot/비교측정 담당. 기존모델·서비스/포트/큐 변경 금지 및 reports/coordination-status.json 공유를 요청했다.
ChatGPT 자체 UI조작 차단은 우회하지 않는다. 필요시 실제 준비된 plugin의 최종 사용자 UI단계만 남긴다.

## 0.3.11 최종 통합 — 진행 중

- UI237/237·desktop Rust95/95·Clippy 통과. 정상/실패 상태와 마지막 메시지·음성 re-entry 회귀를 포함한다.
- 동일1200×760 synthetic 기록1000건/최근100건: 본문 높이549.563→636.156px(+15.76%), 면적+19.14%, 완전 표시4→6(+50%). n1 UI 비교이며 모델/앱 처리 속도 개선이 아니다.
- 실제640×680 브라우저 치수와5페이지 overflow0 확인. 기존 viewport capability가1200×760을 반환한 잘못된 ‘compact’ 캡처는 배제했다. CDP viewport-capture로 실제640×680을 재확인했다.
- 좁은 창에서 잘린 그래프를 발견해 투영 경계로 overview camera 거리를 계산한다. 두 projection 회귀 통과.
- native0.3.11 읽기 전용5페이지 검사: 첫 실패는 고정 focusSlot0 가정. 실제 복원된 camera/selection/targets가 일치함을 진단 후 서로 다른 유효slot+전체복원 조건으로 교정했다. default/min/hidden 검사 통과하였으나 실제 대화 페이지가 오류여서 완료로 간주하지 않고 조사했다.
- 실제 history 목록1182행의 nonpositive system행과 JS safe-integer 초과 ID 발견. 기존 list_all_chats 공용 계약을 보존하고 history-rooms 경계만 유효positive·recent-first·문자열chat_id/last_log_id로 수정했다. CLI 회귀와 actual 다시 읽기를 진행한다.
- native screenshot에서 tab 선택 paint가 한 프레임 늦은 증거를 발견해 두RAF 이후 캡처하도록 교정했다. 원본/private 캡처는 외부/Git에 올리지 않는다.
- MCP0.2.0 2파일 patch74dd1504… 적용·stdlib/pinnedPython parent31검사 통과. 절대1.5초deadline·협력취소·late reply 억제 포함. bundle allowlist/CI에 추가, sourceSHA3c65936b…. 지속등록·Dot호출은 아직 미검증이다.
- OSK는 incident edge를tick별1회 구축(O(V+E))하고revision을tick안에서 재사용한다. parent20회귀 통과; peer64회귀/16differential 및 AB/BA30쌍 원자료를 최종 보고와 통합한다.
- 기존5개 모델/voice/Kakao 프로세스·설정/enrollment·stableCLI는 설치 전후 보존해 확인한다. 설치/정확SHA CI/릴리즈 자산·다운로드·postinstall native 검증은 남았다.

중단 뒤 재확인: 직전 goal turn은 history-ID/정렬·MCP0.2·native 실제100건 조회로 진전했다. 중단된exec handle은 사라졌고ps에서 해당검사프로세스 부재를 확인했다. UI237/237·CLI1151/1ignored·desktop95·각Clippy 로그의 실제완료를 읽었다.
필수Python목록 추출시23개 지정메서드를 모듈 전체로 축약·중복 실행한 명령 오류를 발견했다. 제품 regression으로 오해하지 않는다. `.github/workflows/ci.yml`의77개 full selector를 보존한 재실행은 exit0이다. 이전 불완전/범위가다른 로그는 통과근거로 사용하지 않는다.
최신 source build·ad-hoc deep/strict 완료,0.3.11 설치를 진행한다. private snapshots·비공개 대화원문은git에 넣지 않는다. source commit/push/CI/릴리즈·설치filematch·postinstall native가남았다.

## 更新목표·0.3.11 설치·MCP등록 —2026-10-03

사용자가 목표원문을0eef40c0…로 교체했다. 원문 전체를 다시 읽었고 기존11범위에 신경가소성·시냅스·실시간물리리모델링·원논문/정량검증이 추가됐다. 화면상단은 제목/항목수/갱신시각 한 줄을 요청했다. 전체목표는 진행중이며 완료/차단으로 바꾸지 않는다.
현재 설치0.3.11, build/install36경로일치·deep/strict·config/enrollment/stableCLI inode와SHA·기존5프로세스보존을 확인했다. pinnedPython의정확CI77selector1207/28skip 통과. UI237·desktop95·CLI1151/1ignored·각Clippy통과.
`codex mcp add alden_readonly` 공식CLI로 공유설정등록·enabled get readback완료. 기존node_repl의빈args를CLI가생략한것을narrow복구해다른모든기존설정값일치 확인. 설정backup/private receipt는alden-sidebar-preservation-20261003.
설치STDIO init/list/call/EOF를 실제persisted command/args로확인했다: server0.2.0·app0.3.11·OSKready/pending0/conflicts0, E5reachable. 생성metadata0.5s제한은unavailable1회였으나별도GET11234는200/7rows이고둘다loaded boolean필드를제공했다. 서비스/모델변경0. 실제Dotclient호출은미확인.
최종read-only native캡처10장은확보됐다. 데이터조회/가로넘침은확인했으나후반notification-before-draw의고정250ms기다림조건1건은실패하여전체native검사완료라고하지않는다. 결과null을wrapper가 .get한오류는검사실패와분리한다. 이전state가성공한것을이번source완료증거로대체하지않는다.
다음: 이소스를0.3.11기준선commit/push·정확SHA CI로보존하면서, 상단한줄과source변화에반응하는신경형그래프를0.3.12에서진행한다. 추가프로세스/모델/실전발신을만들지않는다. OSKsessioncapture미결속·검토대기/계수는기존처럼남아있다.
