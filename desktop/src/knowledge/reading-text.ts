const NUMERIC_LINE = /^\d{1,4}$/;
const NOISE_LINE = /^(?:\d{1,4}|\/)$/;
const INLINE_TAIL = /\s+\d{1,4}\s+\/\s+\d{1,4}(?:\s+\d{1,4})+\s*$/;

/** Split trailing page-counter chrome from reading text.
 * Stored source stays unchanged. A body that is only digits stays intact.
 */
export function separateReadingNoise(body: string): { reading: string; separated: string[] } {
  const lines = body.replace(/\r\n/g, '\n').split('\n');
  const separated: string[] = [];
  let cursor = lines.length;
  const tail: string[] = [];
  while (cursor > 0) {
    const trimmed = lines[cursor - 1].trim();
    if (trimmed === '') { cursor--; continue; }
    if (!NOISE_LINE.test(trimmed)) break;
    tail.push(trimmed);
    cursor--;
  }
  let end = lines.length;
  if (tail.some(token => NUMERIC_LINE.test(token))) {
    separated.push(...tail);
    end = cursor;
  }
  const kept = lines.slice(0, end);
  if (kept.length > 0) {
    const last = kept[kept.length - 1].replace(/\u00a0/g, ' ');
    const match = INLINE_TAIL.exec(last);
    if (match && /[A-Za-z가-힣]/.test(last.slice(0, match.index))) {
      separated.push(...last.slice(match.index).trim().split(/\s+/).reverse());
      kept[kept.length - 1] = last.slice(0, match.index).replace(/[\s\u00a0]+$/g, '');
    }
  }
  if (!kept.some(line => /[A-Za-z가-힣]/.test(line))) return { reading: body, separated: [] };
  return { reading: kept.join('\n').replace(/[\n\s\u00a0]+$/g, ''), separated: separated.reverse() };
}

export function isNumericToken(value: string): boolean {
  return NUMERIC_LINE.test(value.trim());
}
