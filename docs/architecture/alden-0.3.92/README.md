# Alden 0.3.92 구현 도식

설치 런타임 소스 `546ca7eb0758b4b0e8a88302ce9a58c28b150b9d`의 구현과 검증 경계를 설명하는 9개 Archify HTML입니다. 작성 문구는 한국어이며 고정 Viewer UI는 영어입니다. `alden-0.3.49`와 `alden-current`는 해당 판본의 이력으로 보존합니다.

| 요구 범위 | 도식 | 구현과 검증 경계 |
|---|---|---|
| 전체 프로세스·추론 | [시스템](system.html) | Tauri/Python, 저장된 모델 선택, Gemini Flash/high 기본값, RAM 허용 로컬 경로. 모든 모델의 추론 성공은 별도 |
| 대화·음성·취소·비상 중단 | [입력과 턴](voice.html) | Desktop 텍스트·사진, 실제 바이트 검증, OpenCodex 검색 참조·로컬 텍스트 분기, 취소 펜스. 물리 음성은 미완료 |
| 다중 대상·출처 수집 | [수집](collection.html) | 대상·권한·예약/영속 요청, 수집/capture/index 상태, 이전 snapshot 복원. 넓은 자동 재취득은 미완료 |
| 기존 원본→단일 뷰어 | [계보](lineage.html) | CollectionStore와 OSK 정본, 범위와 페이지에 따른 표시 합성, 부분 실패. 전량 복제·동일인 병합과 구분 |
| MCP·충돌·중복 | [호스트와 MCP](mcp.html) | 시작 프로젝트 allowlist, 읽기 전용 조회·검색·범위 밖 거절. 전역 등록·양 호스트 독립 실행은 미완료 |
| 정규화·검토·저장·검색 | [정규화와 검색](normalization.html) | 원문 보존, NFC/줄바꿈, 후보와 명시 관계, CAS 적용/되돌리기, 현재 본문 근거. 독립 의미·동일인 gold는 미완료 |
| 기억 정리·3D 활동 | [이력과 활동](activity.html) | 저널·집계·cursor/epoch/TTL·표시 버전, 저장/읽기 활동, R/S 배치. 공간 근접을 사실 관계로 저장하지 않음 |
| 재연결·창 수명·복원 | [복구](recovery.html) | 새 snapshot·작업 세대, 완료 solver checkpoint·선택 pin, late-blur 수정, 단일 소비자·정착 후 RAF 0. 모든 물리 순서는 미완료 |
| 평가·DPO·제품 반영 | [평가](evaluation.html) | 자료 분리·토큰 확률·조건부 offline 학습, 완료 코드 경로의 영수증 필드, 독립 게이트·승격 보류 |

각 최종 도식은 showcase 9/9 검사와 오류·경고 0개를 통과했습니다. 정확한 HTML을 Chrome에서 검사했고 1440×900·1600×1000·1920×1080·2048×1320의 넘침·가독성 검사가 모두 통과했습니다. 양 끝 크기의 밝은/어두운 PNG 총 36개를 이미지로 직접 검토했습니다. 라벨·노드·화살표·설명 카드의 잘림과 겹침, 대비와 화면 배치를 확인했습니다. 이 크기별 브라우저 검사는 물리 Retina나 Alden 제품 성능 검사가 아닙니다.

[manifest](manifest.json)는 정확한 JSON/HTML/PNG SHA-256, 47개 코드 참조와 줄 앵커, 전달/브라우저 영수증을 연결합니다. 코드 바이트는 위 런타임 소스 커밋과 대조했습니다. `*-delivery.json`은 결정적 산출물 검사, `*-browser.json`과 `*.visual-check.json`은 브라우저 관측입니다. 브라우저 영수증의 `visualReview: pending`을 보존하고 이미지 검토 판정은 manifest에 별도로 기록했습니다.

`chatgpt-web/gpt-6-sol` / `xhigh`의 실제 턴 모델을 확인한 병렬 검토를 적용했습니다. [독립 보고](independent-review.md)는 제공한 요약과 코드 발췌만 검토한 결과입니다. [지적 반영](review-resolution.md)은 조건부 검색·사진 모델 제한·부분 합성·학습 기록을 코드에 대조한 부모 작업입니다. 전체 저장소나 제품 실행을 독립 인증한 검토로 확대하지 않습니다.

실제 제품 전달 근거는 날짜별 문서를 따릅니다: [본문 근거 검색](../alden-rag-body-evidence-20261013.md), [그래프 복원](../alden-graph-restore-20261013.md), [solver 배치 전달](../alden-layout-handoff-20261013.md), [화면 중앙 충돌](../alden-display-collision-20261013.md), [읽기 전용 품질 확인](../alden-graph-quality-panel-20261012.md), [원문 검토](../alden-source-review-20261012.md).

본문 우선 단계는 한국어 양의 어휘 계획과 BM25 또는 어휘 가중치가 있는 RRF에 적용하고 명시적 최신 요청은 건너뜁니다. 검색 일치·버전 결속을 원문 사실성으로 판단하지 않습니다. 한 화면의 통합은 정본 전체 복제나 정체성 병합의 완료를 뜻하지 않습니다. 원문 의미·시간 유효성·독립 gold, 물리 음성/UI, 여러 호스트, RAM을 허용한 넓은 모델/학습, 전체 앱 자원 측정과 Developer ID/공증/프로덕션 배포는 남아 있습니다.

이번 전달은 문서 작업입니다. 설치 앱은 0.3.92이며 도식 전달 때문에 재설치하거나 운영 worker를 활성화하지 않았습니다.
