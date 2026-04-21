#!/usr/bin/env python3
"""Generate Amazon KDP-ready localized covers for output/天机录.

The existing source cover art is portrait-oriented but too small and too tall
for the KDP-recommended eBook marketing cover. This script rebuilds a
1600x2560 RGB cover for each language using deterministic text rendering so
localized titles are not mangled by image generation.
"""

from __future__ import annotations

import json
import shutil
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont


ROOT = Path(__file__).resolve().parents[1]
BOOK_ROOT = ROOT / "output" / "天机录"
AMAZON_ROOT = BOOK_ROOT / "amazon"
SOURCE_ART = AMAZON_ROOT / "cover_assets" / "tianjilu-kdp-base-v2.png"

WIDTH = 1600
HEIGHT = 2560
DPI = (300, 300)


@dataclass(frozen=True)
class CoverText:
    lang: str
    title: str
    subtitle: str
    author: str
    title_font: Path
    body_font: Path
    serif_font: Path
    title_size: int
    subtitle_size: int
    author_size: int
    title_tracking: int
    title_lines: tuple[str, ...] | None = None
    subtitle_lines: tuple[str, ...] | None = None


FONT_DIR = Path("/System/Library/Fonts")
SUP_FONT_DIR = FONT_DIR / "Supplemental"
ASSET_FONT_DIR = Path("/System/Library/AssetsV2/com_apple_MobileAsset_Font8")


def first_existing(*paths: str | Path) -> Path:
    for path in paths:
        candidate = Path(path)
        if candidate.exists():
            return candidate
    raise FileNotFoundError("No usable font found from candidates")


PINGFANG = first_existing(
    ASSET_FONT_DIR
    / "86ba2c91f017a3749571a82f2c6d890ac7ffb2fb.asset"
    / "AssetData"
    / "PingFang.ttc",
    FONT_DIR / "STHeiti Medium.ttc",
)
SONGTI = first_existing(SUP_FONT_DIR / "Songti.ttc", FONT_DIR / "STHeiti Medium.ttc")
HIRAGINO_MINCHO = first_existing(FONT_DIR / "ヒラギノ明朝 ProN.ttc", FONT_DIR / "ヒラギノ角ゴシック W6.ttc")
HIRAGINO_SANS = first_existing(FONT_DIR / "ヒラギノ角ゴシック W6.ttc", FONT_DIR / "Hiragino Sans GB.ttc")
APPLE_MYUNGJO = first_existing(SUP_FONT_DIR / "AppleMyungjo.ttf", FONT_DIR / "AppleSDGothicNeo.ttc")
APPLE_GOTHIC = first_existing(FONT_DIR / "AppleSDGothicNeo.ttc", SUP_FONT_DIR / "AppleMyungjo.ttf")
TIMES_BOLD = first_existing(SUP_FONT_DIR / "Times New Roman Bold.ttf", FONT_DIR / "Times.ttc")
TIMES = first_existing(SUP_FONT_DIR / "Times New Roman.ttf", FONT_DIR / "Times.ttc")
HELVETICA = first_existing(FONT_DIR / "Helvetica.ttc", FONT_DIR / "HelveticaNeue.ttc")


def load_metadata(lang: str) -> dict:
    with (AMAZON_ROOT / lang / "metadata" / "book_metadata.json").open(encoding="utf-8") as handle:
        return json.load(handle)


def cover_specs() -> list[CoverText]:
    metadata = {lang: load_metadata(lang) for lang in ("zh", "en", "ja", "ko")}
    return [
        CoverText(
            lang="zh",
            title=metadata["zh"]["title"],
            subtitle=metadata["zh"]["subtitle"],
            author=metadata["zh"]["author"],
            title_font=SONGTI,
            body_font=PINGFANG,
            serif_font=SONGTI,
            title_size=250,
            subtitle_size=56,
            author_size=50,
            title_tracking=12,
        ),
        CoverText(
            lang="en",
            title=metadata["en"]["title"],
            subtitle=metadata["en"]["subtitle"],
            author=metadata["en"]["author"],
            title_font=TIMES_BOLD,
            body_font=HELVETICA,
            serif_font=TIMES,
            title_size=128,
            subtitle_size=42,
            author_size=46,
            title_tracking=5,
            title_lines=("THE HEAVEN'S", "ANNAL"),
            subtitle_lines=("A CULTIVATION SAGA OF", "FORESIGHT AND FATE"),
        ),
        CoverText(
            lang="ja",
            title=metadata["ja"]["title"],
            subtitle=metadata["ja"]["subtitle"],
            author=metadata["ja"]["author"],
            title_font=HIRAGINO_MINCHO,
            body_font=HIRAGINO_SANS,
            serif_font=HIRAGINO_MINCHO,
            title_size=238,
            subtitle_size=50,
            author_size=48,
            title_tracking=10,
        ),
        CoverText(
            lang="ko",
            title=metadata["ko"]["title"],
            subtitle=metadata["ko"]["subtitle"],
            author=metadata["ko"]["author"],
            title_font=APPLE_MYUNGJO,
            body_font=APPLE_GOTHIC,
            serif_font=APPLE_MYUNGJO,
            title_size=240,
            subtitle_size=50,
            author_size=48,
            title_tracking=2,
        ),
    ]


def font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(path), size=size)


def fill_resize(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    target_w, target_h = size
    scale = max(target_w / image.width, target_h / image.height)
    resized = image.resize((round(image.width * scale), round(image.height * scale)), Image.Resampling.LANCZOS)
    left = (resized.width - target_w) // 2
    top = (resized.height - target_h) // 2
    return resized.crop((left, top, left + target_w, top + target_h))


def fit_height(image: Image.Image, height: int) -> Image.Image:
    width = round(image.width * (height / image.height))
    return image.resize((width, height), Image.Resampling.LANCZOS)


def apply_vertical_gradient(base: Image.Image, top_alpha: int, bottom_alpha: int) -> None:
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    pixels = overlay.load()
    for y in range(HEIGHT):
        top = max(0, top_alpha - int(top_alpha * y / 760))
        bottom = int(bottom_alpha * max(0, y - 1280) / (HEIGHT - 1280))
        alpha = max(top, bottom)
        for x in range(WIDTH):
            pixels[x, y] = (0, 0, 0, min(255, alpha))
    base.alpha_composite(overlay)


def add_vignette(base: Image.Image) -> None:
    mask = Image.new("L", (WIDTH, HEIGHT), 0)
    draw = ImageDraw.Draw(mask)
    inset = -240
    draw.ellipse((inset, inset, WIDTH - inset, HEIGHT - inset), fill=255)
    mask = Image.eval(mask.filter(ImageFilter.GaussianBlur(180)), lambda value: 255 - value)
    overlay = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 115))
    overlay.putalpha(mask)
    base.alpha_composite(overlay)


def draw_tracking_text(
    layer: Image.Image,
    text: str,
    xy: tuple[int, int],
    text_font: ImageFont.FreeTypeFont,
    fill: tuple[int, int, int, int],
    tracking: int = 0,
    anchor: str = "la",
    stroke_width: int = 0,
    stroke_fill: tuple[int, int, int, int] = (0, 0, 0, 255),
) -> None:
    draw = ImageDraw.Draw(layer)
    x, y = xy
    if tracking <= 0:
        draw.text(xy, text, font=text_font, fill=fill, anchor=anchor, stroke_width=stroke_width, stroke_fill=stroke_fill)
        return

    total = text_width(draw, text, text_font, tracking)
    if anchor.startswith("m"):
        x -= total / 2
    elif anchor.startswith("r"):
        x -= total

    for char in text:
        draw.text((round(x), y), char, font=text_font, fill=fill, anchor="la", stroke_width=stroke_width, stroke_fill=stroke_fill)
        x += draw.textlength(char, font=text_font) + tracking


def text_width(draw: ImageDraw.ImageDraw, text: str, text_font: ImageFont.FreeTypeFont, tracking: int = 0) -> float:
    if not text:
        return 0
    return sum(draw.textlength(char, font=text_font) for char in text) + tracking * max(0, len(text) - 1)


def draw_centered(
    layer: Image.Image,
    text: str,
    center_x: int,
    y: int,
    text_font: ImageFont.FreeTypeFont,
    fill: tuple[int, int, int, int],
    tracking: int = 0,
    stroke_width: int = 0,
    stroke_fill: tuple[int, int, int, int] = (0, 0, 0, 255),
) -> None:
    draw_tracking_text(
        layer,
        text,
        (center_x, y),
        text_font,
        fill,
        tracking=tracking,
        anchor="ma",
        stroke_width=stroke_width,
        stroke_fill=stroke_fill,
    )


def draw_glow_text(
    layer: Image.Image,
    lines: tuple[str, ...],
    center_x: int,
    y: int,
    text_font: ImageFont.FreeTypeFont,
    fill: tuple[int, int, int, int],
    tracking: int,
    line_gap: int,
    stroke_width: int,
) -> int:
    glow = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    current_y = y
    for line in lines:
        draw_centered(glow, line, center_x, current_y, text_font, (255, 198, 74, 210), tracking, stroke_width + 5, (61, 28, 5, 230))
        bbox = ImageDraw.Draw(glow).textbbox((center_x, current_y), line, font=text_font, anchor="ma", stroke_width=stroke_width)
        current_y += (bbox[3] - bbox[1]) + line_gap
    glow = glow.filter(ImageFilter.GaussianBlur(8))
    layer.alpha_composite(glow)

    current_y = y
    for line in lines:
        draw_centered(layer, line, center_x, current_y, text_font, fill, tracking, stroke_width, (18, 10, 3, 245))
        bbox = ImageDraw.Draw(layer).textbbox((center_x, current_y), line, font=text_font, anchor="ma", stroke_width=stroke_width)
        current_y += (bbox[3] - bbox[1]) + line_gap
    return current_y


def build_background() -> Image.Image:
    src = Image.open(SOURCE_ART).convert("RGB")
    src = ImageEnhance.Color(src).enhance(1.18)
    src = ImageEnhance.Contrast(src).enhance(1.12)

    background = fill_resize(src, (WIDTH, HEIGHT)).filter(ImageFilter.GaussianBlur(18))
    background = ImageEnhance.Brightness(background).enhance(0.72).convert("RGBA")

    foreground = fit_height(src, HEIGHT).convert("RGBA")
    mask = Image.new("L", foreground.size, 255)
    mask_pixels = mask.load()
    fade = 95
    for x in range(foreground.width):
        edge_alpha = min(255, int(255 * min(x, foreground.width - 1 - x) / fade))
        for y in range(HEIGHT):
            mask_pixels[x, y] = edge_alpha
    x = (WIDTH - foreground.width) // 2
    background.paste(foreground, (x, 0), mask)
    apply_vertical_gradient(background, top_alpha=205, bottom_alpha=230)
    add_vignette(background)

    # Thin print-safe border, useful if Amazon displays the cover on a light page.
    border = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    d = ImageDraw.Draw(border)
    d.rectangle((14, 14, WIDTH - 15, HEIGHT - 15), outline=(202, 164, 84, 160), width=3)
    d.rectangle((26, 26, WIDTH - 27, HEIGHT - 27), outline=(255, 234, 177, 70), width=1)
    background.alpha_composite(border)
    return background


def draw_cover_text(base: Image.Image, spec: CoverText) -> None:
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    title_font = font(spec.title_font, spec.title_size)
    subtitle_font = font(spec.body_font, spec.subtitle_size)
    author_font = font(spec.body_font, spec.author_size)

    title_lines = spec.title_lines or (spec.title,)
    title_y = 260 if spec.lang != "en" else 230
    title_fill = (255, 235, 169, 255)
    next_y = draw_glow_text(
        layer,
        title_lines,
        WIDTH // 2,
        title_y,
        title_font,
        title_fill,
        spec.title_tracking,
        line_gap=26 if spec.lang == "en" else 34,
        stroke_width=5,
    )

    ornament_y = next_y + 16
    d = ImageDraw.Draw(layer)
    d.line((470, ornament_y, 700, ornament_y), fill=(214, 173, 90, 190), width=3)
    d.line((900, ornament_y, 1130, ornament_y), fill=(214, 173, 90, 190), width=3)
    d.ellipse((770, ornament_y - 10, 830, ornament_y + 10), outline=(244, 215, 140, 210), width=3)

    subtitle_lines = spec.subtitle_lines or (spec.subtitle,)
    sub_y = ornament_y + 46
    for line in subtitle_lines:
        draw_centered(layer, line, WIDTH // 2, sub_y, subtitle_font, (244, 235, 211, 238), tracking=1, stroke_width=2, stroke_fill=(0, 0, 0, 220))
        bbox = d.textbbox((WIDTH // 2, sub_y), line, font=subtitle_font, anchor="ma", stroke_width=2)
        sub_y += (bbox[3] - bbox[1]) + 18

    author_label = spec.author.upper() if spec.lang == "en" else spec.author
    draw_centered(layer, author_label, WIDTH // 2, 2328, author_font, (244, 236, 218, 235), tracking=3 if spec.lang == "en" else 6, stroke_width=2, stroke_fill=(0, 0, 0, 210))

    base.alpha_composite(layer)


def save_cover(image: Image.Image, spec: CoverText) -> dict:
    out_dir = AMAZON_ROOT / spec.lang / "cover"
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = {
        "zh": "tianjilu",
        "en": "the-heavens-annal",
        "ja": "tenkiroku",
        "ko": "cheongirok",
    }[spec.lang]
    png_path = out_dir / f"{slug}-kdp-cover-v2-1600x2560.png"
    jpg_path = out_dir / f"{slug}-kdp-cover-v2-1600x2560.jpg"
    rgb = image.convert("RGB")
    rgb.save(png_path, dpi=DPI)
    rgb.save(jpg_path, quality=92, optimize=True, progressive=True, dpi=DPI)
    return {
        "language": spec.lang,
        "title": spec.title,
        "subtitle": spec.subtitle,
        "author": spec.author,
        "jpg": str(jpg_path.relative_to(ROOT)),
        "png": str(png_path.relative_to(ROOT)),
        "dimensions": [WIDTH, HEIGHT],
        "dpi": list(DPI),
        "color_mode": "RGB",
        "jpg_size_bytes": jpg_path.stat().st_size,
        "source_art": str(SOURCE_ART.relative_to(ROOT)),
    }


def epub_path_for(spec: CoverText) -> Path:
    return AMAZON_ROOT / spec.lang / "epub" / f"{spec.title}.epub"


def sync_epub_cover(spec: CoverText, cover_jpg: Path) -> dict:
    epub_path = epub_path_for(spec)
    if not epub_path.exists():
        return {"language": spec.lang, "epub": str(epub_path.relative_to(ROOT)), "updated": False, "reason": "missing epub"}

    backup_path = epub_path.with_suffix(".before-v2-cover.epub")
    if not backup_path.exists():
        shutil.copy2(epub_path, backup_path)

    temp_path = epub_path.with_suffix(".tmp.epub")
    with zipfile.ZipFile(epub_path, "r") as zin:
        infos = zin.infolist()
        names = {info.filename for info in infos}
        if "EPUB/content.opf" not in names:
            raise ValueError(f"{epub_path} has no EPUB/content.opf")
        if "EPUB/cover.xhtml" not in names:
            raise ValueError(f"{epub_path} has no EPUB/cover.xhtml")

        opf = zin.read("EPUB/content.opf").decode("utf-8")
        cover_xhtml = zin.read("EPUB/cover.xhtml").decode("utf-8")
        opf = opf.replace('href="cover.png" id="cover-img" media-type="image/png"', 'href="cover.jpg" id="cover-img" media-type="image/jpeg"')
        opf = opf.replace('href="cover.png" id="cover-img" media-type="image/png" properties="cover-image"', 'href="cover.jpg" id="cover-img" media-type="image/jpeg" properties="cover-image"')
        opf = opf.replace('href="cover.png"', 'href="cover.jpg"')
        opf = opf.replace('media-type="image/png" properties="cover-image"', 'media-type="image/jpeg" properties="cover-image"')
        modified_start = opf.find('<meta property="dcterms:modified">')
        if modified_start >= 0:
            modified_end = opf.find("</meta>", modified_start) + len("</meta>")
            modified_meta = f'<meta property="dcterms:modified">{datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}</meta>'
            opf = opf[:modified_start] + modified_meta + opf[modified_end:]
        cover_xhtml = cover_xhtml.replace('src="cover.png"', 'src="cover.jpg"')

        with zipfile.ZipFile(temp_path, "w") as zout:
            for info in infos:
                if info.filename in {"EPUB/cover.png", "EPUB/cover.jpg", "EPUB/content.opf", "EPUB/cover.xhtml"}:
                    continue
                data = zin.read(info.filename)
                if info.filename == "mimetype":
                    zout.writestr(info, data, compress_type=zipfile.ZIP_STORED)
                else:
                    zout.writestr(info, data)

            def write_entry(name: str, data: bytes | str, compress_type: int = zipfile.ZIP_DEFLATED) -> None:
                zip_info = zipfile.ZipInfo(name)
                zip_info.compress_type = compress_type
                if isinstance(data, str):
                    data = data.encode("utf-8")
                zout.writestr(zip_info, data)

            write_entry("EPUB/content.opf", opf)
            write_entry("EPUB/cover.xhtml", cover_xhtml)
            write_entry("EPUB/cover.jpg", cover_jpg.read_bytes())

    temp_path.replace(epub_path)
    return {
        "language": spec.lang,
        "epub": str(epub_path.relative_to(ROOT)),
        "updated": True,
        "embedded_cover": "EPUB/cover.jpg",
        "backup": str(backup_path.relative_to(ROOT)),
    }


def write_manifest(records: list[dict]) -> None:
    manifest_path = AMAZON_ROOT / "cover_manifest.json"
    manifest = {
        "kind": "Amazon KDP eBook marketing cover set",
        "generated_from": str(SOURCE_ART.relative_to(ROOT)),
        "target": {
            "dimensions": [WIDTH, HEIGHT],
            "aspect_ratio_height_to_width": 1.6,
            "dpi": list(DPI),
            "color_mode": "RGB",
            "preferred_upload_format": "JPEG",
            "max_recommended_file_size_bytes": 5_000_000,
        },
        "covers": records,
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_preview_sheet(records: list[dict]) -> None:
    thumb_w, thumb_h = 300, 480
    pad = 34
    label_h = 58
    sheet = Image.new("RGB", (pad + len(records) * (thumb_w + pad), pad * 2 + thumb_h + label_h), (24, 24, 24))
    draw = ImageDraw.Draw(sheet)
    label_font = font(HELVETICA, 28)
    for index, record in enumerate(records):
        cover_path = ROOT / record["jpg"]
        thumb = Image.open(cover_path).convert("RGB").resize((thumb_w, thumb_h), Image.Resampling.LANCZOS)
        x = pad + index * (thumb_w + pad)
        y = pad
        sheet.paste(thumb, (x, y))
        draw.text((x + thumb_w // 2, y + thumb_h + 18), record["language"], font=label_font, fill=(235, 235, 235), anchor="ma")
    sheet.save(AMAZON_ROOT / "cover_preview_contact_sheet.jpg", quality=92, optimize=True)


def main() -> None:
    if not SOURCE_ART.exists():
        raise FileNotFoundError(SOURCE_ART)

    records = []
    epub_records = []
    for spec in cover_specs():
        cover = build_background()
        draw_cover_text(cover, spec)
        record = save_cover(cover, spec)
        records.append(record)
        epub_records.append(sync_epub_cover(spec, ROOT / record["jpg"]))
    write_manifest(records)
    write_preview_sheet(records)
    for record in records:
        print(f"{record['language']}: {record['jpg']} ({record['jpg_size_bytes']} bytes)")
    for record in epub_records:
        status = "updated" if record["updated"] else f"skipped: {record['reason']}"
        print(f"{record['language']} epub: {status} {record['epub']}")


if __name__ == "__main__":
    main()
