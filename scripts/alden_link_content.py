"""Bind fetched link bytes to bounded, explicitly scoped text evidence.

No fetching, model calls, code execution or attachment permissions live here.
The caller retains DNS/redirect/cancellation guards and owns supplemental reads.
"""
from __future__ import annotations
import hashlib
from html.parser import HTMLParser
import math
import re
import time
import unicodedata
from urllib.parse import urlsplit

MAX_SOURCE_BYTES = 1_000_000
MAX_TEXT_BYTES = 16_000
SCOPES = {'page_text', 'metadata', 'captions_excerpt', 'readme_excerpt'}


class LinkContentUnavailable(ValueError):
    pass


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.head = 0
        self.hidden = 0
        self.title_depth = 0
        self.title = []
        self.parts = []
        self.meta = {}

    def handle_starttag(self, tag, attrs):
        if tag == 'head': self.head += 1
        if tag == 'title': self.title_depth += 1
        if tag in {'script', 'style', 'template', 'noscript'}: self.hidden += 1
        if tag == 'meta':
            values = dict(attrs)
            key = values.get('property') or values.get('name')
            if key in {'og:title', 'og:description'} and isinstance(values.get('content'), str):
                self.meta[key] = values['content'][:4000]

    def handle_endtag(self, tag):
        if tag == 'head': self.head = max(0, self.head - 1)
        if tag == 'title': self.title_depth = max(0, self.title_depth - 1)
        if tag in {'script', 'style', 'template', 'noscript'}: self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if self.hidden: return
        if self.title_depth: self.title.append(data)
        elif not self.head: self.parts.append(data)


def _clean(text):
    return ' '.join(text.split())


def _identity(url, source_hash, supplement_hash):
    return 'link:' + hashlib.sha256((url + '\0' + source_hash + '\0' + supplement_hash).encode()).hexdigest()


def observed_preview(url, raw, *, title, text, scopes, supplemental='', truncated=False):
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_SOURCE_BYTES:
        raise LinkContentUnavailable('link_source_budget')
    parsed = urlsplit(url)
    if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password:
        raise LinkContentUnavailable('link_source_identity_invalid')
    title = _clean(title)[:160]
    text = text.strip()
    encoded = text.encode('utf-8')
    limited = encoded[:MAX_TEXT_BYTES].decode('utf-8', 'ignore').rstrip()
    source_hash = hashlib.sha256(raw).hexdigest()
    supplement_hash = hashlib.sha256(supplemental.encode()).hexdigest()
    observation = {'source_sha256': source_hash, 'source_bytes': len(raw),
                   'supplement_sha256': supplement_hash, 'supplement_basis': 'extracted_text',
                   'observed_at': time.time(), 'text_sha256': hashlib.sha256(limited.encode()).hexdigest(),
                   'text_bytes': len(limited.encode()), 'content_scope': list(scopes),
                   'truncated': truncated or len(encoded) > MAX_TEXT_BYTES}
    return {'url': url, 'title': title, 'text': limited, 'complete': bool(title or limited),
            'evidence_id': _identity(url, source_hash, supplement_hash), 'observation': observation}


def html_preview(url, raw, *, readme=''):
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_SOURCE_BYTES:
        raise LinkContentUnavailable('link_source_budget')
    if raw.startswith((b'%PDF-', b'\x1f\x8b', b'PK\x03\x04')):
        raise LinkContentUnavailable('link_format_unavailable')
    encoding = 'utf-16' if raw.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8-sig'
    declaration = re.search(br'<meta\b[^>]*charset\s*=\s*["\']?([A-Za-z0-9_-]+)', raw[:4096], re.I)
    if declaration:
        declared = declaration[1].decode('ascii').lower()
        if declared not in {'utf-8', 'utf8', 'euc-kr', 'cp949', 'iso-8859-1', 'windows-1252'}:
            raise LinkContentUnavailable('link_encoding_unavailable')
        encoding = declared
    try: source = raw.decode(encoding)
    except UnicodeError as error:
        raise LinkContentUnavailable('link_encoding_unavailable') from error
    if any(unicodedata.category(char) == 'Cc' and char not in '\n\r\t' for char in source):
        raise LinkContentUnavailable('link_binary_content')
    parser = _Text(); parser.feed(source); parser.close()
    title = _clean(''.join(parser.title)) or parser.meta.get('og:title', '')
    body = _clean(' '.join(parser.parts))
    description = _clean(parser.meta.get('og:description', ''))
    scopes = ['page_text'] if body else ['metadata']
    if readme: scopes.append('readme_excerpt')
    text = '\n'.join(part for part in (description, body, readme) if part)
    return observed_preview(url, raw, title=title, text=text, scopes=scopes,
                            supplemental=readme, truncated=bool(readme))


def validated_preview(value):
    if not isinstance(value, dict) or not isinstance(value.get('observation'), dict): return None
    o = value['observation']
    if set(value) - {'url', 'title', 'text', 'complete', 'evidence_id', 'observation', 'captions'}:
        return None
    if set(o) != {'source_sha256', 'source_bytes', 'supplement_sha256', 'supplement_basis',
                  'observed_at', 'text_sha256', 'text_bytes', 'content_scope', 'truncated'}:
        return None
    try:
        url = value['url']; parsed = urlsplit(url)
        text = value['text']; data = text.encode('utf-8')
        if (parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password
                or not isinstance(value['title'], str) or len(value['title']) > 160
                or type(value['complete']) is not bool or value['complete'] != bool(value['title'] or text)
                or len(data) > MAX_TEXT_BYTES or type(o['truncated']) is not bool
                or any(unicodedata.category(c) == 'Cc' and c not in '\n\r\t' for c in text + value['title'])
                or ('captions' in value and type(value['captions']) is not bool)
                or type(o['source_bytes']) is not int or not 0 < o['source_bytes'] <= MAX_SOURCE_BYTES
                or type(o['text_bytes']) is not int or o['text_bytes'] != len(data)
                or o['text_sha256'] != hashlib.sha256(data).hexdigest()
                or not isinstance(o['content_scope'], list) or not o['content_scope'] or len(o['content_scope']) > 4
                or any(scope not in SCOPES for scope in o['content_scope'])
                or type(o['observed_at']) not in (int, float) or not math.isfinite(o['observed_at']) or o['observed_at'] <= 0
                or o['supplement_basis'] != 'extracted_text'
                or any(not isinstance(o[key], str) or not re.fullmatch('[0-9a-f]{64}', o[key]) for key in ('source_sha256', 'supplement_sha256'))
                or value['evidence_id'] != _identity(url, o['source_sha256'], o['supplement_sha256'])):
            return None
    except (KeyError, TypeError, ValueError, AttributeError, UnicodeError): return None
    return {**value, 'observation': {**o, 'content_scope': list(o['content_scope'])}}


def trim_preview(value):
    """Keep source identity while explicitly changing the delivered excerpt."""
    value = validated_preview(value)
    if value is None: raise LinkContentUnavailable('link_evidence_invalid')
    if len(value['text']) <= 1: return value
    text = value['text'][:max(1, len(value['text']) // 2)]
    o = {**value['observation'], 'truncated': True, 'text_bytes': len(text.encode()),
         'text_sha256': hashlib.sha256(text.encode()).hexdigest()}
    return {**value, 'text': text, 'observation': o}
