from pathlib import Path
from typing import Tuple

from PIL import Image, ImageFont, ImageDraw


def get_digit_emoji(number: int) -> str:
    """Convert digit to emoji.

    :param number: Number from 0 to 9.
    :return: Emoji of that digit.
    """
    if number < 0 or number > 9:
        raise ValueError("Number must be between 0 and 9.")
    numbers = (
        "0️⃣",
        "1️⃣",
        "2️⃣",
        "3️⃣",
        "4️⃣",
        "5️⃣",
        "6️⃣",
        "7️⃣",
        "8️⃣",
        "9️⃣",
    )
    return numbers[number]


def generate_header(
    text: str,
    *,
    height: int = 160,
    width: int = 600,
    foreground: Tuple[int, int, int] = (229, 0, 43),
    background: Tuple[int, int, int, float] = (0, 0, 0, 0),
    fontsize: int = 90,
    lines: bool = True,
    line_thickness: int = 5,
) -> Image:
    image = Image.new("RGBA", (width, height), background)
    font_path = Path(__file__).parent / "font.pfb"
    font = ImageFont.truetype(str(font_path), 90)
    draw = ImageDraw.Draw(image)

    if lines:
        draw.line(
            (20, 20, image.size[0] - 20, 20),
            fill=foreground,
            width=line_thickness,
        )
        draw.line(
            (20, image.size[1] - 20, image.size[0] - 20, image.size[1] - 20),
            fill=foreground,
            width=line_thickness,
        )

    w, h = draw.textsize(text, font=font)
    draw.text(
        ((width - w) / 2, (height - h) / 2 - 10),
        text,
        font=font,
        fill=foreground,
    )

    return image
