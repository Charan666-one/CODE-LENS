"use client";

/** Two shader pairs, and the reason both are hand-written.
 *
 *  **Nodes are billboards measured in pixels, not spheres measured in world
 *  units.** This is the decision the whole 3D renderer turns on. A sphere in
 *  perspective shrinks with distance, and node size here is not decoration —
 *  it is `fan_in`, "how much the project leans on this". Drawing it as a
 *  world-space sphere would quietly convert that encoding into "how far away
 *  the camera happens to be", which is the exact failure `EXPERIENCE.md`
 *  forbids: a visual that is no longer a fact. So a node occupies the same
 *  number of pixels at the back of the scene as at the front, and **position
 *  alone carries the third dimension.** Every encoding the flat renderer uses
 *  survives into 3D untouched.
 *
 *  **Edges are screen-space quads, not `THREE.Line`.** Browsers ignore
 *  `linewidth` on GL lines, and width here is confidence's second channel —
 *  a guess is drawn thinner than a proof. Three's own fat-line material
 *  carries one width and one opacity for the whole material, so per-edge
 *  width and per-edge alpha would mean either thousands of materials or
 *  string-patching three's internal shader. A quad expanded in the vertex
 *  shader is the same technique that material uses, minus the parts we would
 *  have to fight.
 *
 *  Both are plain GLSL compiled by WebGL. No WebAssembly and no code
 *  generation is involved, which matters: the production CSP grants neither
 *  `'unsafe-eval'` nor `'wasm-unsafe-eval'`, so a library that needed either
 *  would pass in development and fail in front of a user.
 */

/** A node: one screen-facing disc, `aSize` pixels across, wherever `aCenter`
 *  lands on screen. `aBias` is the flat renderer's `zIndex`, nudged into the
 *  depth buffer so a selected node wins a tie against a dimmed neighbour it
 *  happens to be coplanar with. */
export const NODE_VERTEX = /* glsl */ `
  attribute vec3 aCenter;
  attribute float aSize;
  attribute vec4 aColor;
  attribute float aBias;

  uniform vec2 uResolution;

  varying vec2 vQuad;
  varying vec4 vColor;

  void main() {
    vColor = aColor;
    vQuad = position.xy;

    vec4 clip = projectionMatrix * modelViewMatrix * vec4(aCenter, 1.0);
    // Behind the camera: park it off screen rather than let it project
    // mirrored back into view.
    if (clip.w <= 0.0 || aColor.a <= 0.004 || aSize <= 0.0) {
      gl_Position = vec4(2.0, 2.0, 2.0, 1.0);
      return;
    }
    // Pixels to clip space: two NDC units span the whole viewport.
    clip.xy += position.xy * aSize * 2.0 / uResolution * clip.w;
    clip.z -= aBias * 1e-4 * clip.w;
    gl_Position = clip;
  }
`;

export const NODE_FRAGMENT = /* glsl */ `
  varying vec2 vQuad;
  varying vec4 vColor;

  void main() {
    // The quad spans -0.5..0.5, so the disc's edge is at 0.5. One pixel of
    // smoothing, because a hard cut on a 4px node reads as a square.
    float edge = 1.0 - smoothstep(0.44, 0.5, length(vQuad));
    if (edge <= 0.002) discard;
    gl_FragColor = vec4(vColor.rgb, vColor.a * edge);
  }
`;

/** An edge: a quad stretched between two projected points and widened
 *  perpendicular to itself by `aWidth` pixels. `position.x` is the side
 *  (-1 or 1), `position.y` is which end (0 or 1). */
export const EDGE_VERTEX = /* glsl */ `
  attribute vec3 aStart;
  attribute vec3 aEnd;
  attribute float aWidth;
  attribute vec4 aColor;

  uniform vec2 uResolution;

  varying vec4 vColor;

  void main() {
    vColor = aColor;

    vec4 clipStart = projectionMatrix * modelViewMatrix * vec4(aStart, 1.0);
    vec4 clipEnd = projectionMatrix * modelViewMatrix * vec4(aEnd, 1.0);
    if (clipStart.w <= 0.0 || clipEnd.w <= 0.0 || aColor.a <= 0.004) {
      gl_Position = vec4(2.0, 2.0, 2.0, 1.0);
      return;
    }

    // Not named 'half': that is a reserved word in GLSL and compiles nowhere.
    vec2 halfRes = uResolution * 0.5;
    vec2 pixelStart = (clipStart.xy / clipStart.w) * halfRes;
    vec2 pixelEnd = (clipEnd.xy / clipEnd.w) * halfRes;
    vec2 along = pixelEnd - pixelStart;
    float span = length(along);
    along = span > 0.0001 ? along / span : vec2(1.0, 0.0);
    vec2 across = vec2(-along.y, along.x);

    // Interpolating in clip space is exact for a straight segment.
    vec4 clip = mix(clipStart, clipEnd, position.y);
    clip.xy += across * position.x * aWidth * 0.5 / halfRes * clip.w;
    gl_Position = clip;
  }
`;

export const EDGE_FRAGMENT = /* glsl */ `
  varying vec4 vColor;

  void main() {
    gl_FragColor = vColor;
  }
`;
