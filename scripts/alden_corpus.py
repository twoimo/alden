"""Read the published local corpus. History is quoted evidence, never input."""
from __future__ import annotations
import json
import re
import sqlite3
import unicodedata
from contextlib import closing, contextmanager
from pathlib import Path


def message_source_role(row) -> str:
    """Database feed notifications remain system records even with a member ID."""
    content = row.get('message')
    kind = row.get('message_type', row.get('type'))
    if type(kind) is int and kind == 0 and isinstance(content, str):
        try:
            feed = json.loads(content)
        except (ValueError, TypeError, RecursionError):
            feed = None
        if isinstance(feed, dict) and type(feed.get('feedType')) is int:
            return 'system_history'
    author = str(row.get('author_id') or '0')
    if not author.isascii() or not author.isdigit() or int(author) <= 0:
        return 'system_history'
    return 'outgoing_unclassified' if row.get('is_self') else 'peer_history'

MAX_ROOM_DISPLAY_IDS = 256


@contextmanager
def _published_rooms(root: Path, expected_account: str = ''):
    """Pin a fully published account/snapshot; never fall back to a legacy DB."""
    from auto_reply_knowledge_graph import _index_db_path
    if not isinstance(expected_account, str) or expected_account and not re.fullmatch(r'[0-9a-f]{64}', expected_account):
        raise ValueError('corpus_account_invalid')
    pointer = root / 'knowledge/corpus/current.json'
    if not pointer.exists() and not pointer.is_symlink():
        yield None, ''
        return
    path = _index_db_path(root)
    # _index_db_path validates the bounded pointer and account directory.
    with pointer.open('rb') as handle:
        payload = handle.read(8193)
    if len(payload) > 8192:
        raise RuntimeError('corpus_pointer_unsafe')
    manifest = json.loads(payload)
    account = manifest.get('account')
    if account != path.parent.name or expected_account and account != expected_account:
        raise RuntimeError('corpus_account_mismatch')
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=.2)) as db:
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='corpus_meta'").fetchone():
            raise RuntimeError('corpus_not_ready')
        meta = dict(db.execute("SELECT key,value FROM corpus_meta WHERE key IN ('account','snapshot','complete')"))
        if meta.get('account') != account:
            raise RuntimeError('corpus_account_mismatch')
        if meta.get('complete') != '1' or not manifest.get('snapshot') or meta.get('snapshot') != manifest['snapshot']:
            raise RuntimeError('corpus_not_ready')
        yield db, account


def room_displays(root: Path, room_ids, expected_account: str = '') -> dict[str, dict[str, str]]:
    """Return {numeric_id: {label, label_source}} from the published corpus.

    Accept at most 256 integer/string IDs, preserving identity rather than
    grouping by name. Missing IDs/publications return no entry. A mismatched
    account or incomplete publication raises RuntimeError; invalid input raises
    ValueError. No graph membership or active-room limit is involved.

    Sources: observed_title/observed_display (verified account/ID observation
    cache, with title versus peer/display provenance), snapshot (captured room
    display name), catalog (saved user title),
    catalog_history (saved past title), activity_alias (recent recorded peers),
    unresolved (identifiable archive label, not a confirmed title).
    A snapshot name may itself be a direct
    chat's peer display name; the current corpus does not distinguish this.
    Catalog files are capped at 64 KiB and history at 32 files. Only requested
    room metadata and at most 256 recent sender rows per unnamed room are read.
    Message bodies and the global author table are never read here.
    Optional observations are read from knowledge/corpus/<account>/room-observations.json
    (2 MiB/10000 rows, same snapshot, at most 24h old). The producer must attest
    source account and numeric ID independently; see _room_observation_displays
    for the schema. An unscoped local-chats array cannot supply observed names.
    Merged/retained observation rows must preserve their own observed_at; a new
    envelope timestamp does not renew their 24h validity. Legacy single-capture
    rows without that field inherit the envelope time. Snapshot matching remains
    exact even when the producer retains an older still-valid observation.
    """
    from auto_reply_knowledge_graph import _corpus_display_policy
    if isinstance(room_ids, (str, bytes)):
        raise ValueError('corpus_scope_invalid')
    requested = []
    for index, value in enumerate(room_ids):
        if index >= MAX_ROOM_DISPLAY_IDS:
            raise ValueError('corpus_room_display_limit')
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            raise ValueError('corpus_scope_invalid')
        number = str(value)
        if not re.fullmatch(r'[0-9]{1,19}', number) or not 0 < int(number) < 2**63:
            raise ValueError('corpus_scope_invalid')
        requested.append(str(int(number)))
    requested = list(dict.fromkeys(requested))
    if not requested:
        return {}
    with _published_rooms(Path(root), expected_account) as (db, account):
        if db is None:
            return {}
        keys = [f'kakao:{account}:room:{number}' for number in requested]
        placeholders = ','.join('?' for _ in keys)
        rows = db.execute(f'SELECT chat,chat_id,label FROM alden_rooms WHERE chat IN ({placeholders})', keys).fetchall()
        labels = {}
        for key, number, label in rows:
            if key != f'kakao:{account}:room:{number}':
                raise RuntimeError('corpus_room_identity_mismatch')
            labels[key] = label
        displays = _corpus_display_policy(db, Path(root), labels, expected_account=account)
        return {key.rsplit(':', 1)[-1]: {'label': info['label'], 'label_source': info['label_source']}
                for key, info in displays.items()}


def resolve_room(root: Path, query: str, *, expected_account: str = '') -> dict:
    """Resolve exact visible names only; never pick between equal names."""
    from auto_reply_knowledge_graph import _corpus_room_displays, _room_catalog_displays, _room_observation_displays, _identity_label
    with _published_rooms(Path(root), expected_account) as (db, account):
        if db is None:return {'state':'none','chat_id':''}
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='alden_rooms'").fetchone():return {'state':'none','chat_id':''}
        needle=_identity_label(query,limit=None).casefold().replace(' ','')
        found=[]
        labels=dict(db.execute('SELECT chat,label FROM alden_rooms WHERE chat LIKE ?', (f'kakao:{account}:room:%',)))
        observed=_room_observation_displays(db,root,labels,expected_account=account)
        displays=_corpus_room_displays(labels,_room_catalog_displays(root,labels,expected_account=account),observations=observed)
        for key,info in displays.items():
            for label in info['aliases']:
                if label==key:continue
                name=_identity_label(label).casefold().replace(' ','')
                if len(name)>=2 and name in needle:found.append((len(name),key.rsplit(':',1)[-1]))
        if not found:return {'state':'none','chat_id':''}
        longest=max(size for size,_ in found);ids={key for size,key in found if size==longest}
        return {'state':'resolved' if len(ids)==1 else 'ambiguous','chat_id':next(iter(ids)) if len(ids)==1 else '', 'matches':len(ids)}

def search(root: Path, query: str, *, chat_id: str = '', author_id: str = '', limit: int = 6, expected_account: str = '', cancelled=None, time_from=None, time_to=None) -> dict:
    if not isinstance(query,str) or len(query)>2048 or not 1<=limit<=20:
        raise ValueError('corpus_query_invalid')
    for value in (chat_id,author_id):
        if value and (not value.isascii() or not value.isdigit() or not 0<int(value)<2**63):
            raise ValueError('corpus_scope_invalid')
    from auto_reply_knowledge_graph import _index_db_path, _message_datetime_kst
    bounds=[]
    for value in (time_from,time_to):
        parsed=_message_datetime_kst(value) if value is not None else None
        if value is not None and parsed is None:raise ValueError('corpus_time_scope_invalid')
        bounds.append(parsed.timestamp() if parsed is not None else None)
    if bounds[0] is not None and bounds[1] is not None and bounds[0]>bounds[1]:raise ValueError('corpus_time_scope_invalid')
    path=_index_db_path(root)
    if not path.exists(): return {'ok':False,'reason':'corpus_not_ready','items':[]}
    terms=list(dict.fromkeys(re.findall(r'[^\W_]{2,}',unicodedata.normalize('NFKC',query),re.UNICODE)))[:16]
    if not terms and not (chat_id or author_id): return {'ok':True,'items':[]}
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=.2) as db:
        db.execute('PRAGMA query_only=ON')
        if cancelled is not None:db.set_progress_handler(lambda:1 if cancelled() else 0,1000)
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='alden_messages'").fetchone():
            return {'ok':False,'reason':'corpus_not_ready','items':[]}
        account=db.execute("SELECT value FROM corpus_meta WHERE key='account'").fetchone()[0]
        if expected_account and account!=expected_account: return {'ok':False,'reason':'corpus_account_mismatch','items':[]}
        params=[];where=[]
        if terms:
            table='context_messages_fts f JOIN alden_messages m ON m.id=f.rowid'
            where.append('context_messages_fts MATCH ?');params.append(' OR '.join('"'+word+'"' for word in terms))
            order='bm25(context_messages_fts),m.id DESC'
        else: table='alden_messages m';order='m.id DESC'
        if chat_id:where.append('m.chat_id=?');params.append(str(int(chat_id)))
        if author_id:where.append('m.author_id=?');params.append(str(int(author_id)))
        if any(value is not None for value in bounds):
            def epoch(value):
                parsed=_message_datetime_kst(value)
                return parsed.timestamp() if parsed is not None else None
            db.create_function('alden_epoch',1,epoch,deterministic=True)
            for value,operator in zip(bounds,('>=','<=')):
                if value is not None:where.append('alden_epoch(m.date) '+operator+' ?');params.append(value)
        sql='SELECT m.chat_id,m.log_id,m.author_id,m.user_name,m.message,m.date,m.is_self,m.message_type FROM '+table
        if where:sql+=' WHERE '+' AND '.join(where)
        sql+=' ORDER BY '+order+' LIMIT ?';params.append(limit)
        items=[];budget=4800
        for room,log,actor,name,text,date,self_row,kind in db.execute(sql,params):
            excerpt=str(text)[:min(1000,budget)];budget-=len(excerpt)
            observed=_message_datetime_kst(date)
            items.append({'source_id':f'kakao:{account}:room:{room}:log:{log}','chat_id':room,'log_id':log,'author_id':actor,'sender':name,'content':excerpt,'date':observed.isoformat() if observed is not None else str(date),'source_date':date,'truncated':len(excerpt)<len(str(text)),
                          'source_kind':'local_db_snapshot','source_role':message_source_role({'message':text,'message_type':kind,'author_id':actor,'is_self':self_row}),'message_type':kind})
            if budget<=0:break
        return {'ok':True,'mode':'bm25','account':account,'items':items}
