# 구조·의미 채널과 증분 군집 감사

2026-10-20. 실행한 제품 소스는 `9a6d50b3a9417e330b4a3feb1d2e6e503718c736`이며 설치본은 0.3.191이다. 이번 전달은 기존 W-A03 비교 검증의 확장이다. 제품 알고리즘·설치본·원문·ID·모델·벡터·큐를 바꾸지 않았다. 전체 18개 목표와 독립 의미/동일인/시간 정답, Gemini 반대감사·최종 역검증은 미완료다.

## 실행 범위와 원본 결속

[생성·실제 엔진 실행](audits/audit_alden_graph_channels.ts)은 96/192노드, seed201..206, 일치·모순·허브·단절·희소의 5조건을 구조만/유사도만/혼합으로 실행했다. 구조 간선은 양의 동일 가중치이며 합성 cosine은 .76~.96, 끝점별 후보 차수4다. 실제 `SemanticLayoutEngine`의 admission·차수 제한·정규화를 통과한 checkpoint의 r/s를 사용한다. 비교 그래프의 가중치는 `s + (구조 차수 비율 >= .5 ? r : 0)`로 현재 코드의 community gate만 재구성한다. 누락·잘못된 입력·예산 잘림이 생긴 fixture는 실패시킨다.

[비교 실행](audits/compare_graph_channels.py)은 기존 `igraph==1.0.0`, `leidenalg==0.12.0` 격리 runtime과 기존 개발 선택 `gamma=.01`을 사용한다. 이전 선택 파일·비교 helper의 해시와 버전을 대사하고 새 결과로 재선택하지 않는다. 현행, 연결 요소, Leiden global modularity, component modularity, CPM을 동일한 admitted graph에서 비교한다. [공식 API](https://leidenalg.readthedocs.io/en/stable/reference.html#leidenalg.find_partition)의 가중치/반복 설정을 구분하며, reference는 기존 seed0·2회 반복이다. 제품8pass/전체 배치와 동등 계산 예산 또는 속도 비교로 해석하지 않는다.

실행은180조건·900비교지만, 고유 입력 그래프는132개이고 크기/seed 가족은12개다. 여러 ablation이 의도적으로 동일 입력을 재사용한다. 이 수를180개의 독립 표본으로 합산하거나 통계적 모집단 무오류·우월성을 주장하지 않는다. 새 합성 개발 점검이며 미사용 실제 의미 holdout이 아니다. 두 생성 라벨이 모순되는 조건에서는 어느 쪽도 의미 정답으로 선택하지 않고 둘 다 보고한다.

전체 결과, 원본 partition·입력·소스 해시와 두 언어의 지표 검산은 `outputs/algorithm-channels-20261020/`에 있다. TypeScript의 ID치환/삽입/복원 ARI540개를 Python의 Fraction 기반 ARI로 다시 계산해 1e-12 이내에서 대사했다. 기존 매개변수 지문 위조와 ARI 변조 반례는 각각 거부되고 결과 보고서도 생성되지 않았다. 최초 fixture 생성은 모든 노드와 연결된 hub를 bridge 출발점으로 고정해 실패했으며, 없는 간선의 실제 두 끝점을 찾도록 생성기만 수정했다. 제품 결함이나 완료 결과로 세지 않았다.

## 측정한 차이

아래는192노드/각6seed의 중앙값이다. ARI1은 생성 라벨과의 일치이며 실제 자료의 의미 정확도가 아니다.

| 조건·채널 | 현행 군집 수 | 구조 라벨 ARI | 유사도 라벨 ARI | 비교에서 드러난 범위 |
| --- | ---: | ---: | ---: | --- |
| 일치·혼합 | 4 | 1.0000 | 1.0000 | 이 조건에서는 네 군집 복원 |
| 일치·유사도만 | 16 | .3166 | .3166 | 끝점별4유사도 연결만으로 목표 해상도를 보장하지 않음 |
| 모순·혼합 | 4 | 1.0000 | -.0160 | 구조 쪽을 따름. 시각 군집을 의미 유사도의 정답으로 볼 수 없음 |
| 단절·혼합 | 32.5 | .1847 | .1847 | 글로벌 modularity/기존 CPM은4개·ARI1, component modularity는20개·.2849 |
| 희소·혼합 | 34 | .2293 | .2293 | 글로벌 modularity는5개·.9099, 같은 CPM은20개·.3139 |
| 허브·혼합 | 4 | 1.0000 | 1.0000 | 이 조건의 hub와 유사도 채널은 네 군집을 유지 |

배열을 뒤집은180조건은 partition과 좌표가 같고 모든 좌표가 유한했다. 각 조건에 새 구조 간선3개를 넣고 제거하는 동안 지정한 한 노드의 좌표도 정확히 유지됐다. 이는 물리 입력·화면 FPS·실제 의미 품질의 검증이 아니다. 유사도 전용 baseline에도 이 perturbation 단계에서는 구조 간선이 추가된다는 점을 결과의 한계에 명시했다.

안정성의 다른 축은 통과하지 못했다. 단절·혼합192노드에서 ID치환 전후 partition ARI 중앙값은 .6702였다. 간선3개 추가 뒤 .1904, 다시 제거한 뒤도 원래 partition과 .1904였다. 고정 좌표 보존과 원래 군집 복원은 별개다. 유사도 전용192노드는 ID치환 ARI .4842, 간선 제거 뒤 .9677이었다. 이 값은 해당 합성 조건의 반례이며 실제 의미 오류율이 아니다.

## 판정과 남은 구현 조건

| ID | 판정 | 근거와 적용 조건 |
| --- | --- | --- |
| CH-01 | ACCEPT | 혼합 채널도 과분할·ID치환·증분 복원 반례가 남는다. 화면 개선이나 원문 보존을 의미 군집 인증으로 올리지 않는다. |
| CH-02 | CONDITIONAL | 구조·유사도·표시 힘을 분리한 기존 계약을 유지한다. 모순되는 두 채널의 업무상 우선순위는 독립 실제 정답과 근거 표시를 평가한 뒤 결정한다. |
| CH-03 | CONDITIONAL | 기존 A03의 계층/해상도 후보를 이 반례와 독립 가족에서 비교한다. 단절 조건의 개선만으로 글로벌 목적함수를 바로 승격하지 않는다. 무관한 컴포넌트의 추가가 기존 군집을 바꾸는 부작용도 대사해야 한다. |
| CH-04 | REJECT | Leiden으로 이름만 바꾸거나 단일 CPM gamma를 전체 채널에 적용하면 해결됐다는 주장. component 방식도 과분할하고 고정 CPM은 희소 조건에서 나빠졌다. |
| CH-05 | ACCEPT | 반복 입력은 지문으로 식별하고 두 생성 라벨·원본 partition·지표 검산을 보존한다. 결과를 보고 파라미터나 정답을 변경하지 않는다. |
| CH-06 | DEFER | 실제 의미/동일인/시간 gold, 원래 Gemini 반대감사와 최종 ChatGPT reverse, 실제 제품 승격·물리/자원 검증. 이번 합성 비교로 대체하지 않는다. |

현재 공유 coordinator는 두 서비스 모두 새 admission을 허용하지 않았다. 다른 owner의 슬롯을 강제 해제하거나 원래 연구를 재전송하지 않았다. 이는 로컬 admission 관측이며 원격 연구가 실제로 계속 실행 중이라는 증거로 사용하지 않는다. 지정된 `chatgpt-web/gpt-6-astra`/max도 현재 병렬 도구의 지원 목록에 없어 대체 모델을 사용하지 않았다. 외부 전문의 해시·원본 URL·모드·소스와 결속된 실제 3단계 검토가 끝나기 전 방향 검증 완료를 주장하지 않는다.

QA는 사용자 요청대로 전면/floating 앱 창 없이 CLI로 실행했다. 설치본과 네이티브 화면을 재실행할 이유가 없는 감사 도구 변경이며 실제 최소화 상태 확인·물리 입력 검증은 별도 미완료다.

## 재현

저장소 루트에서 기존 격리 runtime과 원래 frozen selection을 유지한 채 실행한다. 출력 디렉터리는 먼저 새로 만들고 기존 결과를 덮어쓰지 않는다.

```sh
set -eu
ALDEN_CHANNEL_AUDIT_OUT=outputs/algorithm-channels-review-copy
mkdir "$ALDEN_CHANNEL_AUDIT_OUT"
desktop/node_modules/.bin/esbuild docs/audits/audit_alden_graph_channels.ts --bundle --platform=node --format=esm --outfile="$ALDEN_CHANNEL_AUDIT_OUT/channel-audit.mjs" --log-level=warning
node "$ALDEN_CHANNEL_AUDIT_OUT/channel-audit.mjs" --seed-start 201 --seed-count 6 > "$ALDEN_CHANNEL_AUDIT_OUT/channel-inputs.json"
outputs/algorithm-reference-20261019/runtime/bin/python3 -B docs/audits/compare_graph_channels.py --input "$ALDEN_CHANNEL_AUDIT_OUT/channel-inputs.json" --selection outputs/algorithm-reference-20261019/selection.json --output "$ALDEN_CHANNEL_AUDIT_OUT/channel-comparison.json"
```
