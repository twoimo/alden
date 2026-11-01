제공된 **9개 도식 요약과 코드 발췌만으로 독립 검토**했습니다. 파일·설정·프로세스 수정 및 도구 호출은 수행하지 않았습니다.

**판정: 조건부 적합.** 전체적으로 미검증 범위를 분리하려는 방향은 타당합니다. 다만 코드의 조건부 분기를 단일 실행 경로처럼 표현한 곳이 3개 있으며, 일부 도식은 실제 실행이 확인된 것처럼 읽힐 수 있습니다.

### 1\. 우선 수정할 코드·도식 불일치

| 위치 | 판정 | 충돌 근거 및 수정 방향 |
| --- | --- | --- |
| voice.workflow.json → context92 → infer92 | **수정 필요** | 모든 추론이 검색·인용 맥락을 통과하는 것으로 보입니다. 하지만 generate\_input()에서 \_voice\_knowledge\_reference()는 transport == 'opencodex' 분기에서만 호출됩니다. LocalMlxLlm.generate\_for\_turn()에는 해당 검색 결과가 명시적으로 전달되지 않습니다. 검색 참조를 OpenCodex 조건부 경로로 표시해야 합니다. |
| system.architecture.json → remote92 | **검증 범위 초과** | "Gemini 3.8 Flash high 기본"이라는 정확한 모델·effort는 발췌 코드만으로 확정할 수 없습니다. 실제 기본값은 DEFAULT\_MODEL, DEFAULT\_EFFORT에 의해 결정되지만 상수 정의가 없습니다. |
| lineage.dataflow.json → 첫 페이지 기억 합성 카드 | **조건 누락** | includeMemory는 source === 'legacy'이거나 source === 'all' && !query.focus && offset === 0일 때만 참입니다. 또한 collection 조회에는 this.collection이 필요하며 Promise.allSettled()는 부분 실패를 허용합니다. 현재 문구는 합성 성공을 보장하는 듯합니다. |
| evaluation.workflow.json → 현재 평가 영수증 카드 | **실행 증거 부족** | 발췌는 phase='trained' 영수증을 생성하는 코드입니다. 실제 현재 영수증을 읽은 결과는 아닙니다. weights\_trained=True, model\_promoted=False, final\_test\_evaluated=False는 해당 코드 경로의 기록값입니다. |
| system.architecture.json → choice92 → remote92 | **표현 보완** | "명시 라우트"는 요청마다 직접 선택한다는 의미로 오해될 수 있습니다. selection()은 저장된 설정과 기본값을 사용하며, 자동 모드는 별도 조건입니다. "저장된 모델 선택에 따른 라우팅"이 정확합니다. |

특히 voice.workflow.json의 이미지 경로에는 명확한 제약이 있습니다.

validate\_input()은 Desktop 입력으로 text와 images를 허용하지만, generate\_input()의 로컬 분기는 paths가 있으면 InputUnavailable('assistant\_image\_model\_unsupported')를 발생시킵니다.

따라서 **Desktop 사진 입력 지원과 모든 로컬 모델의 이미지 추론 지원은 구분해야 합니다.**

### 2\. 모델 선택 경계

제공된 automatic\_choice()와 selection()에서 확인되는 동작은 다음과 같습니다.

- **수동 모드:** 저장된 모델 또는 DEFAULT\_MODEL을 선택합니다. selectable과 선택 effort의 지원 여부를 검사합니다.
- **명시 자동 모드:** state\['mode'\] == 'automatic'일 때만 automatic\_choice()가 실행됩니다.
- **자동 후보:** automatic\_candidate가 참이고 ID에 Qwen3.8-27B가 포함되거나 DEFAULT\_MODEL과 일치하는 항목으로 제한됩니다.
- **로컬 우선:** 후보 중 row\['local'\]이 참인 항목을 우선합니다.
- **RAM 경계:** 발췌에는 RAM 측정·판정 코드가 없습니다. automatic\_candidate와 selectable의 산출 과정은 미검증입니다.

추가로 자동 선택에는 조건부 위험이 있습니다. 준비되지 않은 로컬 후보가 automatic\_candidate=True로 남아 있다면, 해당 후보를 먼저 선택한 뒤 selection()에서 오류가 발생할 수 있습니다. 이때 사용 가능한 원격 기본 모델로 재선택하는 로직은 보이지 않습니다. 실제 문제가 발생하는지는 카탈로그 생성 코드를 확인해야 합니다.

자동 모드의 필터는 Astra·Pro·Opus로 자동 확대되지 않도록 제한하는 방향과 부합합니다. 다만 **수동 선택의 전체 허용 목록까지 이 코드만으로 제한한다고 주장할 수는 없습니다.**

### 3\. 검색과 원본 근거

normalization.workflow.json의 본문 우선·최신 요청 보호는 대체로 코드와 일치합니다. 다만 적용 조건을 명확히 해야 합니다.

본문 우선 처리는 \_prefer\_body\_literal\_evidence()가 담당하며 다음 조건에서만 실행됩니다.

- plan이 존재하고 latest\_requested()가 거짓인 경우
- BM25 단독 모드이거나 RRF의 어휘 검색 가중치가 양수인 경우
- 최대 24개 질의 단어에 대해 유효한 본문 검색 계획이 만들어진 경우

명시적 최신 요청은 이 본문 우선 단계를 건너뛰고, 검색 결과가 존재하면 select\_latest()로 시간 조건을 처리합니다.

따라서 **본문 우선은 모든 검색의 절대 규칙이 아니며, 원문 사실성·인용 진실성 판정도 아닙니다.**

kg.\_rrf\_merge(), latest\_requested(), select\_latest()의 호출은 확인되지만, 검색 결과의 실제 정확도와 출처의 진실성은 별도 검증 사항입니다.

### 4\. 나머지 도식의 증거 수준

| 도식 | 독립 판정 |
| --- | --- |
| activity.workflow.json | 저널·cursor·epoch·TTL 및 실제 3D 이벤트 처리 코드는 제공되지 않았습니다. "실제 3D 활동"은 독립 검증 불가입니다. |
| collection.workflow.json | 예약·backoff·snapshot·worker 처리 코드가 없습니다. 등록 및 실행 상태를 검증된 운영 사실로 확장하면 안 됩니다. |
| mcp.workflow.json | scoped MCP 구현과 실제 owned stdio 실행 결과가 없습니다. 범위 밖 거절과 호스트 ACK는 도식의 주장으로만 확인됩니다. |
| normalization.workflow.json | 검색 분기는 일부 확인되지만 NFC, source CAS, 가역 적용·되돌리기 구현은 미검증입니다. |
| recovery.workflow.json | solver checkpoint, late-blur, RAF 0 및 복원 순서의 실제 구현·검증 결과가 없습니다. |
| lineage.dataflow.json | 조건부 knowledge-graph 조회는 확인됩니다. 그러나 sdk92 → memory92의 OSK SDK 출처와 unifiedGraph()의 ID·버전 보존 구현은 제공되지 않았습니다. |
| system.architecture.json | Python 모델 분기는 확인됩니다. Tauri IPC 전체 연결과 osk92 → embed92 직접 연결은 미검증입니다. |
| voice.workflow.json | 입력 검증과 모델 분기는 확인됩니다. wake/VAD/STT/TTS, worker 파일·링크 처리 및 취소·재개 전체 경로는 미검증입니다. |

worker 파일·링크 처리를 Desktop의 validate\_input() 경로와 분리한 설명 자체는 적절합니다. 다만 별도 worker의 실제 입력 스키마와 실행 성공은 이번 코드에 포함되지 않았습니다.

### 5\. 평가·학습·승격 경계

학습 영수증 생성 코드에서 명확히 구분되는 값은 다음과 같습니다.

| 필드 | 코드에 기록되는 값 |
| --- | --- |
| phase | trained |
| objective | dpo |
| weights\_trained | True |
| model\_promoted | False |
| final\_test\_evaluated | False |
| quality\_improvement\_claimed | False |
| adapter\_reload\_verified | True |
| base\_checkpoint\_unchanged | True |

이는 **해당 학습 경로가 완료되어 영수증을 기록할 때의 선언된 상태**입니다. 실제 현재 설치본에서 학습이 실행됐거나 독립 holdout을 통과했다는 증거는 아닙니다.

evaluation92\_4 → evaluation92\_5도 독립 품질 검증 완료 후 승격됐다는 연결로 읽히지 않도록 해야 합니다. 현재의 "제품 승격 보류" 표기는 적절합니다.

### 6\. 최종 검토 의견

현재 도식에서 유지해야 할 정확한 경계는 다음과 같습니다.

- 모든 모델의 실제 추론 성공은 미검증
- 원음, 물리 마이크, wake/echo 및 음성 출처의 실제 검증은 미완료
- 검색 인용과 원문 의미의 독립 gold 검증은 미완료
- OSK 전체 복제·정본 통합 완료는 입증되지 않음
- owned MCP 경로와 전역 호스트 등록·양 호스트 독립 실행은 별개
- 실제 운영 worker 활성화와 예약 실행은 미검증
- DPO 학습 경로와 제품 승격은 별개
- 실제 Chrome 렌더링·배치·물리 성능은 이번 검토 범위 밖

**우선 수정 대상은 voice의 무조건적 검색 경로, lineage의 첫 페이지 합성 조건, system의 기본 모델 단정, evaluation의 현재 실행 영수증 표현입니다.**

나머지는 대체로 설계 의도와 미완료 경계를 구분하고 있으나, 코드가 제공되지 않은 연결은 검증 완료가 아닌 **구현 주장 또는 추가 검증 필요**로 표시해야 합니다.

이번 검토로 운영 활성화, 전체 호스트 복구, 실제 3D 처리, 원문 의미 정확도 또는 제품 승격을 새로 입증한 항목은 없습니다.
