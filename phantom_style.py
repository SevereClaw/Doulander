"""
Оформление «Phantom» — по мотивам стиля Persona 5 Royal: красный, чёрный, белый,
острые углы, «вырезанные» буквы разного шрифта на разноцветных плашках.
Всё рисуется здесь с нуля (PIL), никаких файлов из игры.
"""
import math
import os
import random

from PIL import Image, ImageDraw, ImageFont

RED = "#E60012"
RED_HOVER = "#B3000E"
BLACK = "#0A0A0A"
BLACK2 = "#141414"
BLACK3 = "#1E1E1E"
WHITE = "#FFFFFF"
SOFT = "#BDBDBD"

# шрифты Windows для «вырезанных» букв (берём те, что есть)
LETTER_FONTS = ["impact.ttf", "ariblk.ttf", "timesbd.ttf", "georgiab.ttf", "bahnschrift.ttf",
                "arialbd.ttf", "verdanab.ttf", "trebucbd.ttf", "courbd.ttf"]
FALLBACK_FONTS = ["DejaVuSans-Bold.ttf", "DejaVuSerif-Bold.ttf", "DejaVuSansMono-Bold.ttf"]
TEXT_FONTS = ["bahnschrift.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"]


def _load(names, size):
    fonts = []
    for name in names:
        try:
            fonts.append(ImageFont.truetype(name, size))
        except OSError:
            pass
    return fonts


def _letter_fonts(size):
    fonts = _load(LETTER_FONTS, size)
    return fonts or _load(FALLBACK_FONTS, size) or [ImageFont.load_default()]


def _text_font(size):
    fonts = _load(TEXT_FONTS, size)
    return fonts[0] if fonts else ImageFont.load_default()


def _halftone(draw, box, step, max_r, color, direction=1):
    """Точечный растр: точки уменьшаются к одному краю."""
    x0, y0, x1, y1 = box
    w = x1 - x0
    y = y0
    row = 0
    while y < y1:
        x = x0 + (step / 2 if row % 2 else 0)
        while x < x1:
            k = (x - x0) / w if direction > 0 else (x1 - x) / w
            r = max_r * (1 - k)
            if r > 0.6:
                draw.ellipse((x - r, y - r, x + r, y + r), fill=color)
            x += step
        y += step * 0.87
        row += 1


def _ransom_letter(ch, font, scheme, angle):
    """Одна буква на плашке, повёрнутая под углом."""
    bg, fg = scheme
    l, t, r, b = font.getbbox(ch)
    w, h = r - l, b - t
    pad = max(6, int(h * 0.16))
    tile = Image.new("RGBA", (w + pad * 2, h + pad * 2), bg)
    d = ImageDraw.Draw(tile)
    d.text((pad - l, pad - t), ch, font=font, fill=fg)
    # белая обводка у тёмных плашек, чтобы читались на чёрном фоне
    if bg in (BLACK, BLACK2):
        d.rectangle((0, 0, tile.width - 1, tile.height - 1), outline=WHITE, width=3)
    return tile.rotate(angle, expand=True, resample=Image.BICUBIC)


def make_banner(title="DOWNLOADER", subtitle="", logo_path=None, scale=2, seed=7):
    """Шапка 640x96 (в логических пикселях). scale — для чётких экранов."""
    W, H = 640 * scale, 96 * scale
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    rnd = random.Random(seed)
    s = scale

    # красный осколок под названием и белая полоска
    d.polygon([(70 * s, 6 * s), (600 * s, 0), (575 * s, 66 * s), (52 * s, 74 * s)], fill=RED)
    d.polygon([(48 * s, 78 * s), (560 * s, 70 * s), (556 * s, 76 * s), (46 * s, 84 * s)],
              fill=WHITE)
    # растр по правому краю осколка
    _halftone(d, (430 * s, 4 * s, 600 * s, 66 * s), 9 * s, 3.4 * s, BLACK, direction=-1)

    # логотип
    x = 0
    if logo_path and os.path.isfile(logo_path):
        logo = Image.open(logo_path).convert("RGBA").resize((84 * s, 84 * s), Image.LANCZOS)
        logo = logo.rotate(8, expand=True, resample=Image.BICUBIC)
        img.alpha_composite(logo, (0, (H - logo.height) // 2))
        x = 86 * s

    # «вырезанные» буквы
    schemes = [(WHITE, BLACK), (BLACK, WHITE), (WHITE, RED), (BLACK2, WHITE), (WHITE, BLACK)]
    x += 6 * s
    prev = None
    for i, ch in enumerate(title):
        size = rnd.randint(30, 42) * s
        font = rnd.choice(_letter_fonts(size))
        scheme = rnd.choice([sc for sc in schemes if sc != prev])
        prev = scheme
        angle = rnd.uniform(-11, 11)
        tile = _ransom_letter(ch, font, scheme, angle)
        y = (H - tile.height) // 2 - 10 * s + rnd.randint(-5, 5) * s
        img.alpha_composite(tile, (int(x), max(0, y)))
        x += tile.width - rnd.randint(4, 9) * s

    # подзаголовок на чёрной косой плашке
    if subtitle:
        font = _text_font(13 * s)
        l, t, r, b = font.getbbox(subtitle)
        tw, th = r - l, b - t
        sx, sy = 100 * s, H - th - 10 * s
        d.polygon([(sx - 8 * s, sy - 5 * s), (sx + tw + 14 * s, sy - 7 * s),
                   (sx + tw + 10 * s, sy + th + 5 * s), (sx - 12 * s, sy + th + 6 * s)],
                  fill=BLACK)
        d.text((sx - l, sy - t), subtitle, font=font, fill=WHITE)
    return img


def make_title(text, size=17, scale=2):
    """Заголовок раздела: белый наклонный текст на красной косой плашке с белой «тенью»."""
    font = _text_font(size * scale)
    l, t, r, b = font.getbbox(text)
    tw, th = r - l, b - t
    h = th + 14 * scale
    skew = int(h * 0.38)
    pad = 14 * scale
    w = tw + pad * 2 + skew
    off = 4 * scale
    img = Image.new("RGBA", (w + off, h + off), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    shape = lambda dx, dy: [(skew + dx, dy), (w + dx, dy), (w - skew + dx, h + dy), (dx, h + dy)]  # noqa
    d.polygon(shape(off, off), fill=WHITE)
    d.polygon(shape(0, 0), fill=RED)
    # текст наклоняем как курсив
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(layer).text((pad + skew // 2 - l, (h - th) // 2 - t), text, font=font,
                               fill=WHITE)
    k = 0.22
    layer = layer.transform(layer.size, Image.AFFINE, (1, k, -k * h * 0.5, 0, 1, 0),
                            resample=Image.BICUBIC)
    img.alpha_composite(layer, (0, 0))
    return img


def draw_shards(canvas, width, height, corner="right"):
    """Красно-белые «осколки» на полоске-холсте (перерисовываются при изменении размера)."""
    canvas.delete("all")
    if width < 10:
        return
    if corner == "right":
        x = width
        for i, (w, col) in enumerate([(120, RED), (10, WHITE), (60, RED), (6, WHITE), (26, RED)]):
            canvas.create_polygon(x - w, 0, x, 0, x - height * 0.9, height,
                                  x - w - height * 0.9, height, fill=col, outline="")
            x -= w + 8 + i * 2
    else:   # тонкая полоса снизу: слева красный клин, справа белый штрих
        canvas.create_polygon(0, 0, width * 0.42, 0, width * 0.42 - height * 1.5, height,
                              0, height, fill=RED, outline="")
        canvas.create_polygon(width * 0.42 + 10, 0, width * 0.42 + 26, 0,
                              width * 0.42 + 26 - height * 1.5, height,
                              width * 0.42 + 10 - height * 1.5, height, fill=WHITE, outline="")
        canvas.create_polygon(width - 160, 0, width, 0, width, height,
                              width - 160 - height * 1.5, height, fill=RED, outline="")


def theme_overrides(font_family="Bahnschrift SemiBold"):
    """Цвета и формы для всех виджетов CustomTkinter."""
    R, RH, K, K2, K3, W = RED, RED_HOVER, BLACK, BLACK2, BLACK3, WHITE
    two = lambda c: [c, c]   # noqa: E731 — одинаково в светлой и тёмной теме
    return {
        "CTk": {"fg_color": two(K)},
        "CTkToplevel": {"fg_color": two(K)},
        "CTkFrame": {"corner_radius": 0, "border_width": 0, "fg_color": two(K2),
                     "top_fg_color": two(K3), "border_color": two(R)},
        "CTkButton": {"corner_radius": 0, "border_width": 0, "fg_color": two(R),
                      "hover_color": two(RH), "border_color": two(W), "text_color": two(W),
                      "text_color_disabled": two("#7A7A7A")},
        "CTkLabel": {"corner_radius": 0, "text_color": two(W)},
        "CTkEntry": {"corner_radius": 0, "border_width": 2, "fg_color": two(W),
                     "border_color": two(R), "text_color": two("#000000"),
                     "placeholder_text_color": two("#6E6E6E")},
        "CTkCheckBox": {"corner_radius": 0, "border_width": 2, "fg_color": two(R),
                        "border_color": two(W), "hover_color": two(RH),
                        "checkmark_color": two(W), "text_color": two(W),
                        "text_color_disabled": two("#7A7A7A")},
        "CTkSwitch": {"corner_radius": 0, "border_width": 2, "button_length": 6,
                      "fg_color": two("#3A3A3A"), "progress_color": two(R),
                      "button_color": two(W), "button_hover_color": two("#DDDDDD"),
                      "text_color": two(W), "text_color_disabled": two("#7A7A7A")},
        "CTkProgressBar": {"corner_radius": 0, "border_width": 0, "fg_color": two("#2A2A2A"),
                           "progress_color": two(R), "border_color": two(W)},
        "CTkOptionMenu": {"corner_radius": 0, "fg_color": two(W), "button_color": two("#E2E2E2"),
                          "button_hover_color": two("#FFC9CE"), "text_color": two("#000000"),
                          "text_color_disabled": two("#7A7A7A")},
        "DropdownMenu": {"fg_color": two(K2), "hover_color": two(R), "text_color": two(W)},
        "CTkSegmentedButton": {"corner_radius": 0, "border_width": 2, "fg_color": two(W),
                               "selected_color": two(R), "selected_hover_color": two(RH),
                               "unselected_color": two(K), "unselected_hover_color": two("#333333"),
                               "text_color": two(W), "text_color_disabled": two("#7A7A7A")},
        "CTkScrollbar": {"corner_radius": 0, "button_color": two(R),
                         "button_hover_color": two(RH)},
        "CTkScrollableFrame": {"label_fg_color": two(K3)},
        "CTkTextbox": {"corner_radius": 0, "border_width": 0, "fg_color": two("#101010"),
                       "text_color": two(W), "scrollbar_button_color": two(R),
                       "scrollbar_button_hover_color": two(RH)},
        "CTkFont": {"Windows": {"family": font_family}},
    }


def apply_theme(theme):
    """Применяет оформление к ThemeManager.theme (до создания окон)."""
    for widget, values in theme_overrides().items():
        target = theme.get(widget)
        if target is None:
            continue
        for key, value in values.items():
            if isinstance(value, dict) and isinstance(target.get(key), dict):
                target[key].update(value)
            elif key in target:
                target[key] = value


if __name__ == "__main__":        # предпросмотр: python phantom_style.py out.png
    import sys
    banner = make_banner(subtitle="ВИДЕО · МУЗЫКА · 1000+ САЙТОВ   v1.11.0",
                         logo_path=os.path.join(os.path.dirname(__file__), "icon.png"))
    titles = [make_title(t) for t in ("ОЧЕРЕДЬ", "ПРОГРАММА", "СЕТЬ — ЕСЛИ YOUTUBE С VPN")]
    H = banner.height + sum(t.height + 20 for t in titles) + 20
    bg = Image.new("RGBA", (banner.width, H), BLACK)
    bg.alpha_composite(banner)
    y = banner.height + 20
    for t in titles:
        bg.alpha_composite(t, (40, y))
        y += t.height + 20
    bg.save(sys.argv[1] if len(sys.argv) > 1 else "banner_preview.png")
