# Alden 현재 구현 · 2026-10-27

이 폴더의 9종 다이어그램은 커밋 `d4f088b3fc7d9f6a8b24613bf05d02e6ff882bc4`의 실제 호출부와 저장·권한 검사 소스를 설명한다. 초기 조사 기준 `af36aa02ab80708c44fb61060a15c0a283fdabfb` 이후 부모가 커밋한 f(x)·stage guard를 다시 읽고 현재 파일 바이트를 대조했다. 검증 범위는 아래 소스 대조와 정적 검사에 한정한다.

원래 목표 section 18과 [0.3.151 문서](../alden-0.3.151/README.md)는 역사·범위 배경이다. 과거 구현과 검증 이력은 기존 9종 산출물과 [이전 current 문서](../alden-current/README.md)에서 확인할 수 있다. 새 lifecycle은 schema 3이며 역사 문서를 변환하지 않았다.

| 문서 | HTML | 현재 스키마 JSON | 출처 맵 |
|---|---|---|---|
| 시스템·프로세스 | [system](system.html) | [architecture](system.architecture.json) | [근거](system.source-map.json) |
| 음성·취소 | [voice](voice.html) | [workflow](voice.workflow.json) | [근거](voice.source-map.json) |
| 수집·취득 | [collection](collection.html) | [workflow](collection.workflow.json) | [근거](collection.source-map.json) |
| 계보·출처 | [lineage](lineage.html) | [dataflow](lineage.dataflow.json) | [근거](lineage.source-map.json) |
| MCP·권한 | [mcp](mcp.html) | [workflow](mcp.workflow.json) | [근거](mcp.source-map.json) |
| 정규화·동일성 결정 | [normalization](normalization.html) | [workflow](normalization.workflow.json) | [근거](normalization.source-map.json) |
| 그래프 활동·렌더 생명주기 | [activity](activity.html) | [lifecycle](activity.lifecycle.json) | [근거](activity.source-map.json) |
| 복구·체크포인트·부분 상태 | [recovery](recovery.html) | [workflow](recovery.workflow.json) | [근거](recovery.source-map.json) |
| 평가·학습·승격 경계 | [evaluation](evaluation.html) | [workflow](evaluation.workflow.json) | [근거](evaluation.source-map.json) |

## 소스와 해시

[소스·도구 핀](source-pins.json)은 소스 33개, Archify v3.2.0 설치 모듈 203개 파일, Node `v26.12.0` 실행 파일을 고정한다. 설치·업데이트·의존성 추가는 없었다. [산출물 매니페스트](artifact-manifest.json)가 각 JSON·HTML·출처 맵과 source-pins.json의 SHA-256을 함께 묶는다. [독립 재읽기 결과](verification.json)는 이 연결을 실제 파일에서 다시 확인한다.

각 출처 맵은 노드·관계 주장과 정확한 소스 경로, inclusive line 범위, 파일/범위 SHA-256을 가진다. HTML의 내장 source evidence는 같은 커밋과 JSON의 source references를 가진다. `local-only` 링크 모드는 비공개 소스를 외부에 올리지 않는다. 소스 바이트·본문 데이터는 이 문서에 복제하지 않았다.

정식 CLI 이름은 [Cargo.toml](../../../Cargo.toml)의 `alden-cli`이다. `openkakao-cli`는 호환 실행명이며 둘을 별도 제품으로 표현하지 않는다.

## 구현 범위와 한계

- 요청에서 지칭한 명시 목표 line 514의 기본 경로와 일치하게, 설정이 없으면 [model routes](../../../scripts/alden_model_routes.py)의 `google-antigravity/gemini-3.8-flash` / `high`를 사용한다. [voice 설정](../../../scripts/alden_voice.py)은 사용자 저장 선택을 턴 시작에 고정한다. 고정 MLX 27B 기본값으로 설명하지 않는다. 실제 현재 사용자 설정이나 실행 중 모델은 읽지 않았다.
- 네이티브 모델은 선언된 로컬 `/models`의 정확한 ID·chat capability·단일 ready endpoint를 근거로 한다. 라우터의 평탄화된 폴더 별칭만으로 네이티브 ID를 추정하지 않는다. 현재 inventory나 서비스에 요청하지 않았다.
- [로컬 지식 루프](../../../scripts/alden_knowledge_turn.py)는 최대 2홉·1읽기이다. 원래 입력·route·token binding, 현재 문맥, 출처 receipt를 재검사하고 [텍스트 턴 저장](../../../scripts/alden_chat_turns.py)은 pending/owner/turn/context CAS를 요구한다. 일반 음성 턴·MCP stdio와 동일 호출 경로라고 주장하지 않는다.
- 원본 보존 바이트·문서 버전·권한 및 revision 계보와 GBrain 파생 투영을 구분한다. [FullGbrainReader](../../../scripts/alden_gbrain_import.py)는 조회 전후 현재 export와 generation 파일을 확인한다. GBrain이 원본 권한을 대신하거나 원본 내용을 자동 확정하지 않는다.
- [본문 표기](../../../scripts/alden_body_normalization.py)는 구문 후보이며 사실 판정이 아니다. [동일성 결정](../../../scripts/alden_entity_resolution.py)은 명시 opt-in·계획 해시·권한·revision을 검사하는 파생 오버레이이다. 되돌림은 원문과 불변 결정 이력을 보존한다.
- [Worker driver](../../../desktop/src/knowledge/semantic-layout-driver.ts)는 최신 세대만 적용하며 입력·핀·권한을 검증한다. 동일 입력의 완료 캐시, 신규 generation, 전송/실행 실패, stop/clear/dispose를 구분한다. UI 스레드 대체 solver 성공을 가정하지 않는다. 노드 활동은 별도의 성공 영수증·현재 버전·대상·TTL 조건을 따른다.
- f(x)는 [collection-history](../../../scripts/alden_collection.py)에서 **완료 run의 보존된 stored 문서 전이 added/revised/removed**를 UTC 저장일로 집계한다. 이벤트 의미 ID를 기간 필터 전에 중복 제거하며 unchanged를 제외한다. deferred이면서 published로 확인되지 않은 항목·불완전 항목은 분류되지 않는다. `snapshot_checkpoint`는 페이지 `cursor`와 별도이며 [클라이언트](../../../desktop/src/knowledge/terminal-trend.ts)가 이를 검증한다. `source_coverage`와 `backfill_live`는 unknown, `relations`는 null이다. 빈 날짜는 실제 유입 0의 증거가 아니다. **31일 제한은 출력 기간 한도이며 고정 SQL 작업량 상한이 없다.** 추가된 guard는 trend와 stage `None`/`stored` 조합만 허용한다. [부모 평가 문서](../../evaluation/terminal-event-flow-20261027.md)는 별도 근거다.
- [버전 평가](../../../scripts/alden_model_evaluation.py)는 기본 validation과 명시 final test를 구분한다. [오프라인 DPO](../../../scripts/alden_dpo_training.py)는 사설 어댑터를 만들 수 있으나 `model_promoted=false`, `quality_improvement_claimed=false`이다. 자동 승격 경로를 그리지 않았다.

## 실제 수행한 검사

[원본 CLI 영수증](validation.json): 9/9 스키마·repository source 검사와 showcase 정적 구성 검사 성공, 구성 오류·경고 0. HTML 생성 9/9 exit 0. 생성 HTML `check` 9/9 성공이며 각 9개 정적 항목을 확인했다. 출처 맵·로컬 링크·JSON/HTML/소스 해시 연결·기존 산출물 보존은 [verification.json](verification.json)에 별도로 기록한다.

사용한 설치 CLI와 고정 Node에서 가족별로 실행한 명령은 다음과 같다. 정확한 절대 인자와 결과는 validation.json에 보존했다.

```sh
/opt/homebrew/Cellar/node/26.12.0/bin/node /Users/twoimo/.agents/skills/archify/versions/v3.2.0/bin/archify.mjs validate <type> <family.type.json> --quality showcase --repo-root <owner-repo> --json
/opt/homebrew/Cellar/node/26.12.0/bin/node /Users/twoimo/.agents/skills/archify/versions/v3.2.0/bin/archify.mjs render <type> <family.type.json> <family.html> --quality showcase --repo-root <owner-repo>
/opt/homebrew/Cellar/node/26.12.0/bin/node /Users/twoimo/.agents/skills/archify/versions/v3.2.0/bin/archify.mjs check <family.html> --json
```

CLI 이름 `render`는 여기서 HTML 문자열 생성만 뜻한다. 브라우저·Alden·native app을 시작하지 않았다. `finalize`, `browser-check`, `visual-check`, 이미지/영상 export는 실행하지 않았다. 자동 브라우저를 실행하는 finalize 경로와 업데이트/cache 동작을 포함한 deliver를 사용하지 않았으므로 Archify strict delivery provenance 통과를 주장하지 않는다. 별도 매니페스트의 바이트 결합과 일반 정적 check가 완료 범위다.

선언 검토 viewport는 1440×900이다. 한국어 짧은 노드·레이블과 별도 설명 카드로 작성했고 정적 geometry 검사만 통과했다. 그 viewport에서 실제 렌더·글자 가독성·인터랙션·내보내기 결과는 **미검증**이다. HTML의 외부 Archify 저작자 링크는 설치 템플릿의 attribution이며 네트워크로 재검사하지 않았다.

전체 TSC·production Vite 통과는 별도 구현 검증이다. 이 작업은 source/test를 실행하지 않았다. 보충 Argon 소스 의견과 CI/push 관찰도 정식 `webAstra → Gemini → 역검증`을 대신하지 않는다. 정식 외부 감사·네이티브 검증·설치·릴리스는 **보류/미완료**다. Alden은 시작하지 않았다.

요청 모델/effort: `gpt-6-astra/high`. 실제 세션 model/effort: `unknown` (노출되지 않음).
