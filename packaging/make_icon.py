"""Generate the app icon (prism triangle splitting light) - keeps binary blobs
out of the repo. Run before PyInstaller: python packaging/make_icon.py"""
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).parent


def draw(size: int) -> Image.Image:
    im = Image.new("RGBA", (size, size), (30, 33, 36, 255))
    d = ImageDraw.Draw(im)
    s = size
    # prism
    tri = [(s * 0.50, s * 0.16), (s * 0.20, s * 0.74), (s * 0.80, s * 0.74)]
    d.polygon(tri, fill=(38, 44, 52, 255), outline=(210, 216, 222, 255),
              width=max(1, s // 48))
    # incoming beam
    d.line([(s * 0.02, s * 0.52), (s * 0.40, s * 0.47)],
           fill=(235, 238, 240, 255), width=max(2, s // 26))
    # split beams
    for i, color in enumerate([(239, 83, 80), (255, 167, 38), (255, 238, 88),
                               (102, 187, 106), (41, 182, 246), (171, 71, 188)]):
        y = s * (0.30 + 0.09 * i)
        d.line([(s * 0.58, s * 0.50), (s * 0.98, y)], fill=color + (255,),
               width=max(2, s // 30))
    return im


def main() -> None:
    sizes = [16, 24, 32, 48, 64, 128, 256]
    imgs = [draw(sz) for sz in sizes]
    imgs[-1].save(HERE / "icon.png")
    imgs[-1].save(HERE / "icon.ico", sizes=[(sz, sz) for sz in sizes])
    print("wrote", HERE / "icon.ico")


if __name__ == "__main__":
    main()
