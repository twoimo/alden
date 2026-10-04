# Alden 릴리즈 이력

아래 기록은 각 당시 판본의 구현·측정·제한입니다. 현재 동작은 README와 최신 검증 기록에서 확인합니다.

## Alden 0.3.23 — OSK 정본 노트와 입체 연결

노드를 선택하면 저장된 본문과 실제 노트 링크를 옆에서 읽습니다. 사전 분류 대신 원문과 확인한 논문 초록을 대조해 연구 결론 3개와 명시적인 설계 가설 1개를 SDK로 저장했습니다. 원문 512행의 바이트와 출처·역할을 보존하며 세 전제의 실제 의존 관계를 연결했습니다. 전체 의미 증류·운영 검증의 완료를 뜻하지 않습니다. [증류·읽기 경로와 측정 범위](architecture/alden-source-distillation-20261005.md)

전체 그래프는 더 깊은 3D 공간과 조명이 반영되는 구형 노드로 표시합니다. 실제 관계가 위치와 연결을 정하며, 깊이는 분류나 신뢰도 등급이 아닙니다. 탐색·데이터 변화에 반응하고 숨김·안정 상태의 렌더 중단 정책을 유지합니다.

사전 주제 분류와 주제 확정·승격을 제거했습니다. 그래프는 OSK v4.1.2의 실제 디렉터리, 본문 링크, `derived-from`과 `conflicts`를 읽습니다. Raw 좌표는 출처로 보존하며 노드나 군집으로 승격하지 않습니다.

기존 자동 주제와 관계는 원문을 보존한 채 백업·이행 기록과 함께 철회합니다. 구버전 수집기의 분류 재삽입도 파생 DB에서 차단합니다. 의미 관계는 검토한 OSK 노트에 직접 작성해야 합니다.

빈 그래프에 미리 작성된 사실을 주입하지 않습니다. 근거 없는 옛 bootstrap은 실제 출처가 있는 관측과 구분해 철회하며, 검토로 퇴역시킨 자동 분류 입구는 다시 만들지 않습니다. 과거 릴리즈 기록과 도식은 해당 판본의 이력이며 현재 정본 경로의 완료 증거가 아닙니다.

[변경·실행 근거와 제한](architecture/alden-canonical-osk-20261004.md)

## Alden 0.3.19 — 로컬 사진 답변

공유 서버의 이미지 기능이 꺼져 있어도, 요청한27B 가중치를 확인하고 한 요청 동안만 로컬 이미지 서버를 실행합니다. 전체 사진·맥락의 입력 예산, 메모리 재확인, 연결 소유권·인증과 취소 후 프로세스 회수를 적용합니다. 공개 이미지4종의 실제 답변이 일치했으며 기존 worker·설정은 보존했습니다. [구현·측정·운영 반영 범위](architecture/alden-owned-vision-20261004.md) · [프로세스 흐름](architecture/alden-owned-vision-20261004.html).

## Alden 0.3.18 — 연결 성도와 노트 읽기

[27B 가중치·설정 14개 리비전 대조와 재현 도구](architecture/alden-model-provenance-20261004.md). 디스크 검증과 운영 로딩을 구분하며 앱의 부팅 비용을 늘리지 않습니다.

작은 기억 노드와 실제 연결선으로 전체 구조를 탐색하고, 클릭하면 옆에서 노트를 읽습니다. 연결된 노트 이동·이전 위치 복귀·좁은 창의 세로 배치를 지원합니다. 로컬 연결을 더 확대하면 실제 가지 끝에 연결된 뉴런·접점 형태를 표시하며, 관계 변화가 멈추면 렌더링도 멈춥니다. [구현·근거·검증 범위](architecture/alden-constellation-reader-20261004.md).

긱뉴스 TOP5는 제목·짧은 원문 요약·출처 링크를 나누어 읽기 쉽게 했습니다. 기존 발송 대상·시간·확인 규칙을 유지하며 실제 전송 시험은 하지 않았습니다. 설치·릴리즈와 상시 운영 worker 반영은 검증 기록에서 구분합니다.

## Alden 0.3.16 — 직접 시작하는 음성 대화와 자동화 히스토리

음성 페이지에서 **마이크 켜기·끄기**로 대화를 시작하고 중단합니다. 선택한 음성 대화의 맥락을 이어받고, 긴 입력을 잘라 해석하지 않도록 했습니다. 자동 호출어의 검증 기준은 유지합니다. 설치본 41개 파일과 7개 페이지의 기본·최소 창 검사가 통과했으며 실제 사용자 음성 검증은 남아 있습니다. [구현·검증·범위](architecture/alden-manual-voice-20261004.md) · [음성 상태 흐름](architecture/alden-manual-voice-20261004.v3.html).

사이드바에 **카카오톡 답변**과 **긱뉴스 전송**을 분리했습니다. 채팅방·맥락·내용·전송 결과를 검색하고 이전 기록을 읽습니다. 채팅방 선택도 검색할 수 있으며 로컬 모델 두 개를 명확한 이름으로 표시합니다.

설치된 MLX 앱이 `MLX-Serve.app`으로 바뀌어도 기존 서명·소유권 검사로 인식하도록 수정했습니다. 기존 27B 실행과 모델 설정을 보존합니다. 실제 Flash-Next 준비 요청은 현재 메모리 기준에서 중단됐으며 모델 구동 성공으로 표시하지 않습니다. [원인·실측·검증 범위](architecture/alden-mlx-app-rename-20261004.md).

205만 Raw 기록과 이후 변경을 보존하고 OSK API로 활성 지식 115개를 실제 출처 좌표에 연결했습니다. 설치본 0.3.14의 41개 파일을 대조했으며 현재 저장 자료 2,050,732건, 대기·충돌 0건을 확인했습니다. 같은 자료의 재확인 중앙값은 27.17ms(n=5)이며 새 Raw 파일과 SDK 변경은 0건입니다. 원본 수집의 권한 대기는 별도로 표시합니다. [Raw 변경 추적·측정 범위](architecture/alden-raw-delta-20261004.md) · [히스토리·7개 페이지 검증](architecture/alden-raw-history-20261004.md) · [현재 이어받기 상태](ALDEN_CONTINUATION.md). 로컬 ad-hoc 서명 설치이며 공증·Dot 실제 호출·물리 음성과 전체 목표의 남은 검증은 계속 진행합니다.

2026-10-03 · [Alden 0.3.10 로컬 임베딩·설치 근거](architecture/alden-embedding-memory-20261003.md): 같은 가중치·44개 입력·변형별 새 프로세스 3회에서 E5 메모리 중앙값 14.453→1.517GB(−89.50%)를 재현했고 출력이 일치했습니다. 44개 요청 시간은 0.521→0.726초로 늘었습니다. 35개 설치 파일·별도 네이티브 숨김/설정 검사를 대조했습니다. [메모리 순서도](architecture/alden-embedding-memory-20261003.html) · [현재 이어받기 상태](ALDEN_CONTINUATION.md). 상시 앱 직접 조작·자연 음성·권한·공증·프로덕션 적용은 미완료입니다.

## Alden 0.3.12 — 동적 지식 그래프

확인된 관계가 시냅스 다리로 성장·수축하고, 노드 배치가 실시간으로 재조정됩니다. 화면에서는 은은한 흐름을 유지하며, 숨김·동작 줄이기·일시 중지에서는 멈춥니다. 상단 정보는 한 줄입니다. 같은 1200×760 예시의 5초 관측에서 프레임 타이밍 수정 전후 23.0→30.2fps, 프레임 간격 p95 50.1→35.2ms를 확인했습니다(변형별 1회; 추론·전력 개선 수치가 아닙니다).

[연구·수학·측정 근거](architecture/alden-neural-plasticity-20261003.md) · [변경 감지와 렌더 순서도](architecture/alden-neural-plasticity-20261003.sequence.html) · [예시 화면](architecture/alden-neural-plasticity-20261003/graph-default-fixture.png). 해당 검증 당시의 로컬 설치본은 0.3.12였습니다. 공개 서명·공증, 실제 Dot 호출과 사람의 음성 검증은 별도로 남아 있습니다.

## Alden 0.3.11 — 설정 밀도·실제 기록·읽기 전용 MCP

설정 5페이지를 통일하고 중복 여백을 줄였다. 같은 1200×760 예시에서 본문 면적 +19.14%, 완전히 보이는 메시지 4→6(n=1). 실제 history ID는 문자열·유효 방·최신 순서로 전달하며 시스템 알림의 원문은 접어 읽을 수 있다. OSK 151/512노드의 합성 warm p50은 35.79%/56.84% 감소했다. MCP 0.2.0은 취소와 1.5초 deadline을 지원하고 외부 상태 조회에만 쓴다. 같은 collector의 API→MCP 비용은 1.415→1.653ms(+16.82%, 변형별 9000요청)다.

로컬0.3.11설치36경로·기존설정/5프로세스보존, UI237·desktop95·CLI1151/1ignored·Python1207/28skip을확인했다. 공유MCP등록과설치STDIO는확인했으며실제Dot호출·물리음성·전체native notification검사·서명/최종운영적용은미완료다. [5페이지검토](architecture/alden-sidebar-audit-20261003.md), [OSK/MCP수치와범위](architecture/alden-mcp-performance-20261003.md), [이어가기](ALDEN_CONTINUATION.md).


2026-10-03 · [Alden 0.3.9 전달·근거](architecture/alden-orbs-20261003.md): 정지한 장면을 정상으로 다루도록 네이티브 검사기를 수정하고 실제 WKWebView 기본·최소 설정, 탐색 복원, 합성 가시성 알림의 숨김·복원을 확인했습니다. 별도 검사 인스턴스의 결과이며 상시 primary 조작·물리 잠금·음성·Retina와 구분합니다.

2026-10-03 · Alden 0.3.8: [Thinking Orbs](https://libraries.dev/orbs)의 공식 엔진을 지식 그래프 주요 노드·사이드바·메뉴바에 적용했습니다. 실제 작업 상태에 연결하며 대기·숨김·모션 감소에서는 정지합니다. 노드 선택 시 전체 저장 설명과 채팅방·작성자·날짜가 있는 대표 원문을 보여주고, 네이티브 브리지까지 계정·인물·방 범위를 검증합니다. 같은 검색 화면의 1초 표본 3회에서 라벨 변경 138→0, 대기 렌더 0을 관측했습니다. 이는 소스의 명시적 예시 데이터 측정이며 설치·물리 화면·전체 전력과 구분합니다. [디자인](../desktop/DESIGN.md) · [렌더 수명주기](architecture/alden-three-render-lifecycle.html). 원본 DB 최신화·웨이크/물리 음성·공개 서명·프로덕션 전환의 기존 게이트는 유지합니다.

2026-10-03 음성 진폭: [0.3.7의 재생 PCM·실제 SDK 커서·가벼운 상태 읽기](architecture/alden-playback-amplitude-20261003.md)를 검증했다. 물리 스피커·자연 음성 검증과 구분한다.

2026-10-02 우주 스타일: [지식 그래프·사이드바](architecture/alden-universe-20261002.md)에 이어 [0.3.6 입력 진폭 연결](architecture/alden-voice-envelope-20261002.md)을 검증했다. 멈춘 장면은 새 프레임이 필요할 때까지 대기한다. [설치본 8턴 음성·12회 문맥 측정](architecture/alden-voice-sustained-20261002.md)은 WAV 생성과 실제 재생의 범위를 구분한다.

2026-10-02 지식 개선: [0.3.4 방 제목·정규화·검색 품질과 색인 비교](architecture/alden-graph-quality-20261002.md). 제목이 없는 실제 방을 구별하고, 방·인물·기간을 보존하며 반복 복사와 정렬 비용을 줄였다. 설치·OSK·릴리스 결과는 별도 전달 기록으로 확인한다.

2026-10-02 음성 개선: [0.3.3 디코더 취소와 한국어 발음 검증](architecture/alden-tts-cancellation-20261002.md). 같은 1.7B BF16 모델에서 한국어 원어 화자와 숫자 읽기를 검증하고, 실제 디코더 단계에서 취소를 연결했다. 사람 음성·wake·재생과 전체 응답성 검증은 미완료다.

2026-10-02 설치·검색: [0.3.2 전달 검증](architecture/alden-voice-retrieval-delivery-20261002.json). 설치본 35개 경로와 CI 4개 작업을 확인했고, 로컬 실모델의 방별 검색·주제 전환 5개 턴을 검증했다. 0.3.2 첫 음성 파일의 내용 검사 실패를 위 음성 개선의 기준 사례로 사용했다.

2026-10-01 음성 추가: [MPS·메모리 진입 수정과 실제 합성 실행](architecture/alden-voice-mps-20261001.md). 파일 생성 성공과 발음·내용 품질 검증은 구분한다.

Alden Desktop 0.3.9 includes the redesigned workspaces and reorganizes the wide window around a 3D knowledge graph and four companion pages: KakaoTalk history, voice history, DB updates, and Settings. Settings opens with room automation CRUD, a contextual editor, answer choices, and voice controls. The sidebar footer contains one operating control and a centered version. Left-click opens the 560×420 graph; right-click opens the wide window.

History reads all messages available in the local Kakao DB through fixed-anchor pages; it does not restore messages absent from that DB. Confirmed voice text is stored locally by session. The local corpus now stores every message in a fixed DB+WAL snapshot with full-text search, scoped numeric identities, resumable batches, and atomic publication. Dense/model-assisted retrieval remains a separate capability and is not inferred from a complete raw corpus. An initial real isolated snapshot contained 2,034,371 messages in 1,181 rooms with messages (1,182 roster entries). It produced a 1,913,323,520-byte published store with a successful quick check; this is a dated data snapshot, not a fixed current total. Existing foreground automation workers and send gates are preserved; catalog changes take effect when automation restarts.

The [updated data flow](architecture/alden-history-osk-20261002.html) and [design decisions](../desktop/DESIGN.md) describe the current screens. Version 0.1.9 is an internal source checkpoint; 0.2.0 is the UI delivery milestone. Local ad-hoc installation is distinct from a Developer ID signed/notarized public release, which still requires the unavailable Apple credentials.

2026-10-01 설정 개편: [넓은 설정과 OSK 지식 관리](architecture/alden-settings-osk-20261001.md). 기본 설정은 전체 지식 그래프이며 Style Gallery의 린넨·세이지 디자인을 적용한다. 실제 OSK v4.1.2가 개인 Markdown 지식을 증분 관리한다. 올든 0.1.7 설치·33파일 일치·상시 앱 자동 갱신과 설치된 지식 50개 readback을 확인했다. 상시 창의 직접 조작은 도구 시간 초과로 미검증이며 공개 공증/production은 미완료다.

<div align="center">

2026-10-01 상시 설치본 추가: [실제 메뉴바·기본/최소 설정](architecture/alden-primary-ui-20261001.md)을 기존 primary process에서 확인했다. 코어276×260·normal-level 설정960×880/640×680 이미지3개를 직접 검토했고 크기 복원·native 가시 창0을 확인했다. AXPress만으로는 열리지 않아 짧은 실제 CG 마우스 이벤트를 사용했으며 독립 가상 커서로 보고하지 않는다. 제품 소스·운영 작업자·메시지 전송 변경0. 각1회 창 표시 관측은 rendered UI p95와 구분하며 graph navigation·Retina·물리 중단/잠금·음성·production의 남은 목표를 유지한다.

전달: [Alden 그래프 탐색 후보 초안](https://github.com/twoimo/openkakao-bot/releases/tag/untagged-25ad469edd80663739aa)의 **7개 파일**을 다시 다운로드해 바이트·원격 digest를 대조했다. ZIP30파일은 설치본과 일치하고 overlay30파일도 hash 일치다. 소스`51f71c2`/증거`5cfea2b`의 [CI](https://github.com/twoimo/openkakao-bot/actions/runs/36799335869)4/4 성공·독립 설치/개인정보 검토 통과. [릴리즈 대조](architecture/alden-graph-navigation-release-20261001.json). 공개 공증/프로덕션 완료를 뜻하지 않는다.


2026-10-01 추가: [그래프 탐색 복원·설정 렌더링](architecture/alden-graph-navigation-20261001.md)을 소스`51f71c2`의 설치 바이너리에서 확인했다. 이전32단계·전체 보기·A→B→A epoch/dispose fence, 실제 persisted graph·기본/최소 WKWebView·숨김350ms 렌더 0·재개 frame 진행, 설치30/30·resource27/27, UI199/Rust90/Python1056·Clippy/build 통과다. [Archify](architecture/alden-graph-navigation-20261001.html)9/9·4viewport·이미지4개 검토도 통과했다. 감사용 floating 창과 unavailable 다른 backend라는 범위, private native PNG·초기 foreign-exception2회 미해결을 기록한다. 전체 목표·물리UX·음성·worker·signed release/production은 미완료이며 Git/CI/초안 전달은 후속 readback으로 대조한다.


전달: [Alden 0.1.6 화면 가시성 후보 초안](https://github.com/twoimo/openkakao-bot/releases/tag/untagged-6730d9c69d5ac88931de)에 7개 파일을 올리고 다시 다운로드하여 모두 바이트 일치를 확인했다. 앱 ZIP 내부30파일·소스/증거 overlay23파일도 대조했다. 소스 `8791ea1`의 [CI](https://github.com/twoimo/openkakao-bot/actions/runs/36793165993)는 4/4 성공이다. 초안 target/overlay는 증거 checkout `b7bdda9`이며 공개 공증/프로덕션 완료를 뜻하지 않는다.


2026-10-01 추가: [화면 잠자기·세션 전환 처리](architecture/alden-workspace-20261001.md)를 소스 `8791ea1`의 Alden 0.1.6 설치본에서 검증했다. 별도 설치 바이너리의 두 **프로세스 내부 합성 알림** 모두 두 창 숨김·350ms 추가 렌더 0, 일반 숨김·복원10회도 렌더 0이다. 네이티브 숨김 뒤 DOM 재개 차단과 종료 구독 정리를 추가했으며 Rust90/UI194/Clippy/build 통과·설치30/30·리소스27/27 일치를 확인했다. 물리 잠자기·세션 전환·잠금과 설정 렌더러는 미검증이며 전체 목표/공개 릴리즈/프로덕션은 미완료다. 새 Archify 도식은 가독성 미통과로 미전달이며 기존 검증 도식을 보존한다. Git·CI·릴리즈의 최종 상태는 후속 readback으로 대조한다.


네이티브 전달 링크: [Alden 0.1.6 로컬 후보 초안 릴리즈](https://github.com/twoimo/openkakao-bot/releases/tag/untagged-179cb6167afd74a68bd5). 앱 ZIP·Python 동반 실행 환경·소스/증거·체크섬 **7개 산출물**을 다운로드하여 원본과 바이트 일치를 확인했다. 증거 커밋 `60fdb04`의 [CI](https://github.com/twoimo/openkakao-bot/actions/runs/36790224085)도 **4/4 성공**이다. [전달 readback](architecture/alden-native-render-release-20261001.json)에 소스·설치·릴리즈의 범위를 대조했으며 공개 공증/프로덕션 완료를 뜻하지 않는다.


2026-10-01 최신 네이티브 검증: 소스 `4838fff`의 Alden 0.1.6을 설치했고 **30/30 파일·27/27 리소스**가 일치했다. 설치 바이너리의 별도 WKWebView 인스턴스에서 실제 코어 이미지를 확인하고 **10회 × 350ms 숨김 추가 프레임 0**, 복원 3–4프레임/250ms를 관측했다. 숨김 요청→중단 확인 상한 median **14.622ms**, max **28.442ms**(n=10)다. [화면·측정·범위](architecture/alden-native-render-20261001.md)와 [Archify](architecture/alden-native-render-20261001.html)를 제공한다. 물리 화면은 [1.0]뿐이므로 Retina, 확장 설정, 상시 PID의 현재 화면, OS 잠금, 물리 단축키와 음성·생산·공개 서명 릴리즈는 미완료다. 기존 기록은 각 당시 결과로 보존한다.



# openkakao-bot

**A private assistant for the KakaoTalk macOS app.**

