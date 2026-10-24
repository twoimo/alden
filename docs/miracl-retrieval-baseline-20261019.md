# 실제 E5와 사람 판정으로 측정한 한국어 검색 기준선

2026-10-19. **213개 공개 질의 전체의 측정과 지표 검산을 완료했다.** Alden의 개인 자료 정확성·의미 군집·동일인 판정·최적 검색 정책을 인증한 결과는 아니다. 기존 W-A06의 공개 benchmark 실행이며, 제품 가중치·모델·원문·검색 색인·그래프 알고리즘은 바꾸지 않았다. 설치본은0.3.154/runtime `659d3b5cfea08fe26e856d55cdd1f5dac7fd67a8`을 유지한다.

## 자료와 평가 범위

[MIRACL 공식 프로젝트](https://github.com/project-miracl/miracl)와 [공식 topics/qrels](https://huggingface.co/datasets/miracl/miracl)의 한국어 dev를 사용했다. 저자가 제공한 관련성 판정이며, 모델이 생성하거나 이 작업에서 자체 승인한 정답이 아니다. **213질의·3,057판정(관련547/비관련2,510) 전체**를 사용했으며 점수를 본 뒤 질의를 고르거나 방법을 조정하지 않았다.

- topics/qrels revision: `5be20db9509754dadad47689368639fcec739c00`.
- [공식 corpus](https://huggingface.co/datasets/miracl/miracl-corpus) revision: `d921ec7e349ce0d28daf30b2da9da5ee698bef0d`.
- 세 gzip 파일225,970,548bytes를 공식 LFS SHA256·크기와 대사하고, 전체1,486,752passage를 읽어 판정에 등장하는 문서2,835개를 추출했다. 해제된673,954,773bytes와 줄별 원본 hash/좌표를 기록했다.
- 추출 JSONL SHA256: `405d6d0ca8398204d23df163020f3beb050683e7d903b43294355bd065835898`.
- 이2,835문서의 합집합이 검색 corpus다. **148만 문서 전체 검색 점수나 공식 leaderboard 점수가 아니다.** 각 질의에는 다른 질의에서만 판정된 문서도 섞인다.

관련성 판정과 사실의 참은 다르다. 미판정 문서는 `unknown`이다. 표준 qrels 지표 계산에서는 gain0으로 처리하지만 비관련이라는 새 판정을 만들지 않았고, 상위10개 중 미판정 문서 수도 별도로 집계했다. 학습 노출과 완전히 독립된 비공개 holdout인지도 검증하지 않았다.

Hub card의 Apache-2.0 표시와 Wikipedia 원문의 attribution/share-alike 조건을 구분한다. corpus·판정 원문·응답 벡터는 로컬 감사 폴더에 보존하고 Git이나 외부 연구에 업로드하지 않았다. API viewer의 topics 조회는404, corpus filter는 read timeout이었다. 이들을 빈 자료나 서비스 종료로 해석하지 않고, 고정 revision의 공식 원본 파일을 사용했다.

## 실제 실행 경로

설치 Resources의 collection/검색/E5 어댑터를 사용했다. 기존 warm listener의 같은 PID와 고정 모델 `mlx-community/multilingual-e5-small-mlx@5030c7625865046d350eeea28f427d80353d0ac0`을 확인했다. 새 모델·서비스·유료 용량을 시작하거나 다른 encoder로 대체하지 않았다.

원문을 별도 audit SQLite에 ingest했다. 문서 ID2,835개가 그대로 남았으며 label/body의 원래 corpus 대비 변형은 모두0개다. 현재 producer의 label+body, char256/stride192, 단위화·길이 가중 pooling을 사용했다. 실제 query213개와 중복 제거된 passage window5,542개를 **720 POST/5,755입력**으로 인코딩했다. 실행 전 상태 점검의1 POST/1입력은 별도다.

12개 명시적 작업을 순차 실행했다. 각각 최대64요청·요청당8입력·120초이며 전체 한도768요청/6,144입력 안이었다. 설치 어댑터의 요청 상수·모델 준비 가드를 바꾸지 않았다. 매 batch에서 같은 listener·모델·비상 중단·음성 사용 중 여부를 확인했다. 실제 응답을 checkpoint에 보존했다.

설치 producer는 이 실제 응답 cache를 재사용해 audit SQLite를 색인했다. 저장된 float32벡터2,835개가 기준 pooling과 **2,835/2,835 byte-identical**이었다. 질의별 제품 검색도 같은 실제 query vector를 재사용했으며, 추가 인코딩 없이213건 모두 `search_mode=rrf`로 실행됐다. 캐시를 쓴 producer/consumer 경로와 live HTTP 인코딩 단계를 구분한다.

## 결과

질의별 macro 평균이며 모두213질의를 분모로 사용했다. 단순 BM25는 설치 `CollectionStore.search`의 token 경로다. Dense는 같은 실제 producer 벡터의 cosine 순위다. raw RRF는 각 retriever의40후보를 k60/동일 가중치로 융합한다. 제품 consumer는 한국어 literal·본문 근거 우선·버전/권한/내용 검증·최대10항목/16,000자 경로를 그대로 실행한다. RRF의 후보 합집합은 최대80개이므로 네 방법의 계산 비용이 같다고 주장하지 않는다.

| 방법 | Recall@10 | nDCG@10 | MRR@10 | 상위10 미판정 평균 |
|---|---:|---:|---:|---:|
| BM25 token | .6535 | .5252 | .5573 | 2.9765 |
| E5 Dense pooled | .8512 | .7213 | .7525 | 1.8545 |
| raw RRF | .8392 | .6828 | .7098 | 1.7042 |
| 현재 제품 RRF consumer | .8383 | .6802 | .7047 | 1.6761 |

BM25는 평균9.8169건을 반환했고 다른 세 방법은10건을 반환했다. 빈 결과를 제외하거나 가짜 항목을 채우지 않았다. @1/@3 및 질의별 순위·미판정 노출도 보존했다.

현재 자료에서는 Dense와 제품 consumer의 nDCG@10 차이가 **.04109**였다. 질의별로 Dense가 높은85건·낮은65건·같은63건이다. 본문 literal 우선 정책은6질의에서 관측됐다. 이 비교만으로 원인을 그 정책 하나에 귀속하거나, 기본 가중치를 바꾸거나, Dense를 최종 승자로 승격하지 않는다. fusion·후보 단계와 consumer 단계, 실제 개인 corpus·새 holdout·비용을 분리한 후속 검증이 필요하다. 공개 benchmark를 이미 본 회귀 자료와 이후의 미사용 평가셋을 구분한다.

## 독립 지표 검산과 복구

[NIST 공식 trec_eval](https://github.com/usnistgov/trec_eval)의 최신 stable release `v10.0`, commit `f4253652c8efd0d86ddffd0d163cc0a0f813111a`를 별도 폴더에서 빌드하고 공식 quicktest를 통과했다. 해당 tag의 Makefile과 binary 버전 표시는 `10.0-rc3`인 점도 그대로 기록했다. 시스템 설치나 upstream 수정은 없다.

네 run의 상위10개를 TREC 형식으로 내보냈다. 동점 정렬 차이를 피하려고 export score만 엄격한 순위 보존 정수로 기록했으며, 원래 cosine/RRF 정책이나 검색 순위는 바꾸지 않았다. `-c -q`와 원래 qrels를 사용해 결과 없는 질의도 분모에 포함했다. 질의별·전체 Recall@1/3/10, nDCG@1/3/10, MRR@10의 **5,992값 전부**가 원래 계산과 일치했다. 출력4자리 반올림의 허용 오차는.000051이다. NIST 지표 검산은 ChatGPT/Gemini 교차감사를 대신하지 않는다.

최초 실행은213질의와 네 run export를 완료한 뒤 audit target을 pause하는 마지막 단계에서 실패했다. 읽기 전용으로 연 객체에 쓰기를 시도한 것이 원인이다. 원래 script/protocol/로그/질의별 결과를 보존하고, **소유한 audit store만 mutable하게 열도록** 한 줄을 고쳤다. 원래213건과 벡터를 재사용해 정리를 완료했으며 복구 중 질의 재실행·모델 요청은0개다.

그 뒤 유지 도구에는 helper hash 결속과 완료/부분 질의 checkpoint 재실행 거절을 추가했다. 기존 실험의 protocol을 새 코드로 소급해 바꾸지 않았다. 임베딩·지표 함수가 원본과 같음을 AST로 대사했다. 실제 CLI의 완료·부분 결과·출력 범위 이탈·입력 hash 손상·helper 불일치·잘못된 shard 거절6건이 통과했고 원래 근거 파일은 그대로다.

설치 파일77개/mode, strict/deep 서명과 pyc 부재, 모델·등록·중단 설정,7개 수집 대상/정책, 기존 E5·MLX 프로세스를 전후 대사했다. 사용자 자료·운영 검색 색인·큐·모델 선택을 변경하지 않았다. latency p95·GPU·배터리나 비용 절감은 이 실험에서 측정하지 않았다.

## 새 실행과 근거 위치

export와 평가 도구는 `docs/audits/miracl_corpus.py`, `docs/audits/miracl_retrieval.py`다. **새 폴더**에서 준비하고 protocol을 고정한 뒤 실행한다. 준비 완료·실제 인코딩·producer 저장·consumer 조회·지표 검산을 별도 단계로 유지한다.

```sh
python3 -B docs/audits/miracl_corpus.py --output-dir outputs/miracl-ko-dataset-NEW
ALDEN_AUDIT_PYTHON="/Users/twoimo/Library/Application Support/openkakao/runtimes/menubar/bin/python3.11"
"$ALDEN_AUDIT_PYTHON" -B docs/audits/miracl_retrieval.py --phase prepare --dataset-dir outputs/miracl-ko-dataset-NEW --output-dir outputs/miracl-run-NEW
"$ALDEN_AUDIT_PYTHON" -B scripts/manage-alden-local-embedding-launchd.py status > outputs/miracl-run-NEW/manager-status.json
for alden_shard in {0..11}; do
  "$ALDEN_AUDIT_PYTHON" -B docs/audits/miracl_retrieval.py --phase embed --output-dir outputs/miracl-run-NEW --shard "$alden_shard" || break
done
"$ALDEN_AUDIT_PYTHON" -B docs/audits/miracl_retrieval.py --phase evaluate --output-dir outputs/miracl-run-NEW
```

shard 수는 새 protocol의 값을 확인한다. 실패·미완료 checkpoint를 새 요청으로 덮지 말고 원래 근거부터 대사한다. 현재 완료 결과를 다시 인코딩하지 않는다. export 도구의 새 코드도 원래 gzip을 재사용해 전체 파싱했고 같은 선택 문서 hash를 얻었다.

근거는 worktree `outputs/retrieval-human-20261019/`의 `protocol.json`, `embedding-shard-*.jsonl`, `queries.jsonl`, `results.json`, `recovery-and-oracle.json`, `source-lineage.json`, `controls.json`, `preservation-final.json`이다. 원래 data export와 NIST source/quicktest는 task root의 같은 이름 폴더에 있다. 최종 commit/PR/CI와 근거 hash는 후속 delivery receipt에서 대사한다.

개인 실사용 질의·사람 이중 판정·동일인/시간 gold·Graphify Gemini 반대감사·최종 ChatGPT 역검증·전체18목표는 계속 열린 상태다. 이번 실제 공개 측정은 이들 완료의 대체물이 아니다.
