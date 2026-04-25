# assets/

Drop the following files into this directory before the M1 rendering work:

- `forte-logo.png` — primary Forte Securities logo (light background variant). Used to extract the palette and embed in the PDF cover and page header.
- `forte-logo-dark.png` *(optional)* — dark background variant for the dashboard sidebar.
- `hillgate-template.docx` — the existing Hillgate Word template. Used as the structural reference for `api/render/templates/report.html` and `api/render/styles/report.css` (margins, header/footer pattern, heading hierarchy, table styles, callout boxes).

Any additional brand assets (alternate marks, icon set, custom fonts beyond Inter / Source Sans Pro) can also live here.

Filenames matter — the rendering layer expects them as listed above. If you use different names, flag it and I'll wire the new paths through `api/settings.py`.
