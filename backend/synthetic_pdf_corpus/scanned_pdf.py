from __future__ import annotations

from io import BytesIO

_IMAGE_WIDTH = 1240
_IMAGE_HEIGHT = 1754
_LEFT_MARGIN = 70
_TOP_MARGIN = 80
_LINE_HEIGHT = 42
_FONT_SIZE = 28


def generate_scanned_pdf(pages: tuple[tuple[str, ...], ...]) -> bytes:
    from PIL import Image, ImageDraw, ImageFont

    images = []
    try:
        font = _load_font(ImageFont)
        for lines in pages:
            image = Image.new("RGB", (_IMAGE_WIDTH, _IMAGE_HEIGHT), "white")
            draw = ImageDraw.Draw(image)
            y_position = _TOP_MARGIN
            for line in lines:
                draw.text((_LEFT_MARGIN, y_position), line, fill="black", font=font)
                y_position += _LINE_HEIGHT
            images.append(image)

        output = BytesIO()
        images[0].save(
            output,
            format="PDF",
            resolution=150,
            save_all=True,
            append_images=images[1:],
        )
        return output.getvalue()
    finally:
        for image in images:
            image.close()


def _load_font(image_font_module):
    try:
        return image_font_module.truetype("DejaVuSans.ttf", _FONT_SIZE)
    except OSError:
        return image_font_module.load_default()
