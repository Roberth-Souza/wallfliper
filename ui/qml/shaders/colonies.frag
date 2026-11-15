#version 440

// Colonies: the new wallpaper breaks out in scattered spots that spread block
// by block, like cultures eating through the old one, until they merge.
//
// Stands in for `silhouette` when the new wallpaper has no subject to outline.
// Compile with tools/build_shaders.sh.

layout(location = 0) in vec2 qt_TexCoord0;
layout(location = 0) out vec4 fragColor;

layout(std140, binding = 0) uniform buf {
    mat4 qt_Matrix;
    float qt_Opacity;
    float progress;     // 0 = old wallpaper, 1 = new
    float aspect;       // width / height
    float pixelHeight;  // surface height in pixels, to size the blocks
    float seed;         // per-switch random, so colonies never start in the same spots
};

layout(binding = 1) uniform sampler2D srcOld;
layout(binding = 2) uniform sampler2D srcNew;

// Edge of one "pixel" of the colony, in screen pixels.
const float BLOCK_PX = 6.0;
// Colony sites across the screen's height (one per lattice cell, jittered).
const float SITES = 3.2;
// Latest a colony may appear, and how long its spread to a neighbour takes, in
// units of the animation.
const float MAX_DELAY = 0.34;
const float SPREAD = 0.36;
// Raggedness of the eating edge: per block, and in slower lumps.
const float GRAIN = 0.07;
const float LUMPS = 0.16;
// How far a colony has to spread before the last gaps close, in lattice cells.
// Measured, not a bound: a strict bound leaves the animation idle for its last
// third, while the odd block still open at the end just lands with the cut.
const float REACH = 0.6;

float hash(vec2 p) {
    return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453);
}

vec2 hash2(vec2 p) {
    return vec2(hash(p), hash(p + 19.19));
}

float valueNoise(vec2 p) {
    vec2 i = floor(p);
    vec2 f = fract(p);
    f = f * f * (3.0 - 2.0 * f);
    return mix(mix(hash(i), hash(i + vec2(1.0, 0.0)), f.x),
               mix(hash(i + vec2(0.0, 1.0)), hash(i + vec2(1.0, 1.0)), f.x), f.y);
}

void main() {
    vec2 uv = qt_TexCoord0;
    // Everything is decided per block, so a block flips as one.
    vec2 block = floor(uv * vec2(aspect, 1.0) * pixelHeight / BLOCK_PX);
    vec2 p = (block + 0.5) * BLOCK_PX / pixelHeight * SITES;

    vec2 cell = floor(p);
    float arrival = 1e3;
    for (int y = -1; y <= 1; y++) {
        for (int x = -1; x <= 1; x++) {
            vec2 id = cell + vec2(float(x), float(y));
            vec2 site = id + hash2(id + seed * 57.0);
            float delay = hash(id * 1.7 + seed * 13.0) * MAX_DELAY;
            arrival = min(arrival, delay + distance(p, site) * SPREAD);
        }
    }
    arrival += (hash(block + seed * 7.0) - 0.5) * GRAIN
             + (valueNoise(block * 0.11 + seed * 3.0) - 0.5) * LUMPS;

    float longest = MAX_DELAY + REACH * SPREAD + 0.5 * (GRAIN + LUMPS);
    float reveal = step(arrival, progress * longest) * step(1e-4, progress);

    fragColor = mix(texture(srcOld, uv), texture(srcNew, uv), reveal) * qt_Opacity;
}
