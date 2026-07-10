#version 140

in vec4 position;

#if TRAIT_MAP_TEXTURE || TRAIT_MAP_EXTERNAL_TEXTURE || TRAIT_MAP_MULTI_PLANE_TEXTURE
in vec4 texcoord;
out vec2 texcoord0;
#endif

#if TRAIT_ROUNDED_CORNERS | TRAIT_BORDER
out vec2 position0;
#endif

uniform mat4 modelViewProjectionMatrix;

void main()
{
#if TRAIT_MAP_TEXTURE || TRAIT_MAP_EXTERNAL_TEXTURE || TRAIT_MAP_MULTI_PLANE_TEXTURE
    texcoord0 = texcoord.st;
#endif

#if TRAIT_ROUNDED_CORNERS | TRAIT_BORDER
    position0 = position.xy;
#endif

    gl_Position = modelViewProjectionMatrix * position;
}
