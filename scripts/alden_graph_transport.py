"""Lossless references to exactly matching display source-context fields."""
import json

CODEC = 'display-source-context-v1'
RECORD_CODEC = 'record-keys-v1'
OBJECT_RECORD_CODEC = 'record-keys-v2'
OBJECT_FIELDS = {'nodes': ('evidence', 'source_metadata'), 'edges': ('evidence', 'reference_group')}


def _record_table(records, *, dense=False):
    if not isinstance(records, list) or any(not isinstance(row, dict) for row in records):
        raise ValueError('graph_transport_records_invalid')
    all_keys = {key for row in records for key in row}
    if len(all_keys) > 256 or any(not isinstance(key, str) or not key or len(key) > 128 for key in all_keys):
        raise ValueError('graph_transport_keys_invalid')
    keys = sorted(all_keys)
    index = {key: i for i, key in enumerate(keys)}
    # Every present key/value is retained. An absent field differs from null;
    # rows do not fill missing columns or reinterpret nested source records.
    return keys, [[-1, *(row[key] for key in keys)] if dense and len(row) == len(keys)
                  else [item for key, value in row.items() for item in (index[key], value)] for row in records]


def _object_tables(records, fields):
    """Share nested field names; preserve missing/null and every object value."""
    rows = [dict(row) for row in records]
    tables = {}
    for field in fields:
        present = [i for i, row in enumerate(rows) if field in row and row[field] is not None]
        if not present or any(not isinstance(rows[i][field], dict) for i in present):
            continue
        try:
            keys, packed = _record_table([rows[i][field] for i in present], dense=True)
        except ValueError:
            continue  # v1 already retains unencodable nested values verbatim.
        tables[field] = keys
        for i, value in zip(present, packed):
            rows[i][field] = value
    return rows, tables


def pack_graph(value):
    if not isinstance(value, dict) or not isinstance(value.get('nodes'), list):
        return value
    nodes = []
    for node in value['nodes']:
        if not isinstance(node, dict) or not isinstance(node.get('source_contexts'), list):
            nodes.append(node)
            continue
        contexts = []
        for context in node['source_contexts']:
            if (isinstance(context, dict) and set(context) == {'project', 'target_id', 'version', 'label'}
                    and context['target_id'] == node.get('source_target')
                    and context['version'] == node.get('source_version')
                    and context['label'] == node.get('label')
                    and all(isinstance(context[k], str) and context[k] for k in context)):
                contexts.append({'project': context['project'], 'same_as_display': True})
            else:
                contexts.append(context)
        nodes.append({**node, 'source_contexts': contexts})
    packed = {**value, 'nodes': nodes, 'source_context_encoding': CODEC}
    if len(json.dumps(packed, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()) <= 3 * 1024 * 1024:
        return packed
    if 'source_record_encoding' in packed:
        raise ValueError('graph_transport_encoding_collision')
    node_keys, node_rows = _record_table(packed['nodes'])
    edge_keys, edge_rows = _record_table(packed.get('edges', []))
    records = {**packed, 'nodes': node_rows, 'edges': edge_rows,
               'source_record_encoding': {'codec': RECORD_CODEC, 'nodes': node_keys, 'edges': edge_keys}}
    tables, object_tables = {}, {}
    for kind, fields in OBJECT_FIELDS.items():
        rows, nested = _object_tables(packed.get(kind, []), fields)
        tables[kind] = _record_table(rows, dense=True)[1]
        if nested:
            object_tables[kind] = nested
    if not object_tables:
        return records
    nested_records = {**records, **tables, 'source_record_encoding': {
        'codec': OBJECT_RECORD_CODEC, 'nodes': node_keys, 'edges': edge_keys, 'objects': object_tables}}
    size = lambda item: len(json.dumps(item, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode())
    return nested_records if size(nested_records) < size(records) else records
