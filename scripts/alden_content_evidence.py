"""Source declarations and typed descriptions; candidates are not truth verdicts."""
from alden_collection import normalized_text

POLICY = 'source-content-evidence-v4'
DERIVED_SUMMARY_COVERAGE = 'existing privacy-filtered derived metadata; original bytes not opened'
UNREAD_BODY_MARKERS = frozenset({'경로만 색인한 파일. 본문은 읽지 않았습니다.'})
METADATA_DESCRIPTIONS = {
    'code': frozenset({'현재 체크아웃의 소스 파일'}),
    'hub': frozenset({'파일 시스템의 디렉터리'}),
}
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
    return 'content_candidate'
