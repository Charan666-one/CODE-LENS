"use client";

import Graph from "graphology";
import { useEffect, useRef, useState } from "react";
import {
  BufferGeometry,
  DoubleSide,
  Color,
  Float32BufferAttribute,
  Group,
  InstancedBufferAttribute,
  InstancedBufferGeometry,
  Line,
  LineBasicMaterial,
  Mesh,
  NormalBlending,
  PerspectiveCamera,
  Scene,
  ShaderMaterial,
  Vector2,
  Vector3,
  WebGLRenderer,
} from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { CSS2DObject, CSS2DRenderer } from "three/examples/jsm/renderers/CSS2DRenderer.js";
import {
  ARROW_HEADINGS,
  TRANSITION_MS,
  anchorFor,
  buildFrame,
  easeInOutCubic,
  neighbourToward,
} from "@/lib/choreography";
import { useChoreographyClocks } from "@/lib/clocks";
import {
  EDGE_FRAGMENT,
  EDGE_VERTEX,
  NODE_FRAGMENT,
  NODE_VERTEX,
} from "@/lib/three/shaders";
import { frameDistance, massSphere, parseColor, worldOf } from "@/lib/three/spatial";
import { useGraphStore } from "@/lib/store";
import type { ViewNode } from "@/lib/types";

/** The deep renderer. Same world, same choreography, one more axis.
 *
 *  Everything about *what* to draw — the reveal, the ripple, the overlay, the
 *  relevance hierarchy — comes from `lib/choreography.ts`, the same module the
 *  flat renderer reads. This file only knows how to paint it, and the one
 *  thing it paints differently is position: a node's height is its depth in
 *  the import stack, so the code that starts the program sits on top of the
 *  code it rests on.
 *
 *  **Everything else is deliberately identical.** Node size stays `fan_in`
 *  and edge width stays confidence — measured in pixels, not world units, so
 *  perspective cannot quietly turn either into "how far away the camera is"
 *  (see `lib/three/shaders.ts`). The Graph Guide's four legend rows stay true
 *  in both views, which is the only way a toggle between them is honest.
 *
 *  Never import from `three/examples/jsm/libs/**` or the WebGPU/TSL trees:
 *  those carry WebAssembly, and the production CSP grants no `wasm-unsafe-eval`
 *  — it would work in development and fail in front of a reader.
 */

/** Where the camera stands, relative to what it is looking at. Flat-on hides
 *  the depth axis entirely; straight down is just the 2D map with extra steps.
 *  This reads the ground plane and the stack at the same time. */
const HOME_DIRECTION = new Vector3(0, 0.62, 1).normalize();
const FIELD_OF_VIEW = 35;
/** How close a fly-to may get, as a fraction of the level's framing distance.
 *  The flat renderer's `ratio: 0.55`, in world units. */
const CLOSE_IN = 0.55;
const LABEL_POOL = 48;
/** Sigma's `labelRenderedSizeThreshold`. Node sizes here are already in
 *  pixels, so the comparison is direct rather than projected. */
const LABEL_MIN_SIZE = 7;

/** Edge widths, into pixels.
 *
 *  Sigma draws an edge's width in graph units, so zooming in thickens it; the
 *  reader is normally looking at the graph from close enough that a resting
 *  edge is a couple of pixels wide. Here width is stated in pixels directly,
 *  and taken literally a resting edge is 0.4px at 22% alpha — drawn, and
 *  invisible. One constant multiplier and one floor put the ladder back where
 *  the eye can read it. Both are global, so the *relative* widths that carry
 *  confidence are untouched: a guess is still thinner than a proof. */
const EDGE_PIXEL_SCALE = 1.9;
const EDGE_MIN_WIDTH = 0.8;

interface Placement {
  x: number;
  y: number;
  z: number;
  size: number;
}

interface Screen {
  x: number;
  y: number;
  /** Distance from the camera, for breaking ties in favour of what is nearer. */
  depth: number;
  size: number;
}

export default function Graph3DCanvas() {
  const containerRef = useRef<HTMLDivElement>(null);
  const graphRef = useRef<Graph>(new Graph({ multi: true, type: "directed" }));

  const rendererRef = useRef<WebGLRenderer | null>(null);
  const labelRendererRef = useRef<CSS2DRenderer | null>(null);
  const sceneRef = useRef<Scene | null>(null);
  const cameraRef = useRef<PerspectiveCamera | null>(null);
  const controlsRef = useRef<OrbitControls | null>(null);
  const nodeMeshRef = useRef<Mesh | null>(null);
  const edgeMeshRef = useRef<Mesh | null>(null);
  const railsRef = useRef<Group | null>(null);
  const labelsRef = useRef<{ object: CSS2DObject; element: HTMLDivElement }[]>([]);

  /** Node ids in buffer order, and the reverse lookup the paint pass needs. */
  const orderRef = useRef<string[]>([]);
  const edgeOrderRef = useRef<{ key: string; source: string; target: string }[]>([]);
  const screenRef = useRef<Map<string, Screen>>(new Map());

  const tweenRef = useRef<number | null>(null);
  const cameraTweenRef = useRef<number | null>(null);
  const settledAtRef = useRef(0);
  const framedRippleRef = useRef<string | null>(null);
  const homeDistanceRef = useRef(100);
  const hoveredRef = useRef<string | null>(null);
  const paintRef = useRef<() => void>(() => {});
  const [ready, setReady] = useState(false);

  const spec = useGraphStore((s) => s.spec);
  const phase = useGraphStore((s) => s.phase);
  const revealIndex = useGraphStore((s) => s.revealIndex);
  const selectedId = useGraphStore((s) => s.selectedId);
  const selectedPath = useGraphStore((s) => s.explanation?.meta.identity.file_path);
  const blast = useGraphStore((s) => s.blast);
  const rippleFor = useGraphStore((s) => s.rippleFor);
  const rippleFront = useGraphStore((s) => s.rippleFront);
  const overlay = useGraphStore((s) => s.overlay);
  const select = useGraphStore((s) => s.select);
  const skipReveal = useGraphStore((s) => s.skipReveal);
  const clearRipple = useGraphStore((s) => s.clearRipple);
  const clearOverlay = useGraphStore((s) => s.clearOverlay);
  const setZoom = useGraphStore((s) => s.setZoom);
  const dive = useGraphStore((s) => s.dive);

  useChoreographyClocks();

  // ── the scene, once, for the life of the component ──────────────────────
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const renderer = new WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setClearColor(0x000000, 0);
    container.appendChild(renderer.domElement);

    const labelRenderer = new CSS2DRenderer();
    labelRenderer.domElement.className = "graph3d-labels";
    container.appendChild(labelRenderer.domElement);

    const scene = new Scene();
    const camera = new PerspectiveCamera(FIELD_OF_VIEW, 1, 0.1, 10_000);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;
    controls.screenSpacePanning = true;
    controls.rotateSpeed = 0.6;
    controls.zoomSpeed = 0.8;
    // Never fully upside-down: the depth axis has a meaning, and "up" is where
    // the entry points are.
    controls.maxPolarAngle = Math.PI * 0.92;
    // Every animation interruptible (EXPERIENCE): touching the mouse ends any
    // camera flight in progress rather than fighting it.
    controls.addEventListener("start", () => {
      if (cameraTweenRef.current !== null) cancelAnimationFrame(cameraTweenRef.current);
      cameraTweenRef.current = null;
    });

    const rails = new Group();
    scene.add(rails);

    rendererRef.current = renderer;
    labelRendererRef.current = labelRenderer;
    sceneRef.current = scene;
    cameraRef.current = camera;
    controlsRef.current = controls;
    railsRef.current = rails;

    const labels: { object: CSS2DObject; element: HTMLDivElement }[] = [];
    for (let index = 0; index < LABEL_POOL; index += 1) {
      const element = document.createElement("div");
      element.className = "graph3d-label";
      const object = new CSS2DObject(element);
      object.visible = false;
      scene.add(object);
      labels.push({ object, element });
    }
    labelsRef.current = labels;

    const resize = () => {
      const width = container.clientWidth;
      const height = container.clientHeight;
      if (width === 0 || height === 0) return;
      renderer.setSize(width, height, false);
      labelRenderer.setSize(width, height);
      camera.aspect = width / height;
      camera.updateProjectionMatrix();
      for (const mesh of [nodeMeshRef.current, edgeMeshRef.current]) {
        const material = mesh?.material as ShaderMaterial | undefined;
        material?.uniforms.uResolution.value.set(width, height);
      }
      setReady(true);
    };
    const observer = new ResizeObserver(resize);
    observer.observe(container);
    resize();

    let frame = 0;
    const tick = () => {
      frame = requestAnimationFrame(tick);
      controls.update();
      projectAll();
      renderer.render(scene, camera);
      labelRenderer.render(scene, camera);
    };
    frame = requestAnimationFrame(tick);

    /** Every node's position on screen, in CSS pixels. One pass per frame
     *  feeds hit-testing, label placement and the arrow keys, which all ask
     *  the same question and used to be Sigma's job. */
    function projectAll() {
      const width = container!.clientWidth;
      const height = container!.clientHeight;
      const graph = graphRef.current;
      const screens = screenRef.current;
      screens.clear();
      const position = new Vector3();
      graph.forEachNode((id, data) => {
        const world = worldOf(data as unknown as Placement);
        position.set(world.x, world.y, world.z);
        const distance = position.distanceTo(camera.position);
        position.project(camera);
        if (position.z > 1) return; // behind the camera
        screens.set(id, {
          x: ((position.x + 1) / 2) * width,
          y: ((1 - position.y) / 2) * height,
          depth: distance,
          size: (data.size as number) ?? 1,
        });
      });
    }

    return () => {
      cancelAnimationFrame(frame);
      if (tweenRef.current !== null) cancelAnimationFrame(tweenRef.current);
      if (cameraTweenRef.current !== null) cancelAnimationFrame(cameraTweenRef.current);
      observer.disconnect();
      controls.dispose();
      for (const { object, element } of labels) {
        scene.remove(object);
        element.remove();
      }
      disposeMesh(nodeMeshRef.current);
      disposeMesh(edgeMeshRef.current);
      railsRef.current?.children.forEach((child) => {
        if (child instanceof Line) {
          child.geometry.dispose();
          (child.material as LineBasicMaterial).dispose();
        }
      });
      renderer.dispose();
      // The one call that actually hands the context back. Without it a few
      // toggles between flat and deep exhaust the browser's context budget and
      // the oldest canvas silently goes black.
      renderer.forceContextLoss();
      renderer.domElement.remove();
      labelRenderer.domElement.remove();
      rendererRef.current = null;
      nodeMeshRef.current = null;
      edgeMeshRef.current = null;
    };
  }, []);

  // ── the spec: build the graph, the buffers, and the depth rails ─────────
  useEffect(() => {
    const scene = sceneRef.current;
    const camera = cameraRef.current;
    if (!spec || !scene || !camera || !ready) return;

    const graph = graphRef.current;
    const from = new Map<string, Placement>();
    const to = new Map<string, Placement>();
    let survivors = 0;

    const arriving = new Set(spec.nodes.map((node) => node.id));
    for (const id of graph.nodes()) {
      if (arriving.has(id)) {
        survivors += 1;
        from.set(id, {
          x: graph.getNodeAttribute(id, "x") as number,
          y: graph.getNodeAttribute(id, "y") as number,
          z: graph.getNodeAttribute(id, "z") as number,
          size: graph.getNodeAttribute(id, "size") as number,
        });
      } else {
        graph.dropNode(id);
      }
    }
    graph.clearEdges();

    for (const node of spec.nodes) {
      // Same flip the flat renderer uses, so one diff serves both.
      const target: Placement = { x: node.x, y: -node.y, z: node.z ?? 0, size: node.size };
      to.set(node.id, target);
      const attributes = {
        label: node.label,
        color: node.color,
        assemblyIndex: node.assembly_index,
        depth: node.depth ?? 0,
        kind: node.kind,
        filePath: node.file_path,
        startLine: node.start_line,
        cluster: node.cluster,
        explainId: node.explain_id,
      };
      if (graph.hasNode(node.id)) {
        const start = from.get(node.id) ?? target;
        graph.mergeNodeAttributes(node.id, { ...attributes, ...start });
      } else {
        graph.addNode(node.id, { ...attributes, ...target, size: 0.1 });
      }
    }
    for (const edge of spec.edges) {
      if (!graph.hasNode(edge.source) || !graph.hasNode(edge.target)) continue;
      graph.addEdge(edge.source, edge.target, {
        baseSize: Math.min(3, 0.4 + Math.log1p(edge.weight) * 0.6),
        confidence: edge.confidence,
        kind: edge.kind,
      });
    }

    rebuildBuffers();
    rebuildRails(spec.nodes, (spec.meta.depth_max as number) ?? 0);

    // Where the camera lands. A dive named a district, so frame that; the
    // flat renderer installs a bounding box here for the same reason.
    const focus = useGraphStore.getState().pendingFocus;
    const framed = focus
      ? spec.nodes.filter((node) => node.cluster === focus || node.label === focus)
      : [];
    const points = (framed.length > 0 ? framed : spec.nodes).map((node) =>
      worldOf({ x: node.x, y: -node.y, z: node.z ?? 0 }),
    );
    const sphere = massSphere(points, framed.length > 0 ? 2 : 1);
    if (framed.length > 0) useGraphStore.getState().consumeFocus();

    const home = frameDistance(
      massSphere(
        spec.nodes.map((node) => worldOf({ x: node.x, y: -node.y, z: node.z ?? 0 })),
      ).radius,
      FIELD_OF_VIEW,
      camera.aspect,
    );
    homeDistanceRef.current = home;
    const controls = controlsRef.current;
    if (controls) {
      // The flat renderer's minCameraRatio / maxCameraRatio, in world units.
      controls.minDistance = home * 0.05;
      controls.maxDistance = home * 4;
    }
    camera.near = Math.max(home / 500, 0.01);
    camera.far = home * 20;
    camera.updateProjectionMatrix();

    frameOn(
      new Vector3(sphere.center.x, sphere.center.y, sphere.center.z),
      frameDistance(sphere.radius, FIELD_OF_VIEW, camera.aspect),
      survivors > 0 ? TRANSITION_MS : 1,
    );

    // The same 620ms glide the flat renderer uses, so a level change is
    // beat-for-beat the same event in both views.
    if (tweenRef.current !== null) cancelAnimationFrame(tweenRef.current);
    const started = performance.now();
    settledAtRef.current = started + TRANSITION_MS;
    const step = () => {
      const t = Math.min(1, (performance.now() - started) / TRANSITION_MS);
      const eased = easeInOutCubic(t);
      graph.forEachNode((id) => {
        const start = from.get(id);
        const end = to.get(id);
        if (!end) return;
        const origin = start ?? end;
        graph.setNodeAttribute(id, "x", origin.x + (end.x - origin.x) * eased);
        graph.setNodeAttribute(id, "y", origin.y + (end.y - origin.y) * eased);
        graph.setNodeAttribute(id, "z", origin.z + (end.z - origin.z) * eased);
        graph.setNodeAttribute(
          id,
          "size",
          (start?.size ?? 0.1) + (end.size - (start?.size ?? 0.1)) * eased,
        );
      });
      paintRef.current();
      tweenRef.current = t < 1 ? requestAnimationFrame(step) : null;
    };
    tweenRef.current = requestAnimationFrame(step);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [spec, ready]);

  // ── the choreography: what everything looks like right now ──────────────
  useEffect(() => {
    if (!ready) return;
    const graph = graphRef.current;

    const paint = () => {
      const frame = buildFrame(graph, {
        phase,
        revealIndex,
        selectedId,
        selectedPath,
        blast,
        rippleFor,
        rippleFront,
        overlay,
      });
      paintNodes(frame);
      paintEdges(frame);
      paintLabels(frame);
      return frame;
    };
    paintRef.current = () => void paint();
    const frame = paint();

    // Frame the wave's origin once per wave, not once per ring — the flat
    // renderer's rule, and it matters more here: re-aiming a perspective
    // camera under an expanding ripple is motion sickness.
    if (!frame.rippleActive || frame.rippleSource === null) {
      framedRippleRef.current = null;
    } else if (
      framedRippleRef.current !== frame.rippleSource &&
      frame.rippleSource !== frame.anchor
    ) {
      framedRippleRef.current = frame.rippleSource;
      flyTo(frame.rippleSource);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [phase, revealIndex, selectedId, selectedPath, blast, rippleFor, rippleFront, overlay, spec, ready]);

  // ── the camera follows the selection ────────────────────────────────────
  useEffect(() => {
    if (!ready || phase !== "exploring") return;
    const anchor = anchorFor(graphRef.current, selectedId, selectedPath);
    if (!anchor) return;
    // Wait for the level change to stop moving things, or the camera flies to
    // where the node used to be.
    const wait = settledAtRef.current - performance.now();
    if (wait <= 0) {
      flyTo(anchor);
      return;
    }
    const timer = window.setTimeout(() => flyTo(anchor), wait);
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId, selectedPath, phase, ready]);

  // ── pointer: hover, click, double-click, and the empty background ───────
  useEffect(() => {
    const container = containerRef.current;
    const renderer = rendererRef.current;
    if (!container || !renderer || !ready) return;
    const canvas = renderer.domElement;

    let downAt = 0;
    let downOn: string | null = null;
    let downPoint = { x: 0, y: 0 };
    let singleClick: number | null = null;

    const at = (event: PointerEvent | MouseEvent): { x: number; y: number } => {
      const box = canvas.getBoundingClientRect();
      return { x: event.clientX - box.left, y: event.clientY - box.top };
    };

    const onMove = (event: PointerEvent) => {
      const point = at(event);
      const hit = nodeAt(point.x, point.y);
      if (hit !== hoveredRef.current) {
        hoveredRef.current = hit;
        canvas.style.cursor = hit ? "pointer" : "default";
        paintRef.current();
      }
    };

    const onDown = (event: PointerEvent) => {
      downAt = performance.now();
      downPoint = at(event);
      downOn = nodeAt(downPoint.x, downPoint.y);
    };

    const onUp = (event: PointerEvent) => {
      const point = at(event);
      const travelled = Math.hypot(point.x - downPoint.x, point.y - downPoint.y);
      // An orbit that happens to end on a node is not a click on it.
      if (travelled > 5 || performance.now() - downAt > 400) return;
      const hit = nodeAt(point.x, point.y);
      if (hit === null || hit !== downOn) {
        if (hit === null) runStageClick();
        return;
      }
      // Hold the selection for a moment in case a second click is coming —
      // the equivalent of Sigma's `preventSigmaDefault` on double-click.
      if (singleClick !== null) window.clearTimeout(singleClick);
      singleClick = window.setTimeout(() => {
        singleClick = null;
        const state = useGraphStore.getState();
        if (state.phase === "revealing") {
          skipReveal();
          return;
        }
        select(state.selectedId === hit ? null : hit);
      }, 220);
    };

    const onDoubleClick = (event: MouseEvent) => {
      const point = at(event);
      const hit = nodeAt(point.x, point.y);
      if (hit === null) return;
      if (singleClick !== null) {
        window.clearTimeout(singleClick);
        singleClick = null;
      }
      if (useGraphStore.getState().phase === "revealing") return;
      void dive(hit);
    };

    const runStageClick = () => {
      const state = useGraphStore.getState();
      if (state.phase === "revealing") skipReveal();
      else if (state.rippleFor) clearRipple();
      else if (state.overlay) clearOverlay();
      else select(null);
    };

    canvas.addEventListener("pointermove", onMove);
    canvas.addEventListener("pointerdown", onDown);
    canvas.addEventListener("pointerup", onUp);
    canvas.addEventListener("dblclick", onDoubleClick);
    return () => {
      if (singleClick !== null) window.clearTimeout(singleClick);
      canvas.removeEventListener("pointermove", onMove);
      canvas.removeEventListener("pointerdown", onDown);
      canvas.removeEventListener("pointerup", onUp);
      canvas.removeEventListener("dblclick", onDoubleClick);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready]);

  // ── keys: the same ladder, the same arrows, projected ───────────────────
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const state = useGraphStore.getState();
      if (event.key === "Escape") {
        if (state.phase === "revealing") skipReveal();
        else if (state.rippleFor) clearRipple();
        else if (state.overlay) clearOverlay();
        else select(null);
        return;
      }
      const target = event.target as HTMLElement | null;
      if (
        state.paletteOpen ||
        state.guideOpen ||
        target?.isContentEditable ||
        target instanceof HTMLInputElement ||
        target instanceof HTMLTextAreaElement
      ) {
        return;
      }
      if (event.metaKey || event.ctrlKey || event.altKey) return;

      if (event.key === "1" || event.key === "2" || event.key === "3") {
        const level = Number(event.key);
        if (level !== state.zoom) void setZoom(level);
        return;
      }

      const heading = ARROW_HEADINGS[event.key];
      if (!heading) return;
      const graph = graphRef.current;
      const from = anchorFor(graph, state.selectedId, selectedPath);
      if (!from) return;
      // The 45° cone means what it meant in 2D — "to the right on screen" —
      // which now depends on where the camera is standing. That is the
      // feature, not a bug: the arrows follow the picture you are looking at.
      const next = neighbourToward(
        graph,
        (id) => screenRef.current.get(id) ?? null,
        from,
        heading,
      );
      if (next) {
        event.preventDefault();
        select(next);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedPath]);

  return <div ref={containerRef} className="graph-canvas graph3d" />;

  // ── painting ────────────────────────────────────────────────────────────

  function rebuildBuffers() {
    const scene = sceneRef.current;
    const container = containerRef.current;
    if (!scene || !container) return;
    const graph = graphRef.current;

    disposeMesh(nodeMeshRef.current);
    disposeMesh(edgeMeshRef.current);

    const resolution = new Vector2(container.clientWidth, container.clientHeight);

    const ids = graph.nodes();
    orderRef.current = ids;
    const nodeGeometry = new InstancedBufferGeometry();
    nodeGeometry.setAttribute(
      "position",
      new Float32BufferAttribute([-0.5, -0.5, 0, 0.5, -0.5, 0, 0.5, 0.5, 0, -0.5, 0.5, 0], 3),
    );
    nodeGeometry.setIndex([0, 1, 2, 0, 2, 3]);
    nodeGeometry.setAttribute(
      "aCenter",
      new InstancedBufferAttribute(new Float32Array(ids.length * 3), 3),
    );
    nodeGeometry.setAttribute(
      "aSize",
      new InstancedBufferAttribute(new Float32Array(ids.length), 1),
    );
    nodeGeometry.setAttribute(
      "aColor",
      new InstancedBufferAttribute(new Float32Array(ids.length * 4), 4),
    );
    nodeGeometry.setAttribute(
      "aBias",
      new InstancedBufferAttribute(new Float32Array(ids.length), 1),
    );
    nodeGeometry.instanceCount = ids.length;
    const nodeMesh = new Mesh(
      nodeGeometry,
      new ShaderMaterial({
        vertexShader: NODE_VERTEX,
        fragmentShader: NODE_FRAGMENT,
        uniforms: { uResolution: { value: resolution.clone() } },
        transparent: true,
        depthWrite: false,
        blending: NormalBlending,
      }),
    );
    nodeMesh.frustumCulled = false;
    nodeMesh.renderOrder = 1;
    scene.add(nodeMesh);
    nodeMeshRef.current = nodeMesh;

    const edges = graph.edges().map((key) => {
      const [source, target] = graph.extremities(key);
      return { key, source, target };
    });
    edgeOrderRef.current = edges;
    const edgeGeometry = new InstancedBufferGeometry();
    edgeGeometry.setAttribute(
      "position",
      new Float32BufferAttribute([-1, 0, 0, 1, 0, 0, 1, 1, 0, -1, 1, 0], 3),
    );
    edgeGeometry.setIndex([0, 1, 2, 0, 2, 3]);
    edgeGeometry.setAttribute(
      "aStart",
      new InstancedBufferAttribute(new Float32Array(edges.length * 3), 3),
    );
    edgeGeometry.setAttribute(
      "aEnd",
      new InstancedBufferAttribute(new Float32Array(edges.length * 3), 3),
    );
    edgeGeometry.setAttribute(
      "aWidth",
      new InstancedBufferAttribute(new Float32Array(edges.length), 1),
    );
    edgeGeometry.setAttribute(
      "aColor",
      new InstancedBufferAttribute(new Float32Array(edges.length * 4), 4),
    );
    edgeGeometry.instanceCount = edges.length;
    const edgeMesh = new Mesh(
      edgeGeometry,
      new ShaderMaterial({
        vertexShader: EDGE_VERTEX,
        fragmentShader: EDGE_FRAGMENT,
        uniforms: { uResolution: { value: resolution.clone() } },
        transparent: true,
        depthWrite: false,
        blending: NormalBlending,
        // Both faces, and this is not a detail. An edge quad is built along
        // whatever direction the segment happens to run on screen, so its
        // winding depends on that direction — and with the near-uniform
        // orientation these segments end up having, every triangle came out
        // back-facing and the entire edge layer was culled. The graph drew
        // its nodes and none of its relationships, silently.
        side: DoubleSide,
      }),
    );
    edgeMesh.frustumCulled = false;
    edgeMesh.renderOrder = 0; // under the nodes, as in the flat renderer
    scene.add(edgeMesh);
    edgeMeshRef.current = edgeMesh;
  }

  /** One faint ring per layer, labelled. Without it the vertical axis is a
   *  vibe; with it the reader can count the stack and check the claim. */
  function rebuildRails(nodes: ViewNode[], deepest: number) {
    const rails = railsRef.current;
    if (!rails) return;
    for (const child of [...rails.children]) {
      if (child instanceof Line) {
        child.geometry.dispose();
        (child.material as LineBasicMaterial).dispose();
      }
      rails.remove(child);
    }
    if (nodes.length === 0 || deepest === 0) return;

    const points = nodes.map((node) => worldOf({ x: node.x, y: -node.y, z: node.z ?? 0 }));
    const sphere = massSphere(points);
    const heightOf = new Map<number, number>();
    for (const node of nodes) heightOf.set(node.depth ?? 0, node.z ?? 0);

    // A rail every layer while they are countable; every other one when the
    // stack is deep enough that a ring per layer becomes a moiré.
    const stride = deepest > 12 ? 2 : 1;
    const material = new LineBasicMaterial({
      color: new Color(0x94a3b8),
      transparent: true,
      opacity: 0.1,
    });
    for (let depth = 0; depth <= deepest; depth += stride) {
      const height = heightOf.get(depth);
      if (height === undefined) continue;
      const vertices: number[] = [];
      for (let step = 0; step <= 72; step += 1) {
        const angle = (step / 72) * Math.PI * 2;
        vertices.push(
          sphere.center.x + Math.cos(angle) * sphere.radius,
          height,
          sphere.center.z + Math.sin(angle) * sphere.radius,
        );
      }
      const geometry = new BufferGeometry();
      geometry.setAttribute("position", new Float32BufferAttribute(vertices, 3));
      const ring = new Line(geometry, material.clone());
      ring.renderOrder = -1;
      rails.add(ring);
    }
    material.dispose();
  }

  function paintNodes(frame: ReturnType<typeof buildFrame>) {
    const mesh = nodeMeshRef.current;
    if (!mesh) return;
    const graph = graphRef.current;
    const geometry = mesh.geometry as InstancedBufferGeometry;
    const centers = geometry.getAttribute("aCenter") as InstancedBufferAttribute;
    const sizes = geometry.getAttribute("aSize") as InstancedBufferAttribute;
    const colors = geometry.getAttribute("aColor") as InstancedBufferAttribute;
    const biases = geometry.getAttribute("aBias") as InstancedBufferAttribute;

    orderRef.current.forEach((id, index) => {
      if (!graph.hasNode(id)) return;
      const data = graph.getNodeAttributes(id);
      const display = frame.node(id, data);
      const world = worldOf(data as unknown as Placement);
      centers.setXYZ(index, world.x, world.y, world.z);

      const hovered = hoveredRef.current === id;
      const radius = display.size ?? (data.size as number);
      // A node's diameter is what the shader draws, in pixels — Sigma's
      // `size` is a radius, so the two views agree by construction.
      sizes.setX(index, display.hidden ? 0 : radius * 2 * (hovered ? 1.15 : 1));

      const [r, g, b, a] = parseColor(display.color ?? (data.color as string));
      colors.setXYZW(index, r, g, b, display.hidden ? 0 : a);
      biases.setX(index, display.zIndex ?? 0);
    });
    centers.needsUpdate = true;
    sizes.needsUpdate = true;
    colors.needsUpdate = true;
    biases.needsUpdate = true;
  }

  function paintEdges(frame: ReturnType<typeof buildFrame>) {
    const mesh = edgeMeshRef.current;
    if (!mesh) return;
    const graph = graphRef.current;
    const geometry = mesh.geometry as InstancedBufferGeometry;
    const starts = geometry.getAttribute("aStart") as InstancedBufferAttribute;
    const ends = geometry.getAttribute("aEnd") as InstancedBufferAttribute;
    const widths = geometry.getAttribute("aWidth") as InstancedBufferAttribute;
    const colors = geometry.getAttribute("aColor") as InstancedBufferAttribute;

    edgeOrderRef.current.forEach(({ key, source, target }, index) => {
      if (!graph.hasEdge(key)) return;
      const data = graph.getEdgeAttributes(key);
      const display = frame.edge(source, target, data);
      const a = worldOf(graph.getNodeAttributes(source) as unknown as Placement);
      const b = worldOf(graph.getNodeAttributes(target) as unknown as Placement);
      starts.setXYZ(index, a.x, a.y, a.z);
      ends.setXYZ(index, b.x, b.y, b.z);
      widths.setX(
        index,
        display.hidden ? 0 : Math.max((display.size ?? 1) * EDGE_PIXEL_SCALE, EDGE_MIN_WIDTH),
      );
      const [r, g, bb, alpha] = parseColor(display.color ?? "rgba(148,163,184,0.22)");
      colors.setXYZW(index, r, g, bb, display.hidden ? 0 : alpha);
    });
    starts.needsUpdate = true;
    ends.needsUpdate = true;
    widths.needsUpdate = true;
    colors.needsUpdate = true;
  }

  /** Which nodes get a name, and where it goes.
   *
   *  Sigma's rule, kept: a label appears when the choreography forced it, or
   *  when the node is big enough to carry one. What Sigma did for free and
   *  this has to do by hand is the decluttering — in perspective, two nodes
   *  far apart in the world can land on top of each other on screen.
   */
  function paintLabels(frame: ReturnType<typeof buildFrame>) {
    const labels = labelsRef.current;
    if (labels.length === 0) return;
    const graph = graphRef.current;

    const candidates: { id: string; label: string; forced: boolean; size: number }[] = [];
    graph.forEachNode((id, data) => {
      const display = frame.node(id, data);
      if (display.hidden || display.label === null) return;
      const size = display.size ?? (data.size as number);
      const forced = display.forceLabel === true || hoveredRef.current === id;
      if (!forced && size < LABEL_MIN_SIZE) return;
      candidates.push({ id, label: data.label as string, forced, size });
    });
    candidates.sort((a, b) => Number(b.forced) - Number(a.forced) || b.size - a.size);

    const placed: { x: number; y: number }[] = [];
    let used = 0;
    for (const candidate of candidates) {
      if (used >= labels.length) break;
      const screen = screenRef.current.get(candidate.id);
      if (!screen) continue;
      const collides = placed.some(
        (other) => Math.abs(other.x - screen.x) < 72 && Math.abs(other.y - screen.y) < 15,
      );
      if (collides) continue;
      placed.push({ x: screen.x, y: screen.y });

      const slot = labels[used];
      used += 1;
      slot.element.textContent = candidate.label;
      slot.element.dataset.forced = candidate.forced ? "1" : "0";
      const data = graph.getNodeAttributes(candidate.id);
      const world = worldOf(data as unknown as Placement);
      slot.object.position.set(world.x, world.y, world.z);
      slot.object.visible = true;
    }
    for (let index = used; index < labels.length; index += 1) {
      labels[index].object.visible = false;
    }
  }

  // ── camera ──────────────────────────────────────────────────────────────

  /** The node nearest this point, if the point is actually on it. */
  function nodeAt(x: number, y: number): string | null {
    let best: string | null = null;
    let bestDepth = Infinity;
    for (const [id, screen] of screenRef.current) {
      const radius = Math.max(screen.size, 5); // small nodes still need a target
      if (Math.hypot(screen.x - x, screen.y - y) > radius) continue;
      if (screen.depth < bestDepth) {
        bestDepth = screen.depth;
        best = id;
      }
    }
    return best;
  }

  /** Move to a node — closing in, never pulling out. The flat renderer's
   *  `ratio: Math.min(camera.ratio, 0.55)`, said in world units. */
  function flyTo(id: string) {
    const graph = graphRef.current;
    if (!graph.hasNode(id)) return;
    const world = worldOf(graph.getNodeAttributes(id) as unknown as Placement);
    frameOn(
      new Vector3(world.x, world.y, world.z),
      Math.min(currentDistance(), homeDistanceRef.current * CLOSE_IN),
      420,
    );
  }

  function currentDistance(): number {
    const camera = cameraRef.current;
    const controls = controlsRef.current;
    if (!camera || !controls) return homeDistanceRef.current;
    const distance = camera.position.distanceTo(controls.target);
    return Number.isFinite(distance) && distance > 0 ? distance : homeDistanceRef.current;
  }

  /** Look at `target` from `distance` away, keeping the direction the reader
   *  already chose. Preserving their viewing angle is what makes this read as
   *  "the camera went there" rather than "the view was reset". */
  function frameOn(target: Vector3, distance: number, duration: number) {
    const camera = cameraRef.current;
    const controls = controlsRef.current;
    if (!camera || !controls) return;
    if (cameraTweenRef.current !== null) cancelAnimationFrame(cameraTweenRef.current);

    const direction = camera.position.clone().sub(controls.target);
    const heading =
      direction.lengthSq() > 1e-6 ? direction.normalize() : HOME_DIRECTION.clone();
    const toPosition = target.clone().addScaledVector(heading, Math.max(distance, 0.01));
    const fromPosition = camera.position.clone();
    const fromTarget = controls.target.clone();
    const started = performance.now();

    const step = () => {
      const t = Math.min(1, (performance.now() - started) / Math.max(duration, 1));
      const eased = easeInOutCubic(t);
      camera.position.lerpVectors(fromPosition, toPosition, eased);
      controls.target.lerpVectors(fromTarget, target, eased);
      controls.update();
      cameraTweenRef.current = t < 1 ? requestAnimationFrame(step) : null;
    };
    cameraTweenRef.current = requestAnimationFrame(step);
  }
}

function disposeMesh(mesh: Mesh | null) {
  if (!mesh) return;
  mesh.geometry.dispose();
  (mesh.material as ShaderMaterial).dispose();
  mesh.removeFromParent();
}
