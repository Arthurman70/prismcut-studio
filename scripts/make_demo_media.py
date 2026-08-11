"""Create demo media (images / audio / video) for screenshots & manual testing."""
import math
import struct
import subprocess
import shutil
import wave
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

OUT = Path(__file__).resolve().parent.parent / "demo_media"
OUT.mkdir(exist_ok=True)


def sunset(path: Path, w=1600, h=900):
    im = Image.new("RGB", (w, h))
    top, bottom = (18, 24, 66), (255, 132, 64)
    px = im.load()
    for y in range(h):
        t = y / h
        px_row = tuple(int(top[i] + (bottom[i] - top[i]) * (t ** 1.3)) for i in range(3))
        for x in range(w):
            px[x, y] = px_row
    d = ImageDraw.Draw(im, "RGBA")
    d.ellipse([w * 0.42, h * 0.38, w * 0.58, h * 0.66], fill=(255, 214, 140, 255))
    im = im.filter(ImageFilter.GaussianBlur(2))
    d = ImageDraw.Draw(im, "RGBA")
    d.rectangle([0, h * 0.72, w, h], fill=(10, 12, 20, 255))
    for i, (bx, bw, bh) in enumerate([(0.05, 0.08, 0.30), (0.16, 0.06, 0.22), (0.25, 0.10, 0.38),
                                      (0.38, 0.07, 0.26), (0.48, 0.09, 0.34), (0.60, 0.06, 0.20),
                                      (0.69, 0.11, 0.42), (0.83, 0.08, 0.28), (0.93, 0.06, 0.36)]):
        x0, y0 = w * bx, h * 0.72 - h * bh
        d.rectangle([x0, y0, x0 + w * bw, h * 0.72], fill=(16, 18, 30, 255))
        for wy in range(int(y0) + 10, int(h * 0.72) - 6, 18):
            for wx in range(int(x0) + 6, int(x0 + w * bw) - 6, 16):
                if (wx * wy + i) % 5 < 2:
                    d.rectangle([wx, wy, wx + 6, wy + 8], fill=(255, 200, 110, 230))
    im.save(path, quality=92)


def portrait(path: Path, w=1200, h=1500):
    im = Image.new("RGB", (w, h), (44, 48, 56))
    d = ImageDraw.Draw(im)
    for y in range(h):
        c = int(40 + 30 * y / h)
        d.line([(0, y), (w, y)], fill=(c, c + 4, c + 10))
    d.ellipse([w * 0.30, h * 0.16, w * 0.70, h * 0.50], fill=(224, 178, 148))     # face
    d.ellipse([w * 0.38, h * 0.28, w * 0.44, h * 0.33], fill=(52, 60, 72))        # eyes
    d.ellipse([w * 0.56, h * 0.28, w * 0.62, h * 0.33], fill=(52, 60, 72))
    d.arc([w * 0.42, h * 0.36, w * 0.58, h * 0.46], 20, 160, fill=(150, 96, 84), width=8)
    d.polygon([(w * 0.24, h), (w * 0.50, h * 0.52), (w * 0.76, h)], fill=(64, 96, 128))  # body
    im = im.filter(ImageFilter.GaussianBlur(1))
    im.save(path, quality=92)


def chord_wav(path: Path, seconds=8.0, rate=44100):
    freqs = [220.0, 277.18, 329.63]
    n = int(seconds * rate)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        frames = bytearray()
        for i in range(n):
            t = i / rate
            env = min(1.0, t / 0.05) * math.exp(-0.25 * t)
            v = sum(math.sin(2 * math.pi * f * t) * 0.28 for f in freqs) * env
            v += 0.05 * math.sin(2 * math.pi * 2.0 * t)
            frames += struct.pack("<h", int(max(-1, min(1, v)) * 32000))
        w.writeframes(bytes(frames))


def test_video(path: Path):
    ff = shutil.which("ffmpeg")
    if not ff:
        return
    subprocess.run([ff, "-y", "-v", "error", "-f", "lavfi",
                    "-i", "testsrc2=size=1280x720:rate=30:duration=4",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    str(path)], check=False, timeout=120)


if __name__ == "__main__":
    sunset(OUT / "sunset_city.jpg")
    portrait(OUT / "portrait.jpg")
    chord_wav(OUT / "ambient_chord.wav")
    test_video(OUT / "test_clip.mp4")
    print("demo media in", OUT)
