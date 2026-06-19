# Project Monitorize Website

Static showcase site for Project Monitorize.

## Structure

- `index.html` contains the page content.
- `index.html` also embeds the CSS so double-clicking it works in local browsers.
- `styles.css` is kept as the editable source copy of the awards-style visual system.
- `main.js` contains the WebGPU hero scene and Canvas fallback.
- `models/` is reserved for future 3D assets.

## Local Preview

From this folder:

```bash
python3 -m http.server 4173 --bind 127.0.0.1
```

Then open:

```text
http://127.0.0.1:4173/
```

Or run the helper:

```bash
./preview.sh
```

WebGPU requires a secure context, and localhost qualifies in modern browsers.

Opening `index.html` directly also works as a static fallback. Use the local
server when you want the WebGPU canvas and module-driven scroll animations.
