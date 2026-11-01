"""Source declarations and typed descriptions; candidates are not truth verdicts."""
import json
import re
from alden_collection import normalized_text

POLICY = 'source-content-evidence-v6'
ANNOTATION_POLICY = 'retained-declared-tags-v1'
ANNOTATION_FIELDS = ('topics', 'entityTags')
DERIVED_SUMMARY_COVERAGE = 'existing privacy-filtered derived metadata; original bytes not opened'
UNREAD_BODY_MARKERS = frozenset({'경로만 색인한 파일. 본문은 읽지 않았습니다.'})
METADATA_DESCRIPTIONS = {
    'code': frozenset({'현재 체크아웃의 소스 파일'}),
    'hub': frozenset({'파일 시스템의 디렉터리'}),
}


def _annotation_context_bytes(values):
    # Count expansion in the fenced model payload, not just the raw JSON list.
    return len(json.dumps(values, ensure_ascii=False, separators=(',', ':'))
               .replace('<', r'\u003c').replace('>', r'\u003e').replace('&', r'\u0026').encode('utf-8'))


def declared_source_annotations(source, *, max_field_bytes=None):
    """Describe only retained declarations; caller verifies original bytes."""
    fields = {}
    for key in ANNOTATION_FIELDS:
        state, values = 'undeclared', []
        if not isinstance(source, dict):
            state = 'unsupported'
        elif key in source:
            value = source[key]
            if not isinstance(value, list):
                state = 'unsupported'
            elif len(value) > 32:
                state = 'over_budget'
            elif any(not isinstance(v, str) or not v for v in value):
                state = 'unsupported'
            elif any(len(v) > 128 for v in value):
                state = 'over_budget'
            elif max_field_bytes is not None and _annotation_context_bytes(value) > max_field_bytes:
                state = 'over_budget'
            else:
                state, values = 'declared', list(value)
        fields[key] = {'state': state, 'values': values}
    return {'policy': ANNOTATION_POLICY, 'basis': 'verified_retained_record',
            'semantic_status': 'unverified', 'fields': fields}


def validated_source_annotations(value, *, max_field_bytes=None):
    """Copy the bounded declaration contract without unknown data fields."""
    if (not isinstance(value, dict) or value.get('policy') != ANNOTATION_POLICY
            or value.get('basis') != 'verified_retained_record'
            or value.get('semantic_status') != 'unverified'
            or not isinstance(value.get('fields'), dict)
            or set(value['fields']) != set(ANNOTATION_FIELDS)):
        raise ValueError('source_annotations_invalid')
    fields = {}
    for key in ANNOTATION_FIELDS:
        field = value['fields'][key]
        if (not isinstance(field, dict) or field.get('state') not in
                ('declared', 'undeclared', 'unsupported', 'over_budget')
                or not isinstance(field.get('values'), list) or len(field['values']) > 32
                or any(not isinstance(v, str) or not v or len(v) > 128 for v in field['values'])
                or field['state'] != 'declared' and field['values']):
            raise ValueError('source_annotations_invalid')
        if max_field_bytes is not None and _annotation_context_bytes(field['values']) > max_field_bytes:
            raise ValueError('source_annotations_invalid')
        fields[key] = {'state': field['state'], 'values': list(field['values'])}
    return {'policy': ANNOTATION_POLICY, 'basis': 'verified_retained_record',
            'semantic_status': 'unverified', 'fields': fields}
# Whole descriptions observed in the retained file graph. Quoted occurrences
# and unknown record kinds remain separate; an indexed flag is not body proof.


def _declares_noncontent(value):
    if not isinstance(value, dict):
        return False
    indexed = value.get('content_indexed')
    return (value.get('kind') == 'metadata' or indexed is False
            or type(indexed) is int and indexed == 0)


def content_evidence_class(metadata, body, *, source=None, body_source='stored_body'):
    """Keep metadata/name matches apart from substantive content candidates.

    Inspect the admitted projection and, when supplied, its already verified
    retained record. Never follow a source path or infer content from a
    filename, a vector or a missing declaration. The caller binds source bytes.
    A retained-record body_source requires the caller to verify the raw hash
    and the actual localOriginalText, not just a disposable cache stamp.
    """
    if (not isinstance(metadata, dict) or not isinstance(body, str)
            or body_source not in ('stored_body', 'retained_record.localOriginalText')):
        raise ValueError('content_evidence_invalid')
    if _declares_noncontent(metadata):
        return 'metadata_only'
    if _declares_noncontent(source):
        return 'source_metadata_only'
    text = normalized_text(body)
    if text in UNREAD_BODY_MARKERS:
        return 'unread_body_marker'
    if (metadata.get('coverage') == DERIVED_SUMMARY_COVERAGE
            and body_source != 'retained_record.localOriginalText'):
        return 'metadata_description'
    kinds = [metadata.get('kind')]
    if isinstance(source, dict):
        kinds.append(source.get('kind'))
    if any(isinstance(kind, str) and text in METADATA_DESCRIPTIONS.get(kind, ()) for kind in kinds):
        return 'metadata_description'
    if not text:
        return 'empty_body'
    # Literal addresses are retained source references, not fetched articles.
    # This is a text-shape boundary; URLs and their identifiers stay untouched.
    if re.fullmatch(r'https?://[^\s]+(?:\s+https?://[^\s]+)*', text, flags=re.IGNORECASE):
        return 'reference_only'
    return 'content_candidate'
