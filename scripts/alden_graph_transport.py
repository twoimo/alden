"""Lossless references to exactly matching display source-context fields."""
CODEC = 'display-source-context-v1'


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
    return {**value, 'nodes': nodes, 'source_context_encoding': CODEC}
