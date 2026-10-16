/** Restore every field before graph consumers see the bounded wire packet. */
export function expandGraphTransport(value: Record<string, unknown>): Record<string, unknown> {
  if(value.source_record_encoding!==undefined){
    const marker=value.source_record_encoding;
    if(!marker||typeof marker!=='object'||Array.isArray(marker)||value.source_context_encoding!=='display-source-context-v1')throw new Error('graph_transport_invalid');
    const encoding=marker as Record<string,unknown>;
    if(encoding.codec!=='record-keys-v1'||Object.keys(encoding).some(k=>!['codec','nodes','edges'].includes(k)))throw new Error('graph_transport_invalid');
    const restore=(key:'nodes'|'edges',cap:number)=>{
      const columns=encoding[key],rows=value[key];
      if(!Array.isArray(columns)||columns.length>256||new Set(columns).size!==columns.length
        ||!columns.every(k=>typeof k==='string'&&k.length>0&&k.length<=128)
        ||!Array.isArray(rows)||rows.length>cap)throw new Error('graph_transport_invalid');
      return rows.map(raw=>{
        if(!Array.isArray(raw)||raw.length%2||raw.length>columns.length*2)throw new Error('graph_transport_invalid');
        const object=Object.create(null) as Record<string,unknown>,seen=new Set<number>();
        for(let i=0;i<raw.length;i+=2){
          const index=raw[i];
          if(!Number.isSafeInteger(index)||index<0||index>=columns.length||seen.has(index))throw new Error('graph_transport_invalid');
          seen.add(index);Object.defineProperty(object,columns[index],{value:raw[i+1],enumerable:true,writable:true,configurable:true});
        }
        return object;
      });
    };
    value={...value,nodes:restore('nodes',2048),edges:restore('edges',32768)};
    delete value.source_record_encoding;
  }
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
