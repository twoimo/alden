"""Separate a closed explicit collection lookup wrapper from its search terms.

This is query preparation only. The caller retains intent, scope and source
authority, and keeps the original request in the model messages and ledger.
"""

import hashlib
import re
import unicodedata

POLICY = 'explicit-collection-query-v1'
# Existing intent truncates before NFC; composition can expand that input.
MAX_QUERY_CHARS = 4096
_CARRIER = (r'(?:저장된|수집된|보관된)\s+(?:자료|파일|문서|원문|노트)'
            r'\s*(?:중에서|에서|중|의)\s+')
_VERB = re.compile(r'\s+(?:찾아줘|검색해줘|확인해줘)(?:요)?[.!?]?\Z')
_OUTPUT = re.compile(r'(?:\A|\s+)(?:원본\s+)?(?:SHA-?256|해시)(?:을|를)?\Z', re.I)


def scoped_collection_query(query, *, projects):
    """Return a fresh query and hash-only trace; unknown syntax is unchanged."""
    if (not isinstance(query, str) or not query.strip() or len(query) > MAX_QUERY_CHARS
            or not isinstance(projects, (list, tuple))):
        raise ValueError('knowledge_query_invalid')
    original = unicodedata.normalize('NFC', query).strip()
    prepared = original
    names = {unicodedata.normalize('NFC', name).strip()
             for name in projects[:128]
             if isinstance(name, str) and name.strip() and len(name) <= 1024}
    matches = []
    if not any(char in original for char in '\r\n'):
        for name in names:
            prefix = re.match(r'(?:프로젝트\s+)?' + re.escape(name)
                              + r'(?:\s+프로젝트)?\s+' + _CARRIER,
                              original, re.I)
            if prefix is not None:
                matches.append((len(name), name, original[prefix.end():]))
        if matches:
            longest = max(length for length, _, _ in matches)
            choices = [tail for length, _, tail in matches if length == longest]
            if len(choices) == 1:
                tail = choices[0]
                verb = _VERB.search(tail)
                if verb is not None:
                    payload = tail[:verb.start()].strip()
                    output = _OUTPUT.search(payload)
                    if output is not None:
                        payload = payload[:output.start()].strip()
                    if payload:
                        prepared = payload
    digest = lambda value: hashlib.sha256(value.encode('utf-8')).hexdigest()
    return {'query': prepared, 'trace': {
        'policy': POLICY, 'derived': prepared != original,
        'request_sha256': digest(original), 'retrieval_sha256': digest(prepared),
    }}
