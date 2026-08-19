import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { Line2 } from "three/examples/jsm/lines/Line2.js";
import { LineGeometry } from "three/examples/jsm/lines/LineGeometry.js";
import { LineMaterial } from "three/examples/jsm/lines/LineMaterial.js";
import { ConvexGeometry } from "three/examples/jsm/geometries/ConvexGeometry.js";
import { TessellateModifier } from "three/examples/jsm/modifiers/TessellateModifier.js";
import { mergeVertices } from "three/examples/jsm/utils/BufferGeometryUtils.js";

// Rendering-only module for the graph explorer's 3D view. Owns the WebGL
// scene, camera, orbit controls, node fills/avatar textures, edges +
// dependency arrowheads, picking, and screen-space projection queries.
// Everything else (data loading, tick() physics, search/six-degrees/
// compare/permalink, LOD/selection *decisions*) stays in the vanilla
// script in web/template.html -- this module only answers "where is X on
// screen" / "what's under the cursor" and draws what it's told to.
//
// Picking and LOD sizing deliberately do NOT use THREE.Raycaster against
// scene objects: Sprite raycasting has no built-in constant-screen-space
// hit padding (today's 2D findNodeAt/findEdgeAtIn use a `3px`/`6px`
// screen-constant pad), so both are done as a manual screen-space
// distance test using projectToScreen()/projectedRadius() instead --
// see PICK_PAD_PX below.
//
// Every *Core(s, ...) function below takes its state explicitly rather
// than via `this` -- there's exactly one Scene3D instance in this app (one
// canvas, one camera), so the exported public API at the bottom is a thin
// set of wrappers closing over a module-level singleton (set by init()).
// Passing state explicitly instead of relying on `this` also means these
// are directly unit-testable (scene3d.test.mjs) without any method-call
// binding tricks -- `this` inside a function called as `Scene3D.pick(...)`
// from the bundled browser global binds to the Scene3D namespace object,
// not a renderer instance, so relying on `this` here would silently break
// in production while still passing tests that called functions via
// `.call(fakeState, ...)`.

var PICK_PAD_PX = 3;
var EDGE_PICK_PAD_PX = 6;

// Fixed-contrast circular sprite texture (white disc, alpha-masked outside
// the circle) generated once and tinted per-node via SpriteMaterial.color
// -- a flat-colored sprite by default, swapped to a repo's avatar texture
// only after that image loads successfully (see loadAvatarTexture below),
// matching the existing draw()'s explicit two-state fallback semantics
// (logo.complete && logo.naturalWidth > 0) rather than a naive one-shot load.
function buildCircleTexture() {
  var size = 128;
  var canvas = document.createElement("canvas");
  canvas.width = canvas.height = size;
  var ctx = canvas.getContext("2d");
  ctx.beginPath();
  ctx.arc(size / 2, size / 2, size / 2 - 1, 0, Math.PI * 2);
  ctx.fillStyle = "#fff";
  ctx.fill();
  var tex = new THREE.CanvasTexture(canvas);
  tex.needsUpdate = true;
  return tex;
}

function makeState() {
  return {
    renderer: null,
    scene: null,
    camera: null,
    controls: null,
    canvas: null,
    cssWidth: 1,
    cssHeight: 1,
    circleTexture: null,
    nodeObjects: new Map(), // id -> THREE.Sprite
    edgeLines: {}, // tier -> Line2[]
    arrowGroup: null, // dependency-tier arrowheads (THREE.Group of cones)
    clusterVolumes: new Map(), // cluster id -> { mesh } (see syncClusterVolumesCore)
    avatarTextures: new Map(), // owner -> THREE.Texture | "pending" | "error"
    lastNodes: [],
    lastNodesById: new Map(),
    _lastEdgeTiers: {},
    raycaster: new THREE.Raycaster(),
    palette: null,
    orbiting: false,
    axisLines: [],
    axisTopPoint: null,
    axisBottomPoint: null
  };
}

// A vertical reference line spanning the trophic-height axis (see
// layoutWorldPos/TROPHIC_Y_RANGE in template.html), with short end-caps so
// it reads as a bounded ruler rather than an infinite line. In the old 2D
// view, "up" and "down" needed no explanation -- the whole canvas was that
// one plane. A real 3D view can be rotated to any angle, so without some
// fixed reference a viewer has no way to tell which direction is actually
// the meaningful axis (trophic height) versus incidental camera framing.
// The two disconnected end-cap segments are separate Line2 objects rather
// than one polyline with the main shaft, since LineGeometry.setPositions
// draws one continuous connected path -- three short unconnected segments
// need three objects.
function buildAxis(s, range) {
  var half = range / 2;
  var capR = Math.max(15, range * 0.02);
  function makeLine(positions) {
    var material = new LineMaterial({
      color: "#ffffff", linewidth: 3, transparent: true, opacity: 0.9,
      resolution: new THREE.Vector2(s.cssWidth, s.cssHeight)
    });
    var geometry = new LineGeometry();
    geometry.setPositions(positions);
    var line = new Line2(geometry, material);
    line.computeLineDistances();
    s.scene.add(line);
    return line;
  }
  s.axisLines = [
    makeLine([0, -half, 0, 0, half, 0]),
    makeLine([-capR, half, 0, capR, half, 0]),
    makeLine([-capR, -half, 0, capR, -half, 0])
  ];
  s.axisTopPoint = { x: 0, y: half, z: 0 };
  s.axisBottomPoint = { x: 0, y: -half, z: 0 };
}

export function initCore(canvas, opts) {
  opts = opts || {};
  var s = makeState();
  s.canvas = canvas;

  s.renderer = new THREE.WebGLRenderer({ canvas: canvas, antialias: true, alpha: true });
  s.renderer.setClearColor(0x000000, 0);

  s.scene = new THREE.Scene();
  s.camera = new THREE.PerspectiveCamera(50, 1, 1, 20000);
  // Deliberately off-axis, not (0, 0, 1400): the topic-circle embedding's
  // depth component (r*sin(theta), the whole reason this view is real 3D
  // and not just the old 2D projection with an unused third coordinate --
  // see ROADMAP.md Phase 18) runs along Z. A camera starting straight down
  // -Z stares directly along that axis, collapsing it to zero apparent
  // spread on screen -- fitView()/frameNodesCore preserve whatever
  // direction the camera already has and only ever change distance, so an
  // axis-aligned start stayed axis-aligned forever unless a visitor
  // happened to manually orbit. This three-quarter start (~35 degrees
  // azimuth, ~18 degrees elevation) makes the real depth axis visible from
  // the very first frame instead of requiring a drag to discover it exists.
  s.camera.position.set(790, 435, 1147);

  s.controls = new OrbitControls(s.camera, canvas);
  s.controls.enableDamping = true;
  s.controls.dampingFactor = 0.1;
  s.controls.screenSpacePanning = true;
  s.controls.target.set(0, 0, 0);
  s.controls.update();
  // Only fires for user-driven gestures (drag/touch), not programmatic
  // camera moves like frameNodes()/focusNode() calling controls.update()
  // directly -- exactly the "actively rotating the view" window the
  // vanilla layer wants to suppress hover/tooltips during.
  s.controls.addEventListener("start", function () { s.orbiting = true; });
  s.controls.addEventListener("end", function () { s.orbiting = false; });

  // Owners the build step found to have no web/logos/{owner}.png, seeded
  // straight into the "error" state loadAvatarTexture already uses for a
  // failed load. Without this the only way it learns a file is absent is by
  // requesting it and getting a 404 -- once per absent owner, in the console,
  // on every load. Same flat-color result either way, just no dead request.
  (opts.avatarlessOwners || []).forEach(function (owner) {
    s.avatarTextures.set(owner, "error");
  });

  s.circleTexture = buildCircleTexture();
  buildAxis(s, opts.axisRange || 900);

  resizeCore(s, opts.width || 800, opts.height || 600, opts.dpr || 1);
  return s;
}

// ---- resize / render ----

export function resizeCore(s, width, height, dpr) {
  s.cssWidth = Math.max(1, width);
  s.cssHeight = Math.max(1, height);
  s.renderer.setPixelRatio(dpr || 1);
  s.renderer.setSize(s.cssWidth, s.cssHeight, false);
  s.camera.aspect = s.cssWidth / s.cssHeight;
  s.camera.updateProjectionMatrix();
  Object.keys(s.edgeLines).forEach(function (tier) {
    s.edgeLines[tier].forEach(function (line) {
      line.material.resolution.set(s.cssWidth, s.cssHeight);
    });
  });
  s.axisLines.forEach(function (line) {
    line.material.resolution.set(s.cssWidth, s.cssHeight);
  });
}

export function renderCore(s) {
  s.controls.update();
  s.renderer.render(s.scene, s.camera);
}

export function setOrbitEnabledCore(s, enabled) {
  s.controls.enabled = !!enabled;
}

// True only while a user-driven orbit/pan/dolly gesture is actively in
// progress (see the controls "start"/"end" listeners in initCore) -- the
// vanilla layer uses this to suppress hover/tooltips while the camera is
// mid-rotation, since a mouse position sweeping across the screen during
// an orbit drag isn't the user pointing at anything.
export function isOrbitingCore(s) {
  return !!s.orbiting;
}

export function axisEndpointsCore(s) {
  return { top: s.axisTopPoint, bottom: s.axisBottomPoint };
}

export function setPaletteCore(s, palette) {
  s.palette = palette;
  s.axisLines.forEach(function (line) {
    line.material.color.set(palette.axis || "#ffffff");
  });
  s.nodeObjects.forEach(function (sprite, id) {
    var n = s.lastNodesById.get(id);
    if (n) applyNodeMaterial(s, n, sprite);
  });
  // Edge colors are resolved fresh by the vanilla layer into e._color every
  // sync() call (see syncEdgeTier), which runs every frame from loop() --
  // no separate per-tier repaint needed here, the next frame already picks
  // up the new palette.
}

// ---- avatar textures (owner -> texture, cached, two-state fallback) ----
//
// "error" is both the outcome of a failed load and the way initCore seeds
// owners the build step already knows have no logo file (opts.
// avatarlessOwners) -- an owner in that state is never requested again, or
// at all.

export function loadAvatarTextureCore(s, owner, onReady) {
  var cached = s.avatarTextures.get(owner);
  if (cached === "error") return null;
  if (cached instanceof THREE.Texture) return cached;
  if (cached !== "pending") {
    s.avatarTextures.set(owner, "pending");
    var loader = new THREE.TextureLoader();
    loader.load(
      "logos/" + owner + ".png",
      function (tex) {
        tex.colorSpace = THREE.SRGBColorSpace;
        s.avatarTextures.set(owner, tex);
        if (onReady) onReady(tex);
      },
      undefined,
      function () { s.avatarTextures.set(owner, "error"); }
    );
  }
  return null; // not ready yet this call -- flat color renders until onReady swaps it in
}

function applyNodeMaterial(s, n, sprite) {
  var palette = s.palette || { node: {} };
  var clusterTint = n._clusterTint || null;
  var baseColor = clusterTint || (palette.node && palette.node[n.type]) || "#888";
  sprite.material.color.set(baseColor);
  sprite.material.map = s.circleTexture;

  if (n.type === "repository" && !clusterTint) {
    var owner = n.label.split("/")[0];
    var tex = loadAvatarTextureCore(s, owner, function (loadedTex) {
      // Only swap in if this sprite is still showing the same untinted repo.
      if (sprite.material && !sprite.userData.clusterTint) {
        sprite.material.map = loadedTex;
        sprite.material.color.set("#ffffff");
        sprite.material.needsUpdate = true;
      }
    });
    if (tex) { sprite.material.map = tex; sprite.material.color.set("#ffffff"); }
  }
  sprite.userData.clusterTint = !!clusterTint;
  sprite.material.needsUpdate = true;
}

// ---- sync: reconcile THREE objects against the current node/edge arrays ----
//
// `renderOpts.getClusterTint(node) -> cssColor|null` mirrors draw()'s
// `clusterTint = showClusterColors && n.type === "repository" ?
// clusterColorOf(n.id) : null` -- resolved by the caller (which already
// owns showClusterColors/isDarkTheme state) and passed in per node rather
// than duplicated here, keeping this module a pure renderer.
export function syncCore(s, nodes, edgeTiers, renderOpts) {
  renderOpts = renderOpts || {};
  var getClusterTint = renderOpts.getClusterTint || function () { return null; };

  var seen = new Set();
  s.lastNodes = nodes;
  s.lastNodesById = new Map();
  s._lastEdgeTiers = edgeTiers || {};
  nodes.forEach(function (n) {
    seen.add(n.id);
    s.lastNodesById.set(n.id, n);
    n._clusterTint = getClusterTint(n);
    var sprite = s.nodeObjects.get(n.id);
    if (!sprite) {
      var material = new THREE.SpriteMaterial({ map: s.circleTexture, transparent: true, depthWrite: true });
      sprite = new THREE.Sprite(material);
      s.scene.add(sprite);
      s.nodeObjects.set(n.id, sprite);
      applyNodeMaterial(s, n, sprite);
    } else if (sprite.userData.clusterTint !== !!n._clusterTint) {
      applyNodeMaterial(s, n, sprite);
    }
    sprite.position.set(n.x, n.y, n.z);
    var d = Math.max(0.01, n.radius * 2);
    sprite.scale.set(d, d, 1);
    // n._hideMarker (template.html): a collapsed cluster/halo already has a
    // real enclosing volume mesh (syncClusterVolumesCore) built from its
    // members' actual positions -- this sprite would just be a small solid
    // disc duplicating that shape, so it's kept in the pool (position/pick
    // data still tracked, still cheap) but not drawn. A fading-out ghost
    // (template.html's fadingOutGhosts) has no volume of its own and never
    // sets this flag, so it keeps its marker for the whole fade.
    sprite.visible = !n._hideMarker;
    sprite.userData.nodeId = n.id;
    sprite.userData.nodeType = n.type;
    // _fadeOpacity (LOD cluster expand/collapse transitions, template.html)
    // multiplies on top of the dim/full-strength base rather than replacing
    // it, so a newly-materialized node fades in already respecting whatever
    // dim state it should land in, instead of flashing full-bright first.
    var fadeOpacity = n._fadeOpacity != null ? n._fadeOpacity : 1;
    sprite.material.opacity = (n._dim ? 0.25 : 1) * fadeOpacity;
  });
  s.nodeObjects.forEach(function (sprite, id) {
    if (!seen.has(id)) {
      s.scene.remove(sprite);
      s.nodeObjects.delete(id);
    }
  });

  Object.keys(edgeTiers || {}).forEach(function (tier) {
    syncEdgeTier(s, tier, edgeTiers[tier]);
  });
}

// One THREE.Line2 per edge (not a merged per-tier geometry) so the vanilla
// layer can drive per-edge color/opacity/width every frame -- selection
// dimming, hover "hot" state, and selected-node "active" highlighting are
// all real per-edge state today (see draw()'s edges.forEach), not just a
// per-tier default. The vanilla layer resolves that styling (it already
// owns selectedNode/hoveredEdge/highlightPath) and stamps it onto each edge
// object as e._color/e._opacity/e._width before calling sync(); this stays
// a "dumb" renderer that only draws what it's told, same boundary as the
// node _clusterTint/_dim hooks. Edge arrays are already rebuilt wholesale
// (not incrementally diffed) whenever the materialized frontier changes
// (see rebuildTierEdges()), so index-based pool reuse here is safe -- no
// edge "identity" needs to survive across a rebuild.
// An edge's route: its explicit bundled waypoints (ROADMAP.md Phase 16 --
// see bundledControlPoints() in template.html, which stamps e._path on
// dependency-tier edges) if present, otherwise the plain 2-point
// source-target line every other tier still uses.
function edgeRoutePoints(e) {
  return e._path && e._path.length >= 2 ? e._path : [e.source, e.target];
}

// A route's endpoints/waypoints are almost always frozen (repo/cluster
// nodes are static by default -- see template.html's tick(), which zeroes
// their velocity outright unless the opt-in force slider is raised), so the
// same CatmullRomCurve3 sampling + LineGeometry buffer upload was happening
// every single animation frame for the entire edge set regardless of
// whether the camera did anything but orbit -- measured directly (CPU
// profile of loop()) as the dominant cost behind ~450-1000ms/frame at this
// cohort's scale. Caching each pooled line's last-synced route and skipping
// the rebuild when it's unchanged cuts that to near zero for anything
// actually static, while anything genuinely moving (a dragged node, an
// individual-sample node still settling under real physics) keeps updating
// every frame exactly as before -- the comparison is on real coordinates,
// not a node-type allowlist, so it can't silently go stale.
function routeSignature(route) {
  var out = new Array(route.length * 3);
  for (var i = 0; i < route.length; i++) {
    out[i * 3] = route[i].x; out[i * 3 + 1] = route[i].y; out[i * 3 + 2] = route[i].z;
  }
  return out;
}

function routeUnchanged(sig, route) {
  if (!sig || sig.length !== route.length * 3) return false;
  for (var i = 0; i < route.length; i++) {
    if (sig[i * 3] !== route[i].x || sig[i * 3 + 1] !== route[i].y || sig[i * 3 + 2] !== route[i].z) return false;
  }
  return true;
}

// Pools only ever grew. Zooming in materialises the whole cohort, so the
// dependency pool balloons to ~15k Line2 objects, and zooming back out left
// every one of them in the scene as an invisible child -- still traversed and
// matrix-updated every frame. Measured: the far view costs 17ms on load but
// 34ms after one zoom in and out, and never recovers. Shrinking releases
// that, with enough slack and enough hysteresis that ordinary zoom jitter
// never triggers a rebuild.
export var POOL_SLACK = 256;
export function shrinkPool(s, pool, needed, onDrop) {
  if (pool.length <= needed * 2 + POOL_SLACK) return 0;
  var keep = needed + POOL_SLACK;
  // Snapshot first: for the arrowhead pool `pool` IS group.children, and
  // Object3D.remove() splices that same array -- walking it while removing
  // from it would skip every other entry.
  var dropped = pool.slice(keep);
  for (var i = 0; i < dropped.length; i++) {
    var obj = dropped[i];
    if (onDrop) onDrop(obj); else s.scene.remove(obj);   // splices group.children for us
    if (obj.geometry && obj.geometry !== s.arrowGeometry) obj.geometry.dispose();
    if (obj.material) obj.material.dispose();
  }
  if (pool.length > keep) pool.length = keep;             // plain arrays need truncating too
  return dropped.length;
}

function syncEdgeTier(s, tier, edges) {
  var pool = s.edgeLines[tier] || (s.edgeLines[tier] = []);
  shrinkPool(s, pool, edges.length);
  while (pool.length < edges.length) {
    var material = new LineMaterial({
      color: "#999999", linewidth: 1, transparent: true, opacity: 0.35,
      resolution: new THREE.Vector2(s.cssWidth, s.cssHeight)
    });
    var line = new Line2(new LineGeometry(), material);
    pool.push(line);
    s.scene.add(line);
  }
  for (var i = 0; i < pool.length; i++) {
    var lineObj = pool[i];
    if (i >= edges.length) { lineObj.visible = false; continue; }
    var e = edges[i];
    var route = edgeRoutePoints(e);
    if (!routeUnchanged(lineObj.userData._routeSig, route)) {
      var flat;
      if (route.length > 2) {
        // Smooth the bundled waypoints into a curve rather than drawing the
        // raw control polygon -- the waypoints themselves are tree-ancestor
        // positions, not points meant to be visually sharp corners.
        var curvePts = route.map(function (p) { return new THREE.Vector3(p.x, p.y, p.z); });
        var curve = new THREE.CatmullRomCurve3(curvePts);
        var sampled = curve.getPoints(Math.max(8, Math.min(32, route.length * 8)));
        flat = [];
        sampled.forEach(function (v) { flat.push(v.x, v.y, v.z); });
      } else {
        flat = [route[0].x, route[0].y, route[0].z, route[1].x, route[1].y, route[1].z];
      }
      lineObj.geometry.setPositions(flat);
      lineObj.computeLineDistances();
      lineObj.userData._routeSig = routeSignature(route);
    }
    // Only write a material property that actually changed. Each of these is
    // a uniform the renderer re-uploads when touched, and color.set() on a
    // string re-parses the string every time -- with one material per edge
    // and tens of thousands of edges on screen at full zoom, that was tens of
    // thousands of redundant string parses and uniform writes per frame for
    // values that are identical frame to frame. Same reasoning as the route
    // caching above, applied to appearance instead of geometry.
    var color = e._color || (s.palette && s.palette.edge) || "#999999";
    var opacity = e._opacity != null ? e._opacity : 0.35;
    var width = e._width || 1;
    var mat = lineObj.material, seen = lineObj.userData;
    if (seen._color !== color) { mat.color.set(color); seen._color = color; }
    if (seen._opacity !== opacity) { mat.opacity = opacity; seen._opacity = opacity; }
    if (seen._width !== width) { mat.linewidth = width; seen._width = width; }
    lineObj.visible = true;
  }

  if (tier === "dependency") syncArrowheads(s, edges);
}

// Dependency edges are the one directed tier -- a small cone near the
// target end, oriented along the edge direction, mirroring the hand-built
// triangular arrowhead draw() computes today from the endpoint unit vector.
function syncArrowheads(s, edges) {
  if (!s.arrowGroup) {
    s.arrowGroup = new THREE.Group();
    s.scene.add(s.arrowGroup);
  }
  // One shared ConeGeometry for every arrowhead rather than one per mesh.
  // The old code built a fresh ConeGeometry per arrow, so a fully zoomed-in
  // view held ~15k identical geometries -- 15k separate GPU buffers and 15k
  // vertex-array binds a frame, for a shape that is the same cone every time.
  // Only the per-mesh transform and material differ, and those still do.
  if (!s.arrowGeometry) {
    s.arrowGeometry = new THREE.ConeGeometry(4, 12, 8);
    s.arrowGeometry.rotateX(Math.PI / 2);
  }
  var group = s.arrowGroup;
  shrinkPool(s, group.children, edges.length, function (m) { group.remove(m); });
  while (group.children.length < edges.length) {
    group.add(new THREE.Mesh(s.arrowGeometry, new THREE.MeshBasicMaterial({ transparent: true })));
  }
  for (var i = 0; i < s.arrowGroup.children.length; i++) {
    var mesh = s.arrowGroup.children[i];
    if (i >= edges.length) { mesh.visible = false; continue; }
    var e = edges[i];
    // Oriented off the route's *final* segment, not the raw source-target
    // vector -- for a bundled edge (see edgeRoutePoints) that's the
    // approach direction the drawn curve actually arrives from, so the
    // arrowhead still backs off the target surface along the right line
    // instead of pointing straight through a detour.
    var route = edgeRoutePoints(e);
    var from = route[route.length - 2], to = route[route.length - 1];
    var dx = to.x - from.x, dy = to.y - from.y, dz = to.z - from.z;
    var dist = Math.sqrt(dx * dx + dy * dy + dz * dz) || 1;
    var t = Math.max(0, (dist - (e.target.radius || 6) - 10) / dist);
    mesh.position.set(from.x + dx * t, from.y + dy * t, from.z + dz * t);
    mesh.lookAt(to.x, to.y, to.z);
    var acolor = e._color || (s.palette && s.palette.edgeDependency) || "#c9834a";
    var aopacity = e._opacity != null ? e._opacity : 1;
    if (mesh.userData._color !== acolor) { mesh.material.color.set(acolor); mesh.userData._color = acolor; }
    if (mesh.userData._opacity !== aopacity) { mesh.material.opacity = aopacity; mesh.userData._opacity = aopacity; }
    var scale = (e._width || 1) > 2 ? 1.25 : 1; // matches the vanilla layer's hot-edge size boost
    mesh.scale.set(scale, scale, scale);
    mesh.visible = true;
  }
}

// ---- cluster volumes ----
//
// A cluster used to draw as a flat 2D blob on the overlay canvas (a
// screen-space convex hull of its members' projected positions, redrawn
// every frame). That canvas always paints *over* the WebGL layer no matter
// what's actually in front of it in 3D -- there's no such thing as "this
// cluster is behind that one from here" on a layer with no concept of
// depth, so two overlapping clusters, or a cluster and the real node in
// front of it, always composited in DOM paint order instead of camera
// distance. A real mesh in the scene gets correct depth test/occlusion for
// free from the same renderer that already draws everything else -- this
// is that fix, not a cosmetic upgrade.
//
// Every cluster's member positions are static (WORLD_POS in template.html
// never changes after load -- that's the whole point of the static-map
// work), so unlike nodeObjects/edgeLines above, geometry here is built
// *once* per cluster id and simply toggled visible/hidden after that --
// nothing about a cluster's true shape ever needs recomputing, only
// whether it's currently one of the ones worth showing.

function inflatePoints3D(points, padding) {
  var cx = 0, cy = 0, cz = 0;
  points.forEach(function (p) { cx += p.x; cy += p.y; cz += p.z; });
  var n = points.length;
  cx /= n; cy /= n; cz /= n;
  return points.map(function (p) {
    var dx = p.x - cx, dy = p.y - cy, dz = p.z - cz;
    var d = Math.sqrt(dx * dx + dy * dy + dz * dz) || 1;
    return new THREE.Vector3(p.x + (dx / d) * padding, p.y + (dy / d) * padding, p.z + (dz / d) * padding);
  });
}

// A cluster whose real members happen to be near-coplanar along one of the
// three world axes (common for a topically-tight cluster: trophic height in
// particular tends to correlate within a real category) hulls into a
// paper-thin pancake -- a valid, non-throwing ConvexGeometry, so it never
// hits the sphere fallback below, it just reads as a flat sliver instead of
// a territory. Checked per-axis (not via a true PCA "flattest direction")
// since x/y/z here are already meaningful independent axes (topic circle,
// trophic height), not arbitrary ones. Points already on the deficient side
// of the axis get pushed further out from the centroid along it, evenly
// growing that axis's extent up to MIN_HULL_AXIS_EXTENT rather than
// touching axes that are already fine.
//
// 50 (through Phase 18) was tuned only against degeneracy, not against how
// big it makes an otherwise-small real cluster look -- template.html's
// per-cluster `padding` (clusterVolumePadding there) used to be a flat 40
// for every cluster regardless of size, so a small/tight real cluster's
// hull was already dominated by fixed padding before this floor ever
// mattered; now that padding scales down with a cluster's own real size,
// this floor is often the *only* thing still forcing a small cluster's
// thin axis open, so it needs to be small enough not to reintroduce the
// same problem on its own -- 18 is comfortably past "degenerate sliver"
// (checked: still >2x a tiny cluster's own scaled padding) without
// forcing every genuinely small cluster's shape toward a uniform blob.
var MIN_HULL_AXIS_EXTENT = 18;
function ensureMinimumSpread(points, minExtent) {
  if (points.length < 2) return points;
  var min = { x: Infinity, y: Infinity, z: Infinity }, max = { x: -Infinity, y: -Infinity, z: -Infinity };
  points.forEach(function (p) {
    ["x", "y", "z"].forEach(function (k) {
      if (p[k] < min[k]) min[k] = p[k];
      if (p[k] > max[k]) max[k] = p[k];
    });
  });
  var center = { x: (min.x + max.x) / 2, y: (min.y + max.y) / 2, z: (min.z + max.z) / 2 };
  var deficits = {};
  var anyDeficit = false;
  ["x", "y", "z"].forEach(function (k) {
    var extent = max[k] - min[k];
    if (extent < minExtent) { deficits[k] = (minExtent - extent) / 2; anyDeficit = true; }
  });
  if (!anyDeficit) return points;
  return points.map(function (p, i) {
    var out = { x: p.x, y: p.y, z: p.z };
    ["x", "y", "z"].forEach(function (k) {
      if (deficits[k] == null) return;
      var offset = p[k] - center[k];
      // A point sitting exactly at the center on a genuinely zero-extent
      // axis has no direction to push in -- alternate by index instead of
      // collapsing every such point onto the same two faces.
      var sign = Math.abs(offset) > 1e-6 ? Math.sign(offset) : (i % 2 === 0 ? 1 : -1);
      out[k] = p[k] + sign * deficits[k];
    });
    return out;
  });
}

// A sphere around the centroid, sized to the real spread plus padding --
// used whenever there aren't enough points for a real hull (<4) or the
// points are too close to coplanar for ConvexGeometry to triangulate
// (it throws on that; a 1-2 wide but otherwise flat cluster is a real,
// unremarkable case here, not something worth failing loudly over).
function buildFallbackVolumeGeometry(points, padding) {
  var cx = 0, cy = 0, cz = 0;
  points.forEach(function (p) { cx += p.x; cy += p.y; cz += p.z; });
  var n = Math.max(1, points.length);
  cx /= n; cy /= n; cz /= n;
  var maxDist = 0;
  points.forEach(function (p) {
    var d = Math.sqrt((p.x - cx) * (p.x - cx) + (p.y - cy) * (p.y - cy) + (p.z - cz) * (p.z - cz));
    if (d > maxDist) maxDist = d;
  });
  var geo = new THREE.SphereGeometry(Math.max(maxDist + padding, padding + 12), 14, 10);
  geo.translate(cx, cy, cz);
  return geo;
}

// A raw ConvexGeometry is low-poly and faceted by construction (as few as
// 4 triangles for a small cluster) -- exactly what reads as a hard-edged
// "3D modeling wireframe" primitive instead of an organic territory. This
// rounds it off in two steps: TessellateModifier first subdivides each
// face until no edge is longer than maxEdge (so the later smoothing pass
// has enough vertices to work with -- relaxing a handful of huge triangles
// would just move the same sharp corners around), then a few rounds of
// Laplacian smoothing (each vertex nudged toward the average of its mesh
// neighbors) round off the sharp corners left over from the hull. This is
// a *local* relaxation, not a blend toward a global sphere radius -- it
// softens creases without erasing the cluster's real elongated/irregular
// shape, which is the whole reason this is a real hull of real member
// positions rather than an abstract bubble in the first place.
//
// maxEdge scales with the hull's own size so a huge diffuse cluster
// doesn't explode into an unbounded triangle count, and a small tight one
// (where a faceted look is most obvious -- as few as 4-6 giant flat faces)
// gets tessellated finely enough to actually round.
function smoothClusterGeometry(hullGeometry) {
  hullGeometry.computeBoundingSphere();
  var radius = hullGeometry.boundingSphere ? hullGeometry.boundingSphere.radius : 100;
  var maxEdge = Math.max(radius * 0.15, 10);
  var tessellated = new TessellateModifier(maxEdge, 4).modify(hullGeometry);
  var welded = mergeVertices(tessellated); // rebuilds a real index, needed for neighbor lookup below
  return laplacianSmooth(welded, 3, 0.4);
}

function buildVertexAdjacency(indexArray, vertexCount) {
  var adjacency = [];
  for (var i = 0; i < vertexCount; i++) adjacency.push(new Set());
  for (var t = 0; t < indexArray.length; t += 3) {
    var a = indexArray[t], b = indexArray[t + 1], c = indexArray[t + 2];
    adjacency[a].add(b); adjacency[a].add(c);
    adjacency[b].add(a); adjacency[b].add(c);
    adjacency[c].add(a); adjacency[c].add(b);
  }
  return adjacency;
}

// factor: how far each pass moves a vertex toward its neighbors' average
// (0 = no change, 1 = snaps straight to it). Naive Laplacian smoothing
// shrinks a closed surface a little on every pass -- a mild, expected
// trade-off here given inflatePoints3D already pads the input outward
// before hulling, and passes/factor are kept low specifically to limit it.
function laplacianSmooth(geometry, passes, factor) {
  var index = geometry.index;
  if (!index) return geometry; // no adjacency to smooth against -- leave as-is
  var position = geometry.attributes.position;
  var count = position.count;
  var adjacency = buildVertexAdjacency(index.array, count);
  var current = position.array.slice();
  for (var p = 0; p < passes; p++) {
    var next = new Float32Array(current.length);
    for (var v = 0; v < count; v++) {
      var neighbors = adjacency[v];
      var ox = current[v * 3], oy = current[v * 3 + 1], oz = current[v * 3 + 2];
      if (!neighbors.size) { next[v * 3] = ox; next[v * 3 + 1] = oy; next[v * 3 + 2] = oz; continue; }
      var sx = 0, sy = 0, sz = 0;
      neighbors.forEach(function (n) { sx += current[n * 3]; sy += current[n * 3 + 1]; sz += current[n * 3 + 2]; });
      var nx = sx / neighbors.size, ny = sy / neighbors.size, nz = sz / neighbors.size;
      next[v * 3] = ox + (nx - ox) * factor;
      next[v * 3 + 1] = oy + (ny - oy) * factor;
      next[v * 3 + 2] = oz + (nz - oz) * factor;
    }
    current = next;
  }
  position.array.set(current);
  position.needsUpdate = true;
  geometry.computeVertexNormals();
  return geometry;
}

function buildClusterVolumeGeometry(points, padding) {
  if (!points || points.length < 4) return buildFallbackVolumeGeometry(points || [], padding);
  try {
    var spread = ensureMinimumSpread(points, MIN_HULL_AXIS_EXTENT);
    return smoothClusterGeometry(new ConvexGeometry(inflatePoints3D(spread, padding)));
  } catch (e) {
    return buildFallbackVolumeGeometry(points, padding); // degenerate/coplanar point set
  }
}

// `clusters`: [{ id, positions: [{x,y,z}], color, fillOpacity, padding }],
// one entry per cluster currently worth showing a volume for (collapsed
// meta-nodes and expanded-cluster halos alike -- template.html decides
// which, this just draws whatever list it's given). No wireframe/edge
// overlay -- a literal line-per-facet reads as a technical/CAD wireframe
// even on a rounded mesh; the smoothed fill alone is meant to read as a
// soft, organic territory instead. depthWrite is off (standard practice
// for translucent geometry -- with it on, a volume can occlude *itself*
// depending on face draw order, and two overlapping translucent volumes
// fight over which wrote depth first instead of blending) while depth
// *test* stays on, so real nodes/other volumes in front still correctly
// occlude these.
export function syncClusterVolumesCore(s, clusters) {
  var seen = new Set();
  (clusters || []).forEach(function (c) {
    seen.add(c.id);
    var entry = s.clusterVolumes.get(c.id);
    if (!entry) {
      var geo = buildClusterVolumeGeometry(c.positions, c.padding || 40);
      var mesh = new THREE.Mesh(geo, new THREE.MeshBasicMaterial({
        transparent: true, depthWrite: false, side: THREE.DoubleSide
      }));
      s.scene.add(mesh);
      entry = { mesh: mesh };
      s.clusterVolumes.set(c.id, entry);
    }
    entry.mesh.material.color.set(c.color || "#888");
    entry.mesh.material.opacity = c.fillOpacity != null ? c.fillOpacity : 0.12;
    entry.mesh.visible = true;
  });
  s.clusterVolumes.forEach(function (entry, id) {
    if (!seen.has(id)) entry.mesh.visible = false;
  });
}

// ---- projection / picking ----

export function projectToScreenCore(s, node) {
  var v = new THREE.Vector3(node.x, node.y, node.z).project(s.camera);
  return {
    x: (v.x * 0.5 + 0.5) * s.cssWidth,
    y: (-v.y * 0.5 + 0.5) * s.cssHeight,
    screenRadius: projectedRadiusCore(s, node),
    visible: v.z < 1
  };
}

export function projectedRadiusCore(s, node) {
  var center = new THREE.Vector3(node.x, node.y, node.z);
  var right = new THREE.Vector3();
  s.camera.matrixWorld.extractBasis(right, new THREE.Vector3(), new THREE.Vector3());
  var edge = center.clone().addScaledVector(right, node.radius || 1);
  var c = center.clone().project(s.camera);
  var e = edge.clone().project(s.camera);
  var dx = (e.x - c.x) * 0.5 * s.cssWidth;
  var dy = (e.y - c.y) * 0.5 * s.cssHeight;
  return Math.hypot(dx, dy);
}

// sx, sy: canvas-local (not page) pixel coordinates, same convention as
// today's findNodeAt(sx, sy)/findEdgeAtIn(list, sx, sy).
export function pickCore(s, sx, sy) {
  var best = null, bestDist = Infinity;
  s.lastNodes.forEach(function (n) {
    var p = projectToScreenCore(s, n);
    if (!p.visible) return;
    var dx = p.x - sx, dy = p.y - sy;
    if (Math.hypot(dx, dy) > p.screenRadius + PICK_PAD_PX) return;
    // Camera-relative distance, not raw view-space z: three.js cameras look
    // down local -Z, so view-space z grows *more negative* with distance --
    // comparing it directly picks the farthest node, not the nearest.
    var dist = s.camera.position.distanceTo(new THREE.Vector3(n.x, n.y, n.z));
    if (dist < bestDist) { bestDist = dist; best = { type: n.type, id: n.id }; }
  });
  if (best) return best;

  var bestEdge = null, bestEdgeDist = EDGE_PICK_PAD_PX;
  // Iterates _lastEdgeTiers (the actual edge data from the last sync()
  // call), not edgeLines (the THREE.Line2 object pool) -- the two always
  // have the same keys in real usage (syncEdgeTier populates both
  // together), but _lastEdgeTiers is the real source of truth and this
  // avoids a needless coupling to whether the render-side pool happens to
  // exist yet.
  Object.keys(s._lastEdgeTiers || {}).forEach(function (tier) {
    // Edge hit-testing works in screen space against each tier's raw edge
    // list, same closest-point-on-segment approach as findEdgeAtIn today.
    var edges = s._lastEdgeTiers[tier];
    if (!edges) return;
    edges.forEach(function (e) {
      // Test every segment of the edge's actual route (see
      // edgeRoutePoints), not just its raw source-target endpoints -- a
      // bundled dependency edge (Phase 16) can bow well away from that
      // straight line, and picking against the wrong segment would miss
      // clicks on the curve as actually drawn.
      var route = edgeRoutePoints(e).map(function (p) { return projectToScreenCore(s, p); });
      for (var k = 0; k < route.length - 1; k++) {
        var a = route[k], b = route[k + 1];
        if (!a.visible || !b.visible) continue;
        var vx = b.x - a.x, vy = b.y - a.y;
        var lenSq = vx * vx + vy * vy || 1;
        var t = Math.max(0, Math.min(1, ((sx - a.x) * vx + (sy - a.y) * vy) / lenSq));
        var px = a.x + vx * t, py = a.y + vy * t;
        var d = Math.hypot(sx - px, sy - py);
        if (d < bestEdgeDist) { bestEdgeDist = d; bestEdge = { type: "edge", tier: tier, edge: e }; }
      }
    });
  });
  return bestEdge;
}

// ---- drag plane ----

export function unprojectToPlaneCore(s, sx, sy, planeNormal, planePoint) {
  var ndcX = (sx / s.cssWidth) * 2 - 1;
  var ndcY = -(sy / s.cssHeight) * 2 + 1;
  s.raycaster.setFromCamera({ x: ndcX, y: ndcY }, s.camera);
  var plane = new THREE.Plane().setFromNormalAndCoplanarPoint(planeNormal, planePoint);
  var target = new THREE.Vector3();
  var hit = s.raycaster.ray.intersectPlane(plane, target);
  return hit ? { x: target.x, y: target.y, z: target.z } : null;
}

export function cameraForwardVectorCore(s) {
  var v = new THREE.Vector3();
  s.camera.getWorldDirection(v);
  return v;
}

// ---- camera framing ----
// Both preserve the camera's current azimuth/elevation and only change
// distance -- matching today's fitView/fitToNodeIds/focusNode, which only
// ever change scale/pan, never rotate.
function frameSphere(s, center, radius, marginMultiplier) {
  var fovRad = (s.camera.fov * Math.PI) / 180;
  var distance = Math.max(50, (radius / Math.sin(fovRad / 2)) * (marginMultiplier || 1.3));
  var dir = new THREE.Vector3().subVectors(s.camera.position, s.controls.target);
  if (dir.lengthSq() < 1e-6) dir.set(0, 0, 1);
  dir.normalize();
  s.controls.target.copy(center);
  s.camera.position.copy(center).addScaledVector(dir, distance);
  s.controls.update();
}

// ---- deterministic orbit (scripted rotation export) ----
//
// OrbitControls only ever moves the camera in response to real pointer/
// wheel input (plus damping decay from the last such input) -- there's no
// "set azimuth to X" entry point on it. A scripted rotation (see
// template.html's exportRotationVideo()) needs frame-exact camera
// placement independent of any of that, so these two functions read/write
// s.camera.position directly via spherical coordinates around
// controls.target, bypassing OrbitControls entirely. They deliberately
// never call controls.update() -- the next real controls.update() (from
// the normal render loop) reads whatever s.camera.position currently is,
// decomposes it back into its own internal spherical state fresh, and
// (with no pending pointer delta) reproduces the same position, so
// control returns to the user cleanly once a scripted sequence ends.
export function getOrbitStateCore(s) {
  var offset = new THREE.Vector3().copy(s.camera.position).sub(s.controls.target);
  var spherical = new THREE.Spherical().setFromVector3(offset);
  return {
    target: { x: s.controls.target.x, y: s.controls.target.y, z: s.controls.target.z },
    distance: spherical.radius,
    azimuth: spherical.theta,
    elevation: Math.PI / 2 - spherical.phi
  };
}

export function setOrbitCameraCore(s, target, distance, azimuth, elevation) {
  var phi = Math.PI / 2 - elevation;
  var offset = new THREE.Vector3().setFromSphericalCoords(distance, phi, azimuth);
  var t = new THREE.Vector3(target.x, target.y, target.z);
  s.camera.position.copy(t).add(offset);
  s.camera.lookAt(t);
}

export function frameNodesCore(s, nodeIds) {
  var idSet = new Set(nodeIds);
  var pts = s.lastNodes.filter(function (n) { return idSet.has(n.id); });
  if (!pts.length) return;
  var sphere = new THREE.Sphere();
  var box = new THREE.Box3();
  pts.forEach(function (n) { box.expandByPoint(new THREE.Vector3(n.x, n.y, n.z)); });
  box.getBoundingSphere(sphere);
  frameSphere(s, sphere.center, Math.max(sphere.radius, 40), 1.3);
}

export function focusNodeCore(s, node) {
  frameSphere(s, new THREE.Vector3(node.x, node.y, node.z), Math.max(node.radius * 3, 60), 1.6);
}

// Frames a bounding sphere built from arbitrary {x,y,z} points rather than
// materialized node ids -- frameNodesCore above only ever sees a cluster
// meta-node's own small marker position, not the real (often much larger)
// spread of its true members, which clusterHullFor() in template.html
// already computes for the volume mesh itself. fitView() passes that same
// hull data here so the initial/reset camera distance accounts for what's
// actually drawn (the translucent volumes) instead of under-framing them.
export function frameBoundingSphereCore(s, points, marginMultiplier) {
  if (!points.length) return;
  var box = new THREE.Box3();
  points.forEach(function (p) { box.expandByPoint(new THREE.Vector3(p.x, p.y, p.z)); });
  var sphere = new THREE.Sphere();
  box.getBoundingSphere(sphere);
  frameSphere(s, sphere.center, Math.max(sphere.radius, 40), marginMultiplier || 1.3);
}

// ---- public singleton API -- what actually ships as the Scene3D global ----
// One instance per page (one canvas, one camera), so every export below is
// just *Core(current, ...args) -- see the file-header comment for why this
// indirection exists instead of relying on `this`.

var current = null;

export function init(canvas, opts) { current = initCore(canvas, opts); return current; }
export function resize(width, height, dpr) { return resizeCore(current, width, height, dpr); }
export function render() { return renderCore(current); }
export function setOrbitEnabled(enabled) { return setOrbitEnabledCore(current, enabled); }
export function isOrbiting() { return isOrbitingCore(current); }
export function axisEndpoints() { return axisEndpointsCore(current); }
export function setPalette(palette) { return setPaletteCore(current, palette); }
export function sync(nodes, edgeTiers, renderOpts) { return syncCore(current, nodes, edgeTiers, renderOpts); }
export function syncClusterVolumes(clusters) { return syncClusterVolumesCore(current, clusters); }
export function projectToScreen(node) { return projectToScreenCore(current, node); }
export function projectedRadius(node) { return projectedRadiusCore(current, node); }
export function pick(sx, sy) { return pickCore(current, sx, sy); }
export function unprojectToPlane(sx, sy, planeNormal, planePoint) { return unprojectToPlaneCore(current, sx, sy, planeNormal, planePoint); }
export function cameraForwardVector() { return cameraForwardVectorCore(current); }
export function frameNodes(nodeIds) { return frameNodesCore(current, nodeIds); }
export function frameBoundingSphere(points, marginMultiplier) { return frameBoundingSphereCore(current, points, marginMultiplier); }
export function getOrbitState() { return getOrbitStateCore(current); }
export function setOrbitCamera(target, distance, azimuth, elevation) { return setOrbitCameraCore(current, target, distance, azimuth, elevation); }
export function focusNode(node) { return focusNodeCore(current, node); }
