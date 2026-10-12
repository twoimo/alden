import type { fetchSettingsAction } from '../runtime';

/** The selector value is a UI discriminator, never a project ID by itself.
 * In particular, "project:all" means the single project named "all". */
export type QualityQuery = { scope: 'all' } | { scope: 'project'; project: string };

type RequestedScope = { requested_scope: 'all' | 'project' | null; requested_project: string | null };
type QualityCounts = {
  memberships: number;
  project_current_versions: number;
  relations: number;
  distinct_current_documents: number;
  bytes_examined: number;
  noncontent_current_versions: number;
  metadata_only_current_versions: number;
  unread_body_marker_current_versions: number;
  metadata_description_current_versions: number;
};
type QualityCandidates = {
  same_raw_bytes: { groups: number; status: 'hash_references_only' };
  same_normalized_search_text: { groups: number; status: 'review_candidate' };
  cross_context_search_text: { groups: number; status: 'review_candidate' };
};
type QualityErrorCode = keyof typeof ERROR_TEXT;
export type QualitySuccess = {
  ok: true;
  schema: 3;
  scope: RequestedScope & {
    projects: string[];
    coverage: 'permitted_current_collection_memberships';
    filters: 'project_only';
    source_verification: 'hash_references_only';
  };
  checked_at: number;
  elapsed_ms: number;
  counts: QualityCounts;
  findings: Record<string, number>;
  duplicate_candidates: QualityCandidates;
  metadata_matches: Record<'within_context' | 'cross_context', { groups: number; status: 'metadata_match_not_content' }>;
  content_evidence: { policy: 'source-content-evidence-v3'; status: 'source_declared_exclusions_not_semantic_verdict' };
  numeric_review_candidates: { mixed_vector_dimensions: { profiles: number; status: 'review_candidate' } };
  normalization: { method: 'NFC_line_endings_preserving_body_spacing'; semantic_validity: 'unverified' };
};
export type QualityFailure = { ok: false; schema: 3; reason: QualityErrorCode; scope: RequestedScope };
export type QualityResult = QualitySuccess | QualityFailure;

const ERROR_TEXT = {
  quality_scope_unavailable: '선택한 범위의 품질 자료를 확인할 수 없습니다.',
  quality_scan_budget: '허용된 검사량에 도달해 결과를 제공할 수 없습니다.',
  quality_time_budget: '검사 시간 제한으로 결과를 제공할 수 없습니다.',
  quality_cancelled: '품질 확인 요청이 취소되었습니다.',
  quality_report_unavailable: '품질 보고서를 가져오지 못했습니다.',
  quality_query_invalid: '품질 확인 요청 범위가 올바르지 않습니다.',
} as const;

// Reviewed keys from alden_graph_quality.py. Unknown keys get a generic label;
// neither findings nor backend identifiers can supply displayed text.
const FINDING_LABELS: Record<string, string> = {
  membership_identity_invalid: '수집 소속 식별값 오류',
  missing_document: '문서 참조 누락',
  missing_or_foreign_current_version: '현재 버전 참조 누락·불일치',
  availability_conflict: '문서 가용 상태 불일치',
  membership_context_invalid: '수집 범위 맥락 오류',
  stable_identity_mismatch: '고정 문서 식별값 불일치',
  stable_identity_invalid: '고정 문서 식별값 오류',
  collection_time_invalid: '수집 시각 오류',
  version_text_invalid: '현재 버전 텍스트 오류',
  version_binding_invalid: '현재 버전 연결 정보 오류',
  normalization_mismatch: 'NFC·줄바꿈 정규화 차이',
  projection_hash_mismatch: '검색 투영 해시 불일치',
  version_identity_mismatch: '버전 식별값 불일치',
  metadata_invalid: '기록 메타데이터 오류',
  source_reference_invalid: '원본 참조 형식 오류',
  source_blob_invalid: '보관된 원본 파일 오류',
  retained_text_binding_invalid: '보존 텍스트 연결 불일치',
  retained_text_source_mismatch: '보존 텍스트와 원본 불일치',
  vector_text_binding_invalid: '벡터 텍스트 연결 불일치',
  vector_profile_invalid: '벡터 프로필 정보 오류',
  vector_shape_invalid: '벡터 형상 오류',
  vector_nonfinite: '유한하지 않은 벡터 값',
  vector_not_unit: '벡터 단위 길이 차이',
  relation_identity_invalid: '관계 식별값 오류',
  relation_type_invalid: '관계 유형 오류',
  relation_endpoint_unavailable_in_scope: '허용 범위에서 관계 끝점 누락',
  relation_evidence_invalid: '관계 근거 형식 오류',
  relation_evidence_hash_mismatch: '관계 근거 해시 불일치',
  relation_evidence_empty: '관계 근거 비어 있음',
};

function object(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null;
}
const count = (value: unknown): value is number => typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;
const finiteNonnegative = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value) && value >= 0;

function checkedDate(value: unknown): Date | null {
  // Backend contract: Unix seconds (possibly fractional), in a plausible
  // modern epoch range. Epoch milliseconds and malformed units fail closed.
  if (!finiteNonnegative(value) || value < 946_684_800 || value >= 4_102_444_800) return null;
  const date = new Date(value * 1000);
  return Number.isFinite(date.getTime()) ? date : null;
}

export function qualityQueryFromSelection(selection: string): QualityQuery | null {
  if (selection === 'all') return { scope: 'all' };
  if (selection.startsWith('project:') && selection.length > 'project:'.length) {
    return { scope: 'project', project: selection.slice('project:'.length) };
  }
  return null;
}

function matchesScope(value: Record<string, unknown>, query: QualityQuery, allowNull: boolean): boolean {
  if (allowNull && value.requested_scope === null && value.requested_project === null) return true;
  return value.requested_scope === query.scope
    && value.requested_project === (query.scope === 'project' ? query.project : null);
}

/** Fail closed on missing, wrong-typed, mismatched, or partial reports.
 * No untrusted identifiers or extra backend fields leave this parser. */
export function parseCollectionQuality(value: unknown, query: QualityQuery): QualityResult | null {
  const row = object(value), scope = object(row?.scope);
  if (!row || row.schema !== 3 || !scope) return null;

  if (row.ok === false) {
    if (!Object.hasOwn(ERROR_TEXT, String(row.reason)) || !matchesScope(scope, query, true)) return null;
    return {
      ok: false, schema: 3, reason: row.reason as QualityErrorCode,
      scope: { requested_scope: scope.requested_scope as RequestedScope['requested_scope'],
        requested_project: scope.requested_project as RequestedScope['requested_project'] },
    };
  }

  const counts = object(row.counts), findings = object(row.findings);
  const duplicates = object(row.duplicate_candidates), numeric = object(row.numeric_review_candidates);
  const raw = object(duplicates?.same_raw_bytes);
  const normalized = object(duplicates?.same_normalized_search_text);
  const cross = object(duplicates?.cross_context_search_text);
  const mixed = object(numeric?.mixed_vector_dimensions);
  const normalization = object(row.normalization);
  const evidence = object(row.content_evidence), metadata = object(row.metadata_matches);
  const metadataWithin = object(metadata?.within_context), metadataCross = object(metadata?.cross_context);
  if (row.ok !== true || !matchesScope(scope, query, false)
    || scope.coverage !== 'permitted_current_collection_memberships'
    || scope.filters !== 'project_only' || scope.source_verification !== 'hash_references_only'
    || !Array.isArray(scope.projects) || scope.projects.length < 1 || scope.projects.length > 16
    || new Set(scope.projects).size !== scope.projects.length
    || !scope.projects.every(p => typeof p === 'string' && p.length > 0 && p.length <= 128)
    || (query.scope === 'project' && (scope.projects.length !== 1 || scope.projects[0] !== query.project))
    || !checkedDate(row.checked_at) || !finiteNonnegative(row.elapsed_ms)
    || !counts || !findings || !duplicates || !numeric || !raw || !normalized || !cross || !mixed || !normalization
    || !evidence || !metadataWithin || !metadataCross
    || !(['memberships', 'project_current_versions', 'relations', 'distinct_current_documents', 'bytes_examined',
      'noncontent_current_versions', 'metadata_only_current_versions', 'unread_body_marker_current_versions',
      'metadata_description_current_versions'] as const)
      .every(key => count(counts[key]))
    || (counts.noncontent_current_versions as number) > (counts.project_current_versions as number)
    || (counts.metadata_only_current_versions as number) + (counts.unread_body_marker_current_versions as number)
      + (counts.metadata_description_current_versions as number)
      !== counts.noncontent_current_versions
    || evidence.policy !== 'source-content-evidence-v3' || evidence.status !== 'source_declared_exclusions_not_semantic_verdict'
    || !count(metadataWithin.groups) || metadataWithin.status !== 'metadata_match_not_content'
    || !count(metadataCross.groups) || metadataCross.status !== 'metadata_match_not_content'
    || Object.keys(findings).length > 64
    || !Object.entries(findings).every(([key, value]) => /^[a-z][a-z0-9_]{0,63}$/.test(key) && count(value))
    || !count(raw.groups) || raw.status !== 'hash_references_only'
    || !count(normalized.groups) || normalized.status !== 'review_candidate'
    || !count(cross.groups) || cross.status !== 'review_candidate'
    || !count(mixed.profiles) || mixed.status !== 'review_candidate'
    || normalization.method !== 'NFC_line_endings_preserving_body_spacing'
    || normalization.semantic_validity !== 'unverified') return null;

  return {
    ok: true, schema: 3,
    scope: {
      requested_scope: scope.requested_scope as QualitySuccess['scope']['requested_scope'],
      requested_project: scope.requested_project as string | null,
      projects: scope.projects as string[],
      coverage: 'permitted_current_collection_memberships', filters: 'project_only',
      source_verification: 'hash_references_only',
    },
    checked_at: row.checked_at as number, elapsed_ms: row.elapsed_ms as number,
    counts: counts as QualityCounts,
    findings: Object.fromEntries(Object.entries(findings)) as Record<string, number>,
    duplicate_candidates: {
      same_raw_bytes: { groups: raw.groups as number, status: 'hash_references_only' },
      same_normalized_search_text: { groups: normalized.groups as number, status: 'review_candidate' },
      cross_context_search_text: { groups: cross.groups as number, status: 'review_candidate' },
    },
    numeric_review_candidates: { mixed_vector_dimensions: { profiles: mixed.profiles as number, status: 'review_candidate' } },
    metadata_matches: {
      within_context: { groups: metadataWithin.groups as number, status: 'metadata_match_not_content' },
      cross_context: { groups: metadataCross.groups as number, status: 'metadata_match_not_content' },
    },
    content_evidence: { policy: 'source-content-evidence-v3', status: 'source_declared_exclusions_not_semantic_verdict' },
    normalization: { method: 'NFC_line_endings_preserving_body_spacing', semantic_validity: 'unverified' },
  };
}

const isNormalizationFinding = (key: string): boolean => /normaliz|nfc|line_ending|unicode/i.test(key);
function findingTotal(entries: Array<[string, number]>): string {
  return entries.reduce((sum, [, value]) => sum + BigInt(value), 0n).toLocaleString('ko-KR');
}

/** A click-only report: no timer, background scans, original blobs, or source identifiers. */
export class KnowledgeQualityPanel {
  private readonly controls = new AbortController();
  private readonly infoObserver: MutationObserver;
  private readonly selector: HTMLSelectElement;
  private readonly info: HTMLDetailsElement;
  private readonly button: HTMLButtonElement;
  private readonly scope: HTMLElement;
  private readonly status: HTMLElement;
  private readonly report: HTMLElement;
  private selection: string;
  private active = false;
  private disposed = false;
  private epoch = 0;
  private pendingEpoch: number | null = null;

  constructor(private readonly load: typeof fetchSettingsAction, private readonly root: Document = document) {
    this.selector = root.getElementById('knowledge-project') as HTMLSelectElement;
    this.info = root.getElementById('knowledge-info') as HTMLDetailsElement;
    this.button = root.getElementById('knowledge-quality-run') as HTMLButtonElement;
    this.scope = root.getElementById('knowledge-quality-scope') as HTMLElement;
    this.status = root.getElementById('knowledge-quality-status') as HTMLElement;
    this.report = root.getElementById('knowledge-quality-report') as HTMLElement;
    this.selection = this.selector.value;
    this.button.addEventListener('click', () => { void this.request(); }, { signal: this.controls.signal });
    this.info.addEventListener('toggle', () => { if (!this.info.open) this.invalidate(); }, { signal: this.controls.signal });
    // Even an immediate close-and-reopen invalidates the earlier request.
    // Observing only removal (oldValue is the present attribute) avoids
    // invalidating a click made just after the user first opens the details.
    this.infoObserver = new MutationObserver(changes => {
      if (changes.some(change => change.oldValue !== null)) this.invalidate();
    });
    this.infoObserver.observe(this.info, { attributes: true, attributeFilter: ['open'], attributeOldValue: true });
    this.describeSelection();
    this.updateButton();
  }

  start(): void {
    if (this.disposed || this.active) return;
    this.active = true;
    this.syncSelection();
    this.updateButton();
  }

  stop(): void {
    if (!this.active && this.pendingEpoch === null) return;
    this.active = false;
    this.invalidate();
  }

  syncSelection(): void {
    if (this.disposed || this.selection === this.selector.value) return;
    this.selection = this.selector.value;
    this.invalidate();
    this.describeSelection();
  }

  dispose(): void {
    if (this.disposed) return;
    this.stop();
    this.disposed = true;
    this.epoch++;
    this.infoObserver.disconnect();
    this.controls.abort();
    this.updateButton();
  }

  private current(epoch: number, selection: string): boolean {
    return this.active && !this.disposed && this.info.open && this.epoch === epoch
      && this.selection === selection && this.selector.value === selection
      && !this.root.getElementById('settings-page-memory')?.hidden;
  }

  private updateButton(): void {
    this.button.disabled = !this.active || this.disposed || !qualityQueryFromSelection(this.selection)
      || this.pendingEpoch !== null;
  }

  private describeSelection(): void {
    const query = qualityQueryFromSelection(this.selection);
    if (!query) {
      this.scope.textContent = this.selection === 'legacy'
        ? '기억 · 대화는 수집 기록 품질 확인 대상이 아닙니다.'
        : '수집 프로젝트 범위를 선택해야 합니다.';
    } else {
      this.scope.textContent = `${query.scope === 'all' ? '모든 프로젝트' : '선택한 프로젝트'}의 허용된 현재 수집 기록 전체를 확인합니다. 검색·출처·대상·유형·관계·기간 등 화면 필터는 반영하지 않습니다.`;
    }
  }

  private invalidate(): void {
    this.epoch++;
    this.pendingEpoch = null;
    this.report.replaceChildren();
    this.report.hidden = true;
    this.report.dataset.state = 'idle';
    this.status.textContent = this.selection === 'legacy' ? '수집 기록 품질 확인에서 제외됩니다.' : '아직 요청하지 않았습니다.';
    this.updateButton();
  }

  private async request(): Promise<void> {
    this.syncSelection();
    const query = qualityQueryFromSelection(this.selection);
    if (!query || !this.active || this.disposed || !this.info.open
      || this.root.getElementById('settings-page-memory')?.hidden
      || this.pendingEpoch !== null) return;
    const epoch = ++this.epoch, selection = this.selection;
    this.pendingEpoch = epoch;
    this.button.disabled = true;
    this.status.textContent = '수집 기록 품질을 확인 중입니다.';
    this.report.replaceChildren();
    this.report.hidden = true;
    this.report.dataset.state = 'loading';
    try {
      const response = await this.load('collection-quality', { query: JSON.stringify(query) });
      if (!this.current(epoch, selection)) return;
      const result = parseCollectionQuality(response, query);
      if (!result) {
        this.status.textContent = '품질 보고서를 확인할 수 없습니다. 다시 요청할 수 있습니다.';
        this.report.dataset.state = 'unavailable';
      } else if (!result.ok) {
        this.status.textContent = ERROR_TEXT[result.reason] + ' 다시 요청할 수 있습니다.';
        this.report.dataset.state = 'unavailable';
      } else {
        this.render(result);
        this.status.textContent = '선택한 범위의 현재 수집 기록 확인을 마쳤습니다.';
        this.report.dataset.state = 'complete';
      }
    } catch {
      if (this.current(epoch, selection)) {
        this.status.textContent = '품질 보고서를 가져오지 못했습니다. 다시 요청할 수 있습니다.';
        this.report.dataset.state = 'unavailable';
      }
    } finally {
      if (this.pendingEpoch === epoch) {
        if (!this.current(epoch, selection)) {
          this.syncSelection();
          if (this.pendingEpoch === epoch) this.invalidate();
        } else {
          this.pendingEpoch = null;
          this.updateButton();
        }
      }
    }
  }

  private render(value: QualitySuccess): void {
    const elements: HTMLElement[] = [];
    const paragraph = (copy: string, className?: string) => {
      const p = this.root.createElement('p');
      p.textContent = copy;
      if (className) p.className = className;
      elements.push(p);
    };
    const metrics = (title: string, entries: Array<[string, string | number]>) => {
      const h = this.root.createElement('h3');
      h.textContent = title;
      elements.push(h);
      const dl = this.root.createElement('dl');
      for (const [label, metric] of entries) {
        const dt = this.root.createElement('dt'), dd = this.root.createElement('dd');
        dt.textContent = label;
        dd.textContent = typeof metric === 'number' ? metric.toLocaleString('ko-KR') : metric;
        dl.append(dt, dd);
      }
      elements.push(dl);
    };
    const timestamp = checkedDate(value.checked_at)!;
    paragraph(`범위: ${value.scope.requested_scope === 'all' ? '모든 프로젝트' : '선택한 프로젝트'}의 허용된 현재 기록 전체 · 수집 소속 ${value.counts.memberships.toLocaleString('ko-KR')}건 · 문서 ${value.counts.distinct_current_documents.toLocaleString('ko-KR')}개 · 관계 ${value.counts.relations.toLocaleString('ko-KR')}개`);
    paragraph(`확인: ${new Intl.DateTimeFormat('ko-KR', {
      timeZone: 'Asia/Seoul', dateStyle: 'short', timeStyle: 'medium',
    }).format(timestamp)}`);
    const findings = Object.entries(value.findings).sort(([a], [b]) => a.localeCompare(b));
    const normal = findings.filter(([key]) => isNormalizationFinding(key));
    const integrity = findings.filter(([key]) => !isNormalizationFinding(key));
    metrics('관찰된 발견 건수', [
      ['정규화 차이', findingTotal(normal)],
      ['그 밖의 무결성 발견', findingTotal(integrity)],
    ]);
    if (findings.length) {
      const details = this.root.createElement('details');
      details.className = 'knowledge-quality-findings';
      const summary = this.root.createElement('summary');
      summary.textContent = `발견 세부 항목 ${findings.length}종`;
      const dl = this.root.createElement('dl');
      for (const [index, [key, value]] of findings.entries()) {
        const dt = this.root.createElement('dt'), dd = this.root.createElement('dd');
        dt.textContent = Object.hasOwn(FINDING_LABELS, key)
          ? FINDING_LABELS[key] : `${isNormalizationFinding(key) ? '정규화' : '그 밖의'} 세부 항목 ${index + 1}`;
        dd.textContent = value.toLocaleString('ko-KR');
        dl.append(dt, dd);
      }
      details.append(summary, dl);
      elements.push(details);
    }
    metrics('검토 후보', [
      ['동일 원본 해시 참조 그룹', value.duplicate_candidates.same_raw_bytes.groups],
      ['동일 정규화 본문 후보 그룹', value.duplicate_candidates.same_normalized_search_text.groups],
      ['교차 맥락 본문 후보 그룹', value.duplicate_candidates.cross_context_search_text.groups],
      ['벡터 차원 혼합 프로필', value.numeric_review_candidates.mixed_vector_dimensions.profiles],
    ]);
    metrics('본문 근거에서 제외한 기록', [
      ['메타데이터만 있는 현재 버전', value.counts.metadata_only_current_versions],
      ['본문 미열람 안내만 있는 현재 버전', value.counts.unread_body_marker_current_versions],
      ['내용 대신 자료 설명만 있는 현재 버전', value.counts.metadata_description_current_versions],
      ['같은 맥락의 이름·안내문 일치 그룹', value.metadata_matches.within_context.groups],
      ['교차 맥락의 이름·안내문 일치 그룹', value.metadata_matches.cross_context.groups],
    ]);
    paragraph('본문을 읽지 않은 기록의 이름·안내문 일치는 본문 중복이나 의미 연결의 근거로 사용하지 않습니다. 자료와 구조 관계는 유지합니다.', 'knowledge-quality-limit');
    paragraph('원본은 해시 참조만 확인하고 원문 바이트를 다시 읽지 않았습니다. NFC·줄바꿈 차이는 관찰만 하며 본문 공백을 유지하고 수정하지 않습니다. 일치 텍스트·차원 혼합은 검토 후보로, 의미상 중복·타당성·이상 여부는 판정하지 않았습니다.', 'knowledge-quality-limit');
    this.report.replaceChildren(...elements);
    this.report.hidden = false;
  }
}
