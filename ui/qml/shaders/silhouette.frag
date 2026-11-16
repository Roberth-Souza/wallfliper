#version 440

// Silhouette reveal: a thin line draws the outline of the new wallpaper's
// subject over the old one, then the new wallpaper grows out of that outline —
// the subject fills in first, the rest of the scene spreads from it.
//
// srcMask is the field texture written by core/silhouette_worker.py:
//   r  signed distance to the outline, +-NEAR_TEXELS texels around 0.5
//   g  sqrt(|distance| / farthest distance)
//   b  distance along the outline from where its line starts, 0..1
//
// Compile with tools/build_shaders.sh.

layout(location = 0) in vec2 qt_TexCoord0;
layout(location = 0) out vec4 fragColor;

layout(std140, binding = 0) uniform buf {
    mat4 qt_Matrix;
    float qt_Opacity;
    float progress;   // 0 = old wallpaper, 1 = new
    float aspect;     // width / height
    float maskScale;  // screen pixels per field texel
    float seed;       // per-switch random, so the growth never repeats
    float liveNew;    // 1 = the new wallpaper is live under the surface: draw nothing for it
};

layout(binding = 1) uniform sampler2D srcOld;
layout(binding = 2) uniform sampler2D srcNew;
layout(binding = 3) uniform sampler2D srcMask;

// Must match NEAR_TEXELS in core/silhouette_worker.py.
const float NEAR_TEXELS = 8.0;
// Share of the animation spent drawing the outline before anything grows.
const float DRAW_SHARE = 0.42;
// Growth starts a little before the pens meet: both motions ease to a stop at
// their ends, and run strictly one after the other they read as a stall.
const float GROW_START = 0.35;
const float LINE_HALF_PX = 0.9;
const float LINE_ALPHA = 0.9;
// Faint glow around the line, so it also reads on a bright, busy picture.
const float HALO_PX = 3.5;
const float HALO_ALPHA = 0.22;
// Length of the pen tip's fade, as a share of the longest outline.
const float TIP = 0.04;
// The subject is small next to the scene; without a head start it would still
// be filling in while the background is half done.
const float INSIDE_COST = 0.35;
// How far the growing edge wanders from a plain offset of the outline, as a
// share of the farthest distance.
const float WANDER = 0.22;
// Softness of the growing edge, at the outline and at the farthest point. It
// widens with distance because the far field is stored coarsely out there, and
// a hard edge would show that as terraces.
const float FEATHER_NEAR = 0.03;
const float FEATHER_FAR = 0.09;

float hash(vec2 p) {
    return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453);
}

float valueNoise(vec2 p) {
    vec2 i = floor(p);
    vec2 f = fract(p);
    f = f * f * (3.0 - 2.0 * f);
    return mix(mix(hash(i), hash(i + vec2(1.0, 0.0)), f.x),
               mix(hash(i + vec2(0.0, 1.0)), hash(i + vec2(1.0, 1.0)), f.x), f.y);
}

float fbm(vec2 p) {
    return 0.5 * valueNoise(p) + 0.3 * valueNoise(p * 2.3 + 7.1) + 0.2 * valueNoise(p * 5.1 + 3.7);
}

void main() {
    vec2 uv = qt_TexCoord0;
    vec3 field = texture(srcMask, uv).rgb;

    float edgePx = (field.r - 0.5) * 2.0 * NEAR_TEXELS * maskScale;
    float inside = step(field.r, 0.5);
    float far = field.g * field.g;

    // Outline: the pen has passed wherever the arc coordinate is behind it.
    float draw = smoothstep(0.0, 1.0, clamp(progress / DRAW_SHARE, 0.0, 1.0));
    float drawn = smoothstep(0.0, TIP, draw * (1.0 + TIP) - field.b);
    float line = 1.0 - smoothstep(LINE_HALF_PX - 0.6, LINE_HALF_PX + 0.6, abs(edgePx));
    float halo = exp(-edgePx * edgePx / (2.0 * HALO_PX * HALO_PX)) * HALO_ALPHA;

    // Growth: the edge leaves the outline and wanders outwards. The noise is
    // faded in with distance so the growth starts exactly on the drawn line.
    float grow = clamp((progress - GROW_START) / (1.0 - GROW_START), 0.0, 1.0);
    float wander = (fbm(vec2(uv.x * aspect, uv.y) * 5.0 + seed * 31.0) - 0.5) * WANDER;
    float cost = far * mix(1.0, INSIDE_COST, inside) + wander * smoothstep(0.0, 0.08, far);
    // Eased at both ends: it leaves the line gently and settles into the far
    // corners instead of snapping shut.
    float eased = mix(grow, grow * grow * (3.0 - 2.0 * grow), 0.75);
    float front = eased * (1.0 + 0.5 * WANDER + FEATHER_FAR);
    float feather = mix(FEATHER_NEAR, FEATHER_FAR, far);
    float reveal = smoothstep(0.0, feather, front - cost) * step(1e-4, grow);

    // The line has done its job once the scene starts growing from it.
    float ink = max(line * LINE_ALPHA, halo) * drawn * (1.0 - smoothstep(0.0, 0.5, grow));

    // Premultiplied: scaling the new texel to nothing leaves a hole the live
    // wallpaper underneath shows through.
    vec4 fresh = texture(srcNew, uv) * (1.0 - liveNew);
    vec4 color = mix(texture(srcOld, uv), fresh, reveal);
    fragColor = mix(color, vec4(1.0), ink) * qt_Opacity;
}
