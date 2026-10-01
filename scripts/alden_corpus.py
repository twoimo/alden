"""Read the published local corpus. History is quoted evidence, never input."""
from __future__ import annotations
import json
import re
import sqlite3
import unicodedata
from pathlib import Path

def search(root: Path, query: str, *, chat_id: str = '', author_id: str = '', limit: int = 6, expected_account: str = '') -> dict:
    if not isinstance(query,str) or len(query)>2048 or not 1<=limit<=20:
        raise ValueError('corpus_query_invalid')
    for value in (chat_id,author_id):
        if value and (not value.isascii() or not value.isdigit() or not 0<int(value)<2**63):
            raise ValueError('corpus_scope_invalid')
    from auto_reply_knowledge_graph import _index_db_path
    path=_index_db_path(root)
    if not path.exists(): return {'ok':False,'reason':'corpus_not_ready','items':[]}
    terms=list(dict.fromkeys(re.findall(r'[^\W_]{2,}',unicodedata.normalize('NFKC',query),re.UNICODE)))[:16]
    if not terms and not (chat_id or author_id): return {'ok':True,'items':[]}
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=.2) as db:
        db.execute('PRAGMA query_only=ON')
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
        sql='SELECT m.chat_id,m.log_id,m.author_id,m.user_name,m.message,m.date,m.is_self,m.message_type FROM '+table
        if where:sql+=' WHERE '+' AND '.join(where)
        sql+=' ORDER BY '+order+' LIMIT ?';params.append(limit)
        items=[];budget=4800
        for room,log,actor,name,text,date,self_row,kind in db.execute(sql,params):
            excerpt=str(text)[:min(1000,budget)];budget-=len(excerpt)
            items.append({'source_id':f'kakao:{account}:room:{room}:log:{log}','chat_id':room,'log_id':log,'author_id':actor,'sender':name,'content':excerpt,'date':date,'truncated':len(excerpt)<len(str(text)),
                          'source_kind':'local_db_snapshot','source_role':'outgoing_unclassified' if self_row else 'peer_history' if int(actor)>0 else 'system_history','message_type':kind})
            if budget<=0:break
        return {'ok':True,'mode':'bm25','account':account,'items':items}
