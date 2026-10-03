# Copyright (C) 2026 fierman
# SPDX-License-Identifier: GPL-3.0-only
"""Toolbar and table-of-contents icons, drawn with PIL (no image files).

Each icon is drawn on a 64 x 64 canvas and reduced to the toolbar size, which
gives smooth edges at any display scale.
"""

from PIL import Image, ImageDraw

BLACK = (0, 0, 0, 255)
WHITE = (255, 255, 255, 255)
GREY = (128, 128, 128, 255)
NAVY = (10, 36, 106, 255)
BLUE = (40, 90, 200, 255)
GREEN = (30, 140, 50, 255)
RED = (200, 30, 30, 255)
YELLOW = (232, 192, 74, 255)
FACE = (212, 208, 200, 255)


def _canvas():
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    return img, ImageDraw.Draw(img)


def _magnifier(d, sign=None, box=False):
    d.ellipse((6, 6, 40, 40), fill=(220, 235, 255, 255), outline=BLACK, width=5)
    d.line((36, 36, 58, 58), fill=BLACK, width=10)
    if sign:
        d.line((14, 23, 32, 23), fill=BLACK, width=5)
        if sign == "+":
            d.line((23, 14, 23, 32), fill=BLACK, width=5)
    if box:
        d.rectangle((15, 15, 31, 31), fill=YELLOW, outline=BLACK, width=3)


def _arc_arrow(d, clockwise, colour=GREEN):
    d.arc((8, 8, 56, 56), 200 if clockwise else -20, 520 if clockwise else 340 - 20, fill=colour, width=8)
    if clockwise:
        d.polygon([(44, 2), (60, 18), (38, 22)], fill=colour)
    else:
        d.polygon([(20, 2), (4, 18), (26, 22)], fill=colour)


def _triangle_plus(d, colour):
    d.polygon([(26, 6), (46, 50), (6, 50)], fill=colour, outline=BLACK)
    d.rectangle((40, 36, 62, 44), fill=GREEN)
    d.rectangle((47, 29, 55, 51), fill=GREEN)


def draw(name):
    img, d = _canvas()
    if name == "open":
        d.polygon([(4, 16), (24, 16), (30, 22), (58, 22), (58, 54), (4, 54)], fill=YELLOW, outline=BLACK)
        d.polygon([(10, 30), (62, 30), (54, 54), (4, 54)], fill=(250, 220, 120, 255), outline=BLACK)
    elif name == "save":
        d.rectangle((6, 6, 58, 58), fill=NAVY, outline=BLACK, width=2)
        d.rectangle((16, 6, 48, 26), fill=WHITE)
        d.rectangle((14, 38, 50, 58), fill=GREY)
        d.rectangle((36, 42, 44, 54), fill=NAVY)
    elif name == "undo":
        d.arc((12, 14, 58, 56), 180, 450, fill=BLUE, width=8)
        d.polygon([(2, 30), (22, 30), (12, 14)], fill=BLUE)
    elif name == "select":
        d.polygon([(14, 4), (14, 50), (25, 39), (34, 58), (42, 54), (33, 36), (48, 36)], fill=WHITE, outline=BLACK)
        d.line([(14, 4), (14, 50), (25, 39), (34, 58), (42, 54), (33, 36), (48, 36), (14, 4)], fill=BLACK, width=3)
    elif name == "pan":
        d.line((32, 6, 32, 58), fill=BLACK, width=6)
        d.line((6, 32, 58, 32), fill=BLACK, width=6)
        for pts in ([(32, 0), (22, 12), (42, 12)], [(32, 64), (22, 52), (42, 52)],
                    [(0, 32), (12, 22), (12, 42)], [(64, 32), (52, 22), (52, 42)]):
            d.polygon(pts, fill=BLACK)
    elif name == "zoomin":
        _magnifier(d, "+")
    elif name == "zoomout":
        _magnifier(d, "-")
    elif name == "zoomsel":
        _magnifier(d, box=True)
    elif name == "fullext":
        d.ellipse((4, 4, 60, 60), fill=(90, 150, 230, 255), outline=BLACK, width=3)
        d.polygon([(14, 18), (30, 12), (34, 26), (22, 34), (14, 30)], fill=GREEN)
        d.polygon([(36, 34), (52, 30), (50, 48), (38, 52)], fill=GREEN)
        d.arc((18, 4, 46, 60), 0, 360, fill=(255, 255, 255, 180), width=2)
        d.line((4, 32, 60, 32), fill=(255, 255, 255, 180), width=2)
    elif name == "rotate":
        _arc_arrow(d, True, BLACK)
        d.ellipse((26, 26, 38, 38), fill=RED)
    elif name == "rotl":
        _arc_arrow(d, False)
    elif name == "rotr":
        _arc_arrow(d, True)
    elif name == "dup":
        d.rectangle((4, 4, 38, 38), fill=WHITE, outline=BLACK, width=3)
        d.rectangle((24, 24, 58, 58), fill=(255, 255, 200, 255), outline=BLACK, width=3)
    elif name == "delete":
        d.line((10, 10, 54, 54), fill=RED, width=11)
        d.line((54, 10, 10, 54), fill=RED, width=11)
    elif name == "parent":
        d.rectangle((6, 34, 58, 58), fill=WHITE, outline=BLACK, width=3)
        d.line((32, 30, 32, 6), fill=BLUE, width=7)
        d.polygon([(32, 0), (18, 16), (46, 16)], fill=BLUE)
    elif name == "addobj":
        d.polygon([(4, 30), (28, 8), (52, 30)], fill=RED, outline=BLACK)
        d.rectangle((10, 30, 46, 56), fill=(230, 210, 170, 255), outline=BLACK, width=2)
        d.rectangle((22, 40, 32, 56), fill=(120, 80, 40, 255))
        d.rectangle((42, 40, 62, 48), fill=GREEN)
        d.rectangle((48, 34, 56, 54), fill=GREEN)
    elif name == "addhost":
        _triangle_plus(d, (240, 70, 60, 255))
    elif name == "addfriend":
        _triangle_plus(d, (80, 160, 255, 255))
    elif name == "events":
        d.rectangle((6, 4, 58, 60), fill=WHITE, outline=BLACK, width=3)
        for y in (16, 28, 40, 52):
            d.rectangle((14, y - 3, 18, y + 1), fill=NAVY)
            d.line((24, y - 1, 50, y - 1), fill=BLACK, width=3)
    elif name == "nextspawn":
        d.polygon([(22, 6), (40, 46), (4, 46)], fill=(240, 70, 60, 255), outline=BLACK)
        d.polygon([(44, 18), (62, 32), (44, 46)], fill=BLUE)
    elif name == "follow":
        d.polygon([(2, 56), (22, 22), (34, 40), (44, 28), (62, 56)], fill=(140, 120, 80, 255), outline=BLACK)
        d.polygon([(22, 22), (16, 32), (28, 32)], fill=WHITE)
        d.line((10, 14, 54, 14), fill=BLUE, width=4)
        d.polygon([(54, 6), (62, 14), (54, 22)], fill=BLUE)
    elif name == "newscen":
        d.rectangle((10, 4, 50, 60), fill=WHITE, outline=BLACK, width=3)
        d.polygon([(18, 40), (30, 20), (42, 40)], fill=(240, 70, 60, 255))
        d.rectangle((40, 40, 62, 48), fill=GREEN)
        d.rectangle((47, 33, 55, 55), fill=GREEN)
    elif name == "cut":
        d.ellipse((6, 38, 26, 58), outline=BLACK, width=5)
        d.ellipse((38, 38, 58, 58), outline=BLACK, width=5)
        d.line((22, 42, 46, 4), fill=GREY, width=6)
        d.line((42, 42, 18, 4), fill=GREY, width=6)
    elif name == "copy":
        d.rectangle((4, 4, 38, 46), fill=WHITE, outline=BLACK, width=3)
        d.rectangle((24, 18, 58, 60), fill=WHITE, outline=BLACK, width=3)
        for y in (30, 40, 50):
            d.line((30, y, 52, y), fill=NAVY, width=3)
    elif name == "paste":
        d.rectangle((6, 10, 48, 60), fill=(200, 160, 90, 255), outline=BLACK, width=3)
        d.rectangle((18, 4, 36, 16), fill=GREY, outline=BLACK, width=2)
        d.rectangle((26, 26, 60, 62), fill=WHITE, outline=BLACK, width=3)
        for y in (38, 48):
            d.line((32, y, 54, y), fill=NAVY, width=3)
    elif name == "app":
        d.rounded_rectangle((0, 0, 63, 63), radius=10, fill=(95, 110, 60, 255), outline=(40, 50, 25, 255), width=2)
        d.polygon([(0, 46), (18, 30), (30, 38), (42, 26), (63, 44), (63, 63), (0, 63)], fill=(140, 120, 80, 255))
        d.rectangle((14, 30, 50, 44), fill=(60, 75, 45, 255), outline=BLACK, width=2)
        d.rectangle((24, 22, 40, 32), fill=(70, 88, 52, 255), outline=BLACK, width=2)
        d.line((40, 26, 58, 22), fill=BLACK, width=4)
        d.rounded_rectangle((10, 42, 54, 52), radius=5, fill=(40, 40, 40, 255))
        for x in (16, 26, 36, 46):
            d.ellipse((x - 3, 44, x + 3, 50), fill=(120, 120, 120, 255))
    elif name in ("addwp", "addroute", "addevent"):
        if name == "addwp":
            d.polygon([(26, 8), (46, 28), (26, 48), (6, 28)], fill=(255, 160, 40, 255), outline=BLACK)
        elif name == "addroute":
            d.line([(6, 50), (22, 14), (40, 38), (54, 8)], fill=(255, 160, 40, 255), width=6)
            for x, y in ((6, 50), (22, 14), (40, 38)):
                d.polygon([(x, y - 6), (x + 6, y), (x, y + 6), (x - 6, y)], fill=(255, 160, 40, 255), outline=BLACK)
        else:
            d.rectangle((6, 4, 46, 52), fill=WHITE, outline=BLACK, width=3)
            for y in (16, 28, 40):
                d.line((14, y, 38, y), fill=NAVY, width=3)
        d.rectangle((40, 42, 62, 50), fill=GREEN)
        d.rectangle((47, 35, 55, 57), fill=GREEN)
    elif name == "layers":
        for k, col in enumerate(((250, 220, 120, 255), YELLOW, (210, 160, 40, 255))):
            y = 34 - k * 12
            d.polygon([(4, y + 10), (32, y), (60, y + 10), (32, y + 20)], fill=col, outline=BLACK)
    elif name == "toc":
        d.rectangle((4, 6, 60, 58), fill=WHITE, outline=BLACK, width=2)
        for y, col in ((16, (240, 70, 60, 255)), (30, GREEN), (44, BLUE)):
            d.rectangle((10, y - 4, 18, y + 4), fill=col)
            d.line((24, y, 52, y), fill=BLACK, width=3)
    elif name == "objects":
        d.polygon([(6, 30), (26, 12), (46, 30)], fill=RED, outline=BLACK)
        d.rectangle((12, 30, 40, 54), fill=(230, 210, 170, 255), outline=BLACK, width=2)
        d.ellipse((40, 22, 62, 44), fill=GREEN, outline=BLACK)
        d.rectangle((49, 44, 53, 56), fill=(120, 80, 40, 255))
    elif name == "props":
        d.rectangle((4, 4, 60, 60), fill=WHITE, outline=BLACK, width=2)
        d.rectangle((4, 4, 60, 14), fill=NAVY)
        for y in (24, 36, 48):
            d.line((10, y, 28, y), fill=BLACK, width=3)
            d.rectangle((32, y - 4, 54, y + 4), outline=GREY, width=2)
    elif name == "catalog":
        d.polygon([(4, 10), (20, 10), (24, 16), (40, 16), (40, 30), (4, 30)], fill=YELLOW, outline=BLACK)
        d.line((12, 30, 12, 50), fill=BLACK, width=2)
        d.line((12, 50, 24, 50), fill=BLACK, width=2)
        d.polygon([(24, 40), (38, 40), (42, 44), (60, 44), (60, 58), (24, 58)], fill=YELLOW, outline=BLACK)
    elif name == "table":
        d.rectangle((4, 8, 60, 56), fill=WHITE, outline=BLACK, width=2)
        d.rectangle((4, 8, 60, 20), fill=FACE, outline=BLACK, width=2)
        for y in (32, 44):
            d.line((4, y, 60, y), fill=GREY, width=2)
        for x in (24, 42):
            d.line((x, 8, x, 56), fill=GREY, width=2)
    elif name == "gamefolder":
        d.polygon([(4, 16), (24, 16), (30, 22), (58, 22), (58, 54), (4, 54)], fill=YELLOW, outline=BLACK)
        d.polygon([(30, 30), (44, 46), (16, 46)], fill=(240, 70, 60, 255), outline=BLACK)
    return img


def toolbar_icon(name, size):
    return draw(name).resize((size, size), Image.LANCZOS)


def checkbox(checked, size):
    """Flat Windows 10 check box."""
    img, d = _canvas()
    d.rectangle((6, 6, 57, 57), fill=WHITE, outline=(51, 51, 51, 255), width=4)
    if checked:
        d.line([(16, 32), (28, 44), (48, 20)], fill=(30, 30, 30, 255), width=7)
    return img.resize((size, size), Image.LANCZOS)


def swatch(kind, colour, size):
    """Legend symbol for a table-of-contents entry."""
    img, d = _canvas()
    c = tuple(colour[:3]) + (255,)
    if kind == "triangle":
        d.polygon([(32, 4), (60, 58), (4, 58)], fill=c, outline=BLACK)
    elif kind == "diamond":
        d.polygon([(32, 4), (60, 32), (32, 60), (4, 32)], fill=c, outline=BLACK)
    elif kind == "terrain":
        d.rectangle((4, 4, 60, 60), fill=(95, 110, 60, 255), outline=BLACK, width=2)
        d.polygon([(4, 60), (24, 26), (36, 44), (44, 34), (60, 60)], fill=(140, 120, 80, 255))
    elif kind == "outline":
        d.rectangle((6, 6, 58, 58), outline=c, width=6)
    else:
        d.rectangle((6, 6, 58, 58), fill=c, outline=BLACK, width=2)
    return img.resize((size, size), Image.LANCZOS)


def toc_image(checked, kind, colour, size):
    """Check box followed by a legend symbol, as one image."""
    cb = checkbox(checked, size)
    out = Image.new("RGBA", (size * 2 + 4, size), (0, 0, 0, 0))
    out.paste(cb, (0, 0), cb)
    if kind:
        sw = swatch(kind, colour, size)
        out.paste(sw, (size + 4, 0), sw)
    return out


def app_icon_images(sizes=(16, 24, 32, 48, 64, 128, 256)):
    """Application icon at several sizes (window icon and .ico file)."""
    base = draw("app").resize((256, 256), Image.LANCZOS)
    return [base.resize((n, n), Image.LANCZOS) for n in sizes]
