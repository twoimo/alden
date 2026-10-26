"""Full authorized source archives in separately owned GBrain generations.

Original collections stay read-only. This is an explicit full rebuild/import,
not a display packet, background collector, global host, or delta scheduler.
"""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time
import uuid

from alden_collection import CollectionStore,encoded
from alden_collection_export import (export_graph,verify_export,verify_current_export,
    ExportError,_sha_file,_file_token,_stamp)
from alden_gbrain_bridge import GbrainGraphReader
from alden_gbrain_cache import (ManagedGbrainCache,_UnreapedWorker,_write,_read,
    _projects,_sha)
from alden_gbrain_client import (GbrainError,GbrainReadClient,PROTOCOL,VERSION,
    _Stdio,_json,_path,validate_binary,validate_runtime,TRAVERSE_ROW_LIMIT,LINK_CONTEXT_LIMIT)
from alden_gbrain_projection import GbrainProjection,_text

POLICY='alden-gbrain-full-source-v1'
STABLE_POLICY='alden-gbrain-record-content-v2'
MAX_QUERY_EDGES=8192
INDEX_SCHEMA='''PRAGMA foreign_keys=ON; PRAGMA journal_mode=DELETE;
CREATE TABLE pages(slug TEXT PRIMARY KEY,id TEXT UNIQUE NOT NULL,kind TEXT NOT NULL,
 payload TEXT NOT NULL,content TEXT NOT NULL,revision TEXT);
CREATE TABLE links(a TEXT NOT NULL REFERENCES pages(slug),b TEXT NOT NULL REFERENCES pages(slug),
 verb TEXT NOT NULL,kind TEXT NOT NULL,payload TEXT NOT NULL,context TEXT NOT NULL,
 PRIMARY KEY(a,b,verb));
CREATE INDEX links_to ON links(b);'''


def _digest(value):return _sha(encoded(value))


def _db(path,write=False):
    db=sqlite3.connect(_path(path).as_uri()+('?mode=rw' if write else '?mode=ro'),uri=True,timeout=.15)
    db.row_factory=sqlite3.Row
    if not write:db.execute('PRAGMA query_only=ON')
    return db


class FullProjection:
    """A disk index of all source records plus actual selected-version origins."""
    def __init__(self,generation,check=lambda:None,*,policy=POLICY):
        if policy not in (POLICY,STABLE_POLICY):raise GbrainError('gbrain_full_policy_invalid')
        self.policy=policy
        self.root=_path(generation);self.archive=self.root/'source-archive';self.path=self.root/'graph-index.sqlite3'
        self.check=check;self.receipt=verify_export(self.archive,check=check)
        self.projects=_projects(self.receipt['projects'])
        self.fingerprint=_digest({'policy':self.policy,**{k:self.receipt[k] for k in
            ('source_root_sha256','projects','as_of','counts','row_hashes')}})
        self.prefix=_digest(self.projects)

    def slug(self,node_id):return 'sources/alden-record-'+_digest([self.prefix,node_id])
    def origin(self,target):return 'alden-origin:'+_digest([self.prefix,target])
    def origin_slug(self,target):return 'sources/alden-origin-'+_digest([self.prefix,target])

    def _page(self,slug,node,kind):
        fields={'type':'source','title':node['label'],'alden_policy':self.policy}
        if self.policy==POLICY:fields['alden_snapshot']=self.fingerprint
        fields.update(alden_node_id=node['id'],alden_source_target=node['source_target'],alden_record_sha256=_digest(node))
        if kind=='record':fields['alden_source_version']=node['source_version']
        content='---\n'+'\n'.join(k+': '+json.dumps(v,ensure_ascii=False) for k,v in fields.items())
        return (slug,node['id'],kind,encoded(node).decode(),
                content+'\n---\nAlden source graph metadata; not original body or claim validity.\n')

    @classmethod
    def prepare(cls,generation,*,check=lambda:None,max_index_records=4_000_000,max_index_bytes=2*1024**3,policy=POLICY,previous=None):
        self=cls(generation,check,policy=policy)
        if type(max_index_records) is not int or max_index_records<1 or type(max_index_bytes) is not int or max_index_bytes<65536:
            raise GbrainError('gbrain_full_index_budget_invalid')
        if self.path.exists():raise GbrainError('gbrain_full_new_index_required')
        self.index_updates={'mode':'full_index' if previous is None else 'index_delta',
            'pages_put':0,'pages_deleted':0,'links_put':0,'links_deleted':0}
        if previous is not None:
            if (policy!=STABLE_POLICY or previous.policy!=policy or previous.projects!=self.projects
                    or previous.receipt['source_root_sha256']!=self.receipt['source_root_sha256']):
                raise GbrainError('gbrain_full_index_base_invalid')
            with closing(_db(previous.path)) as prior,closing(sqlite3.connect(self.path)) as destination:
                self.path.chmod(0o600)
                prior.backup(destination,pages=128,progress=lambda *_:check(),sleep=.01)
            if _file_token(previous.path)!=previous.index_token:raise GbrainError('gbrain_full_projection_changed')
        self._index_records=0;self._index_record_budget=max_index_records;self._index_byte_budget=max_index_bytes
        with closing(_db(self.archive/'graph.sqlite3')) as source, closing(sqlite3.connect(self.path)) as db:
            self.path.chmod(0o600)
            if previous is None:db.executescript(INDEX_SCHEMA)
            else:db.execute('PRAGMA foreign_keys=ON');db.execute('PRAGMA journal_mode=DELETE')
            db.execute('CREATE TEMP TABLE seen_pages(slug TEXT PRIMARY KEY) WITHOUT ROWID')
            db.execute('CREATE TEMP TABLE seen_links(a TEXT,b TEXT,verb TEXT,PRIMARY KEY(a,b,verb)) WITHOUT ROWID')
            db.execute('BEGIN IMMEDIATE')
            targets={r['id']:_json(r['payload']) for r in source.execute('SELECT id,payload FROM targets')}
            for row in source.execute('SELECT payload FROM nodes ORDER BY id'):
                check();node=_json(row['payload']);_text(node['id']);_text(node['label'],1024)
                _text(node['source_version']);_text(node['category'],256)
                target=node['source_target'];contexts=node['source_contexts']
                if target not in targets or not isinstance(contexts,list):raise GbrainError('gbrain_full_source_unbound')
                selected={}
                for context in contexts:
                    if (not isinstance(context,dict) or context.get('project') not in self.projects
                            or context.get('target_id') not in targets):raise GbrainError('gbrain_full_source_unbound')
                    for key in ('project','target_id','version'):_text(context.get(key))
                    if context['version']==node['source_version']:
                        selected.setdefault(context['target_id'],[]).append(context)
                if target not in selected:raise GbrainError('gbrain_full_source_unbound')
                slug=self.slug(node['id'])
                self._insert_page(db,self._page(slug,node,'record'))
                for origin_target,current in sorted(selected.items()):
                    origin={'id':self.origin(origin_target),'label':'출처 · '+targets[origin_target]['label'],
                        'category':'source','source_target':origin_target,
                        'basis':'current selected-version source membership; not independent corroboration'}
                    origin_slug=self.origin_slug(origin_target)
                    self._insert_page(db,self._page(origin_slug,origin,'origin'))
                    edge={'id':'alden-origin-edge:'+_digest([self.prefix,node['id'],origin_target]),
                        'source':node['id'],'target':origin['id'],'relation':'sourced_from',
                        'source_contexts':sorted(current,key=encoded),
                        'basis':'current source membership; not independent corroboration'}
                    self._insert_link(db,slug,origin_slug,'sourced_from','membership',edge)
            for row in source.execute('SELECT payload FROM edges ORDER BY source,relation,target'):
                check();edge=_json(row['payload']);_text(edge['relation'],128)
                proof=edge['reference_group'];evidence=edge['evidence']
                if (proof.get('kind')!='directed-relation-group-v1' or type(proof.get('count')) is not int or proof['count']<1
                        or not isinstance(proof.get('revision'),str) or len(proof['revision'])!=64
                        or evidence.get('retracted') is not False):raise GbrainError('gbrain_full_relation_unbound')
                verb='alden_'+_digest(edge['relation'])[:24]
                self._insert_link(db,self.slug(edge['source']),self.slug(edge['target']),verb,'observed',edge)
            self.index_updates['links_deleted']=db.execute('''DELETE FROM links WHERE NOT EXISTS(
                SELECT 1 FROM seen_links s WHERE s.a=links.a AND s.b=links.b AND s.verb=links.verb)''').rowcount
            self.index_updates['pages_deleted']=db.execute('DELETE FROM pages WHERE slug NOT IN (SELECT slug FROM seen_pages)').rowcount
            if db.execute('PRAGMA foreign_key_check').fetchone() is not None:raise GbrainError('gbrain_full_endpoint_invalid')
            count=db.execute('SELECT (SELECT COUNT(*) FROM pages)+(SELECT COUNT(*) FROM links)').fetchone()[0]
            size=db.execute('PRAGMA page_count').fetchone()[0]*db.execute('PRAGMA page_size').fetchone()[0]
            if count>max_index_records or size>max_index_bytes:raise GbrainError('gbrain_full_index_budget')
            db.commit()
        if self.path.stat().st_size>max_index_bytes:raise GbrainError('gbrain_full_index_budget')
        return self

    def _insert_page(self,db,page):
        if db.execute('INSERT OR IGNORE INTO seen_pages VALUES(?)',(page[0],)).rowcount:self._index_budget(db)
        self.index_updates['pages_put']+=db.execute('''INSERT INTO pages(slug,id,kind,payload,content) VALUES(?,?,?,?,?)
            ON CONFLICT(slug) DO UPDATE SET id=excluded.id,kind=excluded.kind,payload=excluded.payload,
                content=excluded.content,revision=NULL WHERE pages.id!=excluded.id OR pages.kind!=excluded.kind
                OR pages.payload!=excluded.payload OR pages.content!=excluded.content''',page).rowcount

    def _insert_link(self,db,a,b,verb,kind,edge):
        context={'policy':self.policy,'edge':edge}
        if self.policy==POLICY:context['snapshot']=self.fingerprint
        context=encoded(context).decode()
        if db.execute('INSERT OR IGNORE INTO seen_links VALUES(?,?,?)',(a,b,verb)).rowcount:self._index_budget(db)
        self.index_updates['links_put']+=db.execute('''INSERT INTO links VALUES(?,?,?,?,?,?)
            ON CONFLICT(a,b,verb) DO UPDATE SET kind=excluded.kind,payload=excluded.payload,context=excluded.context
            WHERE links.kind!=excluded.kind OR links.payload!=excluded.payload OR links.context!=excluded.context''',
            (a,b,verb,kind,encoded(edge).decode(),context)).rowcount

    def _index_budget(self,db):
        self.check();self._index_records+=1
        if self._index_records>self._index_record_budget:raise GbrainError('gbrain_full_index_budget')
        if self._index_records%128==0:
            size=db.execute('PRAGMA page_count').fetchone()[0]*db.execute('PRAGMA page_size').fetchone()[0]
            if size>self._index_byte_budget:raise GbrainError('gbrain_full_index_budget')

    def counts(self):
        with closing(_db(self.path)) as db:
            records,origins=db.execute("SELECT SUM(kind='record'),SUM(kind='origin') FROM pages").fetchone()
            observed,memberships=db.execute("SELECT SUM(kind='observed'),SUM(kind='membership') FROM links").fetchone()
        counts={'source_nodes':records or 0,'origins':origins or 0,'observed_groups':observed or 0,
                'membership_links':memberships or 0}
        counts['pages']=counts['source_nodes']+counts['origins'];counts['links']=counts['observed_groups']+counts['membership_links']
        if counts['source_nodes']!=self.receipt['counts']['nodes'] or counts['observed_groups']!=self.receipt['counts']['edges']:
            raise GbrainError('gbrain_full_index_incomplete')
        return counts

    def pages(self):
        # Close each SELECT before yielding: batch revision acknowledgments
        # commit to this index while the iterator is paused.
        last=''
        while True:
            with closing(_db(self.path)) as db:
                rows=db.execute('SELECT * FROM pages WHERE slug>? ORDER BY slug LIMIT 50',(last,)).fetchall()
            if not rows:return
            for row in rows:self.check();yield dict(row)
            last=rows[-1]['slug']

    def links(self):
        with closing(_db(self.path)) as db:
            for row in db.execute('SELECT * FROM links ORDER BY a,b,verb'):
                self.check();yield dict(row)

    def record_revisions(self,pages,revisions):
        with closing(_db(self.path,True)) as db:
            for page,revision in zip(pages,revisions):
                db.execute('UPDATE pages SET revision=? WHERE slug=?',(revision,page['slug']))
            db.commit()

    def paged_readback_supported(self):
        """Never accept the publisher's 2,000-character context projection."""
        with closing(_db(self.path)) as db:
            hubs=[r[0] for r in db.execute('SELECT a FROM links GROUP BY a HAVING count(*)>=?',(TRAVERSE_ROW_LIMIT,))]
            for slug in hubs:
                for row in db.execute('SELECT context FROM links WHERE a=?',(slug,)):
                    self.check()
                    # Conservative for either UTF-16 or SQL character counting.
                    if len(row[0].encode('utf-16-le'))//2>LINK_CONTEXT_LIMIT:
                        raise GbrainError('gbrain_full_paged_context_budget')

    def verify_outgoing(self,slug,rows):
        """Stream every engine link against the complete immutable index."""
        token=_file_token(self.path);seen=set()
        with closing(_db(self.path)) as db:
            expected=db.execute('SELECT count(*) FROM links WHERE a=?',(slug,)).fetchone()[0]
            for row in rows:
                self.check()
                if (not isinstance(row,dict) or row.get('from_slug')!=slug
                        or row.get('from_source_id')!='default' or row.get('to_source_id')!='default'
                        or any(not isinstance(row.get(k),str) for k in ('to_slug','link_type','context'))
                        or row.get('link_source')!='alden-full-source'
                        or any(row.get(k) is not None for k in ('origin_slug','origin_source_id','origin_field'))):
                    raise GbrainError('gbrain_full_result_unbound')
                key=(slug,row['to_slug'],row['link_type'])
                bound=db.execute('SELECT context FROM links WHERE a=? AND b=? AND verb=?',key).fetchone()
                if key in seen or bound is None or row['context']!=bound[0]:
                    raise GbrainError('gbrain_full_result_unbound')
                seen.add(key)
            if len(seen)!=expected:raise GbrainError('gbrain_full_result_incomplete')
        if _file_token(self.path)!=token:raise GbrainError('gbrain_full_projection_changed')
        return len(seen)

    def schema_pack(self):
        with closing(_db(self.path)) as db:verbs=[r[0] for r in db.execute("SELECT DISTINCT verb FROM links WHERE verb!='sourced_from' ORDER BY verb")]
        return {'api_version':'gbrain-schema-pack-v1','name':'alden-scoped-graph','version':'2.0.0',
            'gbrain_min_version':VERSION,'description':'Full authorized source records and explicit observations; not inferred validity.',
            'extends':'gbrain-base-v2','borrow_from':[],'page_types':[],'takes_kinds':['fact','take','bet','hunch'],
            'frontmatter_links':[],'enrichable_types':[],'filing_rules':[],
            'link_types':[{'name':v} for v in verbs]}

    schema_yaml=GbrainProjection.schema_yaml

    def manifest(self):
        return {'policy':self.policy,'fingerprint':self.fingerprint,'counts':self.counts(),
            'index_sha256':_sha_file(self.path,self.check),'source_database_sha256':self.receipt['database_sha256']}

    @classmethod
    def open(cls,generation,check=lambda:None):
        manifest=_read(_path(generation)/'projection.json')
        if not isinstance(manifest,dict):raise GbrainError('gbrain_full_projection_changed')
        self=cls(generation,check,policy=manifest.get('policy'))
        token=_file_token(self.path)
        if self.manifest()!=_read(self.root/'projection.json'):raise GbrainError('gbrain_full_projection_changed')
        if _file_token(self.path)!=token:raise GbrainError('gbrain_full_projection_changed')
        self.index_token=token
        return self

    def _incident(self,db,slugs,direction):
        found={}
        ordered=sorted(slugs)
        for offset in range(0,len(ordered),400):
            chunk=ordered[offset:offset+400];marks=','.join('?' for _ in chunk)
            where=('a IN ('+marks+')' if direction=='out' else 'b IN ('+marks+')' if direction=='in'
                   else 'a IN ('+marks+') OR b IN ('+marks+')')
            for row in db.execute('SELECT * FROM links WHERE '+where,chunk if direction!='both' else chunk+chunk):
                self.check();found[(row['a'],row['b'],row['verb'])]=dict(row)
                if len(found)>MAX_QUERY_EDGES:raise GbrainError('gbrain_full_query_budget')
        return found

    def bind(self,rows,*,focus,depth=1,direction='both'):
        if type(depth) is not int or not 1<=depth<=3 or direction not in ('in','out','both') or not isinstance(focus,str):
            raise GbrainError('gbrain_graph_options_invalid')
        if not isinstance(rows,list) or len(rows)>MAX_QUERY_EDGES:raise GbrainError('gbrain_full_result_invalid')
        with closing(_db(self.path)) as db:
            node=db.execute('SELECT * FROM pages WHERE id=?',(focus,)).fetchone()
            if node is None:raise GbrainError('gbrain_focus_unavailable')
            frontier={node['slug']};distances={node['slug']:0};expected={}
            for layer in range(1,depth+1):
                incident=self._incident(db,frontier,direction);next_nodes=set()
                for key,row in incident.items():
                    expected.setdefault(key,(layer,row))
                    if direction in ('out','both'):next_nodes.add(row['b'])
                    if direction in ('in','both'):next_nodes.add(row['a'])
                if len(expected)>MAX_QUERY_EDGES:raise GbrainError('gbrain_full_query_budget')
                frontier=next_nodes-distances.keys();distances.update({s:layer for s in frontier})
            actual={};edges=[];wanted={node['slug']}
            for row in rows:
                if not isinstance(row,dict) or row.get('from_source_id')!='default' or row.get('to_source_id')!='default':
                    raise GbrainError('gbrain_full_result_unbound')
                key=tuple(row.get(k) for k in ('from_slug','to_slug','link_type'))
                if any(not isinstance(v,str) for v in key) or key not in expected or key in actual:
                    raise GbrainError('gbrain_full_result_unbound')
                layer,bound=expected[key]
                if type(row.get('depth')) is not int or row['depth']!=layer or _json(row.get('context',''))!=_json(bound['context']):
                    raise GbrainError('gbrain_full_result_unbound')
                actual[key]=layer;wanted.update(key[:2]);edges.append({**_json(bound['payload']),'gbrain_depth':layer})
            if set(actual)!=set(expected):raise GbrainError('gbrain_full_result_incomplete')
            nodes=[]
            for slug in sorted(wanted):nodes.append(_json(db.execute('SELECT payload FROM pages WHERE slug=?',(slug,)).fetchone()[0]))
        return {'ok':True,'nodes':nodes,'edges':edges,'scope':self.projects,
            'basis':{'engine':'gbrain','policy':self.policy,'snapshot':self.fingerprint,'checked_edges':len(expected),
                'depth':depth,'direction':direction,'complete_within_snapshot':True,
                'scope_limit':'all authorized source records at the verified collection revision; not semantic truth'}}


class GbrainImportClient(GbrainReadClient):
    """Only a generation writer uses this private full-access engine session."""
    WRITE_TOOLS=('put_pages','add_link','get_page','list_pages')
    REQUIRED_TOOLS=frozenset(WRITE_TOOLS)|{'traverse_graph'}
    def __enter__(self):
        self.check()
        env={k:os.environ[k] for k in ('PATH','LANG','LC_ALL','TZ','HOME','TMPDIR') if k in os.environ}
        env.update(GBRAIN_HOME=str(self.home),GBRAIN_AUDIT_DIR=str(self.home.parent/'audit'),
            GBRAIN_SOURCE=self.source_id,GBRAIN_SWEEP='0',GBRAIN_SERVE_SYNC_IPC='0')
        self.transport=_Stdio([str(self.binary),'serve','--surface','full','--access','full',
            '--source-guard','--stdio-idle-timeout','30','--fail-fast'],cwd=self.root,env=env,
            check=self.check,timeout=self.timeout,pass_fds=() if self.lease_fd is None else (self.lease_fd,))
        try:
            result=self.transport.request('initialize',{'protocolVersion':PROTOCOL,'capabilities':{},
                'clientInfo':{'name':'alden-full-generation-import','version':'1'}})
            if result.get('protocolVersion')!=PROTOCOL or result.get('serverInfo')!={'name':'gbrain','version':VERSION}:
                raise GbrainError('gbrain_peer_mismatch')
            self.transport.send({'jsonrpc':'2.0','method':'notifications/initialized'})
            tools=self.transport.request('tools/list',{});listing=tools.get('tools')
            if (tools.get('nextCursor') or not isinstance(listing,list) or len(listing)>512
                    or any(not isinstance(t,dict) or not isinstance(t.get('name'),str) for t in listing)
                    or not self.REQUIRED_TOOLS<={t['name'] for t in listing}):
                raise GbrainError('gbrain_full_import_surface_invalid')
            self._read_tools={t['name'] for t in listing}
        except BaseException:
            self.transport.close()
            if self.transport.proc.poll() is None:raise _UnreapedWorker('gbrain_cache_worker_not_reaped')
            raise
        return self

    def __exit__(self,kind,value,traceback):
        transport=self.transport
        code=transport.close();self.transport=None
        if transport.proc.poll() is None:raise _UnreapedWorker('gbrain_cache_worker_not_reaped')
        if kind is None and code!=0:raise GbrainError('gbrain_unclean_shutdown')

    def _call(self,name,arguments):
        if name not in self.WRITE_TOOLS:raise GbrainError('gbrain_full_operation_invalid')
        self.check();result=self.transport.request('tools/call',{'name':name,'arguments':arguments})
        content=result.get('content');meta=result.get('_meta',{});notices=meta.get('gbrain_notices',[]) if isinstance(meta,dict) else None
        if (result.get('isError') or not isinstance(content,list) or not isinstance(notices,list) or len(notices)>16
                or len(content)!=1+len(notices) or any(not isinstance(p,dict) or p.get('type')!='text' or not isinstance(p.get('text'),str) for p in content)):
            raise GbrainError('gbrain_full_operation_failed')
        reviewed={'first_run_decisions':'ask','backup_coverage':'coaching'}
        if name=='list_pages':reviewed['listing_truncated']='info'
        for notice,block in zip(notices,content[1:]):
            if not isinstance(notice,dict):raise GbrainError('gbrain_graph_notice_unreviewed')
            code,kind=notice.get('code'),notice.get('kind')
            if (not isinstance(code,str) or code not in reviewed or reviewed[code]!=kind
                    or not block['text'].startswith('[gbrain notice '+code+' kind='+kind+']\n')):
                raise GbrainError('gbrain_graph_notice_unreviewed')
        value=_json(content[0]['text'])
        if name=='list_pages':
            if not isinstance(value,list):raise GbrainError('gbrain_full_operation_failed')
        elif not isinstance(value,dict) or value.get('ok') is False or value.get('error'):
            raise GbrainError('gbrain_full_operation_failed')
        self.check();return value

    def put(self,pages,request):
        return self._call('put_pages',{'pages':[{'slug':p['slug'],'content':p['content'],
            **({'expected_revision':p['revision']} if p.get('revision') is not None else {})} for p in pages],
            'source_id':'default','request_id':request,'wait_ms':25000})

    def link(self,row,request):
        return self._call('add_link',{'from':row['a'],'to':row['b'],'link_type':row['verb'],
            'link_source':'alden-full-source','context':row['context'],'request_id':request})

    def get(self,slug):return self._call('get_page',{'slug':slug,'source_id':'default','include_content':True})

    def list(self,offset):return self._call('list_pages',{'source_id':'default','sort':'slug','limit':100,'offset':offset})


class FullGbrainCache(ManagedGbrainCache):
    KIND='alden-gbrain-full-cache-v1'

    def __init__(self,root,*,collection_root,projects):
        super().__init__(root,collection_root=collection_root,projects=projects)
        self.collection_root=_path(collection_root)

    def refresh(self,*,store_factory=None,check=lambda:None,rebuild=False,incremental=False):
        if type(incremental) is not bool:raise GbrainError('gbrain_full_options_invalid')
        def cancelled():check();return False
        def current(projection):
            return verify_current_export(self.collection_root,self.owner['projects'],projection.archive,
                store_factory=store_factory,cancelled=cancelled)
        with self.lease(exclusive=True,check=check) as fd:
            active=self.active();name='g'+uuid.uuid4().hex;generation=self.generations/name
            generation.mkdir(mode=0o700)
            _write(generation/'generation.json',{'kind':self.KIND,'owner':self.owner['owner'],'generation':name})
            try:
                old=FullProjection.open(self._generation(active['generation']),check) if active else None
                if incremental and old is not None:
                    from alden_collection_delta import export_incremental
                    source_receipt=export_incremental(self.collection_root,self.owner['projects'],generation/'source-archive',
                        previous=old.archive,store_factory=store_factory,cancelled=cancelled)
                else:
                    source_receipt=export_graph(self.collection_root,self.owner['projects'],generation/'source-archive',
                        store_factory=store_factory,cancelled=cancelled)
                policy=STABLE_POLICY if incremental else old.policy if old is not None else POLICY
                reuse_index=None
                if incremental and old is not None and old.policy==policy:
                    from alden_collection_delta import _compatible
                    if _compatible(old.receipt['as_of'],source_receipt['as_of']):reuse_index=old
                projection=FullProjection.prepare(generation,check=check,policy=policy,previous=reuse_index);current(projection)
                projection.paged_readback_supported()
                if active and active['fingerprint']==projection.fingerprint and not rebuild:
                    old=FullProjection.open(self._generation(active['generation']),check)
                    if old.fingerprint!=projection.fingerprint:raise GbrainError('gbrain_full_projection_changed')
                    removed=self._retire(active,preserve=active['generation']);current(old)
                    if _file_token(old.path)!=old.index_token:raise GbrainError('gbrain_full_projection_changed')
                    return {'ok':True,'state':'unchanged','fingerprint':old.fingerprint,
                        'removed_generations':removed,**old.counts()}
                use_delta=False
                if incremental and not rebuild and old is not None:
                    from alden_gbrain_delta import eligible,build_delta
                    use_delta=eligible(old,projection)
                if use_delta:
                    acceptance,removed=build_delta(self,generation,projection,old,fd=fd,check=check)
                else:
                    removed=self._retire(active,preserve=name)
                    acceptance=self._build(generation,projection,fd=fd,check=check)
                    if incremental:acceptance['update_mode']='full_rebuild'
                if incremental:
                    acceptance['source_update']=source_receipt.get('source_update',{'mode':'full_export','reason':'new_baseline'})
                    acceptance['index_updates']=projection.index_updates
                current(projection);check()
                manifest=projection.manifest();_write(generation/'projection.json',manifest)
                _write(generation/'ready.json',{'fingerprint':projection.fingerprint,
                    'manifest_sha256':_sha((generation/'projection.json').read_bytes()),**acceptance})
                current(projection)
                _write(self.root/'active.json',{'owner':self.owner['owner'],'generation':name,'fingerprint':projection.fingerprint})
                if self.active()['generation']!=name:raise GbrainError('gbrain_cache_publish_invalid')
                current(projection)
                return {'ok':True,'state':'published','fingerprint':projection.fingerprint,
                    'removed_generations':removed,**acceptance}
            except _UnreapedWorker:raise
            except BaseException:
                self._retire(None);raise

    def _build(self,generation,projection,*,fd,check):
        validate_binary(self.root/'runtime-receipt.json',self.root,check)
        def command(args,text=False):
            raw=self._command(generation,args,fd=fd,check=check)
            result=raw.decode() if text else _json(raw)
            if not text and (not isinstance(result,dict) or result.get('ok') is False or result.get('error')):
                raise GbrainError('gbrain_full_operation_failed')
            return result
        command(['init','--pglite','--no-embedding','--db-only','--path',str(generation/'brain.pglite'),
            '--non-interactive','--search-mode','conservative','--json'])
        pack=generation/'home/.gbrain/schema-packs/alden-scoped-graph';_path(pack);pack.mkdir(parents=True,mode=0o700)
        (pack/'pack.yaml').write_text(projection.schema_yaml())
        if 'valid manifest' not in command(['schema','validate','alden-scoped-graph'],True):raise GbrainError('gbrain_cache_schema_invalid')
        command(['schema','use','alden-scoped-graph'],True)
        schema=command(['schema','active','--source','default','--json'])
        if schema.get('pack')!='alden-scoped-graph' or schema.get('degraded'):raise GbrainError('gbrain_cache_schema_invalid')
        validate_runtime(self.root/'runtime-receipt.json',self.root,generation/'home',check)
        def request(phase,key):
            identity=str(uuid.uuid5(uuid.UUID(self.owner['owner']),generation.name+':'+phase+':'+key))
            _write(generation/'operation.json',{'phase':phase,'key':key,'request_id':identity,'state':'intent'})
            return identity
        with GbrainImportClient(self.root/'runtime-receipt.json',owned_root=self.root,
                home=generation/'home',check=check,lease_fd=fd,timeout=60) as client:
            batch=[];pages_written=0;links_written=0
            for page in projection.pages():
                # This engine is empty even when the index reused stable rows.
                # Prior opaque revisions belong only to a copied delta engine.
                batch.append(dict(page,revision=None))
                if len(batch)==50:self._put_batch(client,projection,batch,request);pages_written+=len(batch);batch=[]
            if batch:self._put_batch(client,projection,batch,request);pages_written+=len(batch)
            for row in projection.links():
                result=client.link(row,request('link',_digest([row['a'],row['b'],row['verb']])))
                if result.get('status')!='ok':raise GbrainError('gbrain_full_link_uncommitted')
                links_written+=1
            acceptance=self._verify(client,projection)
            if pages_written!=acceptance['pages'] or links_written!=acceptance['links']:
                raise GbrainError('gbrain_full_import_incomplete')
        _write(generation/'operation.json',{'state':'verified',**acceptance})
        return acceptance

    def _put_batch(self,client,projection,batch,request):
        saved=client.put(batch,request('pages',batch[0]['slug']))
        if (saved.get('state')!='committed' or saved.get('terminal') is not True or saved.get('source_id')!='default'
                or saved.get('counts')!={'total':len(batch),'committed':len(batch),'pending':0,'failed':0}
                or any(type(v) is not int for v in saved.get('counts',{}).values())
                or not isinstance(saved.get('pages'),list) or len(saved['pages'])!=len(batch)):
            raise GbrainError('gbrain_cache_batch_uncommitted')
        revisions=[]
        for index,(expected,page) in enumerate(zip(batch,saved['pages'])):
            if (not isinstance(page,dict) or type(page.get('index')) is not int or page['index']!=index
                    or page.get('slug')!=expected['slug'] or page.get('state')!='committed'
                    or page.get('embedding_state')!='disabled'):
                raise GbrainError('gbrain_cache_batch_uncommitted')
            revisions.append(_text(page.get('revision')))
        projection.record_revisions(batch,revisions)

    def _verify(self,client,projection):
        counts=projection.counts();expected_pages=iter(projection.pages());enumerated=0;verified=0
        with closing(_db(projection.path)) as db:
            degrees=dict(db.execute('SELECT a,count(*) FROM links GROUP BY a'))
        paged_pages=paged_links=0
        for offset in range(0,counts['pages']+101,100):
            listed=client.list(offset)
            if not isinstance(listed,list) or len(listed)>100:raise GbrainError('gbrain_full_page_enumeration_invalid')
            for listed_page in listed:
                expected_page=next(expected_pages,None)
                if (expected_page is None or not isinstance(listed_page,dict)
                        or listed_page.get('source_id')!='default' or listed_page.get('slug')!=expected_page['slug']):
                    raise GbrainError('gbrain_full_page_enumeration_invalid')
                enumerated+=1
            if len(listed)<100:break
        if enumerated!=counts['pages'] or next(expected_pages,None) is not None:
            raise GbrainError('gbrain_full_page_enumeration_invalid')
        for page in projection.pages():
            result=client.get(page['slug']);node=_json(page['payload'])
            expected={'alden_policy':projection.policy,'alden_node_id':node['id'],
                'alden_source_target':node['source_target'],'alden_record_sha256':_digest(node)}
            if projection.policy==POLICY:expected['alden_snapshot']=projection.fingerprint
            if page['kind']=='record':expected['alden_source_version']=node['source_version']
            if (result.get('slug')!=page['slug'] or result.get('source_id')!='default'
                    or result.get('title')!=node['label'] or result.get('revision')!=page['revision']
                    or not isinstance(page['revision'],str) or not isinstance(result.get('frontmatter'),dict)
                    or any(result['frontmatter'].get(k)!=v for k,v in expected.items())):
                raise GbrainError('gbrain_cache_readback_invalid')
            # Every page's outgoing set covers every stored link once.
            # A source origin with many incoming memberships need not fit
            # into one traverse_graph reply to prove complete import.
            degree=degrees.get(page['slug'],0)
            if degree>=TRAVERSE_ROW_LIMIT:
                paged_links+=projection.verify_outgoing(page['slug'],client.outgoing_links(page['slug'],max_rows=degree+1))
                paged_pages+=1
            else:
                rows=client.graph(page['slug'],depth=1,direction='out')
                projection.bind(rows,focus=node['id'],direction='out')
            verified+=1
        if verified!=counts['pages']:raise GbrainError('gbrain_full_import_incomplete')
        return {**counts,'verified_queries':verified,'verified_direction':'out','enumerated_pages':enumerated,
            **({'paged_outgoing_pages':paged_pages,'paged_outgoing_links':paged_links} if paged_pages else {})}



class FullGbrainReader(GbrainGraphReader):
    def __init__(self,root,*,collection_root,projects):
        self.cache=FullGbrainCache(root,collection_root=collection_root,projects=projects)

    def query(self,*,projects,focus,store_factory=None,check=lambda:None,depth=1,direction='both'):
        if _projects(projects)!=self.cache.owner['projects']:raise GbrainError('gbrain_manifest_scope_mismatch')
        if not isinstance(focus,str) or not focus or type(depth) is not int or not 1<=depth<=3 or direction not in ('in','out','both'):
            raise GbrainError('gbrain_graph_options_invalid')
        def cancelled():check();return False
        with self.cache.lease(exclusive=False,check=check) as fd:
            active=self.cache.active()
            if active is None:raise GbrainError('gbrain_cache_unavailable')
            projection=FullProjection.open(self.cache._generation(active['generation']),check)
            if projection.fingerprint!=active['fingerprint']:raise GbrainError('gbrain_full_projection_changed')
            def current():
                try:
                    verify_current_export(self.cache.collection_root,projects,projection.archive,
                        store_factory=store_factory,cancelled=cancelled)
                except ExportError as error:
                    if str(error) in ('collection_export_stale','collection_export_source_changed',
                                     'collection_export_source_withheld','collection_export_authority_unavailable'):
                        raise GbrainError('gbrain_projection_stale') from None
                    raise
            token=projection.index_token
            current()
            if _file_token(projection.path)!=token:raise GbrainError('gbrain_full_projection_changed')
            with closing(_db(projection.path)) as db:
                row=db.execute('SELECT slug FROM pages WHERE id=?',(focus,)).fetchone()
            if row is None:raise GbrainError('gbrain_focus_unavailable')
            with GbrainReadClient(self.cache.root/'runtime-receipt.json',owned_root=self.cache.root,
                    home=projection.root/'home',check=check,lease_fd=fd) as client:
                rows=client.graph(row['slug'],depth=depth,direction=direction)
                result=projection.bind(rows,focus=focus,depth=depth,direction=direction)
            current()
            if _file_token(projection.path)!=token:raise GbrainError('gbrain_full_projection_changed')
            check();return result


def main():
    import argparse
    from alden_abort import ABORT_STATE_NAME,AbortToken,AldenCancelled
    from alden_knowledge_mcp import KnowledgeServer
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('create','refresh','withdraw','query'))
    parser.add_argument('--root',type=Path,required=True);parser.add_argument('--state-root',type=Path,required=True)
    parser.add_argument('--project',action='append',required=True)
    parser.add_argument('--runtime-root',type=Path);parser.add_argument('--runtime-home',type=Path);parser.add_argument('--runtime-receipt',type=Path)
    parser.add_argument('--messenger-source',choices=('kakaotalk','enrolled'));parser.add_argument('--focus')
    parser.add_argument('--depth',type=int,default=1);parser.add_argument('--direction',choices=('in','out','both'),default='both')
    parser.add_argument('--timeout-seconds',type=int,default=1800)
    parser.add_argument('--incremental',action='store_true',help='refresh: mutate only changed engine rows; full source/readback scans remain')
    args=parser.parse_args();runtime=(args.runtime_root,args.runtime_home,args.runtime_receipt)
    if (args.action=='create' and not all(runtime) or args.action!='create' and any(runtime)
            or args.incremental and args.action!='refresh'
            or (args.action=='query')!=(args.focus is not None) or not 1<=args.depth<=3 or not 30<=args.timeout_seconds<=3600):
        parser.error('runtime paths only/together for create; focus only for query; depth1–3 and deadline30–3600')
    token=AbortToken(args.state_root/ABORT_STATE_NAME);until=time.monotonic()+args.timeout_seconds
    def check():
        token.raise_if_cancelled()
        if time.monotonic()>=until:raise GbrainError('gbrain_full_deadline')
    try:
        if args.action=='withdraw':
            result=FullGbrainCache(args.root,collection_root=args.state_root,projects=args.project).withdraw(check=check)
        else:
            source_factory=None
            if args.messenger_source:
                from alden_messenger_factory import open_existing_kakao, open_enrolled_messengers
                source_factory=open_enrolled_messengers if args.messenger_source=='enrolled' else open_existing_kakao
            authority=KnowledgeServer(args.state_root,args.project,source_factory=source_factory)
            def factory():
                return authority.collection_store(token.is_cancelled)
            if args.action=='create':
                store=factory()
                with store.database() as db:_stamp(db,store,_projects(args.project))
                FullGbrainCache.create(args.root,collection_root=args.state_root,projects=args.project,
                    runtime_root=args.runtime_root,runtime_home=args.runtime_home,runtime_receipt=args.runtime_receipt,check=check)
                result={'ok':True,'state':'created'}
            elif args.action=='refresh':
                result=FullGbrainCache(args.root,collection_root=args.state_root,projects=args.project).refresh(
                    store_factory=factory,check=check,incremental=args.incremental)
            else:
                result=FullGbrainReader(args.root,collection_root=args.state_root,projects=args.project).query(
                    projects=args.project,focus=args.focus,store_factory=factory,check=check,depth=args.depth,direction=args.direction)
        print(json.dumps(result,ensure_ascii=False))
    except Exception as error:
        code='gbrain_full_unavailable'
        if isinstance(error,AldenCancelled):code='alden_global_abort'
        elif isinstance(error,PermissionError):code='gbrain_full_permission_denied'
        elif isinstance(error,GbrainError) and re.fullmatch('gbrain_[a-z_]{1,96}',str(error)):
            code=str(error)
        elif isinstance(error,ValueError) and str(error)=='collection_project_scope_invalid':
            code='collection_project_scope_invalid'
        print(json.dumps({'ok':False,'error':code}));raise SystemExit(1) from None


if __name__=='__main__':main()
