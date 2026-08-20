#version 440

// Honeycomb reveal: hexagonal cells open along a diagonal sweep, each growing
// from its own centre, carrying the new wallpaper in.
//
// Uniform block layout is dictated by Qt: qt_Matrix and qt_Opacity first, then
// the ShaderEffect's custom properties in declaration order. Samplers are
// matched to QML properties by name. Compile with tools/build_shaders.sh.

layout(location = 0) in vec2 qt_TexCoord0;
layout(location = 0) out vec4 fragColor;

layout(std140, binding = 0) uniform buf {
    mat4 qt_Matrix;
    float qt_Opacity;
    float progress;  // 0 = old wallpaper, 1 = new
    float aspect;    // width / height, so cells stay regular hexagons
    float cells;     // cells across the screen's height
};

layout(binding = 1) uniform sampler2D srcOld;
layout(binding = 2) uniform sampler2D srcNew;

const float SQRT3 = 1.7320508;
const vec2 LATTICE = vec2(1.0, SQRT3);
// Split of the animation between the sweep across the screen and a single
// cell's growth. 0 would open every cell at once, 1 would make each cell pop
// instantly and spend all the time travelling.
const float STAGGER = 0.72;
// Per-cell noise on the wavefront, so it reads as a swarm and not a ruler.
const float JITTER = 0.14;

float hash(vec2 p) {
    return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453);
}

// Offset from the nearest hex centre. A hex grid is the nearest-neighbour
// diagram of two interleaved rectangular lattices, so the closer candidate of
// the two wins.
vec2 hexOffset(vec2 p) {
    vec2 mid = LATTICE * 0.5;
    vec2 a = mod(p, LATTICE) - mid;
    vec2 b = mod(p - mid, LATTICE) - mid;
    return dot(a, a) < dot(b, b) ? a : b;
}

// Distance to the hexagon's boundary: exactly 0.5 on the edge.
float hexEdge(vec2 p) {
    p = abs(p);
    return max(dot(p, vec2(0.5, SQRT3 * 0.5)), p.x);
}

void main() {
    vec2 uv = qt_TexCoord0;
    vec2 p = vec2(uv.x * aspect, uv.y) * cells;
    vec2 offset = hexOffset(p);
    vec2 id = p - offset;

    float sweep = dot(id, vec2(1.0)) / ((aspect + 1.0) * cells);
    sweep = clamp(sweep + (hash(id) - 0.5) * JITTER, 0.0, 1.0);

    // This cell's own 0..1 time: delayed by the sweep, then grown.
    float local = clamp((progress - sweep * STAGGER) / (1.0 - STAGGER), 0.0, 1.0);

    float edge = hexEdge(offset);
    float radius = 0.5 * local;
    // fwidth keeps the cell rim one pixel wide whatever the cell count is.
    float feather = fwidth(edge) + 1e-4;
    float mask = smoothstep(radius + feather, radius - feather, edge);

    fragColor = mix(texture(srcOld, uv), texture(srcNew, uv), mask) * qt_Opacity;
}
