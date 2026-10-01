/** A bounded DOM window with measured row heights; content is never truncated. */
export class VirtualList<T> {
  private items: T[] = [];
  private readonly heights = new Map<string, number>();
  private offsets: number[] = [0];
  private readonly surface = document.createElement("div");
  private readonly observer: ResizeObserver | null;
  private frame: number | null = null;
  private disposed = false;
  private endPinned = false;
  constructor(private host: HTMLElement, private key: (item:T)=>string, private render: (item:T)=>HTMLElement, private estimate=78) {
    this.surface.style.position="relative"; host.replaceChildren(this.surface);
    this.observer=typeof ResizeObserver==='undefined'?null:new ResizeObserver(entries=>{
      let changed=false;
      for(const entry of entries){const id=(entry.target as HTMLElement).dataset.virtualKey!;const height=entry.borderBoxSize?.[0]?.blockSize || entry.contentRect.height;
        if(height>0 && Math.abs((this.heights.get(id)??this.estimate)-height)>1){this.heights.set(id,height);changed=true;}}
      if(changed){this.measure();if(this.endPinned)this.host.scrollTop=this.offsets.at(-1)!;this.schedule();}
    });
    host.addEventListener('scroll',this.scroll,{passive:true});
  }
  set(items:T[],options:{end?:boolean;preserve?:boolean}={}):void {
    const oldHeight=this.offsets.at(-1)!;const oldTop=this.host.scrollTop;
    this.items=items;this.measure();this.endPinned=options.end===true;
    this.host.scrollTop=options.end?this.offsets.at(-1)!:options.preserve?oldTop+this.offsets.at(-1)!-oldHeight:0;
    this.draw();
  }
  private measure():void {this.offsets=[0];for(const item of this.items)this.offsets.push(this.offsets.at(-1)!+(this.heights.get(this.key(item))??this.estimate));this.surface.style.height=this.offsets.at(-1)+'px';}
  private readonly scroll=():void=>{this.endPinned=this.host.scrollTop+this.host.clientHeight>=this.offsets.at(-1)!-12;this.schedule();};
  private schedule():void {if(this.frame!==null||this.disposed)return;this.frame=requestAnimationFrame(()=>{this.frame=null;this.draw();});}
  private draw():void {
    if(this.disposed)return;this.observer?.disconnect();this.surface.replaceChildren();
    let low=0,high=this.items.length;const top=this.host.scrollTop;
    while(low<high){const mid=(low+high)>>>1;if(this.offsets[mid]<top)low=mid+1;else high=mid;}
    const start=Math.max(0,low-5);const bottom=top+Math.max(this.host.clientHeight,400)+500;
    for(let i=start;i<this.items.length&&i<start+80;i++){
      if(this.offsets[i]>bottom)break;const row=this.render(this.items[i]);row.dataset.virtualKey=this.key(this.items[i]);
      row.style.position='absolute';row.style.top=this.offsets[i]+'px';row.style.left='0';row.style.right='0';
      this.surface.append(row);this.observer?.observe(row);
    }
  }
  dispose():void {this.disposed=true;this.host.removeEventListener('scroll',this.scroll);this.observer?.disconnect();if(this.frame!==null)cancelAnimationFrame(this.frame);this.surface.replaceChildren();}
}
