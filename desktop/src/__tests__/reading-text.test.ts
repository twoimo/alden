import { describe, expect, it } from 'vitest';
import { isNumericToken, separateReadingNoise } from '../knowledge/reading-text';

describe('reading noise separation', () => {
  it('drops a trailing run of digit-only lines and keeps the numbered article', () => {
    const body = '서류에서 높은 점수를 받는 지원자 특징.\n1. 채용공고의 JD를 그대로 옮겨 쓰지 않는다.\n1\n2\n6\n1\n3';
    const result = separateReadingNoise(body);
    expect(result.reading.endsWith('않는다.')).toBe(true);
    expect(result.separated).toEqual(['1', '2', '6', '1', '3']);
    expect(body.endsWith('3')).toBe(true);
  });
  it('does not blank a note that is only numbers', () => {
    expect(separateReadingNoise('1\n2\n6')).toEqual({ reading: '1\n2\n6', separated: [] });
    expect(isNumericToken('12')).toBe(true);
    expect(isNumericToken('1. 기준')).toBe(false);
  });
});
