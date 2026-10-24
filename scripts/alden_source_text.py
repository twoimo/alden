"""Reversible source-specific text projection; never remove original bytes."""
import hashlib
import re
from urllib.parse import urlsplit

POLICY = 'threads-separated-numeric-tail-v1'
TEXT_VERSION = 'retained-local-original-text-v2'


def _legacy_threads_source(body, source):
    if (not isinstance(source, dict) or source.get('platform') != 'threads'
            or source.get('type') != 'source'
            or source.get('localTextOrigin') not in {'local_corpus_json', 'incremental_json'}
            or source.get('localOriginalText') != body):
        return False
    url = source.get('url')
    if not isinstance(url, str):
        return False
    try:
        parsed = urlsplit(url)
        path = re.fullmatch(r'/@[A-Za-z0-9_.]+/post/([A-Za-z0-9_-]+)', parsed.path)
        if (parsed.scheme != 'https' or parsed.hostname not in {'threads.com', 'www.threads.com', 'threads.net', 'www.threads.net'}
                or parsed.username or parsed.password or parsed.port is not None
                or parsed.query or parsed.fragment or path is None
                or source.get('code') != path[1]):
            return False
    except ValueError:
        return False
    return True


def project_source_text(body, source):
    """Separate a narrow legacy Threads UI-tail candidate, not all numbers.

    The body remains an extraction candidate, not a semantic verdict. The
    caller must bind this source object to retained bytes before using it.
    """
    if not isinstance(body, str):
        raise ValueError('source_text_invalid')
    unchanged = {'body': body, 'projection': None}
    if not _legacy_threads_source(body, source):
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



def _graph_reading_tail(body, source):
    """Fold a trailing run of one- and two-digit lines on a retained graph note.

    The original string stays in the archive. Three-digit measurements, years
    and short notes are not folded.
    """
    if (not isinstance(body, str) or not isinstance(source, dict)
            or source.get('platform') != 'graph' or source.get('localOriginalText') != body
            or len(body) > 12000 or '```' in body or '~~~' in body):
        return None
    lines = body.splitlines(keepends=True)
    end = len(lines)
    while end > 0 and lines[end - 1].strip() == '':
        end -= 1
    start = end
    while start > 0 and re.fullmatch(r'[0-9]{1,2}', lines[start - 1].strip() or ''):
        start -= 1
    numeric = end - start
    if not 3 <= numeric <= 6 or start == 0:
        return None
    # Slice on retained boundaries: rebuilding with LF breaks CRLF/CR sources.
    prefix = ''.join(lines[:start])
    reading = prefix.rstrip('\r\n')
    tail = body[len(reading):]
    if not body.startswith(prefix) or len(reading) < 80 or not re.search(r'[A-Za-z가-힣]', reading):
        return None

    sha = lambda value: hashlib.sha256(value.encode('utf-8')).hexdigest()
    return {'policy': 'graph-reading-numeric-lines-v1',
            'status': 'unverified_presentation_candidate',
            'basis': 'retained_graph_original_text',
            'original_preserved': True, 'semantic_validity': 'unverified',
            'reading': reading, 'tail': tail,
            'body_sha256': sha(body), 'reading_sha256': sha(reading),
            'tail_sha256': sha(tail)}


def reading_tail_presentation(body, source):
    """Optional reversible folding for a verified legacy source, never indexing.

    Numeric shape remains semantically unverified. Return exact displayed
    parts; do not change the authoritative body or search extraction policy.
    """
    if isinstance(source, dict) and source.get('platform') == 'graph':
        return _graph_reading_tail(body, source)
    if (not isinstance(body, str) or len(body) > 12000
            or not _legacy_threads_source(body, source)
            or '```' in body or '~~~' in body):
        return None
    # Bind the UI parts to the exact retained body, including outer whitespace.
    # A stripped copy cannot satisfy the reader's reading + tail == body fence.
    match = re.search(r'(?:(?:\r\n|[\r\n])[ \t\u00a0]*(?:[0-9]{1,4}|/)[ \t\u00a0]*)+[ \t\r\n\u00a0]*\Z', body)
    if match is None:
        match = re.search(r'\s+[0-9]{1,4}\s+/\s+[0-9]{1,4}(?:\s+[0-9]{1,4})+\s*\Z', body)
    if match is None:
        return None
    reading = body[:match.start()].rstrip()
    tail = body[len(reading):]
    tokens = tail.split()
    if (len(reading.strip()) < 80 or not re.search(r'[A-Za-z가-힣]', reading)
            or len(tail) > 128 or not 2 <= len(tokens) <= 8
            or sum(token != '/' for token in tokens) < 2
            or '/' in tokens and sum(token != '/' for token in tokens) < 3):
        return None
    sha = lambda value: hashlib.sha256(value.encode('utf-8')).hexdigest()
    return {'policy': 'legacy-threads-reading-tail-v1',
            'status': 'unverified_presentation_candidate',
            'basis': 'verified_retained_legacy_threads_record',
            'original_preserved': True, 'semantic_validity': 'unverified',
            'reading': reading, 'tail': tail,
            'body_sha256': sha(body), 'reading_sha256': sha(reading),
            'tail_sha256': sha(tail)}
