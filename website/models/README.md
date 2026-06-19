# Future 3D Models

Place future `.glb`, `.gltf`, texture, or compressed model assets here.

The current site exposes `window.ProjectMonitorizeShowcase.modelSlots` from `website/main.js`.
When model loading is added later, keep model metadata in that array and route rendering through
the existing `#stage` canvas so the page does not need a layout rewrite.
