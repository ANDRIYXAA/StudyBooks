"""Іконка для ярлика Studybooks на робочому столі: розкрита книга, з якої злітає ракета.

python -I -X utf8 make_icon.py  ->  studybooks.ico + studybooks-preview.png поруч зі скриптом
"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter

S = 1024
HERE = Path(__file__).resolve().parent


def lerp(a, b, t):
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def curve(p0, p1, p2, n=40):
    """Квадратична крива Безьє як список точок."""
    pts = []
    for i in range(n + 1):
        t = i / n
        pts.append(((1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * p1[0] + t * t * p2[0],
                    (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * p1[1] + t * t * p2[1]))
    return pts


def background():
    top, bottom = (49, 50, 120), (24, 24, 37)  # індиго -> Catppuccin crust
    grad = Image.new("RGBA", (S, S))
    gd = ImageDraw.Draw(grad)
    for y in range(S):
        gd.line([(0, y), (S, y)], fill=lerp(top, bottom, y / S) + (255,))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([16, 16, S - 16, S - 16], radius=220, fill=255)
    bg = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    bg.paste(grad, mask=mask)
    d = ImageDraw.Draw(bg)
    for x, y, r in [(170, 180, 14), (300, 110, 8), (820, 250, 10), (880, 130, 16), (220, 380, 7),
                    (700, 90, 7), (900, 420, 8), (120, 560, 9)]:
        d.ellipse([x - r, y - r, x + r, y + r], fill=(245, 224, 220, 255))
    return bg


def book(img):
    d = ImageDraw.Draw(img)
    cover = (250, 179, 135, 255)   # peach
    page = (255, 255, 255, 255)
    line = (180, 190, 254, 255)    # lavender
    # обкладинка трохи ширша й нижча за сторінки
    d.polygon(curve((512, 650), (330, 600), (130, 650)) + curve((130, 900), (330, 860), (512, 920))
              + curve((512, 920), (694, 860), (894, 900)) + curve((894, 650), (694, 600), (512, 650)),
              fill=cover)
    for side in (-1, 1):
        ox = 512 + side * 340
        top = curve((512, 620), (512 + side * 170, 560), (ox, 600))
        bot = curve((ox, 860), (512 + side * 170, 820), (512, 880))
        d.polygon(top + bot, fill=page)
        for k in range(4):
            y0 = 660 + k * 50
            pts = curve((512 + side * 50, y0 + 20), (512 + side * 170, y0 - 25), (ox - side * 50, y0 + 5))
            d.line(pts, fill=line, width=18, joint="curve")
    d.line([(512, 615), (512, 885)], fill=(205, 214, 244, 255), width=10)


def rocket():
    layer = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    cx = 512
    # полум'я
    d.polygon([(cx - 55, 430), (cx, 640), (cx + 55, 430)], fill=(250, 179, 135, 255))
    d.polygon([(cx - 30, 430), (cx, 560), (cx + 30, 430)], fill=(249, 226, 175, 255))
    # стабілізатори
    red = (243, 139, 168, 255)
    d.polygon([(cx - 70, 320), (cx - 140, 450), (cx - 70, 420)], fill=red)
    d.polygon([(cx + 70, 320), (cx + 140, 450), (cx + 70, 420)], fill=red)
    # корпус з носом
    body = curve((cx - 70, 230), (cx - 70, 120), (cx, 60)) + curve((cx, 60), (cx + 70, 120), (cx + 70, 230))
    body += [(cx + 70, 430), (cx - 70, 430)]
    d.polygon(body, fill=(205, 214, 244, 255))
    d.polygon(curve((cx - 52, 150), (cx - 30, 95), (cx, 60)) + curve((cx, 60), (cx + 30, 95), (cx + 52, 150)),
              fill=red)
    d.rectangle([cx - 8, 330, cx + 8, 430], fill=(243, 139, 168, 255))
    # ілюмінатор
    d.ellipse([cx - 40, 210, cx + 40, 290], fill=(255, 255, 255, 255))
    d.ellipse([cx - 27, 223, cx + 27, 277], fill=(116, 199, 236, 255))
    return layer.rotate(-28, resample=Image.BICUBIC, center=(512, 360), translate=(70, -10))


def master():
    img = background()
    r = rocket()
    glow = r.split()[3].filter(ImageFilter.GaussianBlur(30))
    img.alpha_composite(Image.merge("RGBA", (glow.point(lambda _: 250), glow.point(lambda _: 179),
                                             glow.point(lambda _: 135), glow.point(lambda a: a // 3))))
    book(img)
    img.alpha_composite(r)
    return img


def main():
    big = master()
    sizes = [256, 128, 64, 48, 32, 24, 16]
    imgs = [big.resize((s, s), Image.LANCZOS) for s in sizes]
    imgs[0].save(HERE / "studybooks.ico", sizes=[(s, s) for s in sizes], append_images=imgs[1:])
    prev = Image.new("RGBA", (256 + 128 + 64 + 48 + 32 + 16 + 70, 256), (40, 40, 40, 255))
    x = 0
    for im in imgs:
        prev.alpha_composite(im, (x, 0))
        x += im.width + 10
    prev.save(HERE / "studybooks-preview.png")


if __name__ == "__main__":
    main()
