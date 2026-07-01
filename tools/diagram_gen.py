#!/usr/bin/env python3
"""Diagram generation for AIGC-Detector Skill.

Generates PNG images from Mermaid diagram text using the mmdc CLI.

Commands:
  generate [--input <file>] --output <file> [--theme <theme>] [--bg <color>]
"""

import sys
import os
import subprocess
import tempfile


def _auto_crop_png(image_path: str, margin: int = 5, threshold: int = 250):
    """Trim whitespace around a PNG image using PIL.

    Crops to the bounding box of non-white (or near-white) pixels,
    with a small margin preserved.
    """
    try:
        from PIL import Image
    except ImportError:
        return  # PIL not available, skip cropping

    im = Image.open(image_path)
    if im.mode == "RGBA":
        # Composite onto white background
        bg = Image.new("RGB", im.size, (255, 255, 255))
        bg.paste(im, mask=im.split()[3])
        im = bg
    elif im.mode != "RGB":
        im = im.convert("RGB")

    # Find bounding box of non-white pixels
    # White = all channels >= threshold
    pixels = im.load()
    w, h = im.size
    left, top, right, bottom = w, h, 0, 0

    for y in range(h):
        for x in range(w):
            r, g, b = pixels[x, y]
            if r < threshold or g < threshold or b < threshold:
                if x < left:
                    left = x
                if x > right:
                    right = x
                if y < top:
                    top = y
                if y > bottom:
                    bottom = y

    if left > right or top > bottom:
        im.close()
        return  # image is all white, skip

    # Apply margin
    left = max(0, left - margin)
    top = max(0, top - margin)
    right = min(w - 1, right + margin)
    bottom = min(h - 1, bottom + margin)

    cropped = im.crop((left, top, right + 1, bottom + 1))
    cropped.save(image_path)
    cropped.close()
    im.close()


def check_mmdc() -> str:
    """Return path to mmdc or exit with install instructions."""
    result = subprocess.run(["which", "mmdc"], capture_output=True, text=True)
    if result.returncode == 0:
        return result.stdout.strip()
    # Check common locations
    for candidate in [
        os.path.expanduser("~/.npm-global/bin/mmdc"),
        os.path.expanduser("~/node_modules/.bin/mmdc"),
        "/usr/local/bin/mmdc",
    ]:
        if os.path.exists(candidate):
            return candidate
    print("Error: mmdc (Mermaid CLI) not found.", file=sys.stderr)
    print("Install with: npm install -g @mermaid-js/mermaid-cli", file=sys.stderr)
    sys.exit(1)


def generate(mermaid_text: str, output_path: str, theme: str = "default", bg: str = "white") -> str:
    """Generate PNG from Mermaid text using mmdc."""
    mmdc_path = check_mmdc()

    # Ensure output directory exists
    out_dir = os.path.dirname(output_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    # Write mermaid text to temp file (mmdc reads from file)
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".mmd", delete=False)
    try:
        tmp.write(mermaid_text)
        tmp.close()

        cmd = [
            mmdc_path,
            "-i", tmp.name,
            "-o", output_path,
            "-w", "1600",
            "-b", bg,
            "-t", theme,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

        if result.returncode != 0:
            print(f"Error: mmdc failed:\n{result.stderr}", file=sys.stderr)
            sys.exit(1)

        if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            print("Error: mmdc produced no output.", file=sys.stderr)
            sys.exit(1)

        _auto_crop_png(output_path)

        print(output_path)
        return output_path
    finally:
        os.unlink(tmp.name)


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 diagram_gen.py generate [--input <file>] --output <file> "
              "[--theme default|dark|forest|neutral] [--bg <color>]", file=sys.stderr)
        sys.exit(1)

    command = sys.argv[1]
    if command != "generate":
        print(f"Error: unknown command '{command}'. Use 'generate'.", file=sys.stderr)
        sys.exit(1)

    # Parse args
    args = sys.argv[2:]
    input_path = None
    output_path = None
    theme = "default"
    bg = "white"

    i = 0
    while i < len(args):
        if args[i] == "--input" and i + 1 < len(args):
            input_path = args[i + 1]
            i += 2
        elif args[i] == "--output" and i + 1 < len(args):
            output_path = args[i + 1]
            i += 2
        elif args[i] == "--theme" and i + 1 < len(args):
            theme = args[i + 1]
            i += 2
        elif args[i] == "--bg" and i + 1 < len(args):
            bg = args[i + 1]
            i += 2
        else:
            print(f"Error: unknown argument '{args[i]}'", file=sys.stderr)
            sys.exit(1)

    if not output_path:
        print("Error: --output is required.", file=sys.stderr)
        sys.exit(1)

    # Read mermaid text
    if input_path:
        with open(input_path, "r", encoding="utf-8") as f:
            mermaid_text = f.read()
    else:
        mermaid_text = sys.stdin.read()

    if not mermaid_text.strip():
        print("Error: empty mermaid text.", file=sys.stderr)
        sys.exit(1)

    generate(mermaid_text, output_path, theme, bg)


if __name__ == "__main__":
    main()
