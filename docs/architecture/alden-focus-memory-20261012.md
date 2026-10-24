# Alden 0.3.74 — 집중 조회 RAM과 원문 시간 보존

읽기 전용 집중 보기에서 소수 이웃을 표시하기 위해 전체 허용 관계의 JSON·Python 딕셔너리·adjacency를 만들던 경로를 줄였다. 전역 count/facet는 유지하고 첫 제한 이웃을 같은 조회에서 얻는다. 이후에는 현재 선택 ID에 incident한 관계만 SQL에서 처리하며 선택 nodes/edges를 배치로 읽는다.

BFS 거리별 BINARY ID순, hops0~3, min(limit,24)노드·144관계 상한과 원래 방향/타입/시간/프로젝트/대상/권한/버전 필터를 보존한다. degree는 자기 고리를 제외한 모든 허용 고유 이웃이며 표시 밖의 이웃도 포함한다. 원래 관계와 프로젝트·대상별 source contexts를 보존한다. details0의 원문·해시·expected_version 빠른 경로는 유지한다.

## 실제 실패와 전필드 검증

0.3.73 설치본의 여러 프로젝트 집중 의미 요청에서 `affinity_rss_budget`을 재현했다. 한 번의 초기 maximum RSS는167,051,264byte였다. 반복 CTE 실험은 약83MB로 메모리를 줄였지만 추가 조회 비용이 있어 보존 후 보강했다. 최종 incident 범위와 배치 읽기는 전체 허용 scope를 축소한 것이 아니다.

선택 노드·관계의 JSON 묶음에서 SQLite JSON 숫자 포맷 때문에19개 수집 시각의 소수 정밀도가 달라진 것을 실제 대조로 발견했다. fractional clock 회귀1실패를 재현하고 같은 읽기 snapshot의 최대24버전 원래 REAL 값을 한 번에 읽어 복원했다. 실제24노드/23관계·전역26623노드/48355관계·revision399522의 모든 필드가0.3.73과 정확히 같았다.

명시적 BEGIN과 같은 snapshot 보장은 제품의 `open_existing` 읽기 전용 경로 및 affinity의 소유 읽기 transaction에 적용된다. 쓰기 가능한 CollectionStore의 동시 다중 SELECT에는 이 보장을 확대하지 않는다.

## 동일 조건 교대 측정

같은 pinned Python3.11 -B/CLI·실제 저장소·focus·hops1·limit120을5쌍 graph 및3쌍 affinity로 교대했다. 모든 raw JSON/time-l·현재 source SHA를 보존했고 graph5쌍은 전필드 동일했다. 작은 현재 corpus 표본이며 전체 앱/물리 입력/GPU/배터리·장시간·전체 군집 품질 측정이 아니다.

| 경로 | 전 | 후 | 관측 범위 |
| --- | --- | --- | --- |
| graph 최대 RSS 중앙값 |169,476,096byte|81,412,096byte|5쌍,51.96% 감소|
| graph 지연 p50 |1.978초|1.753초|5쌍, 전체 CLI wall 관측|
| graph 관측 p95 |3.279초|1.883초|n=5의 nearest-rank 최대값, 대표 p95 아님|
| affinity 최대 RSS 중앙값 |166,952,960byte|80,232,448byte|3쌍,51.94% 감소|
| affinity RAM 오류 |3/3|0/3|동일128MiB상한 유지|

affinity 후3회는 조회·내용 선언 검사를 마쳤으며 선택24개가 모두 내용 근거에서 제외됐다. `unavailable/layout_no_eligible_content`는 의미 판단을 하지 않은 상태다. 전 요청은 일찍 실패했으므로 그 지연1.880초와 완료 후3.461초를 속도 개선 비교로 사용하지 않는다.

## 검증과 전달

요청한 `chatgpt-web/gpt-6-sol/xhigh` 두 작업자의 actual model/effort를 확인했다. 독립 원본 fixture BFS oracle의192조합과 필터·관계/degree·원래 맥락 검사를 통합했고 부모의 소수 시간 회귀를 추가했다. 최종 pinned Python134, UI728/타입, native131/strict Clippy가 통과했다. 첫 독립 검토의 추가 진단 차단 주장은 실제33도구쌍에 대응 거절이 없어 미확인으로 보존했다. 후속 독립 검토는 현재 code와 기존 receipt에서 계약 위반을 발견하지 않았으며 별도 성능 측정으로 해석하지 않는다.

실행 소스 `beb177ebb45f3ce5f2882979f5473a0f31534277`의 [CI38192382069](https://github.com/twoimo/alden/actions/runs/38192382069) 네 작업이 성공했다. canonical0.3.74를 빌드·설치했고 후보·설치의 실제 읽기 전용 native14상태/28PNG씩이 통과했다. 설치68파일 SHA/모드·strict deep ad-hoc 서명/noPYC·독립0.3.73복구68파일과 설정3/대상7/정책2/모델 listener/음성 이력·수집/검색7표를 대사했다. 설치 CLI의 동일 broad affinity는 내용 제외24/검증 오류0·정책v2를 확인하고 RAM상한 안에서 완료됐다.

가역 검토는 후보·설치에서 새 private synthetic collection/incognito native의 실제 버튼/CLI/SQLite로 두 크기3→2→3·원본12표/관계2개·최종 묶음0/결정이벤트4개를 재검증했다. 이는 실제 사람의 운영 묶음 판단이나 전체 원문 gold가 아니다. 검은 배경·작은 점·가는 선·실제 관계 거리의 색·한 줄 상단과 기존 camera/label 보호를 유지한다.

## 남은 조건

새 source를 배포한 것이 전체18개 목표 완료를 뜻하지 않는다. 여러 실제 focus·대표 규모/장시간 자원·의미 gold/동명이인/철회/이상 원인·전체 힘/방향 군집·물리 음성/입력/Retina·여러 호스트·모델/DPO·최신9도식·정식 서명/공증/태그/프로덕션은 미결이다. 이전 source 판단·R/S·worker proof·원문/권한 보류는 이 조회 최적화로 해제하지 않는다.

단계별 실패·actual native·교대 표본·전필드/clock·보존/복구와 CI 증거는 작업 공간 `outputs/graph-focus-memory-20261012/`에 있다. 기존 원본·실험2SHA·별도 후보·큐·모델·설정·미추적 산출물을 보존한다.
