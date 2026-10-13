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
type BodyMatches = {
  policy: 'stored-body-match-v1';
  basis: 'current_stored_body_projection_not_original_equivalence';
  counts: { eligible_current_versions: number; without_substantive_label: number; reference_only_current_versions: number };
  stored_body: { groups: number; members: number; status: 'text_match_review_only' };
  reference_text: { groups: number; members: number; status: 'text_match_review_only' };
};
type QualityErrorCode = keyof typeof ERROR_TEXT;
type ComparisonRecord = {
  target_id: string; document_id: string; version: string; platform: string; original_id: string;
  label: string; target_label: string; body: string; body_source: 'stored_body' | 'retained_record.localOriginalText';
  truncated: boolean; matched_body: string; matched_body_truncated: boolean;
  collected_at: number; raw_sha256: string; basis: 'permitted_current_stored_record';
  content_evidence: { policy: 'source-content-evidence-v4'; classification: string };
};
type ComparisonSamples = {
  policy: 'source-bound-pair-preview-v1'; coverage: 'bounded_sample';
  maximum_groups: 6; maximum_records_per_group: 2; maximum_body_characters: 4000;
  maximum_pairs_examined: 20; examined_pairs: number;
  source_verification: 'displayed_canonical_records_only'; skipped_noncontent_groups: number;
  groups: Array<{ kind: 'stored_body' | 'canonical_record'; project: string; match_sha256: string;
    members: number; status: 'review_only_not_confirmed_duplicate'; records: ComparisonRecord[] }>;
};
type DeclaredFields = {
  policy: 'source-declared-fields-v1'; time_policy: 'source-declared-time-v1'; quantity_policy: 'source-declared-quantity-v1';
  scope: 'explicit_metadata_fields_only'; truth_status: 'syntax_conversion_not_semantic_verdict';
  records_checked: number;
  time: { fields: number; records_absent: number; normalized: number; ambiguous: number; invalid: number; unsupported: number; missing: number; date_only: number; explicit_offset: number; unknown_local_offset: number };
  quantity: { fields: number; records_absent: number; normalized: number; invalid: number; unsupported: number; missing: number; omitted: number };
  validity_ranges: { none: number; ordered: number; inverted: number; uncomparable: number };
};
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
  body_matches: BodyMatches;
  metadata_matches: Record<'within_context' | 'cross_context', { groups: number; status: 'metadata_match_not_content' }>;
  content_evidence: { policy: 'source-content-evidence-v4'; status: 'source_declared_exclusions_not_semantic_verdict' };
  numeric_review_candidates: { mixed_vector_dimensions: { profiles: number; status: 'review_candidate' } };
  normalization: { method: 'NFC_line_endings_preserving_body_spacing'; semantic_validity: 'unverified'; declared_fields: DeclaredFields };
  comparison_samples?: ComparisonSamples;
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
  quality_comparison_unavailable: '후보의 현재 원문·출처를 대사하지 못했습니다. 다시 검사해 주세요.',
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

function declaredFields(value: unknown, maxRecords: unknown): DeclaredFields | null {
  const row = object(value), time = object(row?.time), quantity = object(row?.quantity), ranges = object(row?.validity_ranges);
  if (!row || !time || !quantity || !ranges || !count(maxRecords) || !count(row.records_checked)
    || row.records_checked > maxRecords || row.policy !== 'source-declared-fields-v1'
    || row.time_policy !== 'source-declared-time-v1' || row.quantity_policy !== 'source-declared-quantity-v1'
    || row.scope !== 'explicit_metadata_fields_only' || row.truth_status !== 'syntax_conversion_not_semantic_verdict') return null;
  const timeKeys = ['fields', 'records_absent', 'normalized', 'ambiguous', 'invalid', 'unsupported', 'missing', 'date_only', 'explicit_offset', 'unknown_local_offset'] as const;
  const quantityKeys = ['fields', 'records_absent', 'normalized', 'invalid', 'unsupported', 'missing', 'omitted'] as const;
  const rangeKeys = ['none', 'ordered', 'inverted', 'uncomparable'] as const;
  if (!timeKeys.every(k => count(time[k])) || !quantityKeys.every(k => count(quantity[k])) || !rangeKeys.every(k => count(ranges[k]))) return null;
  const t = Object.fromEntries(timeKeys.map(k => [k, time[k]])) as DeclaredFields['time'];
  const q = Object.fromEntries(quantityKeys.map(k => [k, quantity[k]])) as DeclaredFields['quantity'];
  const r = Object.fromEntries(rangeKeys.map(k => [k, ranges[k]])) as DeclaredFields['validity_ranges'];
  const timePresent = row.records_checked - t.records_absent, quantityPresent = row.records_checked - q.records_absent;
  if (timePresent < 0 || quantityPresent < 0 || t.fields < timePresent || t.fields > timePresent * 12
    || q.fields < quantityPresent || q.fields > quantityPresent * 16
    || t.normalized + t.ambiguous + t.invalid + t.unsupported + t.missing !== t.fields
    || t.date_only + t.explicit_offset !== t.normalized
    || t.unknown_local_offset > t.explicit_offset
    || q.normalized + q.invalid + q.unsupported + q.missing !== q.fields
    || (!q.fields && q.omitted !== 0)
    || r.none + r.ordered + r.inverted + r.uncomparable !== row.records_checked
    || (row.records_checked - r.none) * 2 > t.fields) return null;
  return { policy: 'source-declared-fields-v1', time_policy: 'source-declared-time-v1', quantity_policy: 'source-declared-quantity-v1',
    scope: 'explicit_metadata_fields_only', truth_status: 'syntax_conversion_not_semantic_verdict',
    records_checked: row.records_checked, time: t, quantity: q, validity_ranges: r };
}

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

function comparisonSamples(value: unknown, projects: string[], maxMembers: number): ComparisonSamples | null {
  const samples = object(value);
  const bounded = (v: unknown, maximum: number, empty = true): v is string => typeof v === 'string'
    && (empty || v.length > 0) && v.length <= maximum * 2 && [...v].length <= maximum;
  const sha = (v: unknown): v is string => typeof v === 'string' && /^[0-9a-f]{64}$/.test(v);
  if (!samples || samples.policy !== 'source-bound-pair-preview-v1' || samples.coverage !== 'bounded_sample'
    || samples.maximum_groups !== 6 || samples.maximum_records_per_group !== 2 || samples.maximum_body_characters !== 4000
    || samples.maximum_pairs_examined !== 20 || !count(samples.examined_pairs) || (samples.examined_pairs as number) > 20
    || samples.source_verification !== 'displayed_canonical_records_only'
    || !count(samples.skipped_noncontent_groups)
    || !Array.isArray(samples.groups) || samples.groups.length > 6) return null;
  if (samples.groups.length + (samples.skipped_noncontent_groups as number) !== samples.examined_pairs) return null;
  const groups: ComparisonSamples['groups'] = [], keys = new Set<string>(), pairs = new Set<string>();
  for (const value of samples.groups) {
    const group = object(value);
    if (!group || !['stored_body', 'canonical_record'].includes(String(group.kind))
      || !bounded(group.project, 128, false) || !projects.includes(group.project)
      || !sha(group.match_sha256) || !count(group.members) || (group.members as number) < 2
      || (group.members as number) > maxMembers || group.status !== 'review_only_not_confirmed_duplicate'
      || !Array.isArray(group.records) || group.records.length !== 2) return null;
    const key = `${group.project}/${group.kind}/${group.match_sha256}`;
    if (keys.has(key)) return null;
    keys.add(key);
    const records: ComparisonRecord[] = [], ids = new Set<string>();
    for (const value of group.records) {
      const record = object(value);
      const evidence = object(record?.content_evidence);
      if (!record || !bounded(record.target_id, 256, false) || !bounded(record.document_id, 256, false)
        || typeof record.version !== 'string' || !/^version:[0-9a-f]{64}$/.test(record.version)
        || !bounded(record.platform, 64, false) || !bounded(record.original_id, 2048, false)
        || !bounded(record.label, 1024) || !bounded(record.target_label, 1024)
        || !bounded(record.body, 4000) || !bounded(record.matched_body, 4000)
        || typeof record.truncated !== 'boolean' || typeof record.matched_body_truncated !== 'boolean'
        || !['stored_body', 'retained_record.localOriginalText'].includes(String(record.body_source))
        || !checkedDate(record.collected_at) || !sha(record.raw_sha256)
        || !evidence || evidence.policy !== 'source-content-evidence-v4'
        || !['metadata_only', 'source_metadata_only', 'unread_body_marker', 'metadata_description', 'content_candidate'].includes(String(evidence.classification))
        || evidence.classification !== 'content_candidate'
        || record.basis !== 'permitted_current_stored_record' || ids.has(record.document_id)
        || (group.kind === 'canonical_record' && record.raw_sha256 !== group.match_sha256)) return null;
      ids.add(record.document_id);
      records.push({ target_id: record.target_id, document_id: record.document_id, version: record.version,
        platform: record.platform, original_id: record.original_id, label: record.label, target_label: record.target_label,
        body: record.body, body_source: record.body_source as ComparisonRecord['body_source'], truncated: record.truncated,
        matched_body: record.matched_body, matched_body_truncated: record.matched_body_truncated,
        collected_at: record.collected_at as number, raw_sha256: record.raw_sha256, basis: 'permitted_current_stored_record',
        content_evidence: { policy: 'source-content-evidence-v4', classification: evidence.classification as string } });
    }
    const pair = JSON.stringify([group.project,[...ids].sort()]);
    if (pairs.has(pair)) return null;
    pairs.add(pair);
    groups.push({ kind: group.kind as 'stored_body' | 'canonical_record', project: group.project,
      match_sha256: group.match_sha256, members: group.members as number,
      status: 'review_only_not_confirmed_duplicate', records });
  }
  return { policy: 'source-bound-pair-preview-v1', coverage: 'bounded_sample', maximum_groups: 6,
    maximum_records_per_group: 2, maximum_body_characters: 4000,
    maximum_pairs_examined: 20, examined_pairs: samples.examined_pairs as number,
    source_verification: 'displayed_canonical_records_only', skipped_noncontent_groups: samples.skipped_noncontent_groups as number, groups };
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
  const declared = declaredFields(normalization?.declared_fields, counts?.project_current_versions);
  const evidence = object(row.content_evidence), metadata = object(row.metadata_matches);
  const metadataWithin = object(metadata?.within_context), metadataCross = object(metadata?.cross_context);
  const body = object(row.body_matches), bodyCounts = object(body?.counts);
  const storedBody = object(body?.stored_body), references = object(body?.reference_text);
  if (row.ok !== true || !matchesScope(scope, query, false)
    || scope.coverage !== 'permitted_current_collection_memberships'
    || scope.filters !== 'project_only' || scope.source_verification !== 'hash_references_only'
    || !Array.isArray(scope.projects) || scope.projects.length < 1 || scope.projects.length > 16
    || new Set(scope.projects).size !== scope.projects.length
    || !scope.projects.every(p => typeof p === 'string' && p.length > 0 && p.length <= 128)
    || (query.scope === 'project' && (scope.projects.length !== 1 || scope.projects[0] !== query.project))
    || !checkedDate(row.checked_at) || !finiteNonnegative(row.elapsed_ms)
    || !counts || !findings || !duplicates || !numeric || !raw || !normalized || !cross || !mixed || !normalization || !declared
    || !evidence || !metadataWithin || !metadataCross
    || !body || !bodyCounts || !storedBody || !references
    || body.policy !== 'stored-body-match-v1' || body.basis !== 'current_stored_body_projection_not_original_equivalence'
    || !['eligible_current_versions', 'without_substantive_label', 'reference_only_current_versions'].every(k => count(bodyCounts[k]))
    || (bodyCounts.eligible_current_versions as number) > (counts.project_current_versions as number)
    || (bodyCounts.without_substantive_label as number) > (bodyCounts.eligible_current_versions as number)
    || (bodyCounts.reference_only_current_versions as number) > (bodyCounts.eligible_current_versions as number)
    || ![storedBody, references].every(item => count(item.groups) && count(item.members)
      && item.status === 'text_match_review_only' && (item.groups as number) * 2 <= (item.members as number)
      && Boolean(item.groups) === Boolean(item.members))
    || (storedBody.members as number) > (bodyCounts.eligible_current_versions as number) - (bodyCounts.reference_only_current_versions as number)
    || (references.members as number) > (bodyCounts.reference_only_current_versions as number)
    || !(['memberships', 'project_current_versions', 'relations', 'distinct_current_documents', 'bytes_examined',
      'noncontent_current_versions', 'metadata_only_current_versions', 'unread_body_marker_current_versions',
      'metadata_description_current_versions'] as const)
      .every(key => count(counts[key]))
    || (counts.noncontent_current_versions as number) > (counts.project_current_versions as number)
    || (counts.metadata_only_current_versions as number) + (counts.unread_body_marker_current_versions as number)
      + (counts.metadata_description_current_versions as number)
      !== counts.noncontent_current_versions
    || evidence.policy !== 'source-content-evidence-v4' || evidence.status !== 'source_declared_exclusions_not_semantic_verdict'
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

  const comparisons = Object.hasOwn(row, 'comparison_samples')
    ? comparisonSamples(row.comparison_samples, scope.projects as string[], counts.distinct_current_documents as number) : undefined;
  if (comparisons === null) return null;
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
    body_matches: {
      policy: 'stored-body-match-v1', basis: 'current_stored_body_projection_not_original_equivalence',
      counts: bodyCounts as BodyMatches['counts'],
      stored_body: { groups: storedBody.groups as number, members: storedBody.members as number, status: 'text_match_review_only' },
      reference_text: { groups: references.groups as number, members: references.members as number, status: 'text_match_review_only' },
    },
    numeric_review_candidates: { mixed_vector_dimensions: { profiles: mixed.profiles as number, status: 'review_candidate' } },
    metadata_matches: {
      within_context: { groups: metadataWithin.groups as number, status: 'metadata_match_not_content' },
      cross_context: { groups: metadataCross.groups as number, status: 'metadata_match_not_content' },
    },
    content_evidence: { policy: 'source-content-evidence-v4', status: 'source_declared_exclusions_not_semantic_verdict' },
    normalization: { method: 'NFC_line_endings_preserving_body_spacing', semantic_validity: 'unverified', declared_fields: declared },
    ...(comparisons ? { comparison_samples: comparisons } : {}),
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
    this.report.addEventListener('click', event => {
      if ((event.target as Element | null)?.closest?.('button.knowledge-quality-compare')) void this.request(true);
    }, { signal: this.controls.signal });
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

  private async request(includeSamples = false): Promise<void> {
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
      const response = await this.load('collection-quality', { query: JSON.stringify({ ...query,
        ...(includeSamples ? { include_samples: true } : {}) }) });
      if (!this.current(epoch, selection)) return;
      const result = parseCollectionQuality(response, query);
      if (!result || (result.ok && includeSamples && !result.comparison_samples)) {
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
    const metrics = (title: string, entries: Array<[string, string | number]>, id?: string) => {
      const h = this.root.createElement('h3');
      h.textContent = title;
      if (id) h.id = id;
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
      ['제목·본문 일치 후보 그룹', value.duplicate_candidates.same_normalized_search_text.groups],
      ['교차 맥락 제목·본문 후보 그룹', value.duplicate_candidates.cross_context_search_text.groups],
      ['벡터 차원 혼합 프로필', value.numeric_review_candidates.mixed_vector_dimensions.profiles],
    ]);
    if (!value.comparison_samples) {
      const compare = this.root.createElement('button');
      compare.type = 'button'; compare.className = 'knowledge-quality-compare';
      compare.textContent = '일치 후보의 원문 비교';
      elements.push(compare);
    } else {
      const samples = value.comparison_samples;
      paragraph('원문 비교: 최대 6그룹에서 각각 2개 자료를 확인합니다. 문자열 일치 후보이며 병합하지 않았습니다.', 'knowledge-quality-limit');
      if (!samples.groups.length) paragraph('현재 비교할 수 있는 일치 후보가 없습니다.');
      for (const [index, group] of samples.groups.entries()) {
        const details = this.root.createElement('details'); details.className = 'knowledge-quality-comparison';
        details.dataset.kind = group.kind;
        const summary = this.root.createElement('summary');
        summary.textContent = `${index + 1}. ${group.project} · ${group.kind === 'stored_body' ? '저장 본문 일치' : '보존 레코드 바이트 일치'} · ${group.members.toLocaleString('ko-KR')}개 중 2개`;
        details.append(summary);
        for (const [i, record] of group.records.entries()) {
          const item = this.root.createElement('article'); item.dataset.document = record.document_id;
          const heading = this.root.createElement('h4'); heading.textContent = `${i + 1}. ${record.label || '제목 없음'}`;
          const source = this.root.createElement('p'); source.textContent = record.target_label;
          const basis = this.root.createElement('p');
          basis.textContent = `${record.body_source === 'retained_record.localOriginalText' ? '보존 원문' : '저장 본문'}${record.truncated ? ' · 앞부분 4,000자' : ''}`;
          const body = this.root.createElement('pre'); body.textContent = record.body;
          item.append(heading, source, basis, body);
          if (record.matched_body !== record.body) {
            const matched = this.root.createElement('details'), label = this.root.createElement('summary');
            label.textContent = `일치 판정에 사용된 저장 본문${record.matched_body_truncated ? ' · 앞부분 4,000자' : ''}`;
            const text = this.root.createElement('pre'); text.textContent = record.matched_body;
            matched.append(label, text); item.append(matched);
          }
          details.append(item);
        }
        elements.push(details);
      }
      if (samples.skipped_noncontent_groups) paragraph(`본문 근거가 없는 ${samples.skipped_noncontent_groups}그룹은 원문 비교에서 제외했습니다.`);
    }
    metrics('제목과 별도로 확인한 저장 본문', [
      ['검사한 저장 본문', value.body_matches.counts.eligible_current_versions],
      ['검사한 본문 중 유효 제목 없는 기록', value.body_matches.counts.without_substantive_label],
      ['저장 본문 문자열 일치 후보 그룹', value.body_matches.stored_body.groups],
      ['저장 본문 일치 후보 참여 기록', value.body_matches.stored_body.members],
      ['링크 문자열 일치 후보 그룹', value.body_matches.reference_text.groups],
      ['링크 일치 후보 참여 기록', value.body_matches.reference_text.members],
    ], 'knowledge-quality-body');
    paragraph('저장 본문은 제목·검색 캐시와 별도로 비교합니다. 반복 안내·광고도 문자열이 같을 수 있으며, 링크 일치는 연결된 원문의 내용 일치가 아닙니다. 원래 포스트 전체·작성자·시각·같은 인물은 검증하지 않았고 자동 병합하지 않습니다.', 'knowledge-quality-limit');
    metrics('본문 근거에서 제외한 기록', [
      ['메타데이터만 있는 현재 버전', value.counts.metadata_only_current_versions],
      ['본문 미열람 안내만 있는 현재 버전', value.counts.unread_body_marker_current_versions],
      ['내용 대신 자료 설명만 있는 현재 버전', value.counts.metadata_description_current_versions],
      ['같은 맥락의 이름·안내문 일치 그룹', value.metadata_matches.within_context.groups],
      ['교차 맥락의 이름·안내문 일치 그룹', value.metadata_matches.cross_context.groups],
    ]);
    paragraph('본문을 읽지 않은 기록의 이름·안내문·파생 색인 요약은 실제 본문 중복이나 의미 연결의 근거로 사용하지 않습니다. 자료와 구조 관계는 유지합니다.', 'knowledge-quality-limit');
    const declared = value.normalization.declared_fields;
    metrics('날짜·시간대 표기', [
      ['검사한 선언 시각 필드', declared.time.fields],
      ['날짜만 확인한 표기', declared.time.date_only],
      ['UTC·명시 오프셋 변환', declared.time.explicit_offset],
      ['UTC는 알려졌지만 현지 오프셋 미상', declared.time.unknown_local_offset],
      ['시간대 미상', declared.time.ambiguous],
      ['잘못된 날짜·시각 표기', declared.time.invalid],
      ['해석하지 않은 시각 표기', declared.time.unsupported],
      ['값이 없는 선언 시각', declared.time.missing],
      ['시각 선언이 없는 기록', declared.time.records_absent],
      ['역전된 유효 기간 검토', declared.validity_ranges.inverted],
      ['비교할 수 없는 유효 기간', declared.validity_ranges.uncomparable],
    ], 'knowledge-quality-time');
    metrics('명시 값·단위 표기', [
      ['검사한 값·단위 필드', declared.quantity.fields],
      ['정확한 단위 변환', declared.quantity.normalized],
      ['잘못된 값·단위 표기', declared.quantity.invalid],
      ['지원하지 않는 단위·형식', declared.quantity.unsupported],
      ['값·단위가 없는 선언', declared.quantity.missing],
      ['값·단위 선언이 없는 기록', declared.quantity.records_absent],
      ['검사 한도로 남긴 값·단위', declared.quantity.omitted],
    ], 'knowledge-quality-quantity');
    paragraph('현재 메타데이터에 명시된 필드만 해석합니다. 날짜만 있으면 시각을 만들지 않고, 시간대가 없으면 이 Mac의 시간대를 대입하지 않습니다. 단위가 없으면 본문·주소·식별자에서 추정하지 않습니다. 원문을 유지하며 실제 발행·효력·사실 확인이나 자동 병합으로 사용하지 않습니다.', 'knowledge-quality-limit');
    paragraph('원본은 해시 참조만 확인하고 원문 바이트를 다시 읽지 않았습니다. NFC·줄바꿈 차이는 관찰만 하며 본문 공백을 유지하고 수정하지 않습니다. 일치 텍스트·차원 혼합은 검토 후보로, 의미상 중복·타당성·이상 여부는 판정하지 않았습니다.', 'knowledge-quality-limit');
    this.report.replaceChildren(...elements);
    this.report.hidden = false;
  }
}
