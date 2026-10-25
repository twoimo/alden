"""Engine deltas over a closed, privately owned full-cache generation.

Source export/index/verification remain complete scans. Original sources are
never written; only the cache's unpublished copy is mutated.
"""
from contextlib import closing
import hashlib
import os
from pathlib import Path
import shutil
import stat
import uuid

from alden_gbrain_cache import _read,_write
from alden_gbrain_client import GbrainError,_path,validate_runtime
from alden_gbrain_import import STABLE_POLICY,_db,_digest,GbrainImportClient
from alden_collection_export import _file_token


def eligible(previous,current):
    a,b=previous.receipt,current.receipt
    x,y=a['as_of'],b['as_of']
    return (previous.policy==current.policy==STABLE_POLICY
        and a['source_root_sha256']==b['source_root_sha256'] and a['projects']==b['projects']
        and x['authority']==y['authority'] and x['file_identity']==y['file_identity']
        and x['checkpoint']['stream_id']==y['checkpoint']['stream_id']
        and y['checkpoint']['cursor']>=x['checkpoint']['cursor'])


class DeltaPlan:
    """Disk joins, with the accepted base index copied inside this generation."""
    def __init__(self,projection,base_index,check=lambda:None):
        self.projection=projection;self.base=_path(base_index);self.check=check

    def database(self,write=False):
        self.check();db=_db(self.projection.path,write)
        try:db.execute('ATTACH DATABASE ? AS prior',(self.base.as_uri()+'?mode=ro',))
        except BaseException:db.close();raise
        return db

    def carry_revisions(self):
        with closing(self.database(True)) as db:
            db.execute('''UPDATE pages AS n SET revision=(SELECT p.revision FROM prior.pages p
                WHERE p.slug=n.slug AND p.payload=n.payload AND p.content=n.content)
                WHERE EXISTS(SELECT 1 FROM prior.pages p WHERE p.slug=n.slug
                    AND p.payload=n.payload AND p.content=n.content)''')
            db.commit()

    def pages(self):
        last=''
        while True:
            with closing(self.database()) as db:
                rows=db.execute('''SELECT n.*,p.revision AS previous_revision FROM pages n
                    LEFT JOIN prior.pages p ON p.slug=n.slug WHERE n.slug>?
                    AND (p.slug IS NULL OR n.content!=p.content OR n.payload!=p.payload)
                    ORDER BY n.slug LIMIT 50''',(last,)).fetchall()
            if not rows:return
            for row in rows:
                self.check();page=dict(row);page['revision']=page.pop('previous_revision');yield page
            last=rows[-1]['slug']

    def deleted_pages(self):
        with closing(self.database()) as db:
            for row in db.execute('''SELECT p.* FROM prior.pages p LEFT JOIN pages n ON p.slug=n.slug
                    WHERE n.slug IS NULL ORDER BY p.slug'''):
                self.check();yield dict(row)

    def links(self,removed=False):
        table,other=('prior.links','links') if removed else ('links','prior.links')
        # Purging a removed endpoint removes its incident links too. Explicit
        # unlink targets only links whose two pages survive that purge.
        endpoints='JOIN pages a ON a.slug=n.a JOIN pages b ON b.slug=n.b' if removed else ''
        query=f'''SELECT n.* FROM {table} n LEFT JOIN {other} p
            ON n.a=p.a AND n.b=p.b AND n.verb=p.verb {endpoints}
            WHERE p.a IS NULL OR n.context!=p.context OR n.payload!=p.payload
            ORDER BY n.a,n.b,n.verb'''
        with closing(self.database()) as db:
            for row in db.execute(query):self.check();yield dict(row)


def clone_closed(cache,previous,projection,generation,check):
    old=previous.root
    validate_runtime(cache.root/'runtime-receipt.json',cache.root,old/'home',check)
    config=_read(old/'home/.gbrain/config.json')
    if config.get('database_path')!=str(old/'brain.pglite'):
        raise GbrainError('gbrain_delta_database_binding_invalid')
    count=0;size=0
    def copy(source,destination):
        nonlocal count,size
        check();source=_path(source);info=source.stat()
        count+=1;size+=info.st_size
        if (not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_nlink!=1
                or count>100_000 or size>8*1024**3):raise GbrainError('gbrain_delta_copy_invalid')
        before=_file_token(source)
        shutil.copyfile(source,destination);shutil.copymode(source,destination)
        def digest(path):
            h=hashlib.sha256()
            with open(path,'rb') as stream:
                while block:=stream.read(1024*1024):check();h.update(block)
            return h.digest()
        if digest(source)!=digest(destination) or _file_token(source)!=before:
            raise GbrainError('gbrain_delta_copy_changed')
        return str(destination)
    for name in ('brain.pglite','home'):
        source=_path(old/name)
        for path in source.rglob('*'):
            check();_path(path)
            if not path.is_file() and not path.is_dir():raise GbrainError('gbrain_delta_copy_invalid')
        shutil.copytree(source,generation/name,copy_function=copy)
    copy(previous.path,generation/'delta-base.sqlite3')
    if _file_token(previous.path)!=previous.index_token:raise GbrainError('gbrain_full_projection_changed')
    config['database_path']=str(generation/'brain.pglite')
    _write(generation/'home/.gbrain/config.json',config)
    validate_runtime(cache.root/'runtime-receipt.json',cache.root,generation/'home',check)
    return DeltaPlan(projection,generation/'delta-base.sqlite3',check)


class DeltaImportClient(GbrainImportClient):
    WRITE_TOOLS=GbrainImportClient.WRITE_TOOLS+('remove_link',)
    REQUIRED_TOOLS=GbrainImportClient.REQUIRED_TOOLS|{'remove_link'}

    def unlink(self,row,request):
        return self._call('remove_link',{'from':row['a'],'to':row['b'],'link_type':row['verb'],
            'link_source':'alden-full-source','request_id':request})


def build_delta(cache,generation,projection,previous,*,fd,check):
    if not eligible(previous,projection):raise GbrainError('gbrain_delta_base_invalid')
    # The caller holds the exclusive inherited lease. Every prior engine
    # session has closed before that lease becomes available.
    plan=clone_closed(cache,previous,projection,generation,check)
    plan.carry_revisions()
    removed=cache._retire(cache.active(),preserve=generation.name)
    writes={'pages_put':0,'pages_purged':0,'links_added':0,'links_removed':0}
    def request(phase,key):
        identity=str(uuid.uuid5(uuid.UUID(cache.owner['owner']),generation.name+':'+phase+':'+key))
        _write(generation/'operation.json',{'phase':phase,'key':key,'request_id':identity,'state':'intent'})
        return identity
    for page in plan.deleted_pages():
        result=cache._command(generation,['delete',page['slug'],'--source-id','default','--expected-revision',page['revision'],
            '--purge','--json'],fd=fd,check=check,write=True)
        from alden_gbrain_client import _json
        result=_json(result)
        if (not isinstance(result,dict) or result.get('status')!='purged'
                or result.get('slug')!=page['slug'] or result.get('source_id')!='default'):
            raise GbrainError('gbrain_delta_purge_unconfirmed')
        writes['pages_purged']+=1
    with DeltaImportClient(cache.root/'runtime-receipt.json',owned_root=cache.root,
            home=generation/'home',check=check,lease_fd=fd,timeout=60) as client:
        for row in plan.links(removed=True):
            result=client.unlink(row,request('unlink',_digest([row['a'],row['b'],row['verb']])))
            if result.get('status')!='ok' or type(result.get('removed')) is not int or result['removed']!=1:
                raise GbrainError('gbrain_delta_unlink_unconfirmed')
            writes['links_removed']+=1
        batch=[]
        for page in plan.pages():
            batch.append(page)
            if len(batch)==50:cache._put_batch(client,projection,batch,request);writes['pages_put']+=len(batch);batch=[]
        if batch:cache._put_batch(client,projection,batch,request);writes['pages_put']+=len(batch)
        for row in plan.links():
            result=client.link(row,request('link',_digest([row['a'],row['b'],row['verb']])))
            if result.get('status')!='ok':raise GbrainError('gbrain_full_link_uncommitted')
            writes['links_added']+=1
        acceptance=cache._verify(client,projection)
    plan.base.unlink()
    delta={'kind':'alden-gbrain-engine-delta-v1','base_fingerprint':previous.fingerprint,
        'fingerprint':projection.fingerprint,'engine_writes':writes,
        'basis':'full source/index/readback; only changed engine rows mutated'}
    _write(generation/'delta.json',delta)
    _write(generation/'operation.json',{'state':'verified',**acceptance,'engine_writes':writes})
    return {**acceptance,'update_mode':'engine_delta','engine_writes':writes},removed
