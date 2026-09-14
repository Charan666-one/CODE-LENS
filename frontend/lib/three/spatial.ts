"use client";

/** Geometry the 3D renderer needs and the flat one does not.
 *
 *  Nothing here imports three: these are numbers about the graph, and keeping
 *  them separate is what lets the 2D bundle stay free of a 3D dependency.
 */

export interface Point3 {
  x: number;
  y: number;
  z: number;
}

/** The world a node occupies, from the attributes the graph holds.
 *
 *  Both renderers store `y` already flipped (`-spec.y`), because that is the
 *  screen convention the flat one draws in and the diff is shared. Three is
 *  Y-up, so the depth axis — the whole reason this renderer exists — becomes
 *  three's Y and points at the sky, and the flipped `y` becomes three's Z.
 *  Looking straight down at the result reproduces the 2D map rather than its
 *  mirror image.
 */
export function worldOf(node: { x: number; y: number; z?: number }): Point3 {
  return { x: node.x, y: node.z ?? 0, z: node.y };
}

/** The 3D sibling of the flat renderer's `massBBox`: a sphere around the bulk
 *  of the graph rather than around its outliers.
 *
 *  Same rule, one more axis — centre on the centroid, size to the 88th
 *  percentile of each axis's spread, take the largest so nothing is squashed.
 *  A couple of stray nodes must not shrink everything, in either renderer.
 */
export function massSphere(
  points: Point3[],
  pad = 1,
): { center: Point3; radius: number } {
  if (points.length === 0) return { center: { x: 0, y: 0, z: 0 }, radius: 1 };

  const axis = (get: (p: Point3) => number) => points.map(get);
  const xs = axis((p) => p.x);
  const ys = axis((p) => p.y);
  const zs = axis((p) => p.z);
  const mean = (values: number[]) => values.reduce((a, b) => a + b, 0) / values.length;
  const center = { x: mean(xs), y: mean(ys), z: mean(zs) };

  const percentile = (values: number[], centre: number, p: number): number => {
    const spread = values.map((v) => Math.abs(v - centre)).sort((a, b) => a - b);
    const idx = Math.min(spread.length - 1, Math.floor(p * (spread.length - 1)));
    return Math.max(spread[idx], 1);
  };

  // 88th percentile keeps ~1-2 outliers out of the frame; ×1.25 adds margin.
  const half = Math.max(
    percentile(xs, center.x, 0.88),
    percentile(ys, center.y, 0.88),
    percentile(zs, center.z, 0.88),
  );
  return { center, radius: half * 1.25 * pad };
}

/** How far a camera has to stand to fit a sphere of this radius.
 *
 *  The narrower of the two fields of view decides, so the sphere fits both
 *  screen axes — the round equivalent of the flat renderer squaring its
 *  bounding box so neither axis is distorted.
 */
export function frameDistance(radius: number, fovDegrees: number, aspect: number): number {
  const vertical = (fovDegrees * Math.PI) / 180;
  const horizontal = 2 * Math.atan(Math.tan(vertical / 2) * Math.max(aspect, 0.0001));
  return radius / Math.sin(Math.min(vertical, horizontal) / 2);
}

/** Parse the exact colour forms the choreography emits — `#rrggbb` and
 *  `rgba(r,g,b,a)` — into 0..1 components.
 *
 *  Deliberately narrow. It handles what `lib/choreography.ts` and the backend
 *  ramp actually produce and nothing else, and falls back to a visible
 *  magenta rather than to black: a colour this cannot read is a bug, and a
 *  bug should look like one instead of quietly rendering as "unimportant".
 */
export function parseColor(css: string): [number, number, number, number] {
  if (css.startsWith("#") && css.length === 7) {
    return [
      parseInt(css.slice(1, 3), 16) / 255,
      parseInt(css.slice(3, 5), 16) / 255,
      parseInt(css.slice(5, 7), 16) / 255,
      1,
    ];
  }
  const rgba = css.match(
    /^rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*(?:,\s*([\d.]+)\s*)?\)$/,
  );
  if (rgba) {
    return [
      Number(rgba[1]) / 255,
      Number(rgba[2]) / 255,
      Number(rgba[3]) / 255,
      rgba[4] === undefined ? 1 : Number(rgba[4]),
    ];
  }
  return [1, 0, 1, 1];
}
