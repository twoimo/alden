export interface CommunityGroup { id:string; count:number; slot:number }
export interface CommunitySnapshot {
  colors:Map<string,number>; groups:CommunityGroup[]; slots:Map<string,number>;
  nodes:number; clusters:number; multiNodeClusters:number; otherNodes:number;
}

/** Worker geometry labels, never entity identity or stored knowledge groups.
 * Reject the whole batch before assigning any colour. The caller must bind it
 * to the current authority, version, R/S input and completed generation. */
export function communitySnapshot(ids:readonly string[],labels:unknown,
  previousSlots:ReadonlyMap<string,number>=new Map()):CommunitySnapshot|null {
  if(!ids.length||ids.length>2048||new Set(ids).size!==ids.length
    ||!Array.isArray(labels)||labels.length!==ids.length)return null;
  const allowed=new Set(ids), memberships=new Map<string,string[]>(),seen=new Set<string>();
  for(const row of labels){
    if(!Array.isArray(row)||row.length!==2)return null;
    const [id,cluster]=row;
    if(typeof id!=='string'||!allowed.has(id)||seen.has(id)||typeof cluster!=='string'
      ||cluster.length>96||!/^c:[0-9a-f]{1,8}:[0-9a-f]{1,8}:*$/.test(cluster))return null;
    seen.add(id);const members=memberships.get(cluster)??[];members.push(id);memberships.set(cluster,members);
  }
  const ranked=[...memberships].filter(([,members])=>members.length>1)
    .sort((a,b)=>b[1].length-a[1].length||(a[0]<b[0]?-1:a[0]>b[0]?1:0));
  const major=ranked.slice(0,4),slots=new Map<string,number>(),used=new Set<number>();
  for(const [id] of major){
    const slot=previousSlots.get(id);
    if(slot!==undefined&&Number.isInteger(slot)&&slot>=1&&slot<=4&&!used.has(slot)){slots.set(id,slot);used.add(slot);}
  }
  for(const [id] of major)if(!slots.has(id)){
    const slot=[1,2,3,4].find(slot=>!used.has(slot))!;slots.set(id,slot);used.add(slot);
  }
  const colors=new Map<string,number>(),groups=major.map(([id,members])=>({id,count:members.length,slot:slots.get(id)!}));
  for(const [cluster,members] of memberships)for(const id of members)colors.set(id,slots.get(cluster)??0);
  return {colors,groups,slots,nodes:ids.length,clusters:memberships.size,multiNodeClusters:ranked.length,
    otherNodes:ids.length-groups.reduce((sum,g)=>sum+g.count,0)};
}
