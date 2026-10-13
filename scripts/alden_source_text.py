"""Reversible source-specific text projection; never remove original bytes."""
import hashlib
import re
from urllib.parse import urlsplit

POLICY = 'threads-separated-numeric-tail-v1'
TEXT_VERSION = 'retained-local-original-text-v2'


def project_source_text(body, source):
    """Separate a narrow legacy Threads UI-tail candidate, not all numbers.

    The body remains an extraction candidate, not a semantic verdict. The
    caller must bind this source object to retained bytes before using it.
    """
    if not isinstance(body, str):
        raise ValueError('source_text_invalid')
    unchanged = {'body': body, 'projection': None}
    if (not isinstance(source, dict) or source.get('platform') != 'threads'
            or source.get('type') != 'source'
            or source.get('localTextOrigin') != 'local_corpus_json'
            or source.get('localOriginalText') != body):
        return unchanged
    url = source.get('url')
    if not isinstance(url, str):
        return unchanged
    try:
        parsed = urlsplit(url)
        path = re.fullmatch(r'/@[A-Za-z0-9_.]+/post/([A-Za-z0-9_-]+)', parsed.path)
        if (parsed.scheme != 'https' or parsed.hostname not in {'threads.com', 'www.threads.com', 'threads.net', 'www.threads.net'}
                or parsed.username or parsed.password or parsed.port is not None
                or parsed.query or parsed.fragment or path is None
                or source.get('code') != path[1]):
            return unchanged
    except ValueError:
        return unchanged
    # Legacy exports have NBSP at a sentence end or on its own line. Require
    # that exact boundary followed by a newline; ordinary blanks cannot match.
    at = body.rfind('\u00a0')
    if at < 0 or not body[at+1:].startswith(('\n', '\r\n')):
        return unchanged
    prefix = body[:at]
    if not prefix.endswith(('\n', '\r', '.', '!', '?', '。')):
        return unchanged
    values = body[at+1:].splitlines()[1:]
    if (len(prefix.strip()) < 80 or not re.search(r'[A-Za-z가-힣]', prefix)
            or '```' in prefix or '~~~' in prefix or len(values) not in (6, 7)
            or values[1] != '/' or any(re.fullmatch(r'[0-9]{1,7}', value) is None
                                         for i, value in enumerate(values) if i != 1)):
        return unchanged
    page, pages = int(values[0]), int(values[2])
    if not 1 <= page <= pages <= 1000:
        return unchanged
    # Keep the exact separated characters, including the preceding newline.
    clean = prefix.rstrip('\r\n')
    tail = body[len(clean):]
    sha = lambda value: hashlib.sha256(value.encode('utf-8')).hexdigest()
    return {'body': clean, 'projection': {
        'policy': POLICY, 'status': 'structural_tail_candidate',
        'basis': 'retained_threads_local_corpus_nbsp_page_and_counters',
        'original_body_sha256': sha(body), 'body_sha256': sha(clean),
        'separated_tail_sha256': sha(tail), 'separated_tail': tail,
        'removed_characters': len(tail), 'counter_count': len(values) - 3,
        'original_preserved': True, 'semantic_validity': 'unverified',
    }}
