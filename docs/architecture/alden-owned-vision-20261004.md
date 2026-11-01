# Alden0.3.19: 요청별 로컬 이미지 처리

공유 서버의 `--no-vision` 때문에 막혔던 사진 답변을 실제 worker 생성 경로에서 처리한다. 기존 서버를 재시작·채택하지 않고, 이미 설치된 MLX-Serve26.10.1과 요청한27B 체크포인트를 잠시 실행한다. [측정 기록](alden-owned-vision-20261004.verification.json)과 [프로세스 흐름](alden-owned-vision-20261004.html)을 함께 제공한다. 다이어그램의 고정 Viewer UI는 영어이며 작성 내용은 한국어다.

## 실행과 자원 경계

27B 코어14개 파일을 리비전 `901aa73a1ff5456752c02e55582609e0040ca470`의 공개 manifest와 대조한다. 최초·변경 시 실제 해시를 읽고, 이후 inode·크기·mtime·ctime·모드가 동일할 때만 검증 캐시를 재사용한다. 답변 후 파일 신원도 재확인한다. 기존 공유 프로세스가 과거에 로드한 가중치의 리비전과는 구분한다.

입력 전체를 보존하며 기존 prompt fitting을 이미지에 맞는 예산 안에서 수행한다. UTF-8 바이트를 텍스트 토큰의 보수적인 상한으로 쓰고, 고정된16px patch·2배 merge와 최소·최대 pixel 설정으로 이미지 토큰을 추산한다. 출력1024·template512를 포함해32768토큰을 초과하면 추론 전에 거부한다. 이미지 일부를 몰래 누락하지 않는다.

시작 전과 해시 확인 후 사용 가능한 메모리40GiB를 확인한다. 상주 메모리24GB·동시 요청1·KV4bit·prefill512·OS reserve8GiB·wired margin8GiB를 적용한다. speculative/MTP/drafter와 디스크 prefix cache를 사용하지 않는다. 임시 서버는 요청 종료 시 제거되며 항상 상주하는 서비스가 아니다.

인증 키는 메모리에만 두며 요청마다 새로 만든다. HTTP 연결의 server-side socket이 자식PID에 속하는 것을 확인한 뒤 키와 사진을 전달한다. proxy·redirect 경로를 사용하지 않으며 child sandbox는 외부 network-outbound를 차단한다. 실제 임의 외부 주소 연결 probe도 PermissionError로 거부됐다. [공식 고정 버전 API](https://github.com/ddalcu/mlx-serve/blob/v26.10.1/docs/api.md)의 base64 image_url 계약을 따른다.

기존 MLX 요청 잠금을 유지하고, 별도 상태 루트 사이에도 공통 vision 잠금으로 추가 로딩을 직렬화한다. 취소·마감·호출자 pipe EOF에는 감독 프로세스가 자기 자식을 종료·회수한다. 감독 프로세스가 비정상 종료하면 잔여 그룹의 정확한 command와PGID를 확인한다. 소유권이나 정리가 불확실하면 다른PID를 종료하지 않고 다음 로딩을 차단한다. 복구할 때는 `~/Library/Caches/Alden/local-vision/mlx-vision-cleanup-required`를 삭제하기 전에 그 요청의 프로세스 정리가 확인되어야 한다.

## 실제 측정

| 검사 | 결과와 범위 |
| --- | --- |
| 공개 회귀 이미지 | 색·개수·도형·문자4/4 일치, n=4 |
| 샘플링 | 기존 온도1 반복에서 OCR 마지막8 누락1건; 온도0로 전환 후 같은4종4/4 |
| 전체 요청 시간 | 중앙값8.84초, 6.21–10.65초; 매 요청 새 서버 시작·종료, 이미 확인된 파일 캐시, 인증 거부 probe 포함 |
| 취소 | 결과 반환0건, caller70.90ms·transport 종료1.99초, n=1; 실제 키보드 단축키 시험은 아님 |
| 인증 | 누락·잘못된 키8/8 HTTP401 |
| 보존 | 기존 설정6개·프로세스 시작 시각5개 동일, 원래 없던 residency 파일도 유지 |
| 코드 검사 | Python 집중107개·Rust resource layout7개 통과 |

상이한 공개 fixture4개이므로 이 시간은 기술 통계다. 일반적인 p95·전체 사진 정확도·속도 개선율·배터리 절감으로 해석하지 않는다. 초기 이미지 요청의 전체 시간 목표는30초이며, 첫 해시 검증 비용과 실제 운영 맥락은 별도로 관측해야 한다. 기존 경로는 실패했으므로 성공 요청과 비교할 지연 기준값이 없다.

다이어그램은9개 artifact 검사에서 오류·경고0, 네 desktop viewport의 자동 브라우저 검사 통과. 기본 dark와 큰 light 캡처를 직접 검토했으며 라벨 잘림·선 겹침을 발견하지 않았다. UI 기본·최소 노트 pane은 이전0.3.18 구현을 유지한다.

## 전달과 남은 경계

worker helper·manifest·검증기와 session packager·Tauri resource 목록을 함께 변경했다. 현재 실행 중인 기존 foreground worker는 재시작하지 않았으므로 다음 정상 소유 세션의 준비·시작 때 신규 경로가 사용된다. 테스트는 공개 fixture와 격리된 상태 루트로 실제 생성 함수를 호출했으며 카카오톡 메시지를 보내지 않았다.

Developer ID 인증서0개와 GitHub Secrets0개를 확인했다. 로컬 ad-hoc 서명 설치와 정식 서명·공증 릴리즈는 별개다. 물리 음성·실제 글로벌 단축키·Retina·Dot client 호출·전체 앱 전력 및 메모리 검증도 이 변경으로 완료됐다고 주장하지 않는다.
