"""Reversible local triage, not a semantic truth or entity-merge classifier."""
from dataclasses import dataclass
import hashlib
import re
import unicodedata

POLICY = 'messenger-triage-nfc-v3'
ACKNOWLEDGEMENTS = frozenset({'네', '넵', '예', '응', 'ㅇㅇ', '감사합니다', '고마워요',
    '안녕하세요', '수고하세요', '넵 감사합니다', '확인했습니다', '감사합니다!', '네!', '넵!'})
PROMOTIONS = ('수익 보장', '수익보장', '원금 보장', '원금보장', '리딩방 입장', '지금 입장하세요')
TOPIC = re.compile(r'회의|마감|일정|계약|결정|공지|자료|설명|방법|원리|주의|변경|정책|업데이트|가격|배송|주문|운영|동기화')
URL = re.compile(r'https?://\S+')


def text_without_urls(text):
    """Evidence/label view only; canonical body and content hash retain URLs."""
    return URL.sub(' ', text).strip()


@dataclass(frozen=True)
class Triage:
    text: str
    content_hash: str | None
    state: str
    reason: str


def classify(body):
    if body is None:
        return Triage('', None, 'withheld', 'canonical_reader_withheld')
    if not isinstance(body, str):
        raise ValueError('messenger_body_invalid')
    text = unicodedata.normalize('NFC', body)
    content_hash = hashlib.sha256(text.encode()).hexdigest() if text else None
    view = text.strip()  # Classification/presentation only, never content identity.
    if not view:
        return Triage(text, content_hash, 'context', 'empty_or_non_text')
    if view in ACKNOWLEDGEMENTS or re.fullmatch(r'[ㅋㅎㅠㅜ\s.!?~]+', view):
        return Triage(text, content_hash, 'context', 'standalone_acknowledgement')
    if not any(character.isalpha() for character in text):
        # A number/date/time can be important in its surrounding conversation.
        # Keep it in the source ledger rather than deleting it as an outlier.
        return Triage(text, content_hash, 'context', 'numeric_or_symbol_context')
    if re.fullmatch(r'https?://\S+', view):
        return Triage(text, content_hash, 'context', 'link_content_not_acquired')
    substance = text_without_urls(text)
    if not any(character.isalpha() for character in substance):
        return Triage(text, content_hash, 'context', 'link_content_not_acquired')
    if len(text) > 100000 or any(phrase in text for phrase in PROMOTIONS):
        return Triage(text, content_hash, 'review', 'size_or_promotional_signature')
    compact = ''.join(substance.split())
    if len(compact) >= 24 and len(set(compact)) <= 3:
        return Triage(text, content_hash, 'review', 'low_character_diversity')
    if len(substance) >= 40 or len(substance) >= 8 and (TOPIC.search(substance) or any(c.isdigit() for c in substance)):
        return Triage(text, content_hash, 'candidate', 'substantive_text_candidate')
    return Triage(text, content_hash, 'context', 'conversation_context_needed')
