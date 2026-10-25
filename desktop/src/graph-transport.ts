function columns(value:unknown): string[] {
  if(!Array.isArray(value)||value.length>256||new Set(value).size!==value.length
    ||!value.every(k=>typeof k==='string'&&k.length>0&&k.length<=128))throw new Error('graph_transport_invalid');
  return value;
}
function restoreRecord(raw:unknown,keys:string[],dense=false):Record<string,unknown> {
  if(!Array.isArray(raw))throw new Error('graph_transport_invalid');
  if(dense&&raw[0]===-1){
    if(raw.length!==keys.length+1)throw new Error('graph_transport_invalid');
    return Object.assign(Object.create(null),Object.fromEntries(keys.map((key,i)=>[key,raw[i+1]])));
  }
  if(raw.length%2||raw.length>keys.length*2)throw new Error('graph_transport_invalid');
  const object=Object.create(null) as Record<string,unknown>,seen=new Set<number>();
  for(let i=0;i<raw.length;i+=2){
    const index=raw[i];
    if(!Number.isSafeInteger(index)||index<0||index>=keys.length||seen.has(index))throw new Error('graph_transport_invalid');
    seen.add(index);Object.defineProperty(object,keys[index],{value:raw[i+1],enumerable:true,writable:true,configurable:true});
  }
  return object;
}
/** Restore every field before graph consumers see the bounded wire packet. */
export function expandGraphTransport(value: Record<string, unknown>): Record<string, unknown> {
  if(value.source_record_encoding!==undefined){
    const marker=value.source_record_encoding;
    if(!marker||typeof marker!=='object'||Array.isArray(marker)||value.source_context_encoding!=='display-source-context-v1')throw new Error('graph_transport_invalid');
    const encoding=marker as Record<string,unknown>;
    const nested=encoding.codec==='record-keys-v2';
    if((encoding.codec!=='record-keys-v1'&&!nested)
      ||Object.keys(encoding).some(k=>!(nested?['codec','nodes','edges','objects']:['codec','nodes','edges']).includes(k)))throw new Error('graph_transport_invalid');
    const objectTables:Partial<Record<'nodes'|'edges',Record<string,string[]>>>={};
    if(nested){
      const objects=encoding.objects;
      if(!objects||typeof objects!=='object'||Array.isArray(objects)||!Object.keys(objects).length
        ||Object.keys(objects).some(k=>!['nodes','edges'].includes(k)))throw new Error('graph_transport_invalid');
      for(const [kind,table]of Object.entries(objects)){
        const fields=kind==='nodes'?['evidence','source_metadata']:['evidence','reference_group'];
        if(!table||typeof table!=='object'||Array.isArray(table)||!Object.keys(table).length
          ||Object.keys(table).some(k=>!fields.includes(k)))throw new Error('graph_transport_invalid');
        objectTables[kind as 'nodes'|'edges']=Object.fromEntries(Object.entries(table).map(([field,keys])=>[field,columns(keys)]));
      }
    }
    const restore=(key:'nodes'|'edges',cap:number)=>{
      const keys=columns(encoding[key]),rows=value[key],tables=objectTables[key]??{};
      if(Object.keys(tables).some(field=>!keys.includes(field))||!Array.isArray(rows)||rows.length>cap)throw new Error('graph_transport_invalid');
      return rows.map(raw=>{
        const object=restoreRecord(raw,keys,nested);
        for(const [field,names]of Object.entries(tables)){
          if(Object.hasOwn(object,field)&&object[field]!==null)object[field]=restoreRecord(object[field],names,true);
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
