"""Lossless references to exactly matching display source-context fields."""
import json

CODEC = 'display-source-context-v1'
RECORD_CODEC = 'record-keys-v1'


def _record_table(records):
    if not isinstance(records, list) or any(not isinstance(row, dict) for row in records):
        raise ValueError('graph_transport_records_invalid')
    all_keys = {key for row in records for key in row}
    if len(all_keys) > 256 or any(not isinstance(key, str) or not key or len(key) > 128 for key in all_keys):
        raise ValueError('graph_transport_keys_invalid')
    keys = sorted(all_keys)
    index = {key: i for i, key in enumerate(keys)}
    # Every present key/value is retained. An absent field differs from null;
    # rows do not fill missing columns or reinterpret nested source records.
    return keys, [[item for key, value in row.items() for item in (index[key], value)] for row in records]


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
    return {**packed, 'nodes': node_rows, 'edges': edge_rows,
            'source_record_encoding': {'codec': RECORD_CODEC, 'nodes': node_keys, 'edges': edge_keys}}
