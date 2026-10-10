"""Query-time ordering of dated observations. Never merges or deletes sources.

An observation date is not a claim's valid period or a supersession edge.
Missing dates and ambiguous people keep all candidates, with an explicit warning.
"""
from __future__ import annotations
from datetime import datetime, timezone, timedelta
import math
import re
import time
import unicodedata


def _term(value):
    return ''.join(unicodedata.normalize('NFKC',str(value)).casefold().split())


def latest_requested(query, time_from=None, time_to=None):
    if time_from is not None or time_to is not None:return False
    text=str(query).casefold()
    if re.search(r'이전|과거|변경|추이|역사|비교|\b(?:history|historical|compare|comparison)\b',text):return False
    return bool(re.search(r'최신|가장\s*최근|제일\s*최근|\blatest\b|\bmost\s+recent\b',text))


def _interval(value, now):
    if type(value) in (int,float):
        return (float(value),float(value)) if math.isfinite(value) and 0<value<=now else None
    if not isinstance(value,str) or not value.strip():return None
    try:
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}',value):
            # Date-only sources do not attest a timezone or within-day order.
            day=datetime.strptime(value,'%Y-%m-%d').replace(tzinfo=timezone.utc)
            bounds=((day-timedelta(hours=14)).timestamp(),(day+timedelta(days=1,hours=14)).timestamp())
            return bounds if bounds[0]<=now else None
        stamp=datetime.fromisoformat(value.strip().replace('Z','+00:00'))
        if stamp.tzinfo is None:return None
        seconds=stamp.timestamp()
        return (seconds,seconds) if 0<seconds<=now else None
    except (ValueError,OverflowError):return None


def select_latest(query, records, *, needles=None, time_from=None, time_to=None, now=None):
    """Select the newest attested observation for each exact query mention.

    `records` are already permission/identity/retraction-filtered by the caller.
    Names/aliases establish lexical relevance only, never shared identity.
    `observed_at` must come with its original basis, not an indexing timestamp.
    """
    ids=[row['id'] for row in records]
    policy={'mode':'all','basis':'relevance','excluded_older_observations':0}
    if not latest_requested(query,time_from,time_to):return ids,policy
    policy={'mode':'latest_observation','basis':'attested observation date; not claim validity or supersession',
            'excluded_older_observations':0,'matched_mentions':0,'undetermined_mentions':0,
            'ambiguous_identity_mentions':0,'claim_validity':'not_established_by_observation_date'}
    now=time.time() if now is None else now
    policy['conflicting_mentions']=0
    haystacks=[_term(text) for text in needles or [query]]
    groups={}
    for row in records:
        for raw in [row.get('name',''),*(row.get('aliases') or [])[:32]]:
            term=_term(raw)
            if 2<=len(term)<=1024 and any(term in text for text in haystacks):
                groups.setdefault(term,{})[row['id']]=row
    # A more specific matched phrase can contain a generic alias. Do not let
    # that shorter phrase independently discard a specifically requested record.
    if len(groups)>128:
        policy['undetermined_mentions']=1;policy['mention_budget_exceeded']=True;return ids,policy
    def independently_mentioned(term):
        for text in haystacks:
            start=text.find(term)
            while start>=0:
                if not any(term!=longer and term in longer and any(
                    match.start()<=start and start+len(term)<=match.end()
                    for match in re.finditer(re.escape(longer),text)) for longer in groups):return True
                start=text.find(term,start+1)
        return False
    terms=[term for term in groups if independently_mentioned(term)]
    selected,excluded=set(),set()
    for term in terms:
        group=list(groups[term].values());policy['matched_mentions']+=1
        if any(row.get('has_conflict') for row in group):
            policy['conflicting_mentions']+=1;continue
        if len(group)>1 and any(row.get('identity_kind')=='person' for row in group):
            policy['ambiguous_identity_mentions']+=1;continue
        scopes=[set(scope for name,values in row.get('mention_scopes',{}).items() if _term(name)==term for scope in values) for row in group]
        if any(scopes) and not set.intersection(*scopes):
            policy['ambiguous_identity_mentions']+=1;continue
        intervals=[_interval(row.get('observed_at'),now) if row.get('time_basis') else None for row in group]
        if any(value is None for value in intervals):
            policy['undetermined_mentions']+=1;continue
        boundary=max(value[0] for value in intervals)
        for row,interval in zip(group,intervals):
            (selected if interval[1]>=boundary else excluded).add(row['id'])
    excluded-=selected
    excluded-={row['id'] for row in records if row.get('pinned')}
    policy['excluded_older_observations']=len(excluded)
    policy['observation_basis']=sorted({str(row['time_basis']) for row in records if row['id'] in selected})
    if not terms:policy['undetermined_mentions']+=1
    return [identity for identity in ids if identity in selected and identity not in excluded]+[
        identity for identity in ids if identity not in selected and identity not in excluded],policy


def observation_note(stamp, basis):
    labels={'record_confirmed_at':'기록 확인 시각','source_published_at':'출처 발행 시각','note_revision_at':'문서 개정 시각'}
    if not basis or _interval(stamp,time.time()) is None:return '시점 미확인 기록(현재 효력을 단정할 수 없음): '
    if type(stamp) in (int,float) and math.isfinite(stamp):
        stamp=datetime.fromtimestamp(stamp,timezone.utc).isoformat()
    return f'{labels.get(basis,"관측 시각")} {str(stamp)[:64]}(현재 효력의 증명은 아님): '
