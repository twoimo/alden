// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { KnowledgeQualityPanel, parseCollectionQuality, parseCollectionAnnotationReview, parseCollectionSourceProjection, parseAnnotationCandidatePage, qualityQueryFromSelection,
  type QualityQuery } from '../knowledge/quality-panel';
import { settingsMarkup } from '../ui';
import type { fetchSettingsAction } from '../runtime';

const owned: KnowledgeQualityPanel[] = [];
const all = { scope: 'all' } as const;

function success(query: QualityQuery = all) {
  return {
    ok: true, schema: 3,
    scope: {
      requested_scope: query.scope,
      requested_project: query.scope === 'project' ? query.project : null,
      projects: query.scope === 'project' ? [query.project] : ['one', 'all'],
      coverage: 'permitted_current_collection_memberships', filters: 'project_only',
      source_verification: 'hash_references_only',
    },
    checked_at: 1_791_768_900.125, elapsed_ms: 25.125,
    counts: { memberships: 42, project_current_versions: 39, relations: 11,
      distinct_current_documents: 30, bytes_examined: 8000,
      noncontent_current_versions: 15, metadata_only_current_versions: 9, unread_body_marker_current_versions: 2, metadata_description_current_versions: 4,reference_only_current_versions:0,empty_body_current_versions:0 },
    findings: { normalization_mismatch: 2, relation_evidence_invalid: 3 },
    duplicate_candidates: {
      same_raw_bytes: { groups: 4, status: 'hash_references_only' },
      same_normalized_search_text: { groups: 5, status: 'review_candidate' },
      cross_context_search_text: { groups: 6, status: 'review_candidate' },
    },
    body_matches: {
      policy: 'stored-body-match-v1', basis: 'current_stored_body_projection_not_original_equivalence',
      counts: { eligible_current_versions: 20, without_substantive_label: 17, reference_only_current_versions: 8 },
      stored_body: { groups: 3, members: 10, status: 'text_match_review_only' },
      reference_text: { groups: 2, members: 6, status: 'text_match_review_only' },
    },
    numeric_review_candidates: { mixed_vector_dimensions: { profiles: 7, status: 'review_candidate' } },
    metadata_matches: {
      within_context: { groups: 8, status: 'metadata_match_not_content' },
      cross_context: { groups: 1, status: 'metadata_match_not_content' },
    },
    content_evidence: { policy: 'source-content-evidence-v6', status: 'source_declared_exclusions_not_semantic_verdict' },
    normalization: { method: 'NFC_line_endings_preserving_body_spacing', semantic_validity: 'unverified',
      declared_fields: { policy: 'source-declared-fields-v1', time_policy: 'source-declared-time-v1', quantity_policy: 'source-declared-quantity-v1',
        scope: 'explicit_metadata_fields_only', truth_status: 'syntax_conversion_not_semantic_verdict', records_checked: 39,
        time: { fields: 3, records_absent: 36, normalized: 1, date_only: 1, explicit_offset: 0, unknown_local_offset: 0, ambiguous: 1, invalid: 0, unsupported: 0, missing: 1 },
        quantity: { fields: 2, records_absent: 37, normalized: 1, invalid: 0, unsupported: 1, missing: 0, omitted: 0 },
        validity_ranges: { none: 39, ordered: 0, inverted: 0, uncomparable: 0 } } },
  };
}

function pending() {
  let resolve!: (value: Record<string, unknown> | null) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<Record<string, unknown> | null>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

function withComparisons() {
  const value = success();
  return { ...value, comparison_samples: {
    policy: 'source-bound-pair-preview-v1', coverage: 'bounded_sample', maximum_groups: 6,
    maximum_records_per_group: 2, maximum_body_characters: 4000,
    maximum_pairs_examined: 20, examined_pairs: 1,
    source_verification: 'displayed_canonical_records_only', skipped_noncontent_groups: 0,
    groups: [{ kind: 'stored_body', project: 'one', match_sha256: 'a'.repeat(64), members: 2,
      status: 'review_only_not_confirmed_duplicate', records: ['a', 'b'].map(id => ({
        target_id: 'target', document_id: id, version: `version:${'c'.repeat(64)}`,
        platform: 'graph', original_id: id, label: `<img src=x onerror=alert(1)> ${id}`,
        target_label: '취업 자료', body: '보존 원문', body_source: 'retained_record.localOriginalText',
        truncated: false, matched_body: '저장된 요약', matched_body_truncated: false,
        collected_at: value.checked_at, raw_sha256: 'b'.repeat(64), basis: 'permitted_current_stored_record',
        content_evidence: { policy: 'source-content-evidence-v6', classification: 'content_candidate' },
      })) }],
  } };
}

function setup(load: typeof fetchSettingsAction) {
  const panel = new KnowledgeQualityPanel(load);
  owned.push(panel);
  panel.start();
  return panel;
}

const button = () => document.getElementById('knowledge-quality-run') as HTMLButtonElement;
const info = () => document.getElementById('knowledge-info') as HTMLDetailsElement;
const report = () => document.getElementById('knowledge-quality-report') as HTMLElement;
const status = () => document.getElementById('knowledge-quality-status') as HTMLElement;
const calls = (load: ReturnType<typeof vi.fn<typeof fetchSettingsAction>>) =>
  load.mock.calls.filter(([action]) => action === 'collection-quality');
const state = () => report().dataset.state;
async function settle() { await Promise.resolve(); await Promise.resolve(); await Promise.resolve(); }

function select(panel: KnowledgeQualityPanel, value: string) {
  const selector = document.getElementById('knowledge-project') as HTMLSelectElement;
  if (![...selector.options].some(option => option.value === value)) {
    const option = document.createElement('option');
    option.value = value; option.textContent = value;
    selector.append(option);
  }
  selector.value = value;
  panel.syncSelection(); // CollectionGraphController calls this on project changes.
}

function metric(label: string) {
  return [...report().querySelectorAll('dt')].find(node => node.textContent === label)?.nextElementSibling?.textContent;
}

beforeEach(() => { vi.useFakeTimers(); document.body.innerHTML = settingsMarkup(); });
afterEach(() => { for (const panel of owned) panel.dispose(); owned.length = 0; vi.useRealTimers(); });

function withAnnotationReview() {
  const base = withComparisons();
  return { ...base, kind: 'source_annotation_review', versions_scanned: 4,
    scope: { ...base.scope, source_verification: 'retained_canonical_json' }, source_annotation_review: {
    policy: 'retained-annotation-review-v1', status: 'verified_retained_declarations', semantic_status: 'unverified',
    comparison: 'NFC_exact_values_as_unordered_sets_preserving_case_and_spacing',
    scope: 'permitted_current_body_records_retained_bytes',
    binding_findings: { source_blob_invalid: 1, source_reference_invalid: 0, metadata_invalid: 0,
      version_identity_mismatch: 0, projection_hash_mismatch: 0, retained_text_source_mismatch: 0 },
    counts: { source_versions_inspected: 4, records_with_repeated_values: 1,
      same_projected_body_groups_compared: 1, same_projected_body_groups_with_tag_differences: 1,
      same_body_group_members_with_tag_differences: 2 },
    fields: { topics: { declared: 3, undeclared: 1, unsupported: 0, over_budget: 0, repeated_values: 1, body_groups_with_different_values: 1 },
      entityTags: { declared: 4, undeclared: 0, unsupported: 0, over_budget: 0, repeated_values: 0, body_groups_with_different_values: 0 } },
  } };
}

function candidatePage(records=false,offset=0) {
  const item=(i:number)=>records?{document_id:`graph:${String(i).padStart(64,'a')}`,version:`version:${String(i).padStart(64,'b')}`,
    original_id:`post-${i}`,label:'',project:'one',body:'같은 보존 원문 <img src=x>',truncated:false,raw_sha256:'c'.repeat(64),
    projected_body_sha256:'d'.repeat(64),collected_at:1791768900,
    content_evidence:{policy:'source-content-evidence-v6',classification:'content_candidate'},
    source_annotations:{policy:'retained-declared-tags-v1',basis:'verified_retained_record',semantic_status:'unverified',
      fields:{topics:{state:'declared',values:[i===2?'다른 태그':'같은 태그']},entityTags:{state:'undeclared',values:[]}}}}:
    {group:i,project:'one',projected_body_sha256:'d'.repeat(64),members:7,different_fields:['topics']};
  return {ok:true,schema:3,policy:'retained-annotation-candidates-v1',kind:records?'annotation_candidate_records':'annotation_candidate_groups',
    scope:{scope:'all'},review_id:'e'.repeat(64),group:records?0:null,total:7,offset,next_offset:offset+5<7?offset+5:null,
    checked_at:1791768900,semantic_status:'unverified',source_verification:records?'displayed_current_records':'current_scope_metadata_only',
    items:Array.from({length:Math.min(5,7-offset)},(_,i)=>item(i+offset))};
}

describe('manual collection-quality report and fixed native selectors', () => {
  it('shows URL member pages as unread destination references and rejects older classification stamps', async()=>{
    const inspection:any={...withAnnotationReview(),annotation_candidates:candidatePage()};
    Object.assign(inspection.source_annotation_review.counts,{same_projected_body_groups_with_tag_differences:7,
      same_projected_body_groups_compared:7,source_versions_inspected:20,same_body_group_members_with_tag_differences:14});
    inspection.versions_scanned=20;
    inspection.source_annotation_review.fields.topics.declared=19;
    inspection.source_annotation_review.fields.topics.body_groups_with_different_values=7;
    inspection.source_annotation_review.fields.entityTags.declared=20;
    const references:any=candidatePage(true);
    for(const item of references.items) {item.body='https://example.invalid/apply';item.content_evidence.classification='reference_only';}
    expect(parseAnnotationCandidatePage(references,all,{review_id:references.review_id,offset:0,group:0})).not.toBeNull();
    const stale=structuredClone(references);stale.items[0].content_evidence.policy='source-content-evidence-v4';
    expect(parseAnnotationCandidatePage(stale,all,{review_id:references.review_id,offset:0,group:0})).toBeNull();
    const load=vi.fn<typeof fetchSettingsAction>().mockResolvedValueOnce(success()).mockResolvedValueOnce(inspection).mockResolvedValueOnce(references);
    setup(load);info().open=true;button().click();await settle();
    (report().querySelector('.knowledge-quality-annotations') as HTMLButtonElement).click();await settle();
    (report().querySelector('button[data-annotation-group="0"]') as HTMLButtonElement).click();await settle();
    expect(report().querySelectorAll('[data-content-evidence="reference_only"]')).toHaveLength(5);
    expect(report().textContent).toContain('연결된 페이지의 본문은 확인하지 않았습니다');
    expect(report().querySelectorAll('pre')[0].textContent).toBe('https://example.invalid/apply');
  });
  it('opens a specific candidate and preserves a third different declaration as inert source text', async()=>{
    const inspection={...withAnnotationReview(),annotation_candidates:candidatePage()};
    inspection.source_annotation_review.counts.same_projected_body_groups_with_tag_differences=7;
    inspection.source_annotation_review.counts.same_projected_body_groups_compared=7;
    inspection.source_annotation_review.counts.source_versions_inspected=20;
    inspection.versions_scanned=20;
    inspection.source_annotation_review.counts.same_body_group_members_with_tag_differences=14;
    inspection.source_annotation_review.fields.topics.declared=19;inspection.source_annotation_review.fields.topics.body_groups_with_different_values=7;
    inspection.source_annotation_review.fields.entityTags.declared=20;
    const load=vi.fn<typeof fetchSettingsAction>().mockResolvedValueOnce(success()).mockResolvedValueOnce(inspection).mockResolvedValueOnce(candidatePage(true));
    setup(load);info().open=true;button().click();await settle();
    (report().querySelector('.knowledge-quality-annotations') as HTMLButtonElement).click();await settle();
    (report().querySelector('button[data-annotation-group="0"]') as HTMLButtonElement).click();await settle();
    expect(JSON.parse(calls(load)[2][1]!.query!)).toEqual({scope:'all',review_id:'e'.repeat(64),offset:0,group:0});
    expect(report().querySelectorAll('.knowledge-quality-candidate-record')).toHaveLength(5);
    expect(report().textContent).toContain('다른 태그');expect(report().querySelector('img')).toBeNull();
    expect(state()).toBe('complete');
  });

  it('rejects wrong candidate scope, offset, review, source status and incomplete page counts',()=>{
    const expected={review_id:'e'.repeat(64),offset:0};
    expect(parseAnnotationCandidatePage(candidatePage(),all,expected)).not.toBeNull();
    for(const change of [(p:ReturnType<typeof candidatePage>)=>{p.review_id='f'.repeat(64);},
      (p:ReturnType<typeof candidatePage>)=>{p.offset=5;},(p:ReturnType<typeof candidatePage>)=>{p.semantic_status='verified';},
      (p:ReturnType<typeof candidatePage>)=>{p.items.pop();}]){
      const page=candidatePage();change(page);expect(parseAnnotationCandidatePage(page,all,expected)).toBeNull();
    }
    expect(parseAnnotationCandidatePage(candidatePage(),{scope:'project',project:'one'},expected)).toBeNull();
  });
  it('requests retained tag inspection only on its explicit control and keeps its scope separate', async () => {
    const load = vi.fn<typeof fetchSettingsAction>().mockResolvedValueOnce(success()).mockResolvedValueOnce(withAnnotationReview());
    setup(load); info().open = true; button().click(); await settle();
    expect(JSON.parse(calls(load)[0][1]!.query!)).toEqual({ scope: 'all' });
    (report().querySelector('.knowledge-quality-annotations') as HTMLButtonElement).click(); await settle();
    expect(JSON.parse(calls(load)[1][1]!.query!)).toEqual({ scope: 'all', include_annotation_review: true });
    expect(state()).toBe('complete');
    expect(metric('선언 태그가 다른 같은 본문 그룹')).toBe('1');
    expect(metric('원문·버전 검증 오류 발견 건수')).toBe('1');
    expect(report().textContent).toContain('어느 태그가 맞는지');
    expect(report().textContent).toContain('별도 요청입니다');
    expect(report().querySelector('.knowledge-quality-comparison')).toBeNull();
  });

  it('requests retained source projection only on its explicit control and does not merge candidates', async () => {
    const projection = {
      ok: true, schema: 3, kind: 'source_text_projection',
      scope: {
        requested_scope: 'all', requested_project: null, projects: ['one', 'all'],
        coverage: 'permitted_current_collection_memberships', filters: 'project_only',
        source_verification: 'retained_canonical_json',
      },
      checked_at: 1_791_768_900.125, elapsed_ms: 40, versions_scanned: 80,
      source_text_projection: {
        policy: 'threads-separated-numeric-tail-v1', status: 'verified_retained_records',
        numeric_tail_candidates: 9, same_projected_body_groups: 35,
        semantic_validity: 'review_candidates_not_confirmed_duplicates',
        originals: 'preserved', merge: 'not_performed',
      },
    };
    const load = vi.fn<typeof fetchSettingsAction>().mockResolvedValueOnce(success()).mockResolvedValueOnce(projection);
    setup(load); info().open = true; button().click(); await settle();
    expect(report().querySelector('.knowledge-quality-projection')).not.toBeNull();
    (report().querySelector('.knowledge-quality-projection') as HTMLButtonElement).click(); await settle();
    expect(JSON.parse(calls(load)[1][1]!.query!)).toEqual({ scope: 'all', include_source_projection: true });
    expect(state()).toBe('complete');
    expect(metric('숫자 꼬리 후보')).toBe('9');
    expect(metric('같은 본문 후보 그룹')).toBe('35');
    expect(report().textContent).toContain('병합하지 않았고 원문은 유지했습니다.');
    expect(report().querySelector('.knowledge-quality-projection')).toBeNull();
    const forged = { ...projection, source_text_projection: { ...projection.source_text_projection, merge: 'performed' } };
    expect(parseCollectionSourceProjection(forged, all)).toBeNull();
    expect(parseCollectionSourceProjection({ ...projection, source_text_projection: { ...projection.source_text_projection, status: 'not_inspected', numeric_tail_candidates: 0, same_projected_body_groups: 0 } }, all)).toBeNull();
  });

  it('does not show completion when explicitly requested tag inspection is missing', async () => {
    const load = vi.fn<typeof fetchSettingsAction>().mockResolvedValueOnce(success()).mockResolvedValueOnce(withComparisons());
    setup(load); info().open = true; button().click(); await settle();
    (report().querySelector('.knowledge-quality-annotations') as HTMLButtonElement).click(); await settle();
    expect(state()).toBe('unavailable');
  });

  it('rejects a semantic verdict, incomplete counters and forged source inspection scopes', () => {
    for (const change of [
      (r: ReturnType<typeof withAnnotationReview>['source_annotation_review']) => { r.semantic_status = 'verified'; },
      (r: ReturnType<typeof withAnnotationReview>['source_annotation_review']) => { r.counts.source_versions_inspected = 40; },
      (r: ReturnType<typeof withAnnotationReview>['source_annotation_review']) => { r.fields.topics.declared = 2; },
      (r: ReturnType<typeof withAnnotationReview>['source_annotation_review']) => { r.scope = 'all_original_sources'; },
      (r: ReturnType<typeof withAnnotationReview>['source_annotation_review']) => { r.binding_findings.source_blob_invalid = -1; },
    ]) {
      const value = withAnnotationReview(); change(value.source_annotation_review);
      expect(parseCollectionAnnotationReview(value, all)).toBeNull();
    }
  });
  it('shows independently bound tag differences without approving identity or executing markup', async () => {
    const value = withComparisons();
    for (const [index, record] of value.comparison_samples.groups[0].records.entries()) {
      (record as any).source_annotations = { policy: 'retained-declared-tags-v1', basis: 'verified_retained_record',
        semantic_status: 'unverified', fields: { topics: { state: 'declared', values: [index ? 'construction' : '<img src=x onerror=alert(1)>'] },
          entityTags: { state: 'declared', values: [index ? 'public_corp' : 'school'] } } };
    }
    const load = vi.fn<typeof fetchSettingsAction>().mockResolvedValueOnce(success()).mockResolvedValueOnce(value);
    setup(load); info().open = true; button().click(); await settle();
    (report().querySelector('.knowledge-quality-compare') as HTMLButtonElement).click(); await settle();
    expect(state()).toBe('complete'); expect(report().querySelectorAll('.knowledge-quality-source-tags')).toHaveLength(4);
    expect(report().textContent).toContain('주제 태그 표기 차이'); expect(report().textContent).toContain('원문 확인이 필요');
    expect(report().textContent).toContain('construction'); expect(report().textContent).toContain('school');
    expect(report().querySelector('img')).toBeNull();
  });

  it('rejects oversized or truth-upgraded tag declarations rather than claiming verified meaning', async () => {
    for (const change of [
      (a: any) => a.semantic_status = 'verified',
      (a: any) => a.fields.topics.values = Array(33).fill('topic'),
      (a: any) => a.fields.topics.values = ['x'.repeat(129)],
      (a: any) => { a.fields.topics.state = 'undeclared'; a.fields.topics.values = ['invented']; },
    ]) {
      const value = withComparisons(); const annotation = { policy: 'retained-declared-tags-v1', basis: 'verified_retained_record',
        semantic_status: 'unverified', fields: { topics: { state: 'declared', values: ['topic'] }, entityTags: { state: 'declared', values: [] } } };
      change(annotation); (value.comparison_samples.groups[0].records[0] as any).source_annotations = annotation;
      const load = vi.fn<typeof fetchSettingsAction>().mockResolvedValueOnce(success()).mockResolvedValueOnce(value);
      const panel = setup(load); info().open = true; button().click(); await settle();
      (report().querySelector('.knowledge-quality-compare') as HTMLButtonElement).click(); await settle();
      expect(state()).toBe('unavailable'); expect(report().querySelector('.knowledge-quality-source-tags')).toBeNull();
      panel.dispose(); document.body.innerHTML = settingsMarkup();
    }
  });

  it('keeps absent, declared-empty and order/repetition-only differences distinct', async () => {
    const value = withComparisons();
    for (const [index, record] of value.comparison_samples.groups[0].records.entries()) {
      (record as any).source_annotations = { policy: 'retained-declared-tags-v1', basis: 'verified_retained_record',
        semantic_status: 'unverified', fields: {
          topics: { state: 'declared', values: index ? ['A', 'B'] : ['B', 'A', 'A'] },
          entityTags: { state: index ? 'declared' : 'undeclared', values: [] } } };
    }
    const load = vi.fn<typeof fetchSettingsAction>().mockResolvedValueOnce(success()).mockResolvedValueOnce(value);
    setup(load); info().open = true; button().click(); await settle();
    (report().querySelector('.knowledge-quality-compare') as HTMLButtonElement).click(); await settle();
    expect(report().textContent).toContain('주제 태그 순서·반복수 차이');
    expect(report().textContent).toContain('인물·기관 등 태그 확인 범위 차이');
    expect(report().textContent).toContain('선언된 빈 목록'); expect(report().textContent).toContain('미선언');
  });

  it('requests source pairs explicitly and renders stored and original text safely', async () => {
    const value = withComparisons();
    (value.comparison_samples!.groups[0].records[0] as any).secret = 'private-extra-sentinel';
    const load = vi.fn<typeof fetchSettingsAction>().mockResolvedValueOnce(success()).mockResolvedValueOnce(value);
    setup(load); info().open = true; button().click(); await settle();
    expect(JSON.parse(calls(load)[0][1]!.query!)).toEqual({ scope: 'all' });
    (report().querySelector('.knowledge-quality-compare') as HTMLButtonElement).click(); await settle();
    expect(JSON.parse(calls(load)[1][1]!.query!)).toEqual({ scope: 'all', include_samples: true });
    expect(state()).toBe('complete'); expect(report().querySelectorAll('.knowledge-quality-comparison article')).toHaveLength(2);
    expect(report().textContent).toContain('보존 원문'); expect(report().textContent).toContain('저장된 요약');
    expect(report().textContent).toContain('병합하지 않았습니다');
    expect(report().querySelector('img')).toBeNull(); expect(report().textContent).not.toContain('private-extra-sentinel');
  });

  it('does not call a counts-only reply a completed source comparison', async () => {
    const load = vi.fn<typeof fetchSettingsAction>().mockResolvedValue(success());
    setup(load); info().open = true; button().click(); await settle();
    (report().querySelector('.knowledge-quality-compare') as HTMLButtonElement).click(); await settle();
    expect(state()).toBe('unavailable'); expect(report().querySelectorAll('article')).toHaveLength(0);
  });

  it('rejects wrong-scope, unverified, duplicated and over-budget source pairs', () => {
    const changes: Array<(v: any) => void> = [
      v => v.comparison_samples.groups[0].project = 'denied',
      v => v.comparison_samples.source_verification = 'whole_source_verified',
      v => v.comparison_samples.groups[0].records[1].document_id = 'a',
      v => v.comparison_samples.groups[0].records[0].raw_sha256 = 'not-a-hash',
      v => v.comparison_samples.groups[0].records[0].body = 'x'.repeat(4001),
      v => v.comparison_samples.groups[0].records[0].content_evidence.classification = 'metadata_only',
      v => v.comparison_samples.groups[0].members = 1000,
      v => v.comparison_samples.groups.push(...Array(6).fill(v.comparison_samples.groups[0])),
    ];
    for (const change of changes) { const value = withComparisons(); change(value); expect(parseCollectionQuality(value, { scope: 'all' })).toBeNull(); }
  });

  it('accepts a 4000-character Unicode preview without treating code units as characters', () => {
    const value = withComparisons(); value.comparison_samples!.groups[0].records[0].body = '🦊'.repeat(4000);
    value.comparison_samples!.groups[0].records[0].truncated = true;
    expect(parseCollectionQuality(value, { scope: 'all' })?.ok).toBe(true);
  });

  it('drops a late source preview after the project changes', async () => {
    const reply = pending(), load = vi.fn<typeof fetchSettingsAction>().mockResolvedValueOnce(success()).mockReturnValueOnce(reply.promise);
    const panel = setup(load); info().open = true; button().click(); await settle();
    (report().querySelector('.knowledge-quality-compare') as HTMLButtonElement).click(); await settle();
    select(panel, 'project:two'); reply.resolve(withComparisons()); await settle();
    expect(report().querySelectorAll('article')).toHaveLength(0);
    expect(report().textContent).not.toContain('보존 원문');
  });

  it('drops a source preview after close and immediate reopen', async () => {
    const reply = pending(), load = vi.fn<typeof fetchSettingsAction>().mockResolvedValueOnce(success()).mockReturnValueOnce(reply.promise);
    setup(load); info().open = true; button().click(); await settle();
    (report().querySelector('.knowledge-quality-compare') as HTMLButtonElement).click(); await settle();
    info().open = false; info().open = true; await settle(); reply.resolve(withComparisons()); await settle();
    expect(report().querySelectorAll('article')).toHaveLength(0); expect(state()).toBe('idle');
  });

  it('shows declared time and unit syntax separately without inventing source facts', async () => {
    const value = success();
    (value.normalization.declared_fields as any).canonical = 'private-date-sentinel';
    const load = vi.fn<typeof fetchSettingsAction>().mockResolvedValue(value);
    setup(load); info().open = true; button().click(); await settle();
    expect(state()).toBe('complete');
    expect(metric('날짜만 확인한 표기')).toBe('1');
    expect(metric('시간대 미상')).toBe('1');
    expect(metric('정확한 단위 변환')).toBe('1');
    expect(report().textContent).toContain('시간대가 없으면 이 Mac의 시간대를 대입하지 않습니다');
    expect(report().textContent).not.toContain('private-date-sentinel');
  });

  it('rejects missing, impossible or verified declared-field summaries', () => {
    const changes = [
      (v:any) => delete v.normalization.declared_fields,
      (v:any) => v.normalization.declared_fields.truth_status = 'verified',
      (v:any) => v.normalization.declared_fields.time.normalized = 9,
      (v:any) => v.normalization.declared_fields.records_checked = true,
      (v:any) => v.normalization.declared_fields.quantity.records_absent = 41,
      (v:any) => v.normalization.declared_fields.validity_ranges.inverted = 1,
    ];
    for (const change of changes) {const value = success(); change(value); expect(parseCollectionQuality(value,all)).toBeNull();}
  });

  it('starts idle, has stable IDs and makes no request on open, visibility start or time passing', async () => {
    const load = vi.fn<typeof fetchSettingsAction>().mockResolvedValue(success());
    setup(load);
    expect(button().id).toBe('knowledge-quality-run');
    expect(status().id).toBe('knowledge-quality-status');
    expect(report().id).toBe('knowledge-quality-report');
    expect(state()).toBe('idle');
    expect(report().hidden).toBe(true);
    expect(status().textContent).toContain('아직 요청하지');
    info().open = true;
    await vi.advanceTimersByTimeAsync(90_000);
    expect(calls(load)).toHaveLength(0);
    expect(state()).toBe('idle');
  });

  it('sends only the explicit union scope or literal project ID all despite visual filters', async () => {
    const load = vi.fn<typeof fetchSettingsAction>(async (_action, input) =>
      success(JSON.parse(input!.query!) as QualityQuery));
    const panel = setup(load);
    info().open = true;
    (document.getElementById('knowledge-search') as HTMLInputElement).value = 'ignored filter';
    (document.getElementById('knowledge-platform') as HTMLSelectElement).value = 'youtube';
    button().click();
    expect(state()).toBe('loading');
    await settle();
    expect(state()).toBe('complete');
    expect(JSON.parse(calls(load)[0][1]!.query!)).toEqual({ scope: 'all' });
    expect(report().textContent).toContain('모든 프로젝트');
    select(panel, 'project:all');
    expect(state()).toBe('idle');
    button().click(); await settle();
    expect(state()).toBe('complete');
    expect(JSON.parse(calls(load)[1][1]!.query!)).toEqual({ scope: 'project', project: 'all' });
    expect(report().textContent).toContain('선택한 프로젝트');
    expect(qualityQueryFromSelection('legacy')).toBeNull();
    expect(qualityQueryFromSelection('project:')).toBeNull();
  });

  it('reports only observed totals, vetted Korean labels and candidates, without leaking response data', async () => {
    const value = {
      ...success(),
      findings: { normalization_mismatch: 2, relation_evidence_invalid: 3, secret_document_identity: 9 },
      samples: { source_url: 'https://private.example/secret', document_id: 'sensitive-123' },
    };
    const load = vi.fn<typeof fetchSettingsAction>().mockResolvedValue(value);
    setup(load); info().open = true; button().click(); await settle();
    expect(state()).toBe('complete');
    expect(report().hidden).toBe(false);
    expect(metric('정규화 차이')).toBe('2');
    expect(metric('그 밖의 무결성 발견')).toBe('12');
    expect(metric('동일 원본 해시 참조 그룹')).toBe('4');
    expect(metric('제목·본문 일치 후보 그룹')).toBe('5');
    expect(metric('교차 맥락 제목·본문 후보 그룹')).toBe('6');
    expect(metric('저장 본문 문자열 일치 후보 그룹')).toBe('3');
    expect(metric('저장 본문 일치 후보 참여 기록')).toBe('10');
    expect(metric('검사한 본문 중 유효 제목 없는 기록')).toBe('17');
    expect(metric('링크 문자열 일치 후보 그룹')).toBe('2');
    expect(metric('링크 일치 후보 참여 기록')).toBe('6');
    expect(document.getElementById('knowledge-quality-body')?.textContent).toBe('제목과 별도로 확인한 저장 본문');
    expect(metric('메타데이터만 있는 현재 버전')).toBe('9');
    expect(metric('본문 미열람 안내만 있는 현재 버전')).toBe('2');
    expect(metric('같은 맥락의 이름·안내문 일치 그룹')).toBe('8');
    expect(metric('교차 맥락의 이름·안내문 일치 그룹')).toBe('1');
    expect(metric('벡터 차원 혼합 프로필')).toBe('7');
    expect(report().textContent).toContain('문서 30개');
    expect(report().textContent).toContain('NFC·줄바꿈 차이는 관찰만');
    expect(report().textContent).toContain('원문 바이트를 다시 읽지 않았습니다');
    expect(report().textContent).toContain('수정하지 않습니다');
    expect(report().textContent).toContain('의미상 중복');
    expect(report().textContent).not.toContain('확인한 바이트');
    expect(report().textContent).not.toContain('프로젝트별 현재 버전');
    expect(report().textContent).not.toContain('secret_document_identity');
    expect(report().textContent).not.toContain('sensitive-123');
    expect(report().textContent).not.toContain('https://private.example');
    const details = report().querySelector<HTMLDetailsElement>('details.knowledge-quality-findings')!;
    expect(details.open).toBe(false);
    expect(details.textContent).toContain('NFC·줄바꿈 정규화 차이');
    expect(details.textContent).toContain('관계 근거 형식 오류');
    expect(details.textContent).toContain('그 밖의 세부 항목');
    expect(report().getAttributeNames().filter(name => name.startsWith('data-'))).toEqual(['data-state']);
    expect(report().querySelector('img, a, script')).toBeNull();
  });

  it('allows a fully valid empty observation, but never invents a zero result from a missing report', async () => {
    const empty = success();
    empty.findings = {} as typeof empty.findings;
    empty.counts = { memberships: 0, project_current_versions: 0, relations: 0,
      distinct_current_documents: 0, bytes_examined: 0,
      noncontent_current_versions: 0, metadata_only_current_versions: 0, unread_body_marker_current_versions: 0, metadata_description_current_versions: 0,reference_only_current_versions:0,empty_body_current_versions:0 };
    empty.body_matches.counts = { eligible_current_versions: 0,
      without_substantive_label: 0, reference_only_current_versions: 0 };
    empty.body_matches.stored_body = { groups: 0, members: 0, status: 'text_match_review_only' };
    empty.body_matches.reference_text = { groups: 0, members: 0, status: 'text_match_review_only' };
    const declared = empty.normalization.declared_fields;
    declared.records_checked = 0;
    for (const values of [declared.time, declared.quantity, declared.validity_ranges]) {
      for (const key of Object.keys(values)) (values as Record<string, number>)[key] = 0;
    }
    const load = vi.fn<typeof fetchSettingsAction>()
      .mockResolvedValueOnce(empty).mockResolvedValueOnce({ ok: true, schema: 3, scope: empty.scope });
    setup(load); info().open = true;
    button().click(); await settle();
    expect(state()).toBe('complete');
    expect(metric('정규화 차이')).toBe('0');
    expect(metric('그 밖의 무결성 발견')).toBe('0');
    expect(report().textContent).toContain('수집 소속 0건');
    button().click(); await settle();
    expect(state()).toBe('unavailable');
    expect(report().hidden).toBe(true);
    expect(metric('정규화 차이')).toBeUndefined();
    expect(status().textContent).toContain('다시 요청');
  });

  it('prevents concurrent clicks, and fences late results from another project', async () => {
    const old = pending(), next = pending();
    const load = vi.fn<typeof fetchSettingsAction>().mockImplementationOnce(() => old.promise)
      .mockImplementationOnce(() => next.promise);
    const panel = setup(load); info().open = true; button().click();
    expect(state()).toBe('loading');
    expect(button().disabled).toBe(true);
    button().dispatchEvent(new MouseEvent('click', { bubbles: true }));
    expect(calls(load)).toHaveLength(1);
    select(panel, 'project:two');
    expect(state()).toBe('idle');
    button().click();
    expect(calls(load)).toHaveLength(2);
    const earlier = success(); earlier.counts.memberships = 999;
    old.resolve(earlier); await settle();
    expect(state()).toBe('loading');
    expect(report().textContent).not.toContain('999');
    next.resolve(success({ scope: 'project', project: 'two' })); await settle();
    expect(state()).toBe('complete');
    expect(report().textContent).toContain('수집 소속 42건');
    expect(report().textContent).not.toContain('999');
  });

  it('clears a pending result on details close and stays idle after its late return', async () => {
    const delayed = pending();
    const load = vi.fn<typeof fetchSettingsAction>().mockImplementationOnce(() => delayed.promise)
      .mockResolvedValueOnce(success());
    setup(load); info().open = true; button().click();
    info().open = false;
    await vi.advanceTimersByTimeAsync(0);
    expect(state()).toBe('idle');
    delayed.resolve(success()); await settle();
    expect(state()).toBe('idle');
    expect(report().hidden).toBe(true);
    info().open = true; button().click(); await settle();
    expect(state()).toBe('complete');
  });

  it('resets on hidden lifecycle, excludes legacy, and disables listeners on disposal', async () => {
    const delayed = pending();
    const load = vi.fn<typeof fetchSettingsAction>().mockImplementationOnce(() => delayed.promise)
      .mockResolvedValueOnce(success());
    const panel = setup(load); info().open = true; button().click();
    panel.stop();
    expect(state()).toBe('idle');
    delayed.resolve(success()); await settle();
    expect(state()).toBe('idle');
    panel.start(); select(panel, 'legacy');
    expect(button().disabled).toBe(true);
    expect(status().textContent).toContain('제외');
    button().dispatchEvent(new MouseEvent('click', { bubbles: true }));
    expect(calls(load)).toHaveLength(1);
    select(panel, 'all'); button().click(); await settle();
    expect(state()).toBe('complete');
    panel.dispose();
    expect(state()).toBe('idle');
    expect(button().disabled).toBe(true);
    button().dispatchEvent(new MouseEvent('click', { bubbles: true }));
    expect(calls(load)).toHaveLength(2);
  });

  it.each([
    ['quality_annotation_review_stale', '검사 중 수집 자료가 갱신'],
    ['quality_annotation_review_busy', '다른 태그 검사'],
    ['quality_annotation_review_capacity', '한도에 도달'],
  ])('explains %s without an automatic retry or a successful report', async (reason, message) => {
    const load = vi.fn<typeof fetchSettingsAction>().mockResolvedValueOnce(success())
      .mockResolvedValueOnce({ ok: false, schema: 3, reason,
        scope: { requested_scope: 'all', requested_project: null } });
    setup(load); info().open = true; button().click(); await settle();
    (report().querySelector('button.knowledge-quality-annotations') as HTMLButtonElement).click(); await settle();
    expect(state()).toBe('unavailable');
    expect(report().hidden).toBe(true);
    expect(status().textContent).toContain(message);
    expect(button().disabled).toBe(false);
    expect(calls(load)).toHaveLength(2);
  });

  it('exposes known errors and transport failures as unavailable, then allows retry', async () => {
    const load = vi.fn<typeof fetchSettingsAction>()
      .mockResolvedValueOnce({ ok: false, schema: 3, reason: 'quality_time_budget',
        scope: { requested_scope: 'all', requested_project: null } })
      .mockRejectedValueOnce(new Error('network error'))
      .mockResolvedValueOnce(success());
    setup(load); info().open = true;
    button().click(); await settle();
    expect(state()).toBe('unavailable');
    expect(report().hidden).toBe(true);
    expect(status().textContent).toContain('시간 제한');
    expect(button().disabled).toBe(false);
    button().click(); await settle();
    expect(state()).toBe('unavailable');
    expect(status().textContent).toContain('가져오지 못했습니다');
    button().click(); await settle();
    expect(state()).toBe('complete');
  });
});

describe('schema and scope validation must fail closed', () => {
  it('requires a reconciled empty-body count and rejects the old content policy', () => {
    const value = success();
    value.counts.noncontent_current_versions += 1;
    value.counts.empty_body_current_versions = 1;
    expect(parseCollectionQuality(value, all)?.ok).toBe(true);
    value.counts.empty_body_current_versions = 2;
    expect(parseCollectionQuality(value, all)).toBeNull();
    value.counts.empty_body_current_versions = 1;
    (value.content_evidence.policy as string) = 'source-content-evidence-v5';
    expect(parseCollectionQuality(value, all)).toBeNull();
    value.content_evidence.policy = 'source-content-evidence-v6';
    delete (value.counts as Partial<typeof value.counts>).empty_body_current_versions;
    expect(parseCollectionQuality(value, all)).toBeNull();
  });

  it('separates a project literally named all from the union and handles scoped errors', () => {
    const one = { scope: 'project', project: 'all' } as const;
    expect(parseCollectionQuality(success(one), one)).toMatchObject({ ok: true });
    expect(parseCollectionQuality(success(one), all)).toBeNull();
    expect(parseCollectionQuality(success(all), one)).toBeNull();
    expect(parseCollectionQuality({ ok: false, schema: 3, reason: 'quality_query_invalid',
      scope: { requested_scope: null, requested_project: null } }, one)).toMatchObject({ ok: false });
    expect(parseCollectionQuality({ ok: false, schema: 3, reason: 'quality_query_invalid',
      scope: { requested_scope: 'all', requested_project: null } }, one)).toBeNull();
    expect(parseCollectionQuality({ ok: false, schema: 3, reason: 'unknown_error',
      scope: { requested_scope: null, requested_project: null } }, one)).toBeNull();
  });

  it.each([
    ['missing body coverage', (item: any) => { delete item.body_matches; }],
    ['old body coverage policy', (item: any) => { item.body_matches.policy = 'title-only'; }],
    ['body match promoted to original identity', (item: any) => { item.body_matches.basis = 'verified_original_equivalence'; }],
    ['boolean body count', (item: any) => { item.body_matches.counts.eligible_current_versions = true; }],
    ['eligible bodies exceed current scope', (item: any) => { item.body_matches.counts.eligible_current_versions = 40; }],
    ['untitled bodies exceed eligible', (item: any) => { item.body_matches.counts.without_substantive_label = 21; }],
    ['reference bodies exceed eligible', (item: any) => { item.body_matches.counts.reference_only_current_versions = 21; }],
    ['body match claimed confirmed', (item: any) => { item.body_matches.stored_body.status = 'confirmed'; }],
    ['content members exceed eligible body records', (item: any) => { item.body_matches.stored_body.members = 21; }],
    ['reference members exceed eligible references', (item: any) => { item.body_matches.reference_text.members = 9; }],
    ['singleton groups', (item: any) => { item.body_matches.stored_body.groups = 6; }],
    ['members without groups', (item: any) => { item.body_matches.reference_text.groups = 0; }],
    ['groups without members', (item: any) => { item.body_matches.reference_text.members = 0; }],
    ['old report with ambiguous text candidates', (item: any) => { item.schema = 1; }],
    ['missing content policy', (item: any) => { delete item.content_evidence; }],
    ['unknown content policy', (item: any) => { item.content_evidence.policy = 'future-policy'; }],
    ['missing exclusion count', (item: any) => { delete item.counts.noncontent_current_versions; }],
    ['impossible exclusion partition', (item: any) => { item.counts.metadata_only_current_versions = 0; }],
    ['more exclusions than current versions', (item: any) => { item.counts.project_current_versions = 1; }],
    ['missing metadata matches', (item: any) => { delete item.metadata_matches; }],
    ['metadata match claimed content', (item: any) => { item.metadata_matches.within_context.status = 'review_candidate'; }],
    ['epoch milliseconds', (item: any) => { item.checked_at = 1_791_768_900_125; }],
    ['implausible epoch seconds', (item: any) => { item.checked_at = 42; }],
    ['string time', (item: any) => { item.checked_at = '1791768900'; }],
    ['missing count', (item: any) => { delete item.counts.memberships; }],
    ['negative count', (item: any) => { item.counts.relations = -1; }],
    ['fractional count', (item: any) => { item.counts.project_current_versions = 3.14; }],
    ['missing findings', (item: any) => { delete item.findings; }],
    ['negative finding', (item: any) => { item.findings.relation_evidence_invalid = -7; }],
    ['bad finding key', (item: any) => { item.findings['<img src=x>'] = 1; }],
    ['missing candidate status', (item: any) => { delete item.duplicate_candidates.same_raw_bytes.status; }],
    ['wrong candidate status', (item: any) => { item.duplicate_candidates.same_normalized_search_text.status = 'confirmed'; }],
    ['bad numeric status', (item: any) => { item.numeric_review_candidates.mixed_vector_dimensions.status = 'outlier'; }],
    ['bad normalization method', (item: any) => { item.normalization.method = 'aggressive_trim'; }],
    ['semantics claimed confirmed', (item: any) => { item.normalization.semantic_validity = 'verified'; }],
    ['missing coverage', (item: any) => { delete item.scope.coverage; }],
    ['wrong project echo', (item: any) => { item.scope.requested_project = 'foreign'; }],
    ['non-array project coverage', (item: any) => { item.scope.projects = 'one'; }],
    ['empty union coverage', (item: any) => { item.scope.projects = []; }],
    ['duplicate union coverage', (item: any) => { item.scope.projects = ['one', 'one']; }],
    ['over-budget union coverage', (item: any) => { item.scope.projects = Array.from({ length: 17 }, (_, i) => `project-${i}`); }],
    ['not project-only', (item: any) => { item.scope.filters = 'graph_filters'; }],
  ] as Array<[string, (item: any) => void]>)('rejects %s without showing an all-clear', (_name, alter) => {
    const item: any = success();
    alter(item);
    expect(parseCollectionQuality(item, all)).toBeNull();
  });

  it('rejects a wrong project list even with a matching requested project echo', () => {
    const query = { scope: 'project', project: 'all' } as const;
    const item = success(query);
    item.scope.projects = ['another-project'];
    expect(parseCollectionQuality(item, query)).toBeNull();
  });
});
