# 27B 파일 리비전과 오프라인 검증

대상은 MLX 변환 배포자인 `ddalcu/Qwen3.8-27B-MLX-Serve-4bit`다. 로컬 핵심 파일 14개(가중치 4개, 토크나이저·설정 10개)가 리비전 `901aa73a1ff5456752c02e55582609e0040ca470`의 게시 메타데이터와 일치한다. 전체 저장소 snapshot이나 과거 다운로드 리비전이 확인됐다는 의미는 아니다. 선택적인 drafter는 설치되어 있지 않다.

이번 명시적 재검사는 18,219,586,850바이트를 9.2159초에 읽었다(n=1). 이는 해시 검사 시간이며 모델 로딩·첫 토큰 지연·RAM 측정이 아니다. 현재 실행 중인 서버의 RAM에 올라간 리비전까지 검증하지 않는다. 모델 이름·파일 존재·디스크 내용·로딩·운영 호출을 별도 증거로 다룬다.

[고정 리비전의 게시 메타데이터](https://huggingface.co/api/models/ddalcu/Qwen3.8-27B-MLX-Serve-4bit/revision/901aa73a1ff5456752c02e55582609e0040ca470?blobs=true)와 [LICENSE](https://huggingface.co/ddalcu/Qwen3.8-27B-MLX-Serve-4bit/resolve/901aa73a1ff5456752c02e55582609e0040ca470/LICENSE)를 HTTPS로 재조회했다. Web 도구에서는 접근되지 않아 표준 Python HTTPS 경로를 사용했다. 게시 라이선스 ID는 `apache-2.0`, LICENSE는 11,544바이트이고 Git blob ID와 SHA-256을 확인했다. 변환 배포 저장소의 게시 조건이며 기반 모델의 별도 출처나 실제 한국어·비전 품질을 대신하지 않는다.

## 재현

```sh
python3 scripts/verify_model_provenance.py \
  --model-dir "$HOME/.mlx-serve/models/ddalcu/Qwen3.8-27B-MLX-Serve-4bit" \
  --manifest docs/architecture/alden-model-provenance-20261004/qwen27b-core-manifest.json
```

명시적으로 실행하는 오프라인 도구다. 앱 부팅·UI polling·모델 선택에 연결하지 않는다. 네트워크·모델 로드·파일 변경은 없다. 4MiB 읽기 버퍼, 파일 수/크기/manifest 상한, 안전한 단일 파일명, regular-file 및 symlink 거부, 읽기 중 내용/경로 교체 검사를 갖춘다. 가중치는 SHA-256, 일반 Git 파일은 `SHA1("blob " + bytes + NUL + content)`로 게시 값과 비교한다.

[기대 manifest](alden-model-provenance-20261004/qwen27b-core-manifest.json) · [실제 검사 결과](alden-model-provenance-20261004/qwen27b-core-verification.json). 손상·길이 차이·누락·symlink/FIFO·읽기 중 파일 교체·잘못된 manifest를 다룬 집중 테스트 6개가 설치된 CPython 3.11.16에서 통과했다.

기존 모델과 다섯 운영 프로세스를 재시작하거나 설정을 변경하지 않았다. Flash-Next의 실제 로딩, 운영 비전 경로, 물리 음성, 소유권 전환, Developer ID 공증과 전체 목표의 남은 항목은 별도다. 이 도구는 빌드 앱의 포함 리소스나 운영 상태를 변경하지 않는 저장소 개발 도구다.

## Flash-Next iQ

배포 ID는 `ddalcu/Qwen3.8-Flash-Next-MLX-Serve-iQ-MLX-3.3bpw`다. [기대 manifest](alden-model-provenance-20261004/flash-iq-core-manifest.json)는 리비전 `b5ee278c61ad445890f1fb718e48c6bb7b780535`의 110개 핵심 파일을 지정한다. 모델 이름으로 다른 양자화를 대체하지 않는다. 가중치와 n-gram 테이블을 포함한 디스크 내용과 실제 MLX resident memory는 구분한다.

```sh
python3 scripts/verify_model_provenance.py \
  --model-dir "$HOME/.mlx-serve/models/ddalcu/Qwen3.8-Flash-Next-MLX-Serve-iQ-MLX-3.3bpw" \
  --manifest docs/architecture/alden-model-provenance-20261004/flash-iq-core-manifest.json
```

게시 라이선스는 `other / qwen-community-1.0`이며 27B의 Apache 2.0과 다르다. 고정 리비전의 [라이선스 원문](https://huggingface.co/ddalcu/Qwen3.8-Flash-Next-MLX-Serve-iQ-MLX-3.3bpw/resolve/b5ee278c61ad445890f1fb718e48c6bb7b780535/LICENSE)을 다시 조회하고 Git blob/SHA-256을 대조했다. 원문은 복사 시 고지 보존, 큰 상업 제품의 모델명 표시, MaaS/AI Work Assistant 사업의 상업적 사용을 위한 별도 라이선스 및 제한된 내부 사용 예외를 명시한다. Alden의 특정 사업·배포에 해당하는지 판정한 결과로 취급하지 않는다. 현재 앱 배포물에는 모델 가중치를 포함하지 않는다.

현재 관측은 51,837,992,960바이트의 usable estimate와 64,424,509,440바이트의 기존 iQ admission 기준이다. 부족분은 12,586,516,480바이트(약 11.72GiB)다. 파일 검사가 기준을 충족시키거나 로딩을 증명하지 않는다. 현재 shared server는 `model_owner_unmanaged`이며 소유권 증거가 없다. publisher README의 `--skip-mem-preflight` 안내를 실행하거나 제품의 메모리 가드를 낮추지 않았다. 프로세스·설정·모델 선택은 보존했다.

Flash iQ 파일 검사는 **110/110 일치**, 총 **86,355,107,967바이트**, **40.8529초(n=1)**다. 모델 로드·파일 변경·검사기의 네트워크 요청은 0건이다. [전체 검사 결과](alden-model-provenance-20261004/flash-iq-core-verification.json).
