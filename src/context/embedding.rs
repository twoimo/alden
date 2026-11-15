//! The fixed-width message vector: the pluggable `Embedder`, the default
//! local hash embedder, and the vector's byte encoding in SQLite.

pub(super) const VECTOR_DIM: usize = 128;

/// 임베딩 공급원 추상화(착탈식 임베딩 인터페이스). 검색이 사용하는 벡터
/// 표현을 이 트레이트 뒤로 감춰, 기본 로컬 구현 외에 로컬 임베딩 모델이나
/// ANN 인덱스를 붙일 수 있게 한다. 기본 구현은 외부 네트워크 없이 동작한다.
pub trait Embedder {
    /// 임베딩 벡터의 차원 수.
    fn dim(&self) -> usize;
    /// 입력 문자열을 벡터로 인코딩한다.
    fn embed(&self, text: &str) -> Vec<f32>;
    /// 외부 네트워크 사용 없이 로컬에서만 동작하는지 여부.
    /// `Dataset_Builder`는 반드시 `true`인 임베더만 허용한다(R3-12).
    fn is_local_only(&self) -> bool;
}

/// 기본 임베더. 기존 결정적 해시 벡터(`encode_vector`)를 그대로 감싼다.
/// 외부 없이 동작하며 재현 가능한(결정적) 벡터를 만든다. 이 구현을 기본으로
/// 주입하면 검색 동작이 리팩터링 이전과 완전히 동일하게 유지된다.
#[derive(Debug, Clone, Copy, Default)]
pub struct LocalHashEmbedder;

impl LocalHashEmbedder {
    pub fn new() -> Self {
        LocalHashEmbedder
    }
}

impl Embedder for LocalHashEmbedder {
    fn dim(&self) -> usize {
        VECTOR_DIM
    }

    fn embed(&self, text: &str) -> Vec<f32> {
        encode_vector(text)
    }

    fn is_local_only(&self) -> bool {
        true
    }
}

pub(super) fn encode_vector(text: &str) -> Vec<f32> {
    let mut vector = vec![0.0; VECTOR_DIM];
    for token in text
        .split(|c: char| !c.is_alphanumeric())
        .filter(|t| !t.is_empty())
        .map(str::to_lowercase)
    {
        for window in token
            .as_bytes()
            .windows(3)
            .chain(token.as_bytes().windows(2))
        {
            let mut hash = 2166136261u32;
            for byte in window {
                hash = (hash ^ u32::from(*byte)).wrapping_mul(16777619);
            }
            vector[(hash as usize) % VECTOR_DIM] += if hash & 1 == 0 { 1.0 } else { -1.0 };
        }
    }
    let norm = vector.iter().map(|v| v * v).sum::<f32>().sqrt();
    if norm > 0.0 {
        for value in &mut vector {
            *value /= norm;
        }
    }
    vector
}

pub(super) fn cosine(a: &[f32], b: &[f32]) -> f32 {
    a.iter().zip(b).map(|(l, r)| l * r).sum()
}

pub(super) fn vector_to_bytes(vector: &[f32]) -> Vec<u8> {
    vector.iter().flat_map(|v| v.to_le_bytes()).collect()
}

pub(super) fn bytes_to_vector(bytes: &[u8]) -> Vec<f32> {
    bytes
        .chunks_exact(4)
        .map(|c| f32::from_le_bytes(c.try_into().unwrap()))
        .collect()
}
