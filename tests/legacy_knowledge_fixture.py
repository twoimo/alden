"""Explicit historical database fixture; the product does not seed these facts."""
import json
from pathlib import Path

def seed_historical_fixture(conn, graph_module):
    data=json.loads((Path(__file__).parent/'fixtures/legacy-knowledge-bootstrap.json').read_text())
    for e in data['DEFAULT_ENTITIES']:
        conn.execute('INSERT OR REPLACE INTO kg_entities(entity_id,name,category,aliases_json,description,key_facts_json,importance,updated_at) VALUES(?,?,?,?,?,?,?,?)',
            (e['entity_id'],e['name'],e['category'],json.dumps(e['aliases'],ensure_ascii=False),e['description'],json.dumps(e['key_facts'],ensure_ascii=False),e['importance'],1))
    for r in data['DEFAULT_RELATIONS']:
        graph_module._upsert_relation(conn,source_id=r['source_id'],relation=r['relation'],target_id=r['target_id'],context=r['context'],weight=r['weight'],updated_at=1)
    conn.commit()
