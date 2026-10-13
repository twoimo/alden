# Alden 0.3.95 — 링크의 실제 응답과 후속 질문

대화 입력의 현재 HTTP(S) 링크 최대2개를 제한된 public-address 연결로 읽는다. 같은 대화의 최근64개 완료된 링크 요청에서 고유한 이전2개 snapshot을 함께 선택한다. 새 메시지에서 같은 URL을 다시 지정하면 새 취득을 수행하며, 완료된 요청 ID 재실행과 후속 질문은 저장된 snapshot을 사용한다. 취소·실패·철회·다른 대화의 자료를 완료된 이전 근거로 선택하지 않는다.

입력 수신, public URL/DNS/TLS 연결, 실제 HTTP 응답, 파싱과 모델 입력/답변 저장을 분리한다. 현재·최종 URL/status의 제한된 redirect trace, 원문 byte hash, 파싱/발췌 hash·범위·관측 시각과 요청/턴 귀속을 보존한다. 접근 실패와 metadata-only를 본문 확인으로 표시하지 않는다. HTTP response text는 그 응답의 텍스트이며 원래 게시물의 전체 본문이 확보됐다는 뜻이 아니다. 쿠키·브라우저 로그인·JS·이미지/영상·자막·PDF 다운로드를 묵시적으로 실행하지 않는다.

공통 `alden_link_fetch.py`는 기존 worker와 Desktop의 transport를 공유한다. 허용80/443·public DNS 후보 전체 검증·숫자 sockaddr pin·원래 hostname의 TLS 검증·엄격한2xx·identity encoding·1,000,000B/페이지·절대 deadline·소유 연결 닫기를 적용한다. worker의 기존 Kakao/Daum redirect 정책·별도 opt-in·privacy/전송 gate는 유지한다. Desktop은 최대2회 redirect의 HTTPS/public 주소를 다시 검증한다. DNS timeout thread가 늦게 끝나도 그 응답을 사용하지 않으며 Desktop의 소유 child도 부모가 회수한다.

Desktop child는8초 전체 취득 deadline, 부모10초 watchdog과 소유 프로세스 그룹 회수를 따른다. URL은 private600 manifest에 넣고 argv/stderr에 노출하지 않는다. 원문·파싱 receipt는700 namespace/600 content-addressed 파일에 atomic publish한다. regular file·단일 link·소유자·stat/바이트/SHA를 재검증하며, 이전 자료 접근 실패는 제외 건수로 기록한다.

문서와 링크의 합친 전달 맥락은16,000B로 제한하고 큰 텍스트를 줄일 때 원본/파싱 정체성을 보존하고 발췌 SHA와 잘림을 갱신한다. 모델 입력의 full context digest는 요청 ID에 결속한다. 링크 snapshot metadata를 별도 durable ledger에 기록하고 추론 전과 답변 commit에서 원래 request/turn/state와 대사한다. 이미 전송한 외부 모델의 입력을 사후 철회로 회수할 수 있다는 보장은 없다. 기존 사진/문서4개/20MiB와 저장·취소·모델/RAM 정책은 유지한다.

소스와 fake regression, 실제 public 취득, 후보/설치 모델3턴, 직접 화면 검수·설정/데이터/서명/ZIP·CI는 `outputs/assistant-links-20261013/`에 별도로 기록한다. 실제 transport에서 public632221d의6,912원문 바이트가 pinned Git source와 일치했다. 현재 link dialogue16·transport13·기존 link parser/worker 포함39·관련119·UI835·native141/타입·Clippy를 확인했고, 문서4개/링크2개의 현실적인 generation metadata도4,096B 안이다. standalone 취득은 설치나 모델 답변의 증거가 아니다.

자동 답장 read-only preflight는 기존 `room_reply_authors`의 unselected chat ID 설정에서 거절됐다. 해당 설정·allowlist·큐·운영 worker를 변경하지 않았다. 이번 worker transport 회귀는 fake adapters에서 수행한다. 기존 감독자 증빙/활성화 gate와 물리/host/원문 의미/EmbeddingGemma2/DPO/대표 자원/정식배포 미결은 유지한다. 앞 단계에서 자동 승인 검토가 거절한 동일ID scope-only 문서 반례도 재실행하지 않았다.

첫 후보f9777b1의 실제 native 링크→후속→CSV 비교는8→8→8,67로16.024초에 완료됐다. 기본/작은 창 세PNG를 직접 확인했다. 첫 CI38349850316은다른3작업성공·focused1970검사중runtime copy list2실패/1오류였다. 같은3반례를로컬에서재현하고helper를copy list에포함해전체packager13검사를통과했다. 누락을무시하거나운영worker를활성화하지않았다.

설치 .94 첫 요청은3.229초 만에취소됐고native검사는180.339초에기한초과로끝났다. 전역abort hash는불변이며이실패를보존했다. 목록갱신에서미완료첫요청의새세션을자동선택해작성기가대화변경으로취소하는경로를실제reader/composer 회귀로재현했다. .95는pending/uncertain/파일읽기 소유권이있을때자동세션선택만유보하고완료영수증이그세션을채택한다. 명시적사용자선택·숨김·닫힘·취소는계속유효하다. 사용자선택의취소/늦은응답폐기 회귀도확인했다. native검증에는비밀없는퇴출원인 enum을기록하여의도적취소가모델실패/무한대기로분류되지않게했다. 이전실행의직접퇴출event는없으므로동일경로였다고확정하지않으며 수정후설치 검증은별도영수증으로확인한다.
