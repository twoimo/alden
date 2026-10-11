"""Source-declared exclusions; a candidate is never a semantic truth verdict."""
from alden_collection import normalized_text

POLICY = 'source-content-evidence-v1'
UNREAD_BODY_MARKERS = frozenset({'경로만 색인한 파일. 본문은 읽지 않았습니다.'})


def content_evidence_class(metadata, body):
    """Keep metadata/name matches apart from substantive content candidates.

    Inspect the admitted projection only. Never follow a source path, read raw
    files or infer content from a filename, a vector or a missing declaration.
    """
    if not isinstance(metadata, dict) or not isinstance(body, str):
        raise ValueError('content_evidence_invalid')
    indexed = metadata.get('content_indexed')
    if (metadata.get('kind') == 'metadata' or indexed is False
            or type(indexed) is int and indexed == 0):
        return 'metadata_only'
    if normalized_text(body) in UNREAD_BODY_MARKERS:
        return 'unread_body_marker'
    return 'content_candidate'
