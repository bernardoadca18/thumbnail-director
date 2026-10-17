"""Deterministic libvips pixels and Pango text; all photographic pixels are sources."""

from html import escape
from pathlib import Path

import pyvips
import yaml

from app.domain.contracts import RENDERER

PROFILES = yaml.safe_load(
    (Path(__file__).parents[2] / "config/channel_profiles.yaml").read_text()
)


def decode(data):
    image = pyvips.Image.new_from_buffer(data, "", access="random")
    if image.width * image.height > 100_000_000:
        raise ValueError("Source pixel limit exceeded")
    image = image.autorot().colourspace("srgb")
    return image.flatten(background=[0, 0, 0]) if image.hasalpha() else image


def crop(image, width, height, source, zoom=1.0):
    # Crop in source coordinates, then resize without changing aspect ratio.
    ratio = width / height
    cw = min(image.width, int(image.height * ratio))
    ch = min(image.height, int(image.width / ratio))
    cw, ch = max(1, int(cw / zoom)), max(1, int(ch / zoom))
    focused = image.smartcrop(cw, ch, interesting="attention")
    x = int(focused.get("xoffset")) * -1
    y = int(focused.get("yoffset")) * -1
    # libvips smartcrop offsets describe the selected source rectangle.
    source.crop_box = [x, y, cw, ch]
    source.transforms = {
        "crop_strategy": "attention",
        "target": [width, height],
        "zoom": zoom,
        "contrast": 1.04,
        "brightness": -2,
        "sharpen_sigma": 0.5,
    }
    return (
        focused.resize(width / cw, vscale=height / ch)
        .linear(1.04, -2)
        .cast("uchar")
        .sharpen(sigma=0.5)
    )


def text_layer(headline, profile, max_width):
    if not headline:
        return None, {"font_size": None, "lines": []}
    words = headline.upper().split()
    if len(words) > 5:
        raise ValueError("Headline too long")
    text = profile["text"]
    # Evaluate word-boundary breaks with actual Pango glyph extents, no guessed widths.
    options = [words] + [
        [" ".join(words[:i]), " ".join(words[i:])] for i in range(1, len(words))
    ]
    options[0] = [" ".join(words)]
    for size in range(text["max_size"], text["min_size"] - 1, -2):
        fits = []
        for lines in options:
            masks = [
                pyvips.Image.text(escape(line), font=f"{text['font']} {size}", dpi=72)
                for line in lines
            ]
            if (
                max(m.width for m in masks) <= max_width - 20
                and sum(m.height for m in masks) + 12 * (len(masks) - 1) <= 200
            ):
                fits.append((max(m.width for m in masks), lines, masks))
        if not fits:
            continue
        _, lines, masks = min(fits, key=lambda item: (len(item[1]), item[0]))
        layer = pyvips.Image.black(max_width, 224, bands=4).copy(interpretation="srgb")
        y = 10
        for mask in masks:
            x = (max_width - mask.width) // 2
            padded = mask.embed(6, 6, mask.width + 12, mask.height + 12)
            stroke = padded.rank(9, 9, 80)
            rgba = (
                stroke.new_from_image([5, 7, 12])
                .bandjoin(stroke)
                .copy(interpretation="srgb")
            )
            layer = layer.composite2(rgba, "over", x=x - 6, y=y - 6)
            rgba = (
                mask.new_from_image([255, 255, 255])
                .bandjoin(mask)
                .copy(interpretation="srgb")
            )
            layer = layer.composite2(rgba, "over", x=x, y=y)
            y += mask.height + 12
        return layer, {"font_size": size, "font": text["font"], "lines": lines}
    raise ValueError("Headline cannot fit at readable size")


def render(concept, materials, profile_id):
    profile = PROFILES[profile_id]
    sources = [s for _, s in materials]
    images = [decode(data) for data, _ in materials]
    if concept.template_id == "split_compare_v1":
        left = crop(images[0], 634, 720, sources[0])
        right = crop(images[1], 634, 720, sources[1])
        divider = pyvips.Image.black(12, 720, bands=3).new_from_image([255, 207, 51])
        canvas = left.join(divider, "horizontal").join(right, "horizontal")
        text_width, text_x = 1120, 80
    elif concept.template_id == "hero_focus_v1":
        canvas = crop(images[0], 1280, 720, sources[0])
        text_width, text_x = 720, 504
    else:
        canvas = crop(images[0], 1280, 720, sources[0], zoom=1.18)
        text_width, text_x = 920, 56
    if concept.headline:
        y = pyvips.Image.xyz(1280, 720)[1]
        alpha = ((y > 300).ifthenelse((y - 300) / 420, 0) * 210).cast("uchar")
        overlay = (
            pyvips.Image.black(1280, 720, bands=3)
            .bandjoin(alpha)
            .copy(interpretation="srgb")
        )
        canvas = canvas.composite2(overlay, "over")
    text, layout = text_layer(concept.headline, profile, text_width)
    if text is not None:
        canvas = canvas.composite2(text, "over", x=text_x, y=450)
    parameters = {
        "renderer_version": RENDERER,
        "libvips_version": ".".join(str(pyvips.version(i)) for i in range(3)),
        "profile": profile_id,
        "profile_config": profile,
        "text_layout": layout,
        "text_origin": [text_x, 450],
        "gradient": bool(concept.headline),
    }
    if canvas.hasalpha():
        canvas = canvas.flatten(background=[0, 0, 0])
    canvas = canvas.colourspace("srgb").cast("uchar")
    data = canvas.pngsave_buffer(compression=9, strip=True)
    return data, sources, parameters
