/** Colours encode visible relation distance, never confidence or agent depth. */
export const RELATION_DEPTH_COLORS = ['#f4f2f7', '#ab88e6', '#7fd3e9', '#e69acb', '#eddb83'] as const;
export const GRAPH_CONNECTED_COLOR = '#a799be';
export const GRAPH_ISOLATED_COLOR = '#8497a7';
export const GRAPH_UNRELATED_COLOR = '#48515f';
/** Eight largest equal-energy groups, then one dim remainder. Not agent depth. */
export const MAJOR_GROUP_LIMIT = 8;
export const COMMUNITY_COLORS = ['#8497a7','#ab88e6','#7fd3e9','#e69acb','#eddb83','#8fd0a4','#e7a07a','#9bb0e8','#d48ad2'] as const;

export function relationDepthColor(depth: number): string {
  return RELATION_DEPTH_COLORS[Math.min(4, Math.max(0, Math.floor(depth)))] ?? RELATION_DEPTH_COLORS[0];
}
