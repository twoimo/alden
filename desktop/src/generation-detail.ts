const record = (value: unknown): Record<string, unknown> =>
  value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
const model = (value: unknown): string | null => typeof value === 'string'
  && /^[A-Za-z0-9][A-Za-z0-9._+:/-]{0,127}$/.test(value)
  && !value.includes('://') && !/^(?:sk-|ghp_|github_pat_|xox|data:|file:)/i.test(value)
  && value.split('/').every(part => part && part !== '.' && part !== '..') ? value : null;
const count = (value: unknown): number | null => typeof value === 'number'
  && Number.isSafeInteger(value) && value >= 0 ? value : null;

/** Bounded user-facing evidence, with observed and requested models distinct. */
export function generationDetail(value: unknown): string[] {
  const generation = record(value);
  if (generation.schema_version !== 1) return [];
  const selected = record(generation.selection), response = record(generation.response);
  const actual = model(response.model_id), requested = model(selected.model_id);
  const lines: string[] = [];
  if (actual) lines.push(`응답 모델 · ${actual}`);
  else if (requested) lines.push(`요청 모델 · ${requested} · 응답 모델 미확인`);
  const timing = record(generation.timing);
  if (typeof timing.elapsed_ms === 'number' && Number.isFinite(timing.elapsed_ms)
      && timing.elapsed_ms >= 0 && timing.elapsed_ms <= 86_400_000
      && ['routed_generation_call', 'local_call_to_stream_end'].includes(String(timing.elapsed_scope))) {
    lines.push(`응답 처리 · ${(timing.elapsed_ms / 1000).toFixed(2)}초`);
  }
  const tokens = count(record(response.usage).total_tokens);
  if (tokens !== null) lines.push(`관측 사용량 · ${tokens.toLocaleString('ko-KR')} 토큰`);
  const context = record(generation.image_context);
  if (Array.isArray(context.selected)) {
    const images = context.selected.slice(0,4).map(record);
    const current = images.filter(image => image.origin === 'current').length;
    const previous = images.filter(image => image.origin === 'previous').length;
    if (current || previous) lines.push(`전달한 사진 · 이번 ${current}장 · 이전 ${previous}장`);
    const omitted = record(context.omitted);
    const missing = ['capacity','byte_budget','unavailable'].reduce((sum,key) => sum + Math.min(count(omitted[key]) ?? 0,8),0);
    if (missing) lines.push(`사진 ${missing}장 제외 · 한도 또는 접근 실패`);
  }
  const links = record(generation.link_context);
  const parsed = count(links.parsed), metadata = count(links.metadata_only);
  if (parsed !== null && parsed <= 4 && metadata !== null && metadata <= parsed) {
    lines.push(`링크 자료 · 응답 텍스트 ${parsed - metadata}개 · 메타데이터 ${metadata}개`);
    if (Array.isArray(links.unavailable) && links.unavailable.length <= 4 && links.unavailable.length) {
      lines.push(`링크 ${links.unavailable.length}개 접근 실패 · 본문 미확인`);
    }
    const omitted = record(links.omitted);
    const missing = count(omitted.unavailable_snapshot);
    if (missing !== null && missing > 0 && missing <= 2) lines.push(`이전 링크 ${missing}개 제외 · 저장 자료 접근 실패`);
    const collected = count(links.collected_posts);
    if (collected !== null && 0 < collected && collected <= 4) {
      lines.push(`수집된 게시물 ${collected}개 · 공유 페이지 본문은 아님`);
    }
  }
  return lines;
}
