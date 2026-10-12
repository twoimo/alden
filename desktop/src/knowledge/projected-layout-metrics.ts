export interface ProjectedCircle { x:number; y:number; radius:number; inClipVolume?:boolean }
export interface ProjectionViewport { left:number; top:number; width:number; height:number }
const CAP = 2048;
/** On-demand geometric audit, never a frame loop or a semantic-quality score.
 * Circle cores ignore glow, labels, depth occlusion and pixel rasterization.
 * Exact pair work is bounded to 2,096,128 comparisons. */
export function projectedLayoutMetrics(input:readonly ProjectedCircle[], viewport:ProjectionViewport) {
  const sampled = input.slice(0,CAP), {left,top,width,height}=viewport;
  const base={totalPoints:input.length,sampledPoints:sampled.length,truncated:input.length>CAP};
  if (![left,top,width,height,left+width,top+height].every(Number.isFinite)||width<=0||height<=0)
    return {...base,available:false as const,reason:'invalid_viewport'};
  const valid=sampled.filter(p=>[p.x,p.y,p.radius].every(Number.isFinite)&&p.radius>0);
  const visible=valid.filter(p=>p.inClipVolume!==false&&p.x>=left&&p.x<=left+width&&p.y>=top&&p.y<=top+height);
  const nearest=new Float64Array(visible.length).fill(Infinity), overlapped=new Set<number>(), cells=new Set<number>();
  let pairs=0,tests=0,central=0,minX=Infinity,maxX=-Infinity,minY=Infinity,maxY=-Infinity;
  visible.forEach(p=>{
    minX=Math.min(minX,p.x);maxX=Math.max(maxX,p.x);minY=Math.min(minY,p.y);maxY=Math.max(maxY,p.y);
    const x=Math.min(15,Math.floor((p.x-left)/width*16)),y=Math.min(15,Math.floor((p.y-top)/height*16));
    cells.add(y*16+x);
    if(p.x>=left+width*.25&&p.x<=left+width*.75&&p.y>=top+height*.25&&p.y<=top+height*.75)central++;
  });
  for(let a=0;a<visible.length;a++)for(let b=a+1;b<visible.length;b++){
    tests++;const p=visible[a],q=visible[b],square=(p.x-q.x)**2+(p.y-q.y)**2;
    nearest[a]=Math.min(nearest[a],square);nearest[b]=Math.min(nearest[b],square);
    if(square<(p.radius+q.radius)**2){pairs++;overlapped.add(a);overlapped.add(b);}
  }
  const distances=[...nearest].filter(Number.isFinite).map(Math.sqrt).sort((a,b)=>a-b);
  const middle=Math.floor(distances.length/2);
  const median=distances.length ? distances.length%2 ? distances[middle] : (distances[middle-1]+distances[middle])/2 : null;
  return {...base,available:true as const,invalidPoints:sampled.length-valid.length,validPoints:valid.length,
    visiblePoints:visible.length,clippedCenters:valid.length-visible.length,
    overlapPairs:pairs,overlappingPoints:overlapped.size,pairTests:tests,
    nearestMedianCssPx:median,nearestP95CssPx:distances.length?distances[Math.ceil(distances.length*.95)-1]:null,
    occupiedCells:cells.size,totalCells:256,centralQuarterFraction:visible.length?central/visible.length:null,
    boundingCentersAreaFraction:visible.length?(maxX-minX)*(maxY-minY)/(width*height):null};
}
