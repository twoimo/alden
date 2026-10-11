"""Source-declared exclusions; a candidate is never a semantic truth verdict."""
from alden_collection import normalized_text

POLICY = 'source-content-evidence-v2'
UNREAD_BODY_MARKERS = frozenset({'경로만 색인한 파일. 본문은 읽지 않았습니다.'})


def _declares_noncontent(value):
    if not isinstance(value, dict):
        return False
    indexed = value.get('content_indexed')
    return (value.get('kind') == 'metadata' or indexed is False
            or type(indexed) is int and indexed == 0)


def content_evidence_class(metadata, body, *, source=None):
    """Keep metadata/name matches apart from substantive content candidates.

    Inspect the admitted projection and, when supplied, its already verified
    retained record. Never follow a source path or infer content from a
    filename, a vector or a missing declaration. The caller binds source bytes.
    """
    if not isinstance(metadata, dict) or not isinstance(body, str):
        raise ValueError('content_evidence_invalid')
    if _declares_noncontent(metadata):
        return 'metadata_only'
    if _declares_noncontent(source):
        return 'source_metadata_only'
    if normalized_text(body) in UNREAD_BODY_MARKERS:
        return 'unread_body_marker'
    return 'content_candidate'
