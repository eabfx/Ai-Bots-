"""Draws the Kelly_Terminal mark (a K followed by a blinking terminal underscore) and writes every icon from it.
Needs Playwright + Pillow. Run:  python make-logo.py <assets folder>"""
import sys, os, io
from playwright.sync_api import sync_playwright
from PIL import Image

def mark(uid="m", tile="round", glyph=1.0, glow=True):
    """SVG of the mark, viewBox 0 0 512. tile: round | full | none."""
    # glyph geometry: K (flat tops and a flat baseline) and the underscore, centred on the tile
    g = ("<g transform='translate(256 256) scale(%s) translate(-256 -256) translate(-13 20)'>"
         "<clipPath id='%s-c'><rect x='0' y='120' width='512' height='232'/></clipPath>"
         "<g class='kk' fill='url(#%s-k)'>"
         "<rect x='138' y='120' width='34' height='232'/>"
         "<g clip-path='url(#%s-c)'>"
         "<path d='M156 266 L350 90' stroke='url(#%s-k)' stroke-width='31' fill='none'/>"
         "<path d='M156 258 L350 434' stroke='url(#%s-k)' stroke-width='31' fill='none'/></g></g>"
         "<rect class='cur' x='318' y='324' width='88' height='28' fill='#22c55e'/></g>") % (glyph, uid, uid, uid, uid, uid)
    if tile == "round":
        bg = "<rect class='tl' x='32' y='32' width='448' height='448' rx='104' fill='url(#%s-b)'/>" % uid
        rim = "<rect x='33.5' y='33.5' width='445' height='445' rx='102.5' fill='none' stroke='url(#%s-r)' stroke-width='3'/>" % uid
    elif tile == "full":
        bg = "<rect width='512' height='512' fill='url(#%s-b)'/>" % uid
        rim = ""
    else:
        bg = rim = ""
    gl = ("<circle cx='%s' cy='%s' r='170' fill='url(#%s-g)'/>" % (362, 384, uid)) if glow and tile != "none" else ""
    defs = ("<defs>"
            "<radialGradient id='%s-b' cx='30%%' cy='22%%' r='95%%'><stop offset='0' stop-color='#202020'/><stop offset='.55' stop-color='#0c0c0c'/><stop offset='1' stop-color='#030303'/></radialGradient>"
            "<linearGradient id='%s-r' x1='0' y1='0' x2='0' y2='1'><stop offset='0' stop-color='#fff' stop-opacity='.42'/><stop offset='.5' stop-color='#fff' stop-opacity='.07'/><stop offset='1' stop-color='#22c55e' stop-opacity='.35'/></linearGradient>"
            "<radialGradient id='%s-g'><stop offset='0' stop-color='#22c55e' stop-opacity='.30'/><stop offset='1' stop-color='#22c55e' stop-opacity='0'/></radialGradient>"
            "<linearGradient id='%s-k' gradientUnits='userSpaceOnUse' x1='130' y1='110' x2='360' y2='360'><stop offset='0' stop-color='#ffffff'/><stop offset='1' stop-color='#cfcfcf'/></linearGradient>"
            "</defs>") % (uid, uid, uid, uid)
    clip = "<clipPath id='%s-t'><rect x='32' y='32' width='448' height='448' rx='104'/></clipPath>" % uid
    glow_g = ("<g clip-path='url(#%s-t)'>%s</g>" % (uid, gl)) if tile == "round" else gl
    return "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 512 512'>%s%s%s%s%s%s</svg>" % (defs, clip, bg, glow_g, rim, g)

def render(page, svg, size, transparent=True):
    page.set_viewport_size({"width": size, "height": size})
    page.set_content("<style>html,body{margin:0;background:transparent}svg{display:block;width:%dpx;height:%dpx}</style>%s" % (size, size, svg))
    return page.screenshot(omit_background=transparent, clip={"x": 0, "y": 0, "width": size, "height": size})

if __name__ == "__main__":
    out = sys.argv[1]
    with sync_playwright() as p:
        b = p.chromium.launch(); pg = b.new_page(device_scale_factor=1)
        files = {"icon-512.png": (mark("a"), 512), "icon-192.png": (mark("a"), 192),
                 "icon-maskable-512.png": (mark("a", "full", .74), 512), "apple-touch-icon.png": (mark("a", "full", .8), 180)}
        for name, (svg, size) in files.items():
            open(os.path.join(out, name), "wb").write(render(pg, svg, size, name.startswith("icon-5") or name == "icon-192.png"))
        ico = [Image.open(io.BytesIO(render(pg, mark("i"), s))).convert("RGBA") for s in (256, 128, 64, 48, 32, 24, 16)]
        ico[0].save(os.path.join(out, "riskdesk.ico"), format="ICO", sizes=[(s, s) for s in (256, 128, 64, 48, 32, 24, 16)], append_images=ico[1:])
        open(os.path.join(out, "logo.svg"), "w").write(mark("l").replace("<svg ", "<svg width='512' height='512' ", 1))
        b.close()
