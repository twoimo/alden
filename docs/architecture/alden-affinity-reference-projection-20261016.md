# Alden 0.3.121 — 의미힘 조회의 내부 참조 투영

Affinity 조회가 바로 버릴 표시 데이터까지 만들던 경로를 줄인다. 일반 그래프 조회는 전체 표시 데이터를 그대로 반환하고, 내부 `graph_references`만 선택된 ID·원문 버전·수집 대상과 건수·다음 페이지·revision을 반환한다. JSON 질의 플래그로 이 경로를 활성화할 수 없다.

SQL 선택과 관계 이력 검증, JSON1 구성, 필터·focus 탐색·허용 범위, 메타데이터/본문 오류 검증과 target 조회를 유지한다. Python의 edge dictionary와 표시용 context 축적을 줄인다. 본문·벡터 admission, cosine·후보 배분·출처 근거와 원래 CPU/RSS/시간/취소 한도를 바꾸지 않는다.

집중 회귀 118개와 16종 선택 범위 대사가 통과했다. 정상/손상 relation history, JSON 직렬화 불가 관계, 선택·미선택 metadata, 잘못된 body와 권한 거부를 포함한다. scheduler target config는 기존 전체 조회도 파싱하지 않으므로 새 검증을 발명하지 않았다.

동일 실제 collection/cache의 fresh child process 5쌍에서 공개 결과가 일치했다. 비교는 시간·RSS와 SQLite 동적 stat 필드만 제외하며 source/text/vector byte charges와 정책·거부 flags를 포함한다. 최대 RSS 중앙값은 118,128,640→111,722,496바이트(6,406,144바이트, 약6.1MiB 감소)이고 모든 쌍에서 감소했다. 한 confirmation pair의 시간 퇴행도 보존한다. 이 대조는 두 선택 모듈을 대칭적으로 지연 로드한 비공개 probe이며 설치 앱 전체 RAM이나 p95·FPS·배터리 개선 증거가 아니다.

career·jangsin·youtube-tzuyang·sparkuniverse 각1쌍의 실제 query도 결과·바이트·권한을 그대로 보존했다. sparkuniverse의 `layout_no_eligible_content`도 같은 의미로 유지한다. 이 4쌍은 동작 범위 확인이며 성능 표본으로 합치지 않는다.

별도 정상 제품 dispatcher의 소스·후보·설치 API와 빌드/설치/native/보존/복구 검증은 최종 영수증으로 결속한다. source API와 candidate API의 public SHA256은 `66c8397a4c6428f54c3da36346de04cf9f84b0b6102b79636da524c05ba2db51`다. 채택하지 않은 cache8MiB 후보는 정상 경로에서도 RSS 예산을 초과해 원래2MiB로 되돌렸다. 비대칭 선행 import 실패와 시스템 SQLite 오류도 보존한다.

요청한 `chatgpt-web/gpt-6-astra`/max의 최종 제공 패치·검사 검토에서 구체적 회귀 결함이 발견되지 않았다. 독립 파일 검사나 human semantic gold가 아니다. 원문·벡터·관계·모델 설정과 전송 큐를 보존하며 새 모델 추론은 없다.

근거는 작업 루트의 `outputs/affinity-startup-20261015/`다. 전체18 목표, 물리 primary/tray/voice/Retina/lock, 독립 의미/동일인/시간 평가, 유용한 선택 라벨, 다중 host/MCP, RAM 허용 로컬·멀티모달, DPO, 유효 worker proof, 최신 도식과 Developer ID/공증/운영 배포는 계속 미완료다. 이전 자동 승인 검토의 ‘보안 상태 판단 불가’로 보류된 label/abort-reader 소스 읽기를 우회하지 않는다.

## 로컬 전달 검증

런타임 소스는 `0d909a6bb2ddf39430b3aabce4a413dbab4e0192`이며 설치는 `/Applications/Alden.app`의 0.3.121이다. exact-source CI38757126768 네 작업과 canonical build39.706초가 통과했다. 소스·후보·설치의 정상 dispatcher API는 동일한 공개 payload/charge SHA를 반환했다(호출 전체4.221/3.091/5.918초, n각1; 통제된 속도 개선은 아님).

후보/설치 native 감사는 내부 시계 기준75.699/96.454초에 통과했다. 기본·작은 창 총68PNG를 생성했고 관련12장을 직접 확인했다. 정착 시496 의미힘 후보와 worker ready/idle 정지를 확인했다. 설치 기본 화면의 ‘자료 확인 필요’ 표시도 보존하며 전체 원문 품질 완료로 표현하지 않는다. 이전120의 최초 aggregate timeout 기록도 여전히 성능 신뢰도 조건이다.

설치76파일/모드·strict deep ad-hoc 서명·ZIP 추출·독립120복구와 native 종료 뒤 설정3/대상7/정책/모델 리스너·선택 collection/retrieval/voice-history 표·보호 원본2개/snapshot HEAD 보존이 일치했다. 전체 Kakao 원본 DB 불변 주장과 구분한다. ZIP은12,994,432바이트, SHA256 `d76388bedfa62e73e830363c496089485362f5c8034112b69e3bf3d039038804`다. 새 문서만 별도 커밋하며 런타임을 재빌드하지 않는다.
