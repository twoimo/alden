# 현재 Alden 구현과 검증

0.3.25의 직접 참조 예산·축약 입력·턴별 source 기록은 [현재 변경 문서](../alden-canonical-edges-20261005.md)에서 확인합니다. 아래 도식과 검색 측정은 명시한 고정 판본의 기록입니다.

이 문서는 구현 커밋 `9732617bc78c80fadafd431c250b958b3408683c`의 경로를 설명합니다. 설치본은 0.3.24이며, 도식·검색 검사는 물리 음성·권한·공증·프로덕션 완료를 뜻하지 않습니다. 작성 내용은 한국어이고 Archify 뷰어의 고정 UI는 영어입니다.

| 요구 범위 | 현재 도식 | 구현 근거 |
|---|---|---|
| 전체 시스템·프로세스 경계 | [구조](system.html) | Tauri/Python 자식 작업, 기존 MLX/E5 서버, OSK 정본과 파생 색인 |
| 음성·취소·비상 중단 | [상태 흐름](voice.html) | 직접 마이크, 최신 턴, 자기 TTS 입력 차단, 전역 중단과 명시 재개 |
| 3D 렌더링·창 수명 | [렌더 흐름](render.html) | 네이티브 가시성, 고정 자원, 실제 사건 반응, 숨김·정지·해제 |
| GraphRAG 검색 | [검색 시퀀스](retrieval.html) | 정본 SHA, FTS5/E5, 현재 리비전, RRF, 인용 출처와 완결성 |
| 카카오톡 스냅샷·증분 색인 | [수집 흐름](snapshot.html) | 암호화 DB+WAL 전후 서명, 격리 읽기, 완성 증명과 Raw 좌표 |
| 평가·학습·반영 | [후보 경계](evaluation.html) | 데이터 분리, 실제 응답 로그확률·DPO 업데이트, 독립 최종 평가, 제품 보류 |

각 도식은 9/9 showcase 구성 검사, 오류·경고 0개를 통과했습니다. 실제 Chrome에서 1440×900, 1600×1000, 1920×1080, 2048×1320의 문서 넘침·가독성을 확인했고, 양 끝 크기의 밝은/어두운 화면 24개를 직접 검토했습니다. 전체 시스템 도식의 첫 화면은 세 크기에서 넘쳤으며 세로 배치를 정리한 뒤 다시 검사했습니다. 이 실패는 최종 성공 근거로 섞지 않았습니다.

[검증 manifest](manifest.json)는 도식 종류, 소스·HTML SHA-256, 원본 실행 영수증, 브라우저/시각 검토 상태, 실제 코드 파일의 SHA를 연결합니다. 원본 JSON과 HTML은 최종 검증 후 바꾸지 않고 동일 바이트로 복사했습니다. 이 도식의 렌더 검사는 제품의 물리 창·잠금·음성을 실행한 검사가 아닙니다. 예전 날짜의 도식은 해당 판본의 이력입니다.

## 제한된 정본 검색 검사

실제 104개 노트에서 네 개의 연구·설계 노트 본문을 대조해 새 질의 8개의 기대 ID와 SHA를 검색 전에 고정했습니다. 라벨은 주 작업자의 원문 비교로 작성했고 모델이 자기 검색 결과를 승인하지 않았습니다. 외부 독립 라벨러나 전체 사용자 지식의 맹검 평가셋은 아닙니다. dev/heldout 각 네 질의로 나눴으며 정책 조정 없이 현재 기본값을 사용했습니다.

최초 실행에서 8/8 질의의 기대 노트가 1위였고 Recall@1·nDCG@1은 1.0, 검색 중앙값은 0.447초였습니다(n=8, 서로 다른 질의, 상주 E5). 이 값은 네 노트의 제한된 기능 검사입니다. 관련성 없는 질의, 모든 대화방, 전체 의미 증류, p95, 콜드 스타트와 개선율을 입증하지 않습니다. 이전 세 constructed query나 DPO의 소비된 최종 3쌍과 별개입니다. 이 검사에서 본 heldout 질의는 앞으로 새 정책의 미사용 최종 검증으로 취급할 수 없습니다.

동일한 고정 정책으로 재현 스크립트를 한 번 실행해 8/8 순위가 최초 실행과 같음을 확인했습니다. [재현 원자료](canonical-reproduction.json)의 중앙값은 0.320초(n=8)이며, 호스트 부하·캐시 상태를 통제하지 않아 최초 값과의 차이를 개선율로 계산하지 않습니다. 최종 학습 평가 자료를 재사용한 실행이 아닙니다.

정책은 `RRF(d)=1/(60+rank_BM25(d))+1/(60+rank_dense(d))`, 후보 40개, 최종 노트 5개, 본문 예산 16000자입니다. `Recall@k=|top_k∩gold|/|gold|`; `nDCG@k`는 `(2^grade−1)/log₂(rank+1)`의 실제/이상 순위 합 비율입니다. 각 질의의 gold는 직접 대응하는 노트 하나에 grade 3을 부여했습니다. 순위와 출처가 모두 해당 SHA의 정본에 일치하는지 확인했습니다.

[고정 질의](canonical-judgments.json), [최초 원자료](canonical-evaluation-initial.json), [재현 스크립트](canonical-evaluate.py)를 제공합니다. 재현은 정확한 정본 snapshot과 기존 로컬 색인을 요구하며, 다르면 중단합니다. 원본 카카오톡 DB를 읽거나 문서 임베딩·학습·모델 교체를 시작하지 않습니다. 제품 검색 호출이 폐기 가능한 검색 캐시를 갱신할 수 있습니다.

```bash
python3 -B docs/architecture/alden-current/canonical-evaluate.py \
  --scripts-dir /Applications/Alden.app/Contents/Resources/scripts \
  --state-root "$HOME/Library/Application Support/openkakao/bujamentor" \
  --judgments docs/architecture/alden-current/canonical-judgments.json \
  --output /tmp/alden-canonical-reproduction.json
```

## 남은 실제 목표

전체 OSK 의미·조직 검토 104단위, 독립적이고 더 넓은 검색 품질 평가, 물리 primary/tray/Retina/lock/shortcut·음성, Flash-Next 메모리 admission, Dot 실제 호출, Developer ID·공증·프로덕션, 같은 조건의 전체 앱 CPU/RAM/GPU/전력·음성 p50/p95가 남아 있습니다. 기존 모델·설정·전송 큐·운영 worker는 보존합니다. 기본 사용 한도가 잠겨 있어 요청한 추가 네이티브 독립 에이전트 검토도 실행하지 못했습니다.
