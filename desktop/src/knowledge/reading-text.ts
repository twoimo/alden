const NUMERIC_LINE = /^\d{1,4}$/;

/** Split a trailing run of digit-only lines from reading text.
 * Stored source stays unchanged. A body that is only digits stays intact.
 */
export function separateReadingNoise(body: string): { reading: string; separated: string[] } {
  const lines = body.replace(/\r\n/g, '\n').split('\n');
  let end = lines.length;
  const separated: string[] = [];
  while (end > 0) {
    const trimmed = lines[end - 1].trim();
    if (trimmed === '') { end--; continue; }
    if (!NUMERIC_LINE.test(trimmed)) break;
    separated.push(trimmed);
    end--;
  }
  const kept = lines.slice(0, end);
  if (!kept.some(line => /[A-Za-z가-힣]/.test(line))) return { reading: body, separated: [] };
  return { reading: kept.join('\n').replace(/[\n\s]+$/g, ''), separated: separated.reverse() };
}

export function isNumericToken(value: string): boolean {
  return NUMERIC_LINE.test(value.trim());
}
