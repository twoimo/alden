# Alden 0.3.52: 실제 호스트 취득과 입력 갱신

기존 macOS 일정은 선언된 스냅샷을 재처리한다. 외부 사이트에서 다시 취득한 결과가 없으면 원문 재취득으로 표시하지 않는다. 이번 경로는 기존 두 영상 표본의 Aside native API 메타데이터·트랙·한국어 타임스탬프 자막을 실제로 새로 취득하고, 기존 대상의 저장 입력에 안전하게 전달한다. 채널 전체 수집, 자동 live-site 갱신이나 전역 MCP 설정 변경을 뜻하지 않는다.

`alden_collect.py --receive-input <target_id> --input-snapshot <file> --expected-current-sha256 <current_sha> --state-root <private-root>`는 기존 `youtube-video` 또는 `threads-post` 표본만 받는다. 대상 종류·ID·canonical URL·권한·활성 상태와 관측 시각을 검증한다. 보관 위치는 해당 state-root의 private acquisition-inputs/current.json으로 제한한다. 심볼릭 링크·FIFO·바이너리 예산 초과·오래된 자료·변경된 현재 해시·취소를 거부한다.

협조적 수집/수신 클라이언트의 target lock을 공유한다. 이전 입력은 SHA256 이름의 불변 JSON으로 0600 보관하고, 새 바이트를 임시 파일에서 fsync한 뒤 비상 중단 commit guard 안에서 다시 현재 해시를 확인해 교체한다. 원본 외부 사이트·프로젝트 그래프·모델 설정·전송 큐·대상 일정/권한/체크포인트를 변경하지 않는다. 동일 바이트의 완료 요청 재전송은 `unchanged`이며 다시 교체하지 않는다.

메타데이터 관측과 자막 관측은 별도 시각으로 보존한다. 이전에 확인된 자막이 있을 때 접근 실패나 오래된 자막 결과는 기존 입력을 덮어쓰지 않는다. 언어만 지정하는 호스트 API에서 수동/자동 트랙이 모호하면 미확인으로 남긴다. 채널 표시명과 선언된 상위 대상이 안정적인 작성자 ID의 증명은 아니다.

수신 결과 `received`는 입력 파일의 전달만 증명하고 `collected=false`를 명시한다. 기존 일정/수동 요청이 이어서 자료·원문 capture·검색 색인을 처리한 후 별도 영수증으로 확인한다. 현재 슬롯·음성 우선·pause·비상 중단·권한·재시도 간격은 그대로다. 새 일정이나 전송 권한은 생성하지 않는다.

검증은 SHA/CAS 충돌, 정확한 이전 바이트 복구, 반복 수신, ID/시간/권한/종류, 자막 접근 실패, 취소, symlink/FIFO와 영상·게시물 표본 격리 및 기존 수집/검색/스케줄을 포함한다. 실제 취득한 두 영상은 쯔양 F93TnnxCNvY, 장사의 신 k22cVbRHxcs다. 취득은 Aside 호스트, 수신/수집은 설치 제품, read-only MCP 조회는 해당 로컬 executor의 증거로 따로 기록한다. 파일 전달을 전역 connector 발견/권한이나 독립 검색 gold로 확대하지 않는다.

Aside CLI 1.26.1008.1938의 설치 guide/repl과 native YouTube 스킬, [공식 개발자 안내](https://docs.aside.com/help/developers)를 2026-10-09 확인했다. 설치 핀·프로필·모델을 유지했으며 추가 agent/모델/유료 용량을 쓰지 않는다. 별도 작업 공간 `outputs/source-refresh-20261009`에 native 반환 바이트·정규화 입력·전달/저장/검색/host 반환 영수증을 보관한다. API의 compact export와 원 서버 전체 HTTP 본문·영상 픽셀·전체 계정 coverage는 구분한다.
