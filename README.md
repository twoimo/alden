# Alden

현재 CollectionStore의 권한·버전·원문 해시와 동일 내용 후보를 개발 CLI로 읽기 전용 감사할 수 있습니다. 실제 26,623개 문서·48,355개 관계에서 동일 정규화 검색 내용 698개 묶음을 확인했고 원본 ID를 병합하지 않았습니다. [검사 범위·빈 텍스트·후보와 의미 판정의 차이](docs/architecture/alden-graph-quality-20261009.md)

## Alden 0.3.53 — 원본 자동 확인

기존 YouTube 영상 대상의 정기 수집에서 원본 자동 확인을 켤 수 있습니다. 설정한 주기에 Aside native API로 영상 정보·트랙·한국어 자막을 새로 취득한 뒤 검증·저장·원문 보관·색인을 이어갑니다. 실패하면 마지막 확인 자료를 보존하고 이전 자료를 새 취득 성공으로 표시하지 않습니다. 중지·음성 우선·비상 중단과 대상별 재시도 간격을 지키며, 이미 받은 자료의 저장·색인 복구는 외부 조회를 반복하지 않습니다. [취득·설정·복구 경계](docs/architecture/alden-automatic-acquisition-20261009.md)

기존 영상2개의 실제 background 취득·저장·원문·색인·등록범위 MCP/RRF를 확인했고, 설치55개 파일/서명과 기존 설정을 대조했습니다. 완료 요청 재전송의 새 실행은0개였습니다. 기본·작은창의 읽기 전용 native24개 화면을 확인했으며 물리 클릭·전체 채널·독립 호스트·정식 릴리즈는 별도 미완료입니다.

## Alden 0.3.52 — 실제 취득 자료의 안전한 갱신

호스트에서 새로 취득한 자료를 기존 영상·게시물 표본 대상에 전달할 수 있습니다. 대상 ID·원문 URL·관측 시각·권한을 확인하고, 이전 입력의 정확한 바이트를 보관한 뒤 현재 해시가 일치할 때만 교체합니다. 자막 접근 실패는 마지막 확인 자료를 덮어쓰지 않습니다. 입력 수신과 수집·색인 완료를 구분하며 기존 일정·체크포인트는 유지합니다. [호스트 취득·전달·검증 범위](docs/architecture/alden-source-refresh-20261009.md)

실제 기존 YouTube 영상 2개를 Aside native API로 재취득해 설치 앱의 개정·원문 보관·색인·MCP/RRF 조회까지 확인했습니다. 이전 버전 53,247개를 보존하고 새 버전 2개를 추가했으며, 완료 요청 재전송의 새 실행은 0개였습니다. 전체 채널 수집·전역 호스트 연결·물리 입력·정식 릴리즈는 별도 미완료입니다.

## Alden 0.3.51 — 링크 본문과 전달 근거 보존

링크의 본문이 프롬프트 예산에 여유가 있어도 1,200자에서 잘리던 경로를 수정했습니다. 원문과 실제 전달된 발췌의 해시·조회 시각·읽은 범위를 연결하고, 제목/설명·페이지 텍스트·자막/README 발췌를 구분합니다. 축소하면 잘림을 표시하며 근거가 빠지거나 바뀌면 답변 생성을 중단합니다. [계약과 검증 범위](docs/architecture/alden-link-context-20261009.md)

## Alden 0.3.50 — 응답 완료와 첨부 오류 구분

선택 모델과 완료 상태를 JSON·스트리밍 응답에서 함께 확인합니다. 실패·미완료·다른 모델의 답변은 게시하지 않으며, 첨부 오류와 제공자 장애를 구분해 모델 상태 표시를 보존합니다. 이미지의 형식 헤더·해시·용량과 전송 바이트를 같은 스냅샷에 연결합니다. [계약·검증·남은 범위](docs/architecture/alden-media-response-20261009.md)

## Alden 0.3.49 — 대상별 주기와 수동 수집

현재 구현의 9개 요구 범위는 [0.3.49 Archify 도식](docs/architecture/alden-0.3.49/README.md)에서 코드 근거와 남은 검증 경계를 함께 확인합니다.

기억 정리의 정기 수집 목록에서 대상별 주기를 저장하고 “지금 수집”을 요청할 수 있습니다. 요청은 영속 큐에 저장되고 기존 일정이 처리하며, 대기와 완료를 구분합니다. 음성·중지·권한·비상 중단·재시도 간격을 유지하고, 오래된 화면의 주기 저장은 최신 설정을 덮어쓰지 않습니다. [실행·복구·검증 경계](docs/architecture/alden-schedule-execution-20261009.md)

## Alden 0.3.48 — 첫 기억부터 이어지는 이력

빈 저장소의 첫 저장 이벤트를 놓치던 경로를 수정했습니다. 기억 정리 목록은 저장소 교체·커서 되감기를 감지해 복구하고, 선택한 범위의 처리 단계와 실제 추가·수정·유지·실패·중지를 구분해 집계합니다. 집계는 영속 이력과 같은 트랜잭션으로 갱신하며, 기존 자료·관계·모델·전송 큐를 보존합니다. [동작과 측정 범위](docs/architecture/alden-journal-continuity-20261009.md)

## Alden 0.3.47 — 선택 모델 버전·실제 준비 상태 확인

CLI에서 Gemini 3.8이 3.7로 바뀌던 경로를 수정했습니다. 선택한 모델의 실제 완료 응답·모델 ID를 확인하고, 다른 모델이나 CLI 버전 확인으로 성공을 대신하지 않습니다. 기존 3개 대상의 새 런타임을 준비했지만 잔여 감독자 증빙 검증이 막혀 실제 워커 전환은 보류 중입니다. [수정과 실행 경계](docs/architecture/alden-worker-readiness-20261009.md)

최신 자료를 묻는 검색에서 확인·발행·문서 개정 시각이 있는 후보를 우선합니다. 시점 미확인·동명이인·명시적 충돌은 보존하고, 이력 조회에서 이전 자료를 계속 찾을 수 있습니다. 관측 시각을 현재 효력의 증명으로 표시하지 않습니다. [선택 기준과 평가 범위](docs/architecture/alden-retrieval-observation-time-20261009.md)

기억 정리 화면에서 대상별 수집 간격·다음 실행·최근 결과와 macOS 정기 확인 상태를 읽습니다. 전체 일정과 대상별 중지·재개를 따로 제어하며, 비상 중단 해제는 별도의 명시적 동작을 유지합니다. 상태 조회는 작업이나 모델을 시작하지 않습니다. [경계와 검증](docs/architecture/alden-collection-controls-20261009.md)

전체 그래프의 밝게 뭉치던 구형 점과 연결선을 작은 발광점·은은한 실제 출처 군집으로 정리했습니다. 큰 화면의 점은 2개 삼각형으로 그리고, 직선 링크의 불필요한 분할·숨겨진 선택 메시의 렌더 순회·사용하지 않는 라벨 생성을 줄였습니다. Hover는 프레임당 한 번 처리하고, 상세 창을 닫은 뒤 이전 시점 복원도 보존합니다. [변경과 측정 범위](docs/architecture/alden-graph-performance-20261009.md)

0.3.43 전달에서는 Aside에서 등록 채널의 영상2개 메타데이터·한국어 타임스탬프 자막을 실제 수집하고, 사용자 Threads 본문1개를 읽어 설치 앱의 별도 영상/게시물 대상으로 저장·원문 보관·로컬 색인·검색까지 확인했습니다. 전체26,623개 항목·48,355개 관계이며 기존 채널 소속1118/407개와 원문 이력을 유지합니다. 자막 종류가 모호하거나 안정 저자ID가 없으면 미확인으로 남깁니다. 단일 노드의 탐색 기록과 비동기 복원 완료를 native검증에서 구분합니다.52개 설치파일·서명·실행소스d090a8f의CI4개 작업을 대조했습니다. [수집 범위와 호스트 경계](docs/architecture/alden-live-source-acquisition-20261008.md)

EmbeddingGemma2의768/256차원을 고정된 작은 평가셋에서 실제 실행했지만 E5보다 우위가 확인되지 않아 생산 모델은 유지합니다. 실제 사람gold·멀티모달·최신성 컨텍스트 문제·물리음성·최종서명배포는 진행 항목입니다. [측정과 남은 조건](docs/architecture/alden-embeddinggemma2-20261008.md)

## Alden 0.3.41 — 실제 주기 갱신과 검색 반영

등록된 취업·Spark·쯔양·장사의 신 자료를 저장·원문 보관·로컬 검색 색인까지 갱신하는 네이티브 일정이 실제로 실행됐습니다. 60초마다 due를 확인하며 대상 주기는6시간, 한 번에1대상/180초입니다. 전체26,622개 항목·48,355개 관계와 원본 이력을 유지했고, 현재26,162개 벡터를 재사용했습니다. 본문/제목이 빈460개는 검색 제외 상태를 유지합니다. 설치된 MCP의 네 프로젝트 RRF·원문 해시/버전·범위 밖 거절, 기본/최소 native 화면을 확인했습니다. 기본 모델은 Gemini Flash·High 수동 선택입니다.

52개 설치 파일·ad-hoc 서명·실행 소스0946dcd의 CI4개 작업을 대조했습니다. 서명·공증된 최종 프로덕션, 실제 사람 음성, 독립 검색 평가와 양쪽 호스트 수집 검증은 계속 진행 중입니다. [일정·복구·검증 범위](docs/architecture/alden-collection-scheduler-20261008.md), [설치 실행 결과](docs/architecture/alden-collection-scheduler-delivery-20261008.json)

## Alden 0.3.38 — 모델 목록·상태·자동 선택

현재 OpenCodex 카탈로그의 모델과 사고 수준을 선택하고, 각 모델의 연결·실행·메모리·최근 응답 상태를 구분합니다. 요청한 Gemini Flash · High를 기본으로 준비했고, 자동 선택은 사용자가 켤 수 있습니다. 그래프 상단을 한 줄로 정리했습니다. 0.3.38을 정상 설치했고51개 파일과 기존 로컬 모델·E5 프로세스를 대조했습니다. 기본값을 Gemini Flash·High로 저장·확인했고 설치된 생성 경로와 기존 검색/MCP 검증이 통과했습니다. 최종 서명·프로덕션을 포함한 전체 목표는 진행 중입니다. [경로와 검증 범위](docs/architecture/alden-model-routing-20261008.md)

## Alden 0.3.37 — 하나의 지식 그래프

기억·대화와 허용한 수집 프로젝트를 기본 화면에 함께 표시하고 프로젝트·출처로 필터합니다. 작은 실제 노드와 가는 연결이 검은 공간에 군집을 만들며 선택한 항목을 강조합니다. 서로 다른 출처의 정체성과 권한은 유지합니다. 전체 표시2048개, 주변 확장24개로 제한합니다. 후보의 기본·최소 크기 렌더링과 확대·이전·숨김·복원 검사가 통과했습니다. 설치·최종 배포는 진행 중입니다. [통합 방식과 검증 범위](docs/architecture/alden-unified-graph-20261008.md)

## Alden 0.3.36 — 원본 스냅샷의 일관된 갱신

개정 항목·관계·체크포인트를 함께 확정하고, 대상별 제외·오래된 개정 거절·처리 규칙 버전 이력을 보완했습니다. 0.3.36을 정상 설치했고49개 파일·설정3개를 대조했습니다. 운영 파생 저장소를 schema3으로 전환했고 기존 필드·행 수와 복구 백업을 확인했으며 설치된 MCP 검색·허용 범위·원문 해시 조회가 통과했습니다. [구현·실제 복사본 검증과 범위](docs/architecture/alden-source-snapshots-20261008.md)

## Alden 0.3.35 — 수집 원문의 로컬 검색과 MCP

보관된 원문을 읽지 못하던 취업 그래프 검색 경로를 보완하고, 프로젝트 범위·원문 버전·해시를 지키는 BM25/Dense 검색과 읽기 전용 MCP를 연결했습니다.0.3.35를 정상 설치했고49개 파일·설정3개·이전 앱 전체 백업을 대조했습니다. 설치된 MCP와 음성 검색 경로에서 실제 RRF 검색·그래프·이력 근거를 확인했고 실행 소스 CI4개 작업이 통과했습니다. 두 앱 호스트 실행과 전체 목표의 나머지 검증은 별도입니다. [구현·실제 실행 근거와 한계](docs/architecture/alden-collection-retrieval-20261008.md)

## Alden 0.3.34 — 음성 대화의 저장된 로컬 모델 선택

음성·파일 대화가 매 턴 저장된 모델 선택을 읽고, 그 요청의 모델과 로컬 서비스 주소를 고정합니다. 준비되지 않은 모델이나 지원하지 않는 설정은 명확히 실패하며 다른 모델로 우회하지 않습니다. 영향받은 음성 검사88개와 UI385개, 실행 소스 CI4개 작업이 통과했습니다.0.3.34를 정상 설치했고47개 파일·설정3개를 대조했으며 설치된 코드로27B 응답·iQ 준비 실패 경로를 확인했습니다. 사람 발화와 최종 서명 릴리즈·프로덕션 등 전체 목표는 진행 중입니다. [구현과 실제 검증 범위](docs/architecture/alden-voice-model-selection-20261008.md)

## Alden 0.3.33 local install — 덮개에 의해 차단된 내장 마이크

권한 허용·프레임 도착만으로 실제 청취를 판단하던 경로를 보완했습니다.0.3.33을 정상 설치했고47개 파일·설정3개를 대조했습니다. 실제 설치된 음성 시작 경로가 닫힌 덮개와 내장 마이크를 하드웨어 차단으로 표시하며 입력 소비를 중단했습니다. 스피커 출력은 유지되고 출력 관측 후 취소도 통과했습니다. 사람 발화·끼어들기·자기 음성 재입력 검증은 덮개 열림 또는 외장 마이크와 사용자 참여가 필요합니다. UI385·음성/네이티브66·Rust108·Clippy와 검증 소스 CI4개 작업이 통과했습니다. [원인·측정·검증 범위](docs/architecture/alden-audio-input-20261008.md)

## Alden 0.3.32 local install — 실제 로컬 모델 관리 API

실제 MLX Serve26.10.1에서 존재하지 않던 모델 제어 경로를 수정했습니다.0.3.32를 정상 설치했고47개 번들 파일·설정3개를 대조했습니다. 설치된 코드로27B 내려놓기·복원(동일 서버 PID)을 확인했으며126개 집중 검사·Rust108·Clippy와 실행 소스 CI4개 작업이 통과했습니다. Flash-Next는 메모리 검사에서57.6GB 필요/49.3GB 가용으로 거부됐으며 구동 성공으로 처리하지 않았습니다. 물리 음성·최종 릴리즈·프로덕션 등 전체 목표는 진행 중입니다. [API·메모리·복원 근거와 남은 범위](docs/architecture/alden-model-control-20261008.md)

## Alden 0.3.31 local install — 저장 변경과 실제 읽기 활동

저장 완료 이력을 현재 보이는 노드의 정확한 버전·대상과 대조해 표시합니다. 숨김 중 발생한 이력을 새 점등으로 연출하지 않고, 중복·역전·재연결과 조회 예산을 처리합니다. 격리된 파일 개정1건이 실제 WKWebView의 노드 활동으로 이어졌으며 UI385/Python29/Rust108(감사9 포함)·Clippy가 통과했습니다.0.3.31을 정상 설치했고47개 번들 파일과 설정3개를 대조했습니다. 설치 경로의14개 화면, 원문 읽기 활동, 탐색 복원과 합성 숨김 검사가 통과했습니다. 직접 UI 접근은 시간 초과로 미검증이며 물리 음성·단축키·잠금·Retina·서명 릴리즈·프로덕션은 남아 있습니다. [설치본 집계](docs/architecture/alden-activity-installed-20261008.json), [활동 계약과 검증 범위](docs/architecture/alden-activity-journal-20261008.md), [격리 검증과 측정](docs/architecture/alden-activity-readback-20261008.json)

## Alden 0.3.30 local install — 주변 조회 비용 수정

0.3.30을 정상 설치했고47개 번들 파일이 후보와 일치합니다.0.3.29에서 조회가5초를 넘는 병목을 재현해, 같은 노드·관계·버전 결과를 유지하며 허용 범위의 관계를 한 번 읽도록 수정했습니다. 전체 범위1·2·3hop 비교에서 각각 같은 결과를 확인했고 Python24개 검사가 통과했습니다. 설치 경로의 실제 읽기 전용 화면 검사는14개 화면, 주변 확장, 이전 탐색 복원과 숨김 후 중단을 통과했습니다. [설치본 집계 근거](docs/architecture/alden-installed-graph-readback-20261008.json)를 확인하세요. 기본 프로세스의 직접 제스처·물리 음성·잠금·Retina·서명·프로덕션 검증은 남아 있습니다.

## Alden 0.3.29 candidate — 실제 수집 그래프 탐색

수집 저장소의26,622개 기록과48,355개 관계를 프로젝트·출처·대상·유형·관계·수집 기간별로 탐색할 수 있습니다. 검색, 제한된 주변 확장, 원문 버전·해시 확인과 이전 시점 복원을 연결했습니다.0.3.29 후보 번들의 실제 읽기 전용 화면 검사와 기본·작은 창의 시각 확인을 마쳤습니다. 설치 상태는 위 최신 기록을 확인하세요. [검증 범위와 조회 시간 비교](docs/architecture/alden-collection-graph-20261008.md)를 확인하세요. 전체 조회의 속도 개선은 확인되지 않았고, 물리 음성·서명·프로덕션은 각각 별도 검증 단계입니다.

## Alden 0.3.28 candidate — 대화 경계와 실제 요청 metrics

이력 로딩 중 입력과 대화 전환을 함께 보호하고, 실제 backend 요청 ID를 대화·턴·입력 맥락에 연결합니다. 엔진 metrics는 전체 엔진 관측값으로 구분하며 연결되지 않은 값은 미확인으로 남깁니다. 집중83개와 변경 후30쌍을 검증했습니다. 전체 mandatory legacy 계약 정비·최종 설치·릴리즈·확장된 수집/통합 목표는 진행 중입니다. [변경·측정·남은 작업](docs/architecture/alden-context-metrics-20261007.md)

## Alden 0.3.27 — 정본 요약과 과거 관측의 범위

노드 상세가 오래된 수집 캐시로 정본 요약을 덮지 않도록 수정했습니다. 시스템 알림만 있는 원문을 사람의 발화로 설명하지 않습니다. 세 원문 관측 구간의 과거 수량·빈 제목·인용 한계를 정정하고, 원문·ID·근거 좌표·수동 본문을 보존했습니다. OSK 부분 검토의 남은 범위는 100단위입니다. [검토·보존·제한](docs/architecture/alden-observation-review-20261005.md)

후속으로 12개 관측 구간을 원문 36개와 대조하고 관측 시점·역할·미검증 인용의 경계를 보강했습니다. 현재 native 검토는 88단위가 남아 있습니다. 실행 코드는 0.3.27을 유지합니다. [추가 원문 검토 범위](docs/architecture/alden-bounded-source-review-20261005.md)

## Alden 0.3.26 — 원문 역할·방 범위와 정본 읽기

사용자 ID가 붙은 시스템 알림을 사람의 발화와 구분하고, 저장된 계정 포함 방 ID를 숫자 조회와 정확히 연결합니다. 실제 활성 방 노트 40개의 범위 판정 불일치를 수정했습니다. 같은 104개 노트의 전체 반환값을 유지하며 정본 준비 중앙값은 182.37→146.38ms로 줄었습니다(n=20씩). [변경·실측·원문 검토 범위](docs/architecture/alden-source-read-20261005.md)

## Alden 0.3.25 — 저장된 전제와 연결을 함께 읽기

검색 1위 노트가 직접 참조한 전제를 한 단계까지 함께 읽고, 연결의 양쪽 ID·해시를 보존합니다. 같은 실제 설계 질의에서 포함된 전제가 1/3→3/3으로 늘었습니다. 긴 본문이 다른 근거를 밀어내지 않도록 예산을 나누고, 방·게시된 계정 경계를 적용합니다. 음성 답변의 전체 출처 증거는 해당 턴의 비공개 기록에 저장합니다. [변경·실측·한계](docs/architecture/alden-canonical-edges-20261005.md)

## Alden 0.3.24 — OSK 정본 그래프와 답변 검색

그래프와 답변 검색이 같은 OSK 정본 노트를 읽습니다. 본문 링크·`derived-from`·`conflicts`만 연결 근거로 사용하고, 노트 수정·이동·삭제에 맞춰 별도 검색 색인을 갱신합니다. 사전 분류나 검색 결과가 새 의미 관계를 만들지 않습니다. 실제 104개 노트의 E5 색인과 세 질의의 RRF 검색, 로컬 27B의 완결된 답변을 확인했습니다. [정본 검색·측정·제한](docs/architecture/alden-canonical-retrieval-20261005.md)

[이전 판본의 측정·전달 이력](docs/ALDEN_RELEASE_HISTORY.md)

[![CI](https://github.com/twoimo/alden/actions/workflows/ci.yml/badge.svg)](https://github.com/twoimo/alden/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/twoimo/alden?color=blue&logo=github)](https://github.com/twoimo/alden/releases/latest)
[![Platform](https://img.shields.io/badge/platform-macOS-000000?logo=apple&logoColor=white)](https://github.com/twoimo/alden)
[![Rust](https://img.shields.io/badge/core-Rust-dea584?logo=rust&logoColor=white)](https://www.rust-lang.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

<p align="center">
  <a href="#features"><b>Features</b></a> &bull;
  <a href="#architecture"><b>Architecture</b></a> &bull;
  <a href="#model-support"><b>Model Support</b></a> &bull;
  <a href="#quick-start"><b>Quick Start</b></a> &bull;
  <a href="#configuration"><b>Configuration</b></a> &bull;
  <a href="README.ko.md"><b>한국어 문서 (Korean)</b></a>
</p>

</div>

Alden 음성 파일 출력의 취소 중 덮어쓰기 결함을 [수정·설치·원격 검증](docs/architecture/alden-voice-wav-20261001.md)했습니다. 필수 Python 1,020개 통과(17 skip), code CI4/4 성공. STT/TTS admission과 출시 wake 조건은 유지하며 사람 음성 전체 흐름은 미검증입니다.

Alden 첨부 문서 읽기는 [구현·설치·실제 로컬 모델 측정 기록](docs/architecture/alden-file-content-20261001.md)에서 확인할 수 있습니다. 실제 카카오 파일 transport와 운영 worker 적용은 아직 미검증입니다.

---

`Alden` helps you find context and prepare replies in KakaoTalk. Conversation data and AI requests stay on your Mac; replies go through the KakaoTalk app already installed there.

<h2 id="architecture">Architecture</h2>

Alden is a macOS menu-bar assistant. Its compact display is drawn with Tauri v2 and Three.js. KakaoTalk messages, local search, and model requests stay on this Mac; sending a reply uses the installed KakaoTalk app. Press **⌘⌥⇧Esc** from any app to latch the global emergency abort.

Browser jobs use a dedicated, fixed CPython runtime with owned Chromium, separate from menu and voice dependencies. See [browser runtime provisioning and evidence](docs/architecture/alden-browser-runtime-20260927.md) for the pinned dependencies, offline installer and verification boundaries.

The 2026-09-27 installed Python browser backend returned `Example Domain` in **28.770 s**, matched an independent HTTP 200 DOM read, emitted one JSON result and left zero owned Chromium processes. This single check does not exercise the graphical Tauri caller or establish general accuracy or a latency improvement.

Current implementation diagrams are listed in [the six-map guide](docs/architecture/alden-current/README.md): process boundaries, voice/cancellation, rendering, canonical GraphRAG, encrypted snapshots and offline evaluation. Each has 9/9 artifact checks plus browser and image-review evidence. Earlier dated maps remain historical records.

### The knowledge observatory

1. Tauri supplies native visibility; the sidebar also selects which graph surface is visible.
2. Three.js renders at most 120 overview nodes/512 edges, or 24 local nodes/144 edges. Lit node positions and connections follow actual canonical references. Selecting a node reads its saved body, real links and bounded source evidence beside the graph.
3. Fresh voice, reply, retrieval and DB work select the orb state. PCM input/output retain their separate envelope. Quiet, paused and reduced-motion orbs use a static frame.
4. Hidden windows stop render callbacks and owned timers. Restoring a window reuses the bounded frame cache and existing source identities.

The retained legacy gold-core shader benchmark measured 3.4%/2.5% lower CPU submission at 15/30fps (approximately 3–5μs/frame,8 draw calls), with 11/12 pixel cases identical. It concerns that legacy component, not the current graph or GPU/battery power. The current 0.3.8 source fixture instead measured repeated label mutations 138→0 in three 1-second active samples, with idle graph and canvas-orb draws 0 in three 1-second samples.

See the current [render lifecycle](docs/architecture/alden-current/render.html). The older [activity-to-motion map](docs/architecture/alden-core-load-mapping.html) describes its dated component implementation.

Built 0.1.6 rendered in Chromium/WebGL2 with an empty public fixture; all three images were directly reviewed. This is not an installed WKWebView capture. [Compact settings](docs/architecture/alden-renderer-0.1.6-compact.png), [browser-emulated 2x panel](docs/architecture/alden-renderer-0.1.6-retina2x.png), and [review scope](docs/architecture/alden-renderer-0.1.6-review.json).

![Alden champagne-gold spherical core, 0.1.6 browser rendering](docs/architecture/alden-renderer-0.1.6-default.png)

### Finding related conversations

1. The collector copies the encrypted KakaoTalk DB and WAL together into a private replica, verifies before/after file signatures and source identity, and fails after bounded retries if they keep changing. SHM is rebuilt only in the private replica. This path does not claim SQLite Online Backup usage.
2. It opens only the snapshot in read-only mode. A failed or unstable copy never falls back to the live database.
3. It normalizes shortened names and searches by both words and meaning. When both searches are available, reciprocal-rank fusion (RRF) combines their results; otherwise it keeps the working word-search results.
4. The knowledge view reads canonical OSK notes and their actual directed Links and predicates. Selecting a note opens its content and source evidence beside the graph. The global view has a 120-node/512-edge budget; local expansion has a 24-node/144-edge budget. Reading does not copy the original database or start a reindex.

The requested [Threads reference](https://www.threads.com/share/BBIeDkkHei/) was inaccessible during the 2026-09-30 check, so this delivery does not claim a verified interpretation of that post. Raw source records retain their identity and original role. Source metadata and cached ERE rows do not establish semantic groups or knowledge dependencies. Meaningful notes and organization require source-grounded OSK review. BM25 remains available when local embeddings are unavailable; RRF is used when both ranked candidate lists are ready.

GraphRAG defaults to the dedicated loopback embedding adapter at `http://127.0.0.1:11236/v1/embeddings`; `OPENKAKAO_LOCAL_EMBEDDING_URL` can explicitly override that URL. Dense retrieval fails closed: if the adapter is absent, not ready, advertises the wrong model, returns invalid vectors, or is configured off loopback, GraphRAG keeps BM25 and does not fall back to the `11234` generation gateway, the `11235` Flash-Next server, or external inference. See [Alden local embeddings](docs/architecture/alden-local-embeddings.md) for the pinned model and bounded HTTP contract.

The local synthetic GraphRAG refresh persisted **3/3** E5 vectors and combined **one BM25** and **three dense** candidates with RRF. The adapter's fresh-process maximum RSS fell from **1,028.4 to 670.7 MiB** after replacing the Transformers tokenizer import, a measured **34.8%** reduction on this host. The installed app's dedicated LaunchAgent now serves the exact E5 model on `11236`: it reached readiness in **3.5 seconds** from the **256 MiB** minimal runtime and remained resident after four minutes. Its installed plist now has `RunAtLoad=true`; a fresh bootstrap reached readiness in **22.3 seconds**, while actual logout/login startup remains unverified. A backed-up, locked refresh of the live graph indexed **50/50** entities. The three Kakao reply workers were cut over after a clean drain to immutable runtime `20260927T082218Z-23471`; readback found **3/3 ready**, no active jobs or uncertain deliveries, and no watermark regression. A read-only query through that exact runtime combined **two BM25** and **40 dense** candidates in RRF mode. No new conversation job had arrived after the cutover, so real reply quality and skip-rate changes remain unmeasured.

See the current [canonical GraphRAG sequence](docs/architecture/alden-current/retrieval.html) and [snapshot/ingestion map](docs/architecture/alden-current/snapshot.html). The previous conversation-map diagrams are dated historical architecture.

### KakaoTalk reply turns

Each incoming KakaoTalk row remains a durable queue item. Consecutive rows from the same room and numeric author, no more than 15 seconds apart, are assembled into one reply turn, capped at six rows and 8 KiB. The 15-second settle interval lets the newest fragment arrive before inference; a successor supersedes an earlier job only when its saved burst IDs include that earlier row. The assembled prompt keeps each included message in order. Author changes, older attachments, and size/count limits end the burst. Legacy v1 queue records retain their original 2-second interpretation.

Empty Kakao emoticon rows (message types 12, 20, and 22) enter the same durable queue as `[이모티콘]` instead of being acknowledged as empty input.

Punctuation-only follow-ups such as `???` and short referential questions such as `뭐지 저건` are treated as pointers to the current thread. When recent messages give a topic, the worker answers from that context or asks one brief, topic-specific clarification tied to its recent-message evidence; a generic “I don't understand” reply is rejected. If local generation returns malformed output, referential follow-ups use this grounded clarification directly, while other malformed outputs are retried at most once per durable queue event. Factual questions whose answer is absent keep the existing explicit unknown-answer path.

### Voice conversation

The installed voice page supports explicit microphone start/stop. Automatic wake-word startup remains blocked by its release gate. The local pipeline uses four recent turns and 600 characters per message, with a ten-minute idle reset, source/turn identity, cancellation and self-TTS input fencing. A truncated model response is rejected before speech/history commit. Physical microphone-to-playback timing and the full natural-voice flow remain unverified. See the current [voice/cancellation map](docs/architecture/alden-current/voice.html).

Before loading Whisper or Qwen3-TTS, a local-only admission check requires at least 8 GiB or 10 GiB of reclaimable RAM respectively and 2 GiB of free swap. If either probe is unavailable or the budget is low, the voice session reports the condition and does not load the model. A synthetic local voice run on 2026-09-24 reached the safety stop before a complete turn; end-to-end voice remains unverified on this host.

Local MLX replies reject an explicit response model ID that differs from the requested model, after removing an optional `mlx/` transport prefix. Responses without a model ID remain accepted for gateway compatibility; generation readiness is still tracked separately.

The settings UI marks voice status unavailable when its heartbeat is more than five minutes old, instead of presenting a stale `wake_listen` state as live.

### Other architecture diagrams

- [Tauri menu-bar architecture](docs/architecture/alden-openkakao-units1-4.html)
- [Local MLX request drain and model swap](docs/architecture/alden-model-request-drain.html)
- [Offline DREAM-RSI review loop](docs/architecture/dream-rsi-provenance-loop.html)
- [Current local DPO scoring evidence and remaining integration](docs/architecture/alden-dpo-scoring-20260927.md)
- [27B checkpoint compatibility and corrected local DPO scoring](docs/architecture/alden-dpo-checkpoint-compatibility-20260930.md)
- [Versioned offline evaluation, real 27B receipts, and build integration](docs/architecture/alden-version-evaluation-20261001.md)
- [Versioned evaluation workflow](docs/architecture/alden-version-evaluation.html)
- [Actual offline 27B DPO updates, adapter reload, and evaluation](docs/architecture/alden-dpo-training-20261001.md)
- [Offline training workflow and product promotion boundary](docs/architecture/alden-dpo-training.html)
- [Browser-use lifecycle](docs/architecture/alden-browser-use-lifecycle.html)

The [dated local-tool evidence](docs/architecture/alden-local-tools-evidence-20260927.md) records two real Browser-use navigations through the local 27B model: navigation succeeded in **2/2** cases, but the requested field was correct in **1/2**. The source now includes an opt-in exact-target background AX CLI with a five-second maximum and explicit uncertain-effect reporting; **59/59** focused fake-adapter tests passed. No real AX press or installed desktop invocation is established by those tests.

After the DOM-grounding correction, the same two task types returned **2/2** exact matches against independent page reads, with **85/85** focused source checks passing. This covers document titles and the first Wikipedia search result, not general web factual accuracy. The [live receipt](docs/architecture/alden-browser-grounding-live-20260927.json) records the source hash and field evidence. The installed menubar Python lacks the browser dependencies, so installed desktop browser execution remains incomplete.

The corrected source and standalone AX CLI were rebuilt and installed locally at approximately 22:33 KST on 2026-09-27. The installed bundle matched the build with zero differences and one Alden process running; the Kakao host reported **3/3** rooms ready. The installed browser command itself returned `browser_job_failed`, confirming the separate runtime connection gap. This local ad-hoc-signed build is not the signed/notarized public release.

## Source readiness and dated evidence

Installed Alden 0.3.24 matches 45 built files and uses the canonical OSK/E5 path. Its exact source passed [all four CI jobs](https://github.com/twoimo/alden/actions/runs/37229498034). The installed local 27B returned a complete knowledge answer; natural microphone/playback, primary/tray/Retina/lock/shortcut, Flash-Next admission, Dot invocation and Developer ID production delivery remain open. [Current evidence boundaries and six diagrams](docs/architecture/alden-current/README.md) include the limited source-note retrieval checks.

The owned vision path can run the verified local 27B during one bounded request when memory, model, identity/auth and cancellation gates pass. It preserves the existing shared server and model selections. Four public fixture answers and cleanup were verified; actual Kakao image transport and physical UI interaction remain separate unfinished checks. [Owned vision evidence](docs/architecture/alden-owned-vision-20261004.md).

### Earlier dated evidence

The [2026-10-01 local vision evidence](docs/architecture/alden-local-vision-20261001.md) confirms actual pixel interpretation with the same cached 27B weights in a temporary owned MLX Core process: four public fixture answers were correct, and the checkout worker's image plus strict JSON generation path succeeded once through isolated discovery/auth seams. At that date, shared-server photo handling was unavailable with `--no-vision`. The experiment does not verify installed UI interaction or retained visual history. It records request timings, post-run weight fingerprints, sampled MLX allocations, cleanup and the first run's invalid registry accounting; it makes no speedup or peak-memory claim.

The [photo follow-up fix](docs/architecture/alden-photo-followup-20261001.md) rejects future and foreign-room photo sources, preserves the original photo ID separately from the current question, and cancels/cleans owned download processes and scratch files. The same 27B answered one isolated pixel-grounded follow-up through the checkout worker in 16.325 seconds (n=1; fixture retrieval/lease/rerank seams). Local mandatory checks ran 1,027 tests with 17 skips and no failures; an inactive 21-asset candidate preserves the existing three selectors. This external worker is not activated in production or verified through the installed UI.

The [2026-10-01 atomic graph-cycle update](docs/architecture/alden-graph-cycle-20261001.md) shares one DB+WAL snapshot across all stages and rolls back partial graph/FTS changes on failure or cancellation. Five alternating pairs on the same frozen real data reduced graph-only median time from **5.383 to 3.390 seconds** (observed **37.04%**); dense was stubbed and host workloads were not controlled. Full entity/relation/FTS digests matched in all ten runs. Mandatory Python checks passed **981 tests, 16 skips, zero failures**. The [current installation receipt](docs/architecture/alden-graph-cycle-install-20261001.json) verifies **29/29 files, 26/26 resources**, signature and LaunchAgent executable; a separate backed-up real-E5 refresh passed in **3.335 seconds**, with matching graph/dense watermarks. These checks do not verify native window interaction, human voice or production worker activation. Earlier measurements below remain dated evidence.

**Earlier 2026-09-30 installation: Alden 0.1.6.** The [native-audio installation readback](docs/architecture/alden-native-audio-install-20260930.json) found 29/29 matching files, 26/26 resources and one LaunchAgent process, with ad-hoc signature verification. Backend readback retained 3/3 ready rooms and zero uncertain deliveries. The earlier public title task through the [installed browser entrypoint](docs/architecture/alden-installed-browser-0.1.6-20260930.json) matched an independent read in 48.556 seconds using the local 27B model. Native screen capture still times out; human speech, the physical shortcut and installed WKWebView behavior remain unverified. The local ZIP, offline Python sidecar and exact source/checksum manifest are in the ignored `dist/alden-0.1.6-local/` delivery directory; public signing/notarization is blocked by the existing workflow credentials.

The [recorded image-fallback replay](docs/architecture/alden-image-failure-replay-20260930.json) traces historical quality/composition claims to a no-pixel fallback after model failure. The source now states that the image could not be inspected and asks for its relevant text. Sixteen recorded-event helper replays reduced unsupported visual claims from 16 to 0, with no send or model call. The active immutable Kakao worker has not been replaced by this source change. The [actual model-catalog check](docs/architecture/alden-vision-capability-boundary-20260930.json) found that the resident 27B does not advertise vision; the new worker source rejects an image request before generation in that state. This remains the shared server's current boundary; the separate owned vision experiment above is not a production activation.

The earlier link/file boundary retained validated attachment metadata and exact row provenance. The [2026-10-01 file-content implementation](docs/architecture/alden-file-content-20261001.md) subsequently added bounded local parsing and confirmed four synthetic document questions through the actual local 27B model. Metadata alone still cannot substantiate a document answer. The shared production worker has not been replaced, and actual Kakao attachment transport remains unverified.

The [final live E5 evaluation](docs/architecture/alden-retrieval-eval-live-final-20260930.md) tested 13 synthetic queries against the source bytes installed on 2026-09-30: overall Recall@3/nDCG@3 were 0.9091/0.9091, with 24.941/45.865 ms p50/p95 and zero whole-context leaks. The [offline evaluation](docs/architecture/alden-retrieval-eval-final-20260930.md) separately checks fusion and provenance using fixed dense ranks. One older alias without retraction or a validity end remains among seven forbidden candidate judgments. This small corpus and concurrent host load do not establish production relevance or a latency improvement.

The [final focused source checks](docs/architecture/alden-source-verification-final-20260930.json) ran 959 tests with 2 skips and no failures on pinned CPython 3.11.9. They include general questions after a metadata-only file, explicit incoming/same-room file provenance, quoted-source validation and terminal image failures. The fixture-isolated commit `b987ad1` also passed all five remote checks: hosted Python ran 959 tests with 70 skips, the frontend passed 191 tests and the desktop Rust bridge passed 85. See the [exact remote CI receipt](docs/architecture/alden-remote-ci-20260930.json). These checks do not confirm live message delivery, microphone behavior or installed window interaction.

The [current delivery record](docs/ALDEN_DELIVERY.md) tracks the full outcome through installation and release. The voice source assigns each accepted user input a conversation ID, turn ID, context version and cancellation ticket. One worker retains only the newest pending input. Late STT/model/playback results cannot overwrite a newer turn, duplicate events are rejected, and jobs captured before a global abort do not revive after resume. A native voice-processing AVAudioEngine now supplies microphone input and playback inside the existing Python process. Three consecutive processed speech frames cancel the owned playback and preserve the start of the new utterance. Unprocessed input remains suppressed. The [native audio evidence](docs/architecture/alden-native-audio-20260930.md) records 971 focused tests, 191 frontend tests, 85 desktop Rust tests and one installed recorded-playback cancellation: its waiter returned in 12.418 ms. All captured input was zero, so human interruption, echo quality and full voice turns remain unverified. Wake release and memory gates stay enforced. The [voice cancellation diagram](docs/architecture/alden-voice-cancellation.html) reflects this implementation and its remaining limits.

The native-audio code commit `effa5c6` passed all four remote CI jobs, including the unsigned arm64 bundle, native library verification and offline runtime smoke. Hosted Python ran 971 tests with 73 skips; those optional-dependency skips differ from the two local skips. See the [exact native-audio CI receipt](docs/architecture/alden-native-audio-remote-ci-20260930.json).

On this Mac, the existing resident 4-bit 27B adapter answered all **12/12** fixed Korean follow-up cases before and after the socket change. Baseline full-answer p50/p95 were **1.511/2.017 seconds**, versus **0.970/1.269 seconds** in the later nonstream run; cache and competing load were not controlled, so no causal speedup is claimed. The final streamed timing attempt stopped at **2/12** cases when another inference was active. Owned streaming cancellation stopped the client in **3.082–5.383 ms**, but the engine became idle only after **2.279–2.464 seconds** and its cancellation counter did not increase. Immediate backend generation cancellation remains unmet.

The [renderer lifecycle update](docs/architecture/alden-render-lifecycle-20260930.md) prevents stale RAF callbacks from rearming and makes final GPU disposal idempotent. Its 191 frontend tests passed; a real Chromium/WebGL renderer with a synthetic Tauri bridge produced zero additional frames over 750 ms after hiding and retained one listener after 50 hide/show cycles. These are not installed WKWebView or whole-app GPU/battery measurements. The separate Python GraphRAG copier now snapshots stable DB+WAL files and rebuilds SHM only in the private replica. A **1.30 GiB** active plaintext context mirror passed `quick_check` in **5.314 seconds**, with no source SQLite connection. The later [actual encrypted DB and index catch-up](docs/architecture/alden-encrypted-snapshot-20260930.md) verified an **835 MiB** encrypted DB+WAL replica, imported **4 real new events in 0.535 s** through the installed CLI into a private mirror, and independently found the same 4 events in the existing production mirror. A backed-up, coordinated **6.522 s** refresh through the installed GraphRAG module aligned graph/E5 watermarks; it establishes dated catch-up, not continuous freshness or retrieval quality.

The [2026-09-30 requirement coverage table](docs/architecture/alden-goal-coverage-20260930.md) keeps the full goal active and lists the evidence still needed for voice, installed visual behavior, model residency, live replies and public release. A current [installed GraphRAG helper readback](docs/architecture/alden-graphrag-readback-20260930.md) used real local E5 embeddings and returned RRF in **4/4** queries; the persisted index was stale, so freshness and visible drill-down are not inferred.

The [2026-09-30 SQLite replica correction](docs/architecture/alden-sqlite-replica-20260930.md) uses stable DB+WAL signatures and recreates SHM privately. Copy races are bounded to three attempts; only typed exhaustion becomes the watcher's exact retry marker, preserving pending IDs and ACK state. Parent verification passed **16 Rust and 17 unique Python cases**, with the Python suite run on both 3.13 and 3.11. The [activated runtime and app readback](docs/architecture/alden-sqlite-activation-20260930.md) verified **20/20 runtime assets** and **28/28 app files**, with three ready rooms in all four samples. Natural traffic made the strict idle receipt fail, and CI exposed a backtrace-dependent termination diagnostic requiring a follow-up. New inbound jobs were captured, but a live skip-rate improvement is not established.

**2026-09-30 local update:** Alden 0.1.5 was rebuilt from `18a6d4b` and reinstalled; all **28 installed files** matched the built bundle, and LaunchAgent readback showed the new app running ([installation readback](docs/architecture/alden-install-readback-20260930.json)). This ad-hoc-signed local installation does not establish a notarized public release or a direct installed-screen check. A separate, offline teacher-forced DPO scorer now handles the already-converted 27B checkpoint without shifting its RMS norms twice: **99 focused tests passed**, **2/2 synthetic pairs** were scored in **18.522 s**, and greedy arithmetic returned `4`. Its **17,327,024,114-byte MLX peak** covers scoring after model load. Identical policy/reference weights yield exactly `ln(2)` and demonstrate an arithmetic invariant, not improved reply quality. The scorer is an opt-in source CLI, separate from the installed desktop bundle. See the [compatibility receipt](docs/architecture/alden-dpo-checkpoint-compatibility-20260930.json).

The [Python worker cancellation update](docs/architecture/alden-worker-abort-20260930.md) retains one captured abort epoch through generation, owned children and the send boundary. It preserves cancelled jobs and `delivery_unknown`, discards late generation results and releases only the completed request's model-call lease. **32 cancellation/packaging/send-state tests** and a separate **87 existing engine tests** passed. Two pre-existing context-freshness test failures remain documented. The paired Python/Rust runtime was subsequently activated; its [dated readback](docs/architecture/alden-fence-activation-20260930.md) does not establish a measured live skip-rate reduction.

The [native AX effect fence](docs/architecture/alden-native-ax-fence-20260930.md) reads the original job epoch through a strict paired relay and checks it before composing/submitting effects. **72 focused Rust cases**, **20 worker cancellation tests**, **6 send compatibility tests** and **13 packaging tests** passed. A current-session Web Sol child completed the Python relay, and the parent verified its actual Web send and completed-response trace. The replacement runtime retained the CLI's code identity and verified **20/20 packaged assets**. Readback preserved one startup fence followed by three **3/3 ready** samples with zero watchdog restarts; the rebuilt desktop matched **28/28 installed files**. See the [activation receipt](docs/architecture/alden-fence-activation-20260930.md) for limits: a physical shortcut event, installed screen rendering and live cancellation latency remain unverified.

**2026-09-30 live worker correction:** all three supervisors were found stopped for about 48 hours, with the watchdog blocked by one old `sending` job. An exact local self-row confirmation allowed that job to be reconciled to `sent` with **zero retransmissions** and an unchanged remainder of the queue. The existing three-room read-only preflight then passed in **2.641 s**. After further lock/ACK startup failures, the existing watchdog recovered: **four readbacks over 76.227 s** all found the stable host healthy and **3/3 running, reply-ready rooms**. See the [reconciliation evidence and bounded recovery](docs/architecture/alden-queue-reconciliation-20260930.md). A subsequent [fixed-window audit](docs/architecture/alden-current-skip-audit-20260930.md) found **17 stale and 7 superseded jobs**; stale ingress age had median **21.64 h**. The latest 100 local rows per room contained **zero unseen nonself rows** above the watcher checkpoints. With **zero new jobs after activation**, a post-fix skip ratio is undefined and reply-latency improvement remains unproved.

**Alden 0.1.5 was rebuilt and installed locally on 2026-09-27 KST.** The latest installed app matched the built bundle byte for byte, one Alden process was running, and its installed backend snapshot reported **3/3 reply-ready rooms** with Qwen3.8 27B selected. At 11:17 KST, the three live room workers were cut over to the immutable runtime containing the exact iQ/27B routing source: one watchdog, three workers, **3/3 rooms ready**, zero in-flight candidates, and no watermark regression. At approximately 12:28 KST, the bounded degraded-context fix was activated from a second immutable runtime after a three-room read-only preflight and idle/empty-queue check. Readback again showed one watchdog on attempt 1 with zero restarts, **3/3 rooms ready and idle**, no watermark regression, and the stable CLI inode, mtime, and SHA-256 unchanged. The third room recovered from a transient context-sync fence during startup. Around 13:00 KST, the watcher-only timing diagnostic was activated from a third immutable runtime; after its startup, all three room workers again read back ready and idle with numeric-only poll/ingress timing fields, no watermark regression, and the same stable CLI SHA-256. These fields use Δpoll = time between successful poll envelopes and Δingress = first candidate-state write time − KakaoTalk sent_at; neither is a KakaoTalk SQLite insertion timestamp. The scheduled GeekNews slot still selects its worker from the separate `runtime/` directory. See the [dated Flash-Next runtime check](docs/architecture/alden-flash-next-local-eval-20260927.md) and [installation evidence](docs/architecture/alden-install-20260925.md). This does not measure live conversational latency or KakaoTalk delivery.

The widened settings layout passed a fresh synthetic Chromium run on 2026-09-27: **32/32 checks**. At the settings window's 960 px width, its measured columns were 553.719 px and 342.266 px with no horizontal overflow; the knowledge canvas used **872 px** of CSS width. A simulated hidden-window signal produced **zero frames and zero snapshot polls over 1.503 seconds**; blur also produced zero frames over 1.503 seconds. The settings CSS contract separately passed 10/10 checks. These results cover the built frontend with a stubbed Tauri bridge, not the installed WKWebView or whole-machine GPU use. The [render receipt](docs/architecture/alden-desktop-render-check.json) and [settings capture](docs/architecture/alden-render-settings.light.png) use fictional chat and graph data.

The existing mixed 4/8-bit Flash-Next checkpoint could not restart when its memory preflight required about **77.1 GB** and found only **51.8–54.4 GB** available. A smaller Flash-Next 3.3bpw checkpoint was downloaded at a fixed revision and answered a synthetic request on an isolated localhost server while 27B remained loaded; that temporary server was then stopped. The installed Alden app and the active room workers route the exact iQ fast choice through `11235`, with a **60 GiB** app admission gate and exact loaded/ready check before saving. A later app-owned launch failed MLX Core's own memory preflight: **57.6 GB needed, 53.97 GB available**. No iQ listener or live iQ reply remains. On 2026-09-30, the real 27B LLM adapter returned `4` for one warmed arithmetic prompt in **1.485 s**; its prior memory estimate was **58.82 GB**, below the unchanged **64.42 GB** iQ admission requirement. See the [current adapter readback](docs/architecture/alden-local-model-readback-20260930.md) and [dated Flash-Next local check](docs/architecture/alden-flash-next-local-eval-20260927.md). These different procedures do not establish a comparative speedup, general reply-latency gain, voice operation, or a public release. The three bundled menubar bytecode files are pinned in this repository and hash-checked during packaging.

The saved reply model was a legacy mixed Flash pack that was not loaded. After backing up that setting, the menu backend accepted the exact ready 27B ID on `11234`; the active worker runtime read back the new 27B choice. A synthetic 27B request then timed out twice (**30 s** and **15 s**) while one server request remained stuck in prefill. The managed server was restarted with its existing profile, and a fresh localhost request returned HTTP **200** with `OK` in **1.343 s**; prefill returned to zero. These are synthetic observations, not a before/after KakaoTalk reply-latency or skip-rate measurement.

The `Alden Desktop Release` workflow now gates macOS arm64 packages on Developer ID signing, Apple notarization, and published-asset checksums. As of 2026-09-27, it has not produced a release: this repository has no configured signing/notarization secrets, and the local keychain has only a development identity. The app ZIP needs a separate CPython 3.11 runtime on a fresh Mac; the workflow packages a hash-pinned, offline-installable menubar runtime sidecar. On PR commit `c439129`, **4/4 CI jobs passed** with a separate successful GitGuardian check, including an unsigned macOS arm64 bundle build and an offline sidecar install into an isolated temporary home. That sidecar does not provision MLX models or voice dependencies and does not inherit the app's Apple notarization. See the [desktop runtime contract](desktop/README.md).

A 2026-09-27 read-only queue audit separated **9 proactive GeekNews sent** jobs from **2 conversation sent** and **21 conversation skipped** jobs. All 32 predated the 03:00:58 worker cutover. An 08:51 KST readback then found only scheduled GeekNews jobs after that cutover. At 11:44 KST, three new conversation jobs entered the queue after the 11:17 worker-runtime cutover: **2/3 were burst-superseded and the final 1/3 was skipped as stale backlog; 0/3 received a reply**. Their KakaoTalk `sent_at` values preceded queue insertion by **160.435–162.955 s**. The final job's context lookup fell back to degraded `recent_only` after a timeout, then the stale gate ended it before generation. The transition journal first recorded these ingress candidates at 11:44:29–39 KST. A separate read-only `local-poll` probe returned its first envelope in **0.169 s** and four more at roughly one-second intervals; that probe does not explain when those three rows first became visible to the watcher. This is a measured skip regression, with no live reply-latency or skip-rate improvement to report. The queue state alone does not independently establish KakaoTalk delivery. Two fake-adapter watermark regression cases and the turn-hold suite previously passed **40/40** on commit `d41546d`; these checks predate the new regression. See the [queue timing audit](docs/architecture/alden-install-20260925.md).

The source fix for this 11:44 failure bounds the `recent_only` timeout response window to verified queue ingress lag (at most 180 s) + 15 s burst settle + 2 s context timeout + 8 s residual. The observed 160 s case therefore receives a 185 s analysis window; missing or older ingress proof keeps the former 8 s fail-closed window. Seven focused fake-adapter regressions passed locally, including later-self and advancing-watermark guards. The source fix is active in the three local room workers; a real subsequent KakaoTalk conversation is still needed to measure whether it reduces skips.

A separate local 27B API request used a fictional recent-message fact (a meeting at 15:00 in building B, floor 2) followed by `???`. The model returned a clarification that correctly carried the meeting time and place in **3.712 s** (HTTP 200, 142 prompt and 25 completion tokens). This demonstrates one synthetic context reference through the selected local model, not the queue, recipient, or live KakaoTalk reply path.

The reply source now rejects generic confusion drafts such as `무슨 말인지 모르겠네요` in both strict and lenient selection. It uses the nearest grounded topic for `???` or `그래서?`; when no topic is available, it asks one direct clarification. The scheduled pre-send gate validates that exact fallback. A read-only replay of the earlier generic-reply event through the previously installed runtime selected a grounded clarification from six normalized recent rows; **9/9** focused Python checks pass for the new source. This is source and replay evidence, not a measured live reply-quality change.

On 2026-09-27 KST, the reply host was drained and activated from immutable runtime `20260927T105026Z-confusion` after commit `0003e92`. The candidate worker and committed source had the same SHA-256 (`a34376f0…6c042c2deb252766fa8d2`); the installed LaunchAgent pointed to that candidate. The old watchdog and all three room supervisors stopped cleanly before activation. The new watchdog reported one running attempt with zero restarts; **3/3** rooms reported ready with idle workers, **zero** nonterminal or uncertain queue jobs, **zero** pending DB gaps, and nonregressed acknowledged watermarks. This verifies deployment and operational readiness; no new natural conversation reply was observed, so the generic-reply rate remains unmeasured.

A fixed 24-hour read-only audit (2026-09-26 14:00 to 2026-09-27 14:00 KST) found **36 queue jobs: 11 `sent` and 25 `skipped`**. Fourteen skips were stale, nine were superseded fragments of a message burst, and two were already-commented duplicates. The nine supersession edges collapse the 36 jobs to **27 terminal queue chains**; neither count is a count of distinct human conversations. The first evidence for **11/14 stale jobs** recorded `model_temporarily_unavailable` (ten Flash-Next, one 27B); these reached terminal stale state after a median **934.7 s** from queue creation. The 11 `sent` jobs comprised nine GeekNews jobs and two model-unavailable reactions, not verified normal conversation replies. No job was created after the 13:00 diagnostic cutover within this window, so a post-fix skip-rate improvement remains unmeasured. See the [dated skip audit](docs/architecture/alden-skip-audit-20260927.md) for the denominator and method.

Voice startup is deliberately blocked until a wake model passes its release gate. The original six-clip evaluation had carried openWakeWord temporal state from one independent WAV to the next. After resetting and silence-warming the detector before each WAV, the **same six WAV hashes** gave **2/3** synthetic positive accepts and **0/3** negative false accepts at the unchanged 0.65 threshold; the old contaminated results were 3/3 and 1/3. Positive recall now fails the synthetic gate, and there are no human-speaker or microphone/room trials. The candidate remains excluded from the app bundle. These measurements do not establish a working microphone or a complete voice conversation.

A separate experimental v5 wake head was compared with v4 on 12 held-out synthetic WAVs, with voice, negative-phrase, and clip-hash splits disjoint from training. Re-scoring the **exact prior 12 WAV hashes** after the reset gave v5 **2/2** positive accepts and **1/10** false accepts, versus v4 **1/2** and **1/10**. A separately regenerated 12-WAV corpus gave v5 **1/2** and **0/10**, versus v4 **0/2** and **1/10**; all 12 WAV hashes differed from the prior corpus, so these two corpus runs are not a paired before/after comparison. Neither candidate passes the release gate, and neither has human-speaker or microphone/room evidence. See the [paired reset audit](docs/architecture/alden-wake-reset-paired-audit-20260927.md) and [new-corpus evaluation](docs/architecture/alden-wake-v5-heldout-eval.json).

### Historical runtime observations (2026-09-24–25)

At 23:43 KST on 2026-09-24, read-only checks returned HTTP 200 from local MLX `/health` and `/v1/models`. Flash-Next was loaded (75.3 GB resident); Qwen3.8 27B and Qwen3-TTS were unloaded. One bounded local Flash-Next generation returned `OK` in **56.849 s**. This verifies a single short local generation, not normal conversational latency; the earlier 45.060-second zero-token disconnect remains a separate failed attempt. The loaded model has no embedding capability, so live GraphRAG remains BM25-only and dense/RRF is unavailable.

The post-probe session-monitor readback was healthy at that time: all three configured room workers were ready, their reply model was available, delivery was enabled, and there were no pending database gaps. Free swap was **1,322.19 MiB**, **725.81 MiB** below the 2,048 MiB voice-model admission threshold. The observed voice heartbeat was over 25 hours old, so this check did not verify Whisper/TTS loading or a complete wake→STT→LLM→TTS turn. The installed immutable worker matched the source snapshot at the time by SHA-256; that comparison does not cover subsequent edits. The diagnostic request was local-only and used no conversation data; no message was sent and no model was loaded or swapped. See [engineering status](docs/engineering-status.md) for queue counts, historical confusion-reply replay, and measurement limits.

At 23:36 KST on 2026-09-24, Tauri app v0.1.5 was installed and its LaunchAgent process was running. All 27 installed bundle files matched that build. The configured global emergency shortcut is **⌘⌥⇧Esc**; the adjacent **⌘⌥Esc** chord belongs to macOS Force Quit. This bundle check did not exercise a physical global shortcut event.

On 2026-09-28 KST, the emergency-state source gained an explicit **다시 시작** control, shown only while a valid stop is latched. Resume requires a click and an incremented epoch readback; it never revives old cancelled tokens. Python and Rust now share a private, bounded `flock` protocol, reject corrupt or unsafe state without replacing it, and read FIFOs without blocking. **85 Rust, 185 UI and 98 focused Python checks passed**, including a real Python/Rust lock interoperability check over temporary files. Desktop Clippy with `-D warnings` and the frontend production build passed. See the [emergency-state protocol](docs/architecture/alden-abort-state-20260927.md) and [resume integration evidence](docs/architecture/alden-emergency-resume-20260928.md). These checks cover app-owned jobs and the state protocol; Kakao worker stop integration and a physical shortcut event remain unverified.

At 00:28 KST on 2026-09-25, a later read-only host check returned `healthy=false`: two of three room workers were ready and one was fenced during a transient context-sync failure. The primary reply room remained ready, with delivery enabled and no pending gaps. That observation predates the current source edits and does not verify their deployment.

The detailed implementation notes and dated verification records are kept in [engineering status](docs/engineering-status.md). They describe source checks, automated tests, and live runtime observations separately.

<h2 id="features">Features</h2>

- **Private processing**: Conversation search and AI replies run on this Mac.
- **Safe conversation reading**: The app reads a temporary, read-only copy of KakaoTalk's local data.
- **Knowledge graph**: Select a canonical note to read its content, saved links and original evidence.
- **KakaoTalk integration**: Replies are entered in the KakaoTalk app.
- **Protected sending**: The app pauses when it cannot confirm which reply or destination is safe.

<h2 id="model-support">Model Support</h2>

The menu app offers a fast local model for everyday replies and a larger local model when requested. It does not automatically send conversation data to a cloud AI service. Exact model names and setup details are in the [Korean setup guide](README.ko.md); current source gates and dated runtime evidence are in [Source readiness](#source-readiness).

<h2 id="quick-start">Quick Start</h2>

### 1. Build from Source

```bash
git clone https://github.com/twoimo/alden.git
cd alden
cargo build --release
```

### 2. Permissions

In macOS **System Settings -> Privacy & Security**:
- **Full Disk Access**: Grant to your Terminal (or `Alden.app`) to read local database files.
- **Accessibility**: Grant to allow typing replies into KakaoTalk.

<h3 id="configuration">3. Configuration</h3>

```bash
mkdir -p ~/.config/openkakao
cp config.example.toml ~/.config/openkakao/config.toml
```

Minimal `~/.config/openkakao/config.toml`:

```toml
[model]
privacy_mode = "local"
allow_egress = false
provider = "mlx-serve"

[auto_reply]
# Allowed chatrooms: ["bind:<chatId>:<exactOnScreenName>"]
chats = ["bind:123456789012345:TeamChannel"]

# Your display name in KakaoTalk
self_nickname = "Your Name"

# Recommended on-device engine (MLX), not Gemma / llama.cpp / Ollama
reply_runner = "/absolute/path/to/installed/opencodex"
reply_runner_kind = "opencodex"
reply_model = "ddalcu/Qwen3.8-27B-MLX-Serve-4bit"
```

`reply_runner` is a validated transport placeholder for this local MLX profile and must point to the installed `opencodex` executable. The exact 27B and Flash-Next IDs, with or without the `mlx/` prefix, are allowlisted. The current 27B deployment passed one synthetic localhost completion; Flash-Next can be selected explicitly when it is resident and ready. This does not establish normal conversational latency.

### 4. Run

```bash
# Verify environment and discover chat rooms
./target/release/openkakao-cli doctor
./target/release/openkakao-cli local-chats

# Build and install the primary Tauri menu-bar UI
sh scripts/build-alden-desktop.sh
sh scripts/install-alden-desktop.sh

# Start the installed LaunchAgent without rebuilding or reinstalling
sh scripts/start-auto-reply-menubar.command
```

Privacy paths, KakaoTalk table names, and Korean operator notes live in [README.ko.md](README.ko.md). Do not commit chat databases, `context.sqlite3`, `knowledge-graph.sqlite3`, or credentials.

### Versioned collection connection — 2026-10-07 candidate

Alden now stores source versions and per-target project membership separately, with private recovery backups, transactional checkpoints and ordered stage history. The memory-history view has project/target/source/time filters, bounded rows and forward recovery after hiding. A different target's revision cannot replace a project's permitted source version.

The actual application data directory contains26,622 source documents and48,355 explicit relations from four declared graph snapshots. Local0.3.28 is installed through the normal backed-up LaunchAgent cutover; all47 bundle files match the candidate, and existing model/configuration state was preserved. Owned views from the installed bundle rendered real stage history at two sizes. Primary interactive checks remain unverified while CUA observation times out; production is unreleased. See the [collection execution boundary](docs/architecture/alden-collection-20261007.md) and [aggregate native readback](docs/architecture/alden-collection-application-readback-20261007.json).

The developer collector can also retain exact permitted source bytes and replayable evidence pointers with `--capture-source <target_id>`. Three original JSON files and one body-free Spark metadata export were retained and26,622 pointers replayed without changing documents or indexes. That newer collector code is a separate source commit from the currently installed2d167b32 bundle. Live acquisition, scheduler execution, Dense search and the new graph viewer connection remain in progress.

---

## License

[MIT License](LICENSE)

The latest objective also names unsupported color judgments. A [read-only historical color-claim replay](docs/architecture/alden-color-claim-replay-20260930.json) found seven reply records across five events, including two persisted sent receipts. The current pure missing-image helper produced zero color/composition/quality judgments. It made no model or send calls and does not establish active-worker correction or pixel understanding.

The corpus lives separately at `<state-root>/knowledge/corpus/<opaque-account>/`. `working.sqlite3` is resumable; `context.sqlite3` is the last complete published version. The original shared `context.sqlite3`, enrollment, automation queues and source Kakao DB are preserved. Cached encrypted snapshots are reused until their bounded import completes, then refreshed on a five-minute cadence. Edits and removals are reconciled before publishing the new version. The app records incomplete corpus counts as pending. Do not confuse quoted outgoing history with confirmed human speech or delivered assistant output.

The background app currently waits at macOS app-data access confirmation before it can open KakaoTalk's account plist. Direct authorized terminal collection completed, but that does not establish background-app authorization. Metadata discovery is now bounded to three seconds and the DB history reports the access-wait state. Approve the macOS access prompt only if you intend to grant Alden that access; its scope may include other applications' data. The existing published corpus remains readable. Human approval is pending; no TCC grants, resets or database edits are performed by the installer.

The existing pinned E5 adapter was restored after launchd reported it registered but not running. It loaded the exact local 384-dimensional encoder; a backed-up live graph refresh persisted 118 vectors and four public query strings used RRF. Query timing and source freshness are separate from the pending live Kakao collection permission. Voice knowledge requests quote local corpus documents and graph facts, keep the latest actual speech last, preserve room identity, and decline ambiguous equal-name room selection. Changing topics ends the prior retrieval chain. See the [voice retrieval verification](docs/architecture/alden-voice-retrieval-20261002.md) and [installed voice file baseline](docs/architecture/alden-installed-voice-file-20261002.md).
