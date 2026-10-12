/** Colours encode visible relation distance, never confidence or agent depth. */
export const RELATION_DEPTH_COLORS = ['#f4f2f7', '#ab88e6', '#7fd3e9', '#e69acb', '#eddb83'] as const;
export const GRAPH_CONNECTED_COLOR = '#a799be';
export const GRAPH_ISOLATED_COLOR = '#8497a7';
export const GRAPH_UNRELATED_COLOR = '#48515f';
/** Four major computed groups and an aggregate remainder, not agent depth. */
export const COMMUNITY_COLORS = ['#8497a7',...RELATION_DEPTH_COLORS.slice(1)] as readonly string[];

export function relationDepthColor(depth: number): string {
  return RELATION_DEPTH_COLORS[Math.min(4, Math.max(0, Math.floor(depth)))] ?? RELATION_DEPTH_COLORS[0];
}
