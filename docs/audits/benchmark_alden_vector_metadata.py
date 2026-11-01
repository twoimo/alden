"""Read-only comparison of full versus admitted-current vector metadata reads.

Only temporary scope rows are written. No source text, IDs or vectors are
exported. This measures two SELECTs, not end-to-end indexing or a DB migration.
"""
import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import statistics
import time

FULL='SELECT document_id,version,text_hash,model,endpoint,encoding FROM vectors'
SCOPED='''SELECT v.document_id,v.version,v.text_hash,v.model,v.endpoint,v.encoding
  FROM temp.source_text_scope s CROSS JOIN vectors v
  ON v.document_id=s.document_id AND v.version=s.version'''


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-root',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists():parser.error('output already exists')
    folder=args.state_root/'knowledge'/'collection'
    paths=[folder/name for name in ('collection.sqlite3','retrieval.sqlite3')]
    for path in paths:
        if not path.is_file() or any(p.is_symlink() for p in [path,*path.parents]):
            parser.error('owned DB path missing or unsafe')
    result={'schema':1,'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'scope':'Metadata SELECT only. Main databases opened mode=ro. Private IDs remain in memory; temp scope construction timed separately.',
        'sqlite_runtime':sqlite3.sqlite_version,'cases':[]}
    with closing(sqlite3.connect(paths[0].absolute().as_uri()+'?mode=ro',uri=True)) as source:
        source.execute('PRAGMA query_only=ON')
        keys=source.execute('''SELECT document_id,current_version FROM (
          SELECT m.document_id,m.current_version,ROW_NUMBER() OVER(
            PARTITION BY m.document_id ORDER BY v.collected_at DESC,m.target_id) AS choice
          FROM memberships m JOIN versions v ON v.id=m.current_version JOIN documents d ON d.id=m.document_id
          WHERE m.availability='available' AND d.availability='available'
          AND EXISTS(SELECT 1 FROM target_projects p WHERE p.target_id=m.target_id AND p.permission!='denied'))
          WHERE choice=1 ORDER BY document_id LIMIT 100001''').fetchall()
    if len(keys)>100000:parser.error('scope budget exceeded')
    with closing(sqlite3.connect(paths[1].absolute().as_uri()+'?mode=ro',uri=True)) as db:
        db.execute('CREATE TEMP TABLE source_text_scope(document_id TEXT,version TEXT,PRIMARY KEY(document_id,version))')
        for limit in sorted({min(10,len(keys)),min(1000,len(keys)),len(keys)}):
            began=time.perf_counter()
            db.execute('DELETE FROM temp.source_text_scope')
            db.executemany('INSERT INTO temp.source_text_scope VALUES(?,?)',keys[:limit]);db.commit()
            setup=(time.perf_counter()-began)*1000
            keep=set(keys[:limit]);samples={'full':[],'scoped':[]}
            expected=None
            for iteration in range(6):
                for kind in (('full','scoped') if iteration%2==0 else ('scoped','full')):
                    began=time.perf_counter()
                    db.set_progress_handler(lambda:int(time.perf_counter()-began>3),10000)
                    values=db.execute(FULL if kind=='full' else SCOPED).fetchall()
                    samples[kind].append((time.perf_counter()-began)*1000)
                    if kind=='full':
                        expected={row[:2]:row[2:] for row in values if row[:2] in keep}
                        full_count=len(values)
                    else:
                        actual={row[:2]:row[2:] for row in values}
                    db.set_progress_handler(None,0)
                if expected!=actual:raise RuntimeError('scoped metadata changed admitted stamps')
            result['cases'].append({'scope_rows':limit,'matching_vectors':len(actual),'full_vectors':full_count,
                'scope_setup_ms':setup,'samples_ms':samples,
                'median_ms':{k:statistics.median(v) for k,v in samples.items()},'same_admitted_stamps':True,
                'scoped_plan':[row[3] for row in db.execute('EXPLAIN QUERY PLAN '+SCOPED)]})
    args.output.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    with args.output.open('x') as handle:json.dump(result,handle,indent=2);handle.write('\n')
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
