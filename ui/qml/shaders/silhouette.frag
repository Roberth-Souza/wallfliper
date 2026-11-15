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
};

layout(binding = 1) uniform sampler2D srcOld;
layout(binding = 2) uniform sampler2D srcNew;
layout(binding = 3) uniform sampler2D srcMask;

// Must match NEAR_TEXELS in core/silhouette_worker.py.
const float NEAR_TEXELS = 8.0;
// Share of the animation spent drawing the outline before anything grows.
const float DRAW_SHARE = 0.42;
const float LINE_HALF_PX = 0.9;
const float LINE_ALPHA = 0.9;
// The subject is small next to the scene; without a head start it would still
// be filling in while the background is half done.
const float INSIDE_COST = 0.35;
// How far the growing edge wanders from a plain offset of the outline, as a
// share of the farthest distance.
const float WANDER = 0.22;
// Softness of the growing edge, at the outline and at the farthest point. It
// widens with distance because the far field is stored coarsely out there, and
// a hard edge would show that as terraces.
const float FEATHER_NEAR = 0.012;
const float FEATHER_FAR = 0.035;

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
    float drawn = smoothstep(0.0, 0.015, draw * 1.02 - field.b);
    float line = 1.0 - smoothstep(LINE_HALF_PX - 0.6, LINE_HALF_PX + 0.6, abs(edgePx));

    // Growth: the edge leaves the outline and wanders outwards. The noise is
    // faded in with distance so the growth starts exactly on the drawn line.
    float grow = clamp((progress - DRAW_SHARE) / (1.0 - DRAW_SHARE), 0.0, 1.0);
    float wander = (fbm(vec2(uv.x * aspect, uv.y) * 5.0 + seed * 31.0) - 0.5) * WANDER;
    float cost = far * mix(1.0, INSIDE_COST, inside) + wander * smoothstep(0.0, 0.08, far);
    float front = pow(grow, 1.25) * (1.0 + 0.5 * WANDER + FEATHER_FAR);
    float feather = mix(FEATHER_NEAR, FEATHER_FAR, far);
    float reveal = smoothstep(0.0, feather, front - cost) * step(1e-4, grow);

    // The line has done its job once the scene starts growing from it.
    float ink = line * drawn * LINE_ALPHA * (1.0 - smoothstep(0.0, 0.45, grow));

    vec4 color = mix(texture(srcOld, uv), texture(srcNew, uv), reveal);
    fragColor = mix(color, vec4(1.0), ink) * qt_Opacity;
}
