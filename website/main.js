const canvas = document.querySelector("#stage");
const root = document.documentElement;
root.classList.add("motion-ready");

const modelManifest = JSON.parse(document.querySelector("#model-manifest")?.textContent || "{\"slots\":[]}");
const modelSlots = modelManifest.slots;

window.ProjectMonitorizeShowcase = {
  canvas,
  modelSlots,
};

const header = document.querySelector(".site-header");
const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const signalTrack = document.querySelector(".signal-track");

if (signalTrack) {
  signalTrack.append(...[...signalTrack.children].map((child) => child.cloneNode(true)));
}

const revealItems = [...document.querySelectorAll("[data-reveal]")];

if ("IntersectionObserver" in window && !reduceMotion) {
  const revealObserver = new IntersectionObserver((entries) => {
    for (const entry of entries) {
      if (entry.isIntersecting) {
        entry.target.classList.add("is-visible");
        revealObserver.unobserve(entry.target);
      }
    }
  }, {
    rootMargin: "0px 0px -12% 0px",
    threshold: 0.12,
  });

  revealItems.forEach((item, index) => {
    item.style.transitionDelay = `${Math.min(index * 45, 220)}ms`;
    revealObserver.observe(item);
  });
} else {
  revealItems.forEach((item) => item.classList.add("is-visible"));
}

if (!reduceMotion) {
  for (const card of document.querySelectorAll("[data-tilt]")) {
    card.addEventListener("pointermove", (event) => {
      const rect = card.getBoundingClientRect();
      const x = (event.clientX - rect.left) / Math.max(rect.width, 1) - 0.5;
      const y = (event.clientY - rect.top) / Math.max(rect.height, 1) - 0.5;
      card.style.transform = `perspective(900px) rotateX(${-y * 4}deg) rotateY(${x * 5}deg) translateY(-2px)`;
    });

    card.addEventListener("pointerleave", () => {
      card.style.transform = "";
    });
  }
}

const vertexWGSL = `
struct VertexOut {
  @builtin(position) position: vec4f,
  @location(0) uv: vec2f,
};

@vertex
fn main(@builtin(vertex_index) vertexIndex: u32) -> VertexOut {
  var positions = array<vec2f, 3>(
    vec2f(-1.0, -1.0),
    vec2f(3.0, -1.0),
    vec2f(-1.0, 3.0)
  );

  var out: VertexOut;
  out.position = vec4f(positions[vertexIndex], 0.0, 1.0);
  out.uv = positions[vertexIndex] * 0.5 + vec2f(0.5);
  return out;
}
`;

const fragmentWGSL = `
struct Uniforms {
  resolution: vec2f,
  time: f32,
  pointerX: f32,
  pointerY: f32,
  scroll: f32,
  _pad: f32,
};

@group(0) @binding(0) var<uniform> uniforms: Uniforms;

fn rotate2d(p: vec2f, a: f32) -> vec2f {
  let s = sin(a);
  let c = cos(a);
  return vec2f(c * p.x - s * p.y, s * p.x + c * p.y);
}

fn boxSdf(p: vec3f, b: vec3f) -> f32 {
  let q = abs(p) - b;
  return length(max(q, vec3f(0.0))) + min(max(q.x, max(q.y, q.z)), 0.0);
}

fn boxSdf2(p: vec2f, b: vec2f) -> f32 {
  let q = abs(p) - b;
  return length(max(q, vec2f(0.0))) + min(max(q.x, q.y), 0.0);
}

fn scene(pIn: vec3f) -> f32 {
  var p = pIn - vec3f(0.64, -0.04, 0.0);
  p.xz = rotate2d(p.xz, 0.48 + uniforms.pointerX * 0.16);
  p.xy = rotate2d(p.xy, -0.12 + uniforms.pointerY * 0.08);

  let monitor = boxSdf(p - vec3f(0.0, 0.18, 0.0), vec3f(1.28, 0.75, 0.035));
  let tablet = boxSdf(p - vec3f(0.28, -0.72, 0.18), vec3f(0.82, 0.44, 0.026));
  let stand = boxSdf(p - vec3f(0.0, -0.54, 0.0), vec3f(0.16, 0.2, 0.025));
  let base = boxSdf(p - vec3f(0.0, -0.85, 0.0), vec3f(0.48, 0.055, 0.12));
  return min(min(monitor, tablet), min(stand, base));
}

fn normalAt(p: vec3f) -> vec3f {
  let e = 0.002;
  let n = vec3f(
    scene(p + vec3f(e, 0.0, 0.0)) - scene(p - vec3f(e, 0.0, 0.0)),
    scene(p + vec3f(0.0, e, 0.0)) - scene(p - vec3f(0.0, e, 0.0)),
    scene(p + vec3f(0.0, 0.0, e)) - scene(p - vec3f(0.0, 0.0, e))
  );
  return normalize(n);
}

@fragment
fn main(@location(0) uvRaw: vec2f) -> @location(0) vec4f {
  let aspect = uniforms.resolution.x / max(uniforms.resolution.y, 1.0);
  var uv = uvRaw * 2.0 - vec2f(1.0);
  uv.x *= aspect;

  let sweep = 0.5 + 0.5 * sin(uniforms.time * 0.22 + uv.x * 1.7 - uv.y * 0.6);
  var color = mix(vec3f(0.035, 0.055, 0.068), vec3f(0.075, 0.105, 0.12), sweep);

  let ro = vec3f(0.0, 0.02, 3.3);
  let rd = normalize(vec3f(uv.x, uv.y * 0.86, -1.9));
  var t = 0.0;
  var hit = false;

  for (var i = 0; i < 76; i = i + 1) {
    let p = ro + rd * t;
    let d = scene(p);
    if (d < 0.002) {
      hit = true;
      break;
    }
    t += d;
    if (t > 6.0) {
      break;
    }
  }

  if (hit) {
    let p = ro + rd * t;
    let n = normalAt(p);
    let light = normalize(vec3f(-0.35, 0.68, 0.72));
    let rim = pow(1.0 - max(dot(n, -rd), 0.0), 2.0);
    let diffuse = max(dot(n, light), 0.0);
    let glassLine = smoothstep(0.022, 0.0, abs(sin((p.x + p.y) * 9.0 + uniforms.time * 0.55)) * 0.035);

    color = vec3f(0.18, 0.25, 0.28) * (0.72 + diffuse * 1.25);
    color += vec3f(0.54, 0.83, 0.78) * rim * 0.9;
    color += vec3f(0.95, 0.79, 0.37) * glassLine * 0.2;
  }

  let vignette = smoothstep(1.45, 0.28, length(uv));
  color *= 0.58 + vignette * 0.62;
  color += vec3f(0.56, 0.75, 1.0) * pow(max(0.0, 1.0 - length(uv - vec2f(-0.58, 0.18))), 5.0) * 0.22;
  color += vec3f(0.88, 0.72, 0.34) * pow(max(0.0, 1.0 - length(uv - vec2f(0.52, -0.42))), 5.0) * 0.12;

  var deviceUv = uv - vec2f(0.86, 0.08);
  deviceUv = rotate2d(deviceUv, -0.12 + uniforms.pointerX * 0.04);
  let monitorEdge = boxSdf2(deviceUv, vec2f(0.56, 0.34));
  let monitorInner = boxSdf2(deviceUv, vec2f(0.49, 0.27));
  let monitorLine = smoothstep(0.018, 0.0, abs(monitorEdge));
  let monitorGlow = smoothstep(0.16, -0.08, monitorInner);

  var tabletUv = uv - vec2f(0.96, -0.56);
  tabletUv = rotate2d(tabletUv, 0.16 + uniforms.pointerY * 0.05);
  let tabletEdge = boxSdf2(tabletUv, vec2f(0.42, 0.18));
  let tabletLine = smoothstep(0.018, 0.0, abs(tabletEdge));

  let scan = 0.5 + 0.5 * sin((deviceUv.y + uniforms.time * 0.16) * 38.0);
  color += vec3f(0.54, 0.83, 0.78) * monitorLine * 0.55;
  color += vec3f(0.26, 0.45, 0.48) * monitorGlow * (0.16 + scan * 0.08);
  color += vec3f(0.95, 0.79, 0.37) * tabletLine * 0.42;

  return vec4f(color, 1.0);
}
`;

const state = {
  pointerX: 0,
  pointerY: 0,
  scroll: 0,
};

function updateScrollState() {
  const maxScroll = Math.max(document.documentElement.scrollHeight - window.innerHeight, 1);
  state.scroll = window.scrollY / maxScroll;
  root.style.setProperty("--progress", `${state.scroll * 100}%`);
  header?.classList.toggle("is-scrolled", window.scrollY > 18);
}

window.addEventListener("pointermove", (event) => {
  state.pointerX = event.clientX / Math.max(window.innerWidth, 1) - 0.5;
  state.pointerY = event.clientY / Math.max(window.innerHeight, 1) - 0.5;
  root.style.setProperty("--cursor-x", `${event.clientX}px`);
  root.style.setProperty("--cursor-y", `${event.clientY}px`);
});

window.addEventListener("scroll", updateScrollState, { passive: true });

function resizeCanvas() {
  const scale = Math.min(window.devicePixelRatio || 1, 2);
  const width = Math.max(1, Math.floor(canvas.clientWidth * scale));
  const height = Math.max(1, Math.floor(canvas.clientHeight * scale));
  if (canvas.width !== width || canvas.height !== height) {
    canvas.width = width;
    canvas.height = height;
    return true;
  }
  return false;
}

async function startWebGPU() {
  if (!navigator.gpu) {
    return false;
  }

  const adapter = await navigator.gpu.requestAdapter({
    powerPreference: "high-performance",
  });
  if (!adapter) {
    return false;
  }

  const device = await adapter.requestDevice();
  const context = canvas.getContext("webgpu");
  const format = navigator.gpu.getPreferredCanvasFormat();

  context.configure({
    device,
    format,
    alphaMode: "opaque",
  });

  const uniformBuffer = device.createBuffer({
    size: 32,
    usage: GPUBufferUsage.UNIFORM | GPUBufferUsage.COPY_DST,
  });

  const bindGroupLayout = device.createBindGroupLayout({
    entries: [{
      binding: 0,
      visibility: GPUShaderStage.FRAGMENT,
      buffer: { type: "uniform" },
    }],
  });

  const pipeline = device.createRenderPipeline({
    layout: device.createPipelineLayout({ bindGroupLayouts: [bindGroupLayout] }),
    vertex: {
      module: device.createShaderModule({ code: vertexWGSL }),
      entryPoint: "main",
    },
    fragment: {
      module: device.createShaderModule({ code: fragmentWGSL }),
      entryPoint: "main",
      targets: [{ format }],
    },
    primitive: { topology: "triangle-list" },
  });

  const bindGroup = device.createBindGroup({
    layout: bindGroupLayout,
    entries: [{
      binding: 0,
      resource: { buffer: uniformBuffer },
    }],
  });

  const uniforms = new Float32Array(8);
  const start = performance.now();

  function frame(now) {
    resizeCanvas();

    uniforms[0] = canvas.width;
    uniforms[1] = canvas.height;
    uniforms[2] = (now - start) / 1000;
    uniforms[3] = state.pointerX;
    uniforms[4] = state.pointerY;
    uniforms[5] = state.scroll;
    uniforms[6] = 0;
    uniforms[7] = 0;
    device.queue.writeBuffer(uniformBuffer, 0, uniforms);

    const encoder = device.createCommandEncoder();
    const pass = encoder.beginRenderPass({
      colorAttachments: [{
        view: context.getCurrentTexture().createView(),
        loadOp: "clear",
        storeOp: "store",
        clearValue: { r: 0.06, g: 0.085, b: 0.105, a: 1 },
      }],
    });
    pass.setPipeline(pipeline);
    pass.setBindGroup(0, bindGroup);
    pass.draw(3);
    pass.end();
    device.queue.submit([encoder.finish()]);
    requestAnimationFrame(frame);
  }

  requestAnimationFrame(frame);
  return true;
}

function startCanvasFallback() {
  const ctx = canvas.getContext("2d");
  const start = performance.now();

  function frame(now) {
    resizeCanvas();
    const width = canvas.width;
    const height = canvas.height;
    const t = (now - start) / 1000;

    const gradient = ctx.createLinearGradient(0, 0, width, height);
    gradient.addColorStop(0, "#0d1317");
    gradient.addColorStop(0.5, "#142027");
    gradient.addColorStop(1, "#10161b");
    ctx.fillStyle = gradient;
    ctx.fillRect(0, 0, width, height);

    ctx.save();
    ctx.translate(width * 0.66, height * 0.43);
    ctx.rotate(-0.12 + Math.sin(t * 0.2) * 0.035);
    drawDevice(ctx, -width * 0.19, -height * 0.16, width * 0.42, height * 0.28, "#1b2930", "#8fd3c8");
    drawDevice(ctx, -width * 0.06, height * 0.12, width * 0.28, height * 0.14, "#18242b", "#e4c46f");
    ctx.restore();

    requestAnimationFrame(frame);
  }

  requestAnimationFrame(frame);
}

function drawDevice(ctx, x, y, width, height, fill, glow) {
  const radius = Math.min(width, height) * 0.055;
  ctx.shadowColor = glow;
  ctx.shadowBlur = 38;
  ctx.fillStyle = fill;
  roundedRect(ctx, x, y, width, height, radius);
  ctx.fill();
  ctx.shadowBlur = 0;
  ctx.strokeStyle = "rgba(220, 235, 240, 0.22)";
  ctx.lineWidth = 2;
  ctx.stroke();

  ctx.strokeStyle = "rgba(143, 211, 200, 0.32)";
  ctx.lineWidth = 1;
  for (let i = 1; i < 4; i += 1) {
    const yy = y + (height / 4) * i;
    ctx.beginPath();
    ctx.moveTo(x + width * 0.08, yy);
    ctx.lineTo(x + width * 0.92, yy);
    ctx.stroke();
  }
}

function roundedRect(ctx, x, y, width, height, radius) {
  ctx.beginPath();
  ctx.moveTo(x + radius, y);
  ctx.lineTo(x + width - radius, y);
  ctx.quadraticCurveTo(x + width, y, x + width, y + radius);
  ctx.lineTo(x + width, y + height - radius);
  ctx.quadraticCurveTo(x + width, y + height, x + width - radius, y + height);
  ctx.lineTo(x + radius, y + height);
  ctx.quadraticCurveTo(x, y + height, x, y + height - radius);
  ctx.lineTo(x, y + radius);
  ctx.quadraticCurveTo(x, y, x + radius, y);
  ctx.closePath();
}

resizeCanvas();
updateScrollState();
window.addEventListener("resize", resizeCanvas);

startWebGPU().then((started) => {
  if (!started) {
    startCanvasFallback();
  }
}).catch(() => {
  startCanvasFallback();
});
