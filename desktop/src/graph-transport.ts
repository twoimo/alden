/** Restore every field before graph consumers see the bounded wire packet. */
export function expandGraphTransport(value: Record<string, unknown>): Record<string, unknown> {
  if (value.source_context_encoding === undefined) return value;
  if (value.source_context_encoding !== 'display-source-context-v1' || !Array.isArray(value.nodes)
    || value.nodes.length > 2048) throw new Error('graph_transport_invalid');
  const nodes = value.nodes.map(raw => {
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) throw new Error('graph_transport_invalid');
    const node = raw as Record<string, unknown>;
    if (!Array.isArray(node.source_contexts)) return node;
    const source_contexts = node.source_contexts.map(rawContext => {
      if (!rawContext || typeof rawContext !== 'object' || Array.isArray(rawContext)) throw new Error('graph_transport_invalid');
      const context = rawContext as Record<string, unknown>;
      if (context.same_as_display === undefined) return context;
      if (context.same_as_display !== true || Object.keys(context).length !== 2
        || typeof context.project !== 'string' || !context.project
        || !['source_target', 'source_version', 'label'].every(k => typeof node[k] === 'string' && node[k]))
        throw new Error('graph_transport_invalid');
      return { project: context.project, target_id: node.source_target,
        version: node.source_version, label: node.label };
    });
    return { ...node, source_contexts };
  });
  const restored: Record<string, unknown> = { ...value, nodes };
  delete restored.source_context_encoding;
  return restored;
}
