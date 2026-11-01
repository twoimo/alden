# Threads 원문 갱신과 이전 입력 호환

**Alden 0.3.154 설치·실제 한 게시물 갱신 완료.** runtime source는 `659d3b5cfea08fe26e856d55cdd1f5dac7fd67a8`이다. 초기 Threads 표본에는 사이트 관측 시각이 없었다. 기존 수신기는 이전 파일에도 관측 시각을 요구해, 실제로 새 원문을 받아도 `collection_input_observation_required`로 거절했다. 설치153의 등록된 EmbeddingGemma2 표본에서 이를 재현했고, 거절 당시 입력 파일은 변경되지 않았음을 확인했다.

이번 수정은 **이전 입력에서만 시각 필드가 아예 없는 경우**를 다룬다. 새 입력의 관측 시각은 계속 필수다. 명시된 null/잘못된 시각, 과거 입력, 다른 원문, 권한 없는 대상에는 대체 경로를 쓰지 않는다.

이전 시각이 없으면 수집기의 기존 `source_observation` 읽기 시각을 보수적인 갱신 하한으로 사용한다. 이것을 과거 사이트 관측 시각으로 꾸미지 않는다. 다음 근거가 모두 맞아야 한다.

- 완료된 target cursor의 source revision, 읽기 영수증과 보관 capture가 현재 입력 SHA256에 결속한다.
- capture manifest와 실제 보관 파일의 해시·바이트, adapter·원본 경로·정확한 JSON 파일 범위가 맞는다.
- 현재 사용 가능한 해당 target membership, 문서 버전, record hash, JSON evidence pointer가 같은 원문에 결속한다.
- 하한은 유한한 실제 수집기 시각이고, 새 관측은 그 하한보다 뒤에 있다.

하나라도 증명되지 않으면 파일을 바꾸지 않는다. SHA/CAS, 대상 잠금, 취소·권한, 이전 바이트의0600 history 보관을 유지한다. 성공 영수증은 `previous_observation.basis=retained_input_read`와 `original_site_observation=unavailable`로 두 시각의 의미를 구분한다. 입력 수신은 수집·보관·색인 완료와 별개다.

회귀는 같은 legacy 표본의 정상 갱신·정확한 이전 바이트 복원, stale 입력·보관 근거 부재·손상·잘못된 cursor/시각/범위·사용 불가능 membership·명시 null을 다룬다. Python165개, UI983개, Rust146개와 strict Clippy를 통과했다. live source의 읽기 전용 candidate helper도 실제 입력/원본 보관/현재 버전에 결속했다. source CI39161502783의 네 작업이 성공했다.

## 설치본에서 확인한 결과

설치 파일77개와 mode가 후보와 일치하고 기존 고정 서명의 strict/deep 검사가 통과했다. 독립153 복구본, 모델·등록·비상 중단 설정, 7개 수집 대상의 주기·설정·활성화와 취득 정책을 보존했다. 후보·설치본의 소유 WKWebView 프로토콜은 실제 어댑터를 사용한다. 이는 물리 primary/tray/Retina 입력의 검증과 구분한다.

기존 등록된 한 게시물에 대한 요청 `1397e461-a85f-40dc-a837-a42edf0b1dac`과 실제 attempt `61fc6e7010da46beacad736f6d2a2d2d`는 수집→원문 보관→색인까지 완료됐다. 추가0·개정1이며 기존 문서 ID를 유지한다. 입력의 정확한 SHA256은 `61367f0debf9ea2df85470d8496b1a4d1fd9ecd601668a57e64c75031b06d29d`다. 이전967바이트 입력은 날짜를 덧붙이지 않고 history에 보존했다.

새 본문161자는 실제 단락 경계와 `1/14`를 유지한다. 새 버전 `version:6eb9bb67e660c433b516dcd4341a1b2b08e13c7cd8ce327eef84871887a70a8f`의 원문 manifest·cursor·record hash·FTS·벡터 stamp를 대사했다. E5가 개정 버전1건/텍스트 window2개를 색인했다. 새 임베딩 모델로 바꾼 결과가 아니다.

설치된 stdio MCP의5개 RPC(initialization/catalog/search/graph/history)에서 `alden-model-research` 범위의 같은 새 버전을 확인했고 검색은 실제 warm E5를 통한 RRF였다. 원래 완료 요청 ID를 다시 제출해도 request·attempt·대상 attempt 수가 그대로이며 재큐잉되지 않았다. 기존64,395개 버전,118,555개 관계 이력,48,355개 관계의 모든 기존 행과 비대상 문서·membership을 일관된 사전 SQLite 백업과 대사했다. 이 한 요청은 새 세션 두 호스트의 전역 MCP 등록, 의미 정답 품질이나 전체 계정 수집의 증거가 아니다.

Aside의 원래 취득 REPL로 제품 완료 영수증938바이트를 돌려주고 같은 바이트임을 확인했다. 영수증 SHA256은 `9e67ca6a2727615b590a51611a708de979f8974f11ad35fb2cfbb8b25165245b`다. 해당 REPL이 생성한 두 원문 탭만 닫고 기존 사용자 탭은 보존했다.

원문 취득은 기존 Aside 호스트의 관측된 main-post DOM을 사용했다. 본문과 인접 좋아요/답글/리포스트 숫자는 별도 영역이다. `innerText`는 inline `1/14`를 `1\n/\n14`로 나눴으므로, 실제 단락의 textContent와 단락 경계를 보존해 받았다. 숫자를 정규식으로 삭제하지 않는다. 이미지/영상 픽셀과 답글 전체는 이번 한 게시물의 취득 범위가 아니다. 게시물의 모델 설명은 출처의 주장이지 모델 성능 검증이 아니다.

별도 career 게시물의 예전 숫자6·3과 현재 UI16·3은 달랐다. 현재 DOM을 보았다는 이유로 예전 숫자의 의미를 확정하거나 기존44건을 독립 gold로 바꾸지 않았다. 기존 원문·정규화 정책·E5 모델·그래프 알고리즘과 전체18목표를 유지한다.

근거: task root `outputs/threads-refresh-20261019/`, worktree `outputs/threads-receiver-*.log`와 `threads-receiver-live-proof.json`. Graphify의 [보충 감사](graphify-adoption-audit-20261019.md)는 별도 범위이며 Gemini/최종 역검증은 미완료다.
