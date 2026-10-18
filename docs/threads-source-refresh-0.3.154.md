# Threads 원문 갱신과 이전 입력 호환

0.3.154 후보. 초기 Threads 표본에는 사이트 관측 시각이 없었다. 기존 수신기는 이전 파일에도 관측 시각을 요구해, 실제로 새 원문을 받아도 `collection_input_observation_required`로 거절했다. 설치153의 등록된 EmbeddingGemma2 표본에서 이를 재현했고, 입력 파일은 변경되지 않았음을 확인했다.

이번 수정은 **이전 입력에서만 시각 필드가 아예 없는 경우**를 다룬다. 새 입력의 관측 시각은 계속 필수다. 명시된 null/잘못된 시각, 과거 입력, 다른 원문, 권한 없는 대상에는 대체 경로를 쓰지 않는다.

이전 시각이 없으면 수집기의 기존 `source_observation` 읽기 시각을 보수적인 갱신 하한으로 사용한다. 이것을 과거 사이트 관측 시각으로 꾸미지 않는다. 다음 근거가 모두 맞아야 한다.

- 완료된 target cursor의 source revision, 읽기 영수증과 보관 capture가 현재 입력 SHA256에 결속한다.
- capture manifest와 실제 보관 파일의 해시·바이트, adapter·원본 경로·정확한 JSON 파일 범위가 맞는다.
- 현재 사용 가능한 해당 target membership, 문서 버전, record hash, JSON evidence pointer가 같은 원문에 결속한다.
- 하한은 유한한 실제 수집기 시각이고, 새 관측은 그 하한보다 뒤에 있다.

하나라도 증명되지 않으면 파일을 바꾸지 않는다. SHA/CAS, 대상 잠금, 취소·권한, 이전 바이트의0600 history 보관을 유지한다. 성공 영수증은 `previous_observation.basis=retained_input_read`와 `original_site_observation=unavailable`로 두 시각의 의미를 구분한다. 입력 수신은 수집·보관·색인 완료와 별개다.

회귀는 같은 legacy 표본의 정상 갱신·정확한 이전 바이트 복원, stale 입력·보관 근거 부재·손상·잘못된 cursor/시각/범위·사용 불가능 membership·명시 null을 다룬다. live source의 읽기 전용 candidate helper도 실제 입력/원본 보관/현재 버전에 결속했다. 아직 이 문서 시점의 앱 설치와 새 원문 수집 완료를 주장하지 않는다.

원문 취득은 기존 Aside 호스트의 관측된 main-post DOM을 사용했다. 본문과 인접 좋아요/답글/리포스트 숫자는 별도 영역이다. `innerText`는 inline `1/14`를 `1\n/\n14`로 나눴으므로, 실제 단락의 textContent와 단락 경계를 보존해 받았다. 숫자를 정규식으로 삭제하지 않는다. 이미지/영상 픽셀과 답글 전체는 이번 한 게시물의 취득 범위가 아니다. 게시물의 모델 설명은 출처의 주장이지 모델 성능 검증이 아니다.

별도 career 게시물의 예전 숫자6·3과 현재 UI16·3은 달랐다. 현재 DOM을 보았다는 이유로 예전 숫자의 의미를 확정하거나 기존44건을 독립 gold로 바꾸지 않았다. 기존 원문·정규화 정책·E5 모델·그래프 알고리즘과 전체18목표를 유지한다.

근거: task root `outputs/threads-refresh-20261019/`, worktree `outputs/threads-receiver-*.log`와 `threads-receiver-live-proof.json`. Graphify의 [보충 감사](graphify-adoption-audit-20261019.md)는 별도 범위이며 Gemini/최종 역검증은 미완료다.
