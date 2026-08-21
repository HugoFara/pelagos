// Pure-math checks for scene3d.js's projection/picking/camera-framing
// functions -- none of this needs a real GPU/WebGL context (THREE's
// Vector3/Camera/Raycaster/Plane/Box3/Sphere classes are plain math), so
// it runs headless via Node's built-in test runner, no browser needed.
// Deliberately does NOT touch init()/render()/sync() (those need a real
// WebGLRenderer, which needs a real GPU context -- see NOTES.md's "verify
// headless-gl" entry for why that's out of reach here).
//
// Uses the *Core(state, ...) functions directly (explicit state, not
// `this`) -- see scene3d.js's file-header comment for why the public
// Scene3D.pick(...)-style API wraps these instead of being tested directly.
import test from "node:test";
import assert from "node:assert/strict";
import * as THREE from "three";
import { ConvexGeometry } from "three/examples/jsm/geometries/ConvexGeometry.js";
import {
  projectToScreenCore as projectToScreen,
  projectedRadiusCore as projectedRadius,
  pickCore as pick,
  unprojectToPlaneCore as unprojectToPlane,
  cameraForwardVectorCore as cameraForwardVector,
  frameNodesCore as frameNodes,
  focusNodeCore as focusNode,
  syncClusterVolumesCore as syncClusterVolumes,
  getOrbitStateCore as getOrbitState,
  setOrbitCameraCore as setOrbitCamera,
  loadAvatarTextureCore as loadAvatarTexture,
  shrinkPool,
  POOL_SLACK
} from "./scene3d.js";

function fakeState(overrides) {
  var camera = new THREE.PerspectiveCamera(50, 800 / 600, 1, 20000);
  camera.position.set(0, 0, 1000);
  camera.lookAt(0, 0, 0);
  camera.updateMatrixWorld(true);
  var state = {
    camera: camera,
    controls: { target: new THREE.Vector3(0, 0, 0), update: function () {} },
    cssWidth: 800,
    cssHeight: 600,
    lastNodes: [],
    edgeLines: {},
    raycaster: new THREE.Raycaster(),
    scene: new THREE.Scene(),
    clusterVolumes: new Map()
  };
  return Object.assign(state, overrides);
}

test("projectToScreen: a node at the world origin (camera looking straight at it) lands at screen center", () => {
  var s = fakeState();
  var p = projectToScreen(s, { x: 0, y: 0, z: 0, radius: 20 });
  assert.ok(Math.abs(p.x - 400) < 0.5, "expected x near 400, got " + p.x);
  assert.ok(Math.abs(p.y - 300) < 0.5, "expected y near 300, got " + p.y);
  assert.ok(p.visible);
});

test("projectToScreen: a node behind the camera is not visible", () => {
  var s = fakeState();
  var p = projectToScreen(s, { x: 0, y: 0, z: 2000, radius: 20 });
  assert.equal(p.visible, false);
});

test("projectedRadius: larger world radius projects to more screen pixels, and doubling distance roughly halves it", () => {
  var s = fakeState();
  var near = { x: 0, y: 0, z: 0, radius: 20 };
  var nearBigger = { x: 0, y: 0, z: 0, radius: 40 };
  assert.ok(projectedRadius(s, nearBigger) > projectedRadius(s, near));

  var r1 = projectedRadius(s, { x: 0, y: 0, z: 0, radius: 20 });
  var farState = fakeState();
  farState.camera.position.set(0, 0, 2000);
  farState.camera.lookAt(0, 0, 0);
  farState.camera.updateMatrixWorld(true);
  var r2 = projectedRadius(farState, { x: 0, y: 0, z: 0, radius: 20 });
  var ratio = r1 / r2;
  assert.ok(ratio > 1.8 && ratio < 2.2, "expected ~2x falloff, got ratio=" + ratio);
});

test("pick: a node under the cursor within its projected radius + padding is picked", () => {
  var s = fakeState();
  s.lastNodes = [{ id: "a", type: "repository", x: 0, y: 0, z: 0, radius: 20 }];
  var hit = pick(s, 400, 300);
  assert.ok(hit && hit.id === "a", "expected to pick node a, got " + JSON.stringify(hit));
});

test("pick: a node far outside its projected radius is not picked", () => {
  var s = fakeState();
  s.lastNodes = [{ id: "a", type: "repository", x: 0, y: 0, z: 0, radius: 20 }];
  var hit = pick(s, 780, 20); // corner of the viewport, far from center
  assert.equal(hit, null);
});

test("pick: with two nodes along the same screen ray, the nearer-to-camera one wins", () => {
  var s = fakeState();
  s.lastNodes = [
    { id: "far", type: "repository", x: 0, y: 0, z: -500, radius: 20 },
    { id: "near", type: "repository", x: 0, y: 0, z: 500, radius: 20 }
  ];
  var hit = pick(s, 400, 300);
  assert.equal(hit.id, "near");
});

test("pick: falls through to an edge when no node is under the cursor", () => {
  var s = fakeState();
  s.lastNodes = [];
  s._lastEdgeTiers = {
    contrib: [{
      source: { x: -100, y: 0, z: 0 },
      target: { x: 100, y: 0, z: 0 }
    }]
  };
  var hit = pick(s, 400, 300); // screen center projects to world (0,0,0), on the segment
  assert.ok(hit && hit.type === "edge" && hit.tier === "contrib", "expected an edge hit, got " + JSON.stringify(hit));
});

test("pick: a bundled edge (Phase 16 _path) is hit-tested along its bent route, not its straight source-target line", () => {
  var s = fakeState();
  s.lastNodes = [];
  // source/target sit at world y=200 (screen y ~171 either side of center);
  // _path detours down through the world origin (screen center, 400,300).
  // A point on the naive straight source-target chord (screen ~400,171) is
  // ~107px from both real path segments -- picking there must miss unless
  // it's wrongly still testing source/target directly instead of the path.
  s._lastEdgeTiers = {
    dependency: [{
      source: { x: -300, y: 200, z: 0 },
      target: { x: 300, y: 200, z: 0 },
      _path: [
        { x: -300, y: 200, z: 0 },
        { x: 0, y: 0, z: 0 },
        { x: 300, y: 200, z: 0 }
      ]
    }]
  };
  var onNaiveChord = pick(s, 400, 171);
  var onRealDetour = pick(s, 400, 300);
  assert.equal(onNaiveChord, null, "expected no hit on the straight source-target chord the edge no longer actually follows, got " + JSON.stringify(onNaiveChord));
  assert.ok(onRealDetour && onRealDetour.type === "edge" && onRealDetour.tier === "dependency", "expected the bundled route to be pickable at its real detour point, got " + JSON.stringify(onRealDetour));
});

test("unprojectToPlane: screen center against a plane through the origin facing the camera resolves near the origin", () => {
  var s = fakeState();
  var normal = cameraForwardVector(s).negate();
  var hit = unprojectToPlane(s, 400, 300, normal, new THREE.Vector3(0, 0, 0));
  assert.ok(hit, "expected a plane hit");
  assert.ok(Math.hypot(hit.x, hit.y, hit.z) < 0.5, "expected near origin, got " + JSON.stringify(hit));
});

test("unprojectToPlane: moving the plane along the normal shifts the resolved depth accordingly", () => {
  // A plane's normal + coplanar point only pins down the plane's position
  // *along the normal* -- with a camera-facing normal here, that's world z,
  // so a plane point of (anything, anything, 300) is the same plane as
  // (0,0,300). The screen-center ray is the camera's exact view axis
  // (x=0,y=0 for all z, since the camera looks straight down -z at the
  // origin), so it should resolve to (0,0,300), not (0,0,0).
  var s = fakeState();
  var normal = cameraForwardVector(s).negate();
  var hit = unprojectToPlane(s, 400, 300, normal, new THREE.Vector3(999, -999, 300));
  assert.ok(Math.hypot(hit.x, hit.y) < 0.5 && Math.abs(hit.z - 300) < 0.5, "expected near (0,0,300), got " + JSON.stringify(hit));
});

test("unprojectToPlane: an off-center screen point resolves off-axis on the same plane", () => {
  var s = fakeState();
  var normal = cameraForwardVector(s).negate();
  var center = unprojectToPlane(s, 400, 300, normal, new THREE.Vector3(0, 0, 0));
  var offset = unprojectToPlane(s, 500, 300, normal, new THREE.Vector3(0, 0, 0));
  assert.ok(offset.x > center.x + 5, "expected a rightward screen offset to resolve to larger world x, got center=" + JSON.stringify(center) + " offset=" + JSON.stringify(offset));
  assert.ok(Math.abs(offset.z - center.z) < 0.5, "expected both hits on the same z=0 plane");
});

test("frameNodes: camera ends up looking at the group's centroid, preserving its prior view direction", () => {
  var s = fakeState();
  var priorDir = cameraForwardVector(s).clone();
  s.lastNodes = [
    { id: "a", x: -100, y: 0, z: 0, radius: 10 },
    { id: "b", x: 100, y: 0, z: 0, radius: 10 }
  ];
  frameNodes(s, ["a", "b"]);
  assert.ok(Math.hypot(s.controls.target.x, s.controls.target.y, s.controls.target.z) < 0.5, "expected target near centroid (0,0,0)");
  var newDir = cameraForwardVector(s);
  assert.ok(priorDir.angleTo(newDir) < 0.05, "expected view direction preserved, angle=" + priorDir.angleTo(newDir));
  var p = projectToScreen(s, { x: 0, y: 0, z: 0, radius: 1 });
  assert.ok(Math.abs(p.x - 400) < 1 && Math.abs(p.y - 300) < 1, "expected centroid to project near screen center");
});

test("focusNode: camera ends up looking at the node, closer than a wide frameNodes would put it", () => {
  var s = fakeState();
  focusNode(s, { x: 0, y: 0, z: 0, radius: 20 });
  var dist = s.camera.position.distanceTo(s.controls.target);
  assert.ok(dist > 0 && dist < 1000, "expected a reasonable focus distance, got " + dist);
});

test("getOrbitState: round-trips a camera's position as target/distance/azimuth/elevation", () => {
  var s = fakeState();
  s.camera.position.set(0, 500, 1000);
  s.camera.lookAt(0, 0, 0);
  var orbit = getOrbitState(s);
  assert.ok(Math.abs(orbit.target.x) < 1e-6 && Math.abs(orbit.target.y) < 1e-6 && Math.abs(orbit.target.z) < 1e-6);
  assert.ok(Math.abs(orbit.distance - Math.hypot(500, 1000)) < 1e-6);
  assert.ok(orbit.elevation > 0, "camera above the target plane should read a positive elevation");
});

test("setOrbitCamera: sweeping azimuth by a full turn returns the camera to its starting position", () => {
  var s = fakeState();
  var orbit = getOrbitState(s); // starting state: camera at (0,0,1000) looking at origin
  setOrbitCamera(s, orbit.target, orbit.distance, orbit.azimuth + Math.PI * 2, orbit.elevation);
  assert.ok(s.camera.position.distanceTo(new THREE.Vector3(0, 0, 1000)) < 1e-6);
});

test("setOrbitCamera: rotating azimuth by a quarter turn moves the camera around the target at a fixed distance", () => {
  var s = fakeState();
  var orbit = getOrbitState(s);
  setOrbitCamera(s, orbit.target, orbit.distance, orbit.azimuth + Math.PI / 2, orbit.elevation);
  assert.ok(Math.abs(s.camera.position.distanceTo(new THREE.Vector3(0, 0, 0)) - orbit.distance) < 1e-6);
  assert.ok(s.camera.position.distanceTo(new THREE.Vector3(0, 0, 1000)) > 1, "camera should have actually moved");
});

// Note: geometry construction (THREE.Mesh/BufferGeometry/ConvexGeometry)
// needs no real GPU -- only s.renderer.render() does (see the file header
// for why init()/render()/sync() itself stay untested here) -- so
// syncClusterVolumesCore's actual geometry-building logic is fully
// reachable from plain Node.
test("syncClusterVolumes: a real (non-coplanar) point set builds a mesh and adds it to the scene", () => {
  var s = fakeState();
  var positions = [
    { x: 0, y: 0, z: 0 }, { x: 100, y: 0, z: 0 }, { x: 0, y: 100, z: 0 }, { x: 0, y: 0, z: 100 }, { x: 40, y: 40, z: 40 }
  ];
  syncClusterVolumes(s, [{ id: "cluster/2/1", positions: positions, color: "#ff0000", fillOpacity: 0.2 }]);
  var entry = s.clusterVolumes.get("cluster/2/1");
  assert.ok(entry && entry.mesh, "expected a mesh entry");
  assert.ok(entry.mesh.visible, "expected visible after sync");
  assert.ok(s.scene.children.includes(entry.mesh), "expected the mesh added to the scene");
  assert.ok(entry.mesh.geometry.attributes.position.count >= 4, "expected real hull geometry with several vertices");
  assert.equal(entry.mesh.material.opacity, 0.2);
  assert.equal(entry.mesh.material.wireframe, false, "expected a solid fill, no wireframe flag");
});

test("syncClusterVolumes: the smoothed volume has far more vertices than the raw convex hull (tessellated, not a bare low-poly hull)", () => {
  var s = fakeState();
  // A near-tetrahedral point set: a raw ConvexGeometry hull of this is
  // literally 4 triangles, the most "faceted primitive"-looking case --
  // exactly what tessellation + smoothing is meant to round off.
  var positions = [
    { x: 0, y: 0, z: 0 }, { x: 200, y: 0, z: 0 }, { x: 0, y: 200, z: 0 }, { x: 0, y: 0, z: 200 }
  ];
  var rawHull = new ConvexGeometry(positions.map((p) => new THREE.Vector3(p.x, p.y, p.z)));
  syncClusterVolumes(s, [{ id: "tet", positions: positions }]);
  var smoothed = s.clusterVolumes.get("tet").mesh.geometry;
  console.log("    raw hull vertices=" + rawHull.attributes.position.count + " smoothed vertices=" + smoothed.attributes.position.count);
  assert.ok(smoothed.attributes.position.count > rawHull.attributes.position.count * 2, "expected tessellation to substantially increase vertex count");
});

test("syncClusterVolumes: fewer than 4 points falls back to a sphere instead of throwing", () => {
  var s = fakeState();
  assert.doesNotThrow(() => {
    syncClusterVolumes(s, [{ id: "c1", positions: [{ x: 0, y: 0, z: 0 }] }]);
    syncClusterVolumes(s, [{ id: "c2", positions: [{ x: 0, y: 0, z: 0 }, { x: 10, y: 0, z: 0 }] }]);
  });
  var e1 = s.clusterVolumes.get("c1"), e2 = s.clusterVolumes.get("c2");
  assert.ok(e1.mesh.geometry.attributes.position.count > 0, "expected a real fallback sphere geometry for 1 point");
  assert.ok(e2.mesh.geometry.attributes.position.count > 0, "expected a real fallback sphere geometry for 2 points");
});

test("syncClusterVolumes: an exactly-coplanar point set (degenerate hull) does not throw", () => {
  var s = fakeState();
  var flat = [{ x: 0, y: 0, z: 0 }, { x: 100, y: 0, z: 0 }, { x: 0, y: 100, z: 0 }, { x: 100, y: 100, z: 0 }, { x: 50, y: 50, z: 0 }];
  assert.doesNotThrow(() => syncClusterVolumes(s, [{ id: "flat", positions: flat }]));
});

test("syncClusterVolumes: a near-flat (non-coplanar, so ConvexGeometry succeeds) point set is inflated to a minimum thickness instead of rendering as a paper-thin sliver", () => {
  var s = fakeState();
  // z spread of only 2 world units across an otherwise 300x300 spread in
  // x/y -- a real, plausible shape for a topically-tight cluster whose
  // members happen to share nearly the same value on one axis (e.g.
  // trophic height). Non-coplanar (tiny but nonzero z jitter), so this
  // exercises the real hull path, not the sphere/degenerate fallback.
  var flat = [
    { x: 0, y: 0, z: 0 }, { x: 300, y: 0, z: 0.4 }, { x: 0, y: 300, z: -0.6 },
    { x: 300, y: 300, z: 0.8 }, { x: 150, y: 150, z: -1 }, { x: 60, y: 240, z: 1 }
  ];
  syncClusterVolumes(s, [{ id: "pancake", positions: flat, padding: 10 }]);
  var geo = s.clusterVolumes.get("pancake").mesh.geometry;
  geo.computeBoundingBox();
  var box = geo.boundingBox;
  var zExtent = box.max.z - box.min.z;
  console.log("    inflated z-extent=" + zExtent.toFixed(1) + " (raw member z-spread was ~2)");
  // MIN_HULL_AXIS_EXTENT dropped from 50 to 18 (see its own comment in
  // scene3d.js -- the old flat 50 was tuned only against degeneracy, not
  // against how big it made an otherwise-small real cluster look), so the
  // margin here drops with it -- still comfortably past the raw ~2 spread.
  assert.ok(zExtent > 12, "expected the near-zero z spread to be inflated to a real minimum thickness, got " + zExtent.toFixed(2));
  // The already-ample x/y extents shouldn't be touched by the fix.
  var xExtent = box.max.x - box.min.x;
  assert.ok(xExtent > 250 && xExtent < 400, "expected x extent left close to its real ~300+padding spread, got " + xExtent.toFixed(2));
});

test("syncClusterVolumes: geometry is built once and reused across repeated syncs of the same cluster", () => {
  var s = fakeState();
  var positions = [{ x: 0, y: 0, z: 0 }, { x: 100, y: 0, z: 0 }, { x: 0, y: 100, z: 0 }, { x: 0, y: 0, z: 100 }];
  syncClusterVolumes(s, [{ id: "stable", positions: positions, color: "#00ff00" }]);
  var firstMesh = s.clusterVolumes.get("stable").mesh;
  syncClusterVolumes(s, [{ id: "stable", positions: positions, color: "#0000ff" }]);
  var secondMesh = s.clusterVolumes.get("stable").mesh;
  assert.equal(firstMesh, secondMesh, "expected the same mesh instance (geometry cached, not rebuilt) across syncs");
  assert.equal(secondMesh.material.color.getHexString(), "0000ff", "expected color to still update on the cached mesh");
});

test("syncClusterVolumes: a cluster missing from the next sync call gets hidden, not removed", () => {
  var s = fakeState();
  var positions = [{ x: 0, y: 0, z: 0 }, { x: 100, y: 0, z: 0 }, { x: 0, y: 100, z: 0 }, { x: 0, y: 0, z: 100 }];
  syncClusterVolumes(s, [{ id: "a", positions: positions }, { id: "b", positions: positions }]);
  assert.ok(s.clusterVolumes.get("a").mesh.visible && s.clusterVolumes.get("b").mesh.visible);
  syncClusterVolumes(s, [{ id: "b", positions: positions }]);
  assert.equal(s.clusterVolumes.get("a").mesh.visible, false, "expected 'a' hidden once it's no longer in the list");
  assert.ok(s.clusterVolumes.get("b").mesh.visible, "expected 'b' to stay visible");
});

// ---- avatar loading: the "never request a logo we know isn't there" path
// (build/web_explorer.py -> MISSING_LOGO_OWNERS -> initCore's
// opts.avatarlessOwners -> avatarTextures seeded to "error"). Can't go
// through initCore itself (WebGLRenderer, see the file header), so these
// drive loadAvatarTextureCore against a hand-seeded cache and count real
// TextureLoader.load calls.
function avatarState(seed) {
  var s = { avatarTextures: new Map() };
  (seed || []).forEach(function (owner) { s.avatarTextures.set(owner, "error"); });
  return s;
}

function countingLoader(fn) {
  var original = THREE.TextureLoader.prototype.load;
  var calls = [];
  THREE.TextureLoader.prototype.load = function (url) { calls.push(url); };
  try { fn(); } finally { THREE.TextureLoader.prototype.load = original; }
  return calls;
}

test("loadAvatarTexture: an owner seeded avatar-less is never requested at all", () => {
  var s = avatarState(["flagalpha"]);
  var calls = countingLoader(() => {
    assert.equal(loadAvatarTexture(s, "flagalpha", () => {}), null);
  });
  assert.deepEqual(calls, [], "expected zero TextureLoader requests, got " + JSON.stringify(calls));
  assert.equal(s.avatarTextures.get("flagalpha"), "error", "state must stay 'error', not become 'pending'");
});

test("loadAvatarTexture: an owner with no cache entry is still requested normally", () => {
  var s = avatarState(["flagalpha"]);
  var calls = countingLoader(() => {
    assert.equal(loadAvatarTexture(s, "gin-gonic", () => {}), null);
  });
  assert.deepEqual(calls, ["logos/gin-gonic.png"]);
  assert.equal(s.avatarTextures.get("gin-gonic"), "pending");
});

test("loadAvatarTexture: a second call for an in-flight owner does not re-request", () => {
  var s = avatarState();
  var calls = countingLoader(() => {
    loadAvatarTexture(s, "psf", () => {});
    loadAvatarTexture(s, "psf", () => {});
  });
  assert.equal(calls.length, 1, "expected one request for two calls, got " + calls.length);
});

// ---- Object-pool shrinking. Pools only ever grew, so a far view cost 17ms
// on load but 34ms after one zoom in and out -- thousands of invisible Line2
// objects still being traversed. The arrowhead pool is the sharp edge here:
// there `pool` IS group.children, and Object3D.remove() splices that same
// array, so a naive walk-and-remove skips every other entry.

function poolItem() {
  return new THREE.Mesh(new THREE.BufferGeometry(), new THREE.MeshBasicMaterial());
}

test("shrinkPool: leaves the pool alone until it is far larger than needed", () => {
  const s = fakeState();
  const pool = [];
  for (let i = 0; i < 100 + POOL_SLACK; i++) pool.push(poolItem());
  const before = pool.length;
  assert.equal(shrinkPool(s, pool, 100), 0, "reports nothing dropped");
  assert.equal(pool.length, before, "within hysteresis, nothing is dropped");
});

test("shrinkPool: a plain array pool is truncated and its objects leave the scene", () => {
  const s = fakeState();
  const pool = [];
  for (let i = 0; i < 3000; i++) { const m = poolItem(); s.scene.add(m); pool.push(m); }
  const survivor = pool[0], victim = pool[2999];
  shrinkPool(s, pool, 100);
  assert.equal(pool.length, 100 + POOL_SLACK);
  assert.ok(s.scene.children.includes(survivor), "kept objects stay in the scene");
  assert.ok(!s.scene.children.includes(victim), "dropped objects are removed from the scene");
});

test("shrinkPool: a Group's own children array shrinks without skipping entries", () => {
  const s = fakeState();
  const group = new THREE.Group();
  s.scene.add(group);
  for (let i = 0; i < 3000; i++) { const m = poolItem(); m.name = "a" + i; group.add(m); }
  shrinkPool(s, group.children, 100, (m) => group.remove(m));
  assert.equal(group.children.length, 100 + POOL_SLACK);
  // The survivors must be the first N in order -- a splice-while-iterating
  // bug leaves a comb of every-other-entry instead.
  for (let i = 0; i < group.children.length; i++) {
    assert.equal(group.children[i].name, "a" + i);
  }
});

test("shrinkPool: never disposes the shared arrowhead geometry", () => {
  const s = fakeState();
  s.arrowGeometry = new THREE.BufferGeometry();
  let disposed = 0;
  s.arrowGeometry.dispose = () => { disposed++; };
  const group = new THREE.Group();
  for (let i = 0; i < 3000; i++) group.add(new THREE.Mesh(s.arrowGeometry, new THREE.MeshBasicMaterial()));
  shrinkPool(s, group.children, 100, (m) => group.remove(m));
  assert.equal(disposed, 0, "the geometry every arrowhead shares must survive");
});
