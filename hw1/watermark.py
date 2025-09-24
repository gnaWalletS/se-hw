from __future__ import annotations
import argparse
import os
from pathlib import Path
from typing import Optional, Tuple
from datetime import datetime

from PIL import Image, ImageDraw, ImageFont, ImageColor, ExifTags

try:
    import piexif
    _HAS_PIEXIF = True
except Exception:
    _HAS_PIEXIF = False


SUPPORTED_EXTS = {'.jpg', '.jpeg', '.png', '.tiff', '.tif', '.webp', '.bmp'}


def get_exif_date(path: Path) -> Optional[str]:
    """尝试从图片 EXIF 读取拍摄时间，返回 YYYY-MM-DD 字符串，若没有返回 None"""
    try:
        if _HAS_PIEXIF:
            exif_dict = piexif.load(str(path))
            # DateTimeOriginal 位于 ExifIFD 36867
            dto = None
            if piexif.ExifIFD.DateTimeOriginal in exif_dict.get('Exif', {}):
                dto = exif_dict['Exif'].get(piexif.ExifIFD.DateTimeOriginal)
            # fallback to 306 DateTime
            if not dto and piexif.ImageIFD.DateTime in exif_dict.get('0th', {}):
                dto = exif_dict['0th'].get(piexif.ImageIFD.DateTime)
            if dto:
                if isinstance(dto, bytes):
                    dto = dto.decode('utf-8', errors='ignore')
                # common format: "YYYY:MM:DD HH:MM:SS"
                try:
                    dt = datetime.strptime(dto, '%Y:%m:%d %H:%M:%S')
                    return dt.strftime('%Y-%m-%d')
                except Exception:
                    # try other parse attempts
                    try:
                        dt = datetime.fromisoformat(dto)
                        return dt.strftime('%Y-%m-%d')
                    except Exception:
                        return None
        else:
            img = Image.open(path)
            exif = img._getexif() or {}
            if not exif:
                return None
            # map tag ids
            tag_map = {v: k for k, v in ExifTags.TAGS.items()}
            for tag_name in ('DateTimeOriginal', 'DateTime'):
                tag_id = tag_map.get(tag_name)
                if tag_id and tag_id in exif:
                    val = exif[tag_id]
                    if isinstance(val, bytes):
                        val = val.decode('utf-8', errors='ignore')
                    try:
                        dt = datetime.strptime(val, '%Y:%m:%d %H:%M:%S')
                        return dt.strftime('%Y-%m-%d')
                    except Exception:
                        try:
                            dt = datetime.fromisoformat(val)
                            return dt.strftime('%Y-%m-%d')
                        except Exception:
                            return None
    except Exception:
        return None


def ensure_font(font_size: int) -> ImageFont.FreeTypeFont:
    """尝试加载系统 font，否则使用 PIL 默认字体（注意：默认字体不支持指定大小很好）"""
    # 常见位置尝试
    candidates = [
        '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
        '/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf',
        '/Library/Fonts/Arial.ttf',
        '/System/Library/Fonts/Supplemental/Arial.ttf',
    ]
    for p in candidates:
        try:
            if os.path.exists(p):
                return ImageFont.truetype(p, font_size)
        except Exception:
            continue
    # 最后回退到 PIL 内置字体
    try:
        return ImageFont.load_default()
    except Exception:
        raise RuntimeError('无法加载字体，请安装字体或指定字体路径。')


def parse_color(s: str) -> Tuple[int, int, int, int]:
    """接受颜色名字或 hex (#rrggbb 或 #rrggbbaa)，返回 RGBA 元组"""
    try:
        # PIL ImageColor.getrgb 支持 many formats
        rgb = ImageColor.getrgb(s)
        if len(rgb) == 3:
            return (rgb[0], rgb[1], rgb[2], 255)
        elif len(rgb) == 4:
            return rgb
    except Exception:
        pass
    # fallback white
    return (255, 255, 255, 255)


def measure_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont) -> Tuple[int, int]:
    # PIL >=8 推荐 textbbox
    try:
        bbox = draw.textbbox((0, 0), text, font=font)
        w = bbox[2] - bbox[0]
        h = bbox[3] - bbox[1]
        return w, h
    except Exception:
        return draw.textsize(text, font=font)


def place_text_position(img_size: Tuple[int, int], text_size: Tuple[int, int], position: str, margin: int = 10) -> Tuple[int, int]:
    iw, ih = img_size
    tw, th = text_size
    pos = position.lower()
    if pos == 'top-left' or pos == 'tl':
        return margin, margin
    if pos == 'top-right' or pos == 'tr':
        return iw - tw - margin, margin
    if pos == 'bottom-left' or pos == 'bl':
        return margin, ih - th - margin
    if pos == 'bottom-right' or pos == 'br':
        return iw - tw - margin, ih - th - margin
    if pos == 'center' or pos == 'c':
        return (iw - tw) // 2, (ih - th) // 2
    # default top-left
    return margin, margin


def draw_watermark(image: Image.Image, text: str, font_size: int, color: Tuple[int, int, int, int], position: str) -> Image.Image:
    # ensure RGBA
    if image.mode != 'RGBA':
        base = image.convert('RGBA')
    else:
        base = image.copy()

    txt_layer = Image.new('RGBA', base.size, (255, 255, 255, 0))
    draw = ImageDraw.Draw(txt_layer)
    font = ensure_font(font_size)
    text_w, text_h = measure_text(draw, text, font)
    x, y = place_text_position(base.size, (text_w, text_h), position)

    # Draw outline for readability: draw multiple offset texts with black-ish stroke
    outline_color = (0, 0, 0, 200)
    offsets = [(-1, -1), (-1, 1), (1, -1), (1, 1)]
    for ox, oy in offsets:
        draw.text((x + ox, y + oy), text, font=font, fill=outline_color)
    # main text
    draw.text((x, y), text, font=font, fill=color)

    # composite
    out = Image.alpha_composite(base, txt_layer)
    # convert back to original mode if needed
    if image.mode != 'RGBA':
        return out.convert(image.mode)
    return out


def process_image(path: Path, font_size: int, color: Tuple[int, int, int, int], position: str, out_dir: Path) -> Tuple[Path, bool, str]:
    """处理单张图片，返回 (输出路径, 成功标志, message)"""
    try:
        date = get_exif_date(path)
        if not date:
            # 若没有 EXIF 时间，则使用文件修改日期作为回退
            stat = path.stat()
            date = datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d')
        text = date
        img = Image.open(path)
        out_img = draw_watermark(img, text, font_size, color, position)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / path.name
        # 保持原格式
        out_img.save(out_path)
        return out_path, True, f'Saved -> {out_path}'
    except Exception as e:
        return path, False, f'Error: {e}'


def gather_targets(input_path: Path):
    if input_path.is_dir():
        for f in sorted(input_path.iterdir()):
            if f.is_file() and f.suffix.lower() in SUPPORTED_EXTS:
                yield f
    elif input_path.is_file():
        if input_path.suffix.lower() in SUPPORTED_EXTS:
            yield input_path
    else:
        return


def main():
    p = argparse.ArgumentParser(description='Photo Watermark CLI — vibe coding')
    p.add_argument('path', type=str, help='图片文件或目录路径')
    p.add_argument('--font-size', type=int, default=10, help='字体大小（默认 36）')
    p.add_argument('--color', type=str, default='#FFFFFF', help='字体颜色，名字或 hex，例如 #ffffff')
    p.add_argument('--position', type=str, default='bottom-right', help='位置: top-left/top-right/bottom-left/bottom-right/center')
    p.add_argument('--out-subdir', type=str, default='_watermark', help='输出子目录名字（默认 _watermark）')
    args = p.parse_args()

    input_path = Path(args.path).expanduser()
    if not input_path.exists():
        print('路径不存在:', input_path)
        return

    # determine base folder
    base_dir = input_path if input_path.is_dir() else input_path.parent
    out_dir = base_dir / args.out_subdir

    color = parse_color(args.color)

    targets = list(gather_targets(input_path))
    if not targets:
        print('没有发现支持的图片文件。支持扩展名：', ','.join(sorted(SUPPORTED_EXTS)))
        return

    print(f'Found {len(targets)} images. Output dir: {out_dir}')

    success = 0
    for t in targets:
        out_path, ok, msg = process_image(t, args.font_size, color, args.position, out_dir)
        print(t.name, '->', msg)
        if ok:
            success += 1

    print(f'Done. {success}/{len(targets)} processed.')


if __name__ == '__main__':
    main()
