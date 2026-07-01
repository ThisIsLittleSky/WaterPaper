#!/usr/bin/env python3
"""
HTML 科研图表渲染工具

将 HTML 图表文件通过 Playwright 渲染为高分辨率 PNG 图像，用于插入论文。

依赖：pip install playwright && playwright install chromium

用法：
    # 渲染单个文件
    python render_html_chart.py chart.html -o chart.png

    # 批量渲染
    python render_html_chart.py charts/fig1.html charts/fig2.html -o charts/

    # 指定宽度和 DPR
    python render_html_chart.py chart.html -o chart.png --width 800 --dpr 2
"""

import argparse
import os
import sys
from pathlib import Path


def _auto_crop_png(image_path: str, margin: int = 8, threshold: int = 250):
    """Trim whitespace around a PNG image using PIL.

    Crops to the bounding box of non-white (or near-white) pixels,
    with a small margin preserved.
    """
    try:
        from PIL import Image
    except ImportError:
        return

    im = Image.open(image_path)
    if im.mode == "RGBA":
        bg = Image.new("RGB", im.size, (255, 255, 255))
        bg.paste(im, mask=im.split()[3])
        im = bg
    elif im.mode != "RGB":
        im = im.convert("RGB")

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
        return

    left = max(0, left - margin)
    top = max(0, top - margin)
    right = min(w - 1, right + margin)
    bottom = min(h - 1, bottom + margin)

    cropped = im.crop((left, top, right + 1, bottom + 1))
    cropped.save(image_path)
    cropped.close()
    im.close()


def check_playwright():
    """检查 Playwright 是否已安装"""
    try:
        from playwright.sync_api import sync_playwright
        return True
    except ImportError:
        return False


def render_html_to_png(
    html_path: str,
    output_path: str = None,
    width: int = 1200,
    dpr: float = 2.0,
    full_page: bool = True,
    timeout: int = 15000,
    tight_crop: bool = True,
    target_width: int = 1200,
) -> str:
    """
    将 HTML 文件渲染为 PNG 图片

    Args:
        html_path: HTML 文件路径
        output_path: 输出 PNG 路径（默认与 HTML 同名）
        width: 视口宽度（逻辑像素）
        dpr: 设备像素比（2.0 = Retina 级别高清）
        full_page: 是否截取完整页面高度
        timeout: 页面加载超时（毫秒）
        tight_crop: 是否紧贴内容裁剪（去掉页面空白，但保留标题和注释）
        target_width: 目标 SVG 逻辑宽度（仅 tight_crop=True 时生效）

    Returns:
        输出文件的绝对路径
    """
    if not output_path:
        output_path = Path(html_path).with_suffix(".png")

    html_path = os.path.abspath(html_path)
    output_path = os.path.abspath(output_path)

    # 确保输出目录存在
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(
            args=["--no-sandbox", "--disable-setuid-sandbox"]
        )
        # 初始 viewport 设大一些避免内容被提前截断
        context = browser.new_context(
            viewport={"width": width, "height": 900},
            device_scale_factor=dpr,
        )
        page = context.new_page()

        # 使用 file:// 协议加载本地 HTML
        html_path_normalized = html_path.replace(os.sep, '/')
        if os.name == "nt":
            file_url = f"file:///{html_path_normalized}"
        else:
            file_url = f"file:///{html_path_normalized}"

        page.goto(file_url, wait_until="networkidle", timeout=timeout)

        if tight_crop:
            # 若有 SVG 且目标宽度更大，先缩放 SVG 以提升分辨率
            try:
                page.wait_for_selector("svg", timeout=timeout)
            except Exception:
                pass

            svg = page.query_selector("svg")
            if svg:
                page.evaluate(
                    f"""(function() {{
                    var svg = document.querySelector('svg');
                    if (svg) {{
                        var box = svg.getBoundingClientRect();
                        var origW = box.width;
                        var origH = box.height;
                        // 无 viewBox 的 SVG 不能直接改 width/height，否则内部坐标错乱
                        // 先注入 viewBox 保留原始坐标系
                        if (!svg.getAttribute('viewBox') && origW > 0 && origH > 0) {{
                            svg.setAttribute('viewBox', '0 0 ' + origW + ' ' + origH);
                        }}
                        if (origW < {target_width}) {{
                            var ratio = origH / origW;
                            var newH = Math.round({target_width} * ratio);
                            svg.setAttribute('width', '{target_width}');
                            svg.setAttribute('height', newH);
                            svg.style.width = '{target_width}px';
                            svg.style.height = newH + 'px';
                        }}
                    }}
                }})()"""
                )
                page.wait_for_timeout(300)

            # 确保 viewport 足够大以容纳缩放后的内容
            page.set_viewport_size({
                "width": max(width, target_width + 100),
                "height": max(200, page.evaluate("document.body.scrollHeight") + 100),
            })
            page.wait_for_timeout(100)

            # 全页截图后用 PIL 精确裁剪白边
            page.screenshot(path=output_path, full_page=True)
            browser.close()
            _auto_crop_png(output_path, margin=8)
            mode = "tight" if svg else "tight(table)"
            print(f"[OK] 已渲染({mode}): {html_path} → {output_path}")
            return output_path

        # tight_crop=False：传统全页截图
        page.screenshot(path=output_path, full_page=True)
        browser.close()

    print(f"[OK] 已渲染: {html_path} → {output_path}")
    return output_path


def render_html_string_to_png(
    html_content: str,
    output_path: str,
    width: int = 1200,
    dpr: float = 2.0,
    timeout: int = 15000,
    tight_crop: bool = True,
    target_width: int = 1200,
) -> str:
    """
    将 HTML 字符串渲染为 PNG 图片

    Args:
        html_content: HTML 字符串
        output_path: 输出 PNG 路径
        width: 视口宽度
        dpr: 设备像素比
        timeout: 超时（毫秒）
        tight_crop: 是否紧贴 SVG 裁剪
        target_width: 目标 SVG 逻辑宽度

    Returns:
        输出文件的绝对路径
    """
    import tempfile

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".html", delete=False, encoding="utf-8"
    ) as f:
        f.write(html_content)
        tmp_path = f.name

    try:
        result = render_html_to_png(tmp_path, output_path, width, dpr,
                                    timeout=timeout, tight_crop=tight_crop,
                                    target_width=target_width)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    return result


def batch_render(
    input_dir: str,
    output_dir: str = None,
    width: int = 1200,
    dpr: float = 2.0,
    tight_crop: bool = True,
    target_width: int = 1200,
) -> list:
    """批量渲染目录下所有 HTML 文件"""
    if output_dir is None:
        output_dir = input_dir

    os.makedirs(output_dir, exist_ok=True)

    html_files = list(Path(input_dir).glob("*.html"))
    results = []

    for html_file in html_files:
        out_name = html_file.with_suffix(".png").name
        out_path = os.path.join(output_dir, out_name)
        try:
            result = render_html_to_png(str(html_file), out_path, width=width, dpr=dpr,
                                        tight_crop=tight_crop, target_width=target_width)
            results.append(result)
        except Exception as e:
            print(f"[ERROR] 渲染失败 {html_file}: {e}", file=sys.stderr)

    print(f"\n[DONE] 完成 {len(results)}/{len(html_files)} 个文件的渲染")
    return results


def generate_bar_chart_html(
    title: str,
    categories: list,
    values: list,
    x_label: str = "",
    y_label: str = "",
    source_note: str = "",
    filename: str = "chart.html",
) -> str:
    """生成柱状图 HTML 文件"""

    import json as _json

    svg_width = 700
    svg_height = 400
    margin = {"top": 20, "right": 10, "bottom": 60, "left": 70}
    chart_w = svg_width - margin["left"] - margin["right"]
    chart_h = svg_height - margin["top"] - margin["bottom"]

    if not values:
        return ""

    max_val = max(values) * 1.15
    bar_w = min(60, chart_w / len(categories) - 20)
    gap = chart_w / len(categories)

    bars = []
    for i, (cat, val) in enumerate(zip(categories, values)):
        x = margin["left"] + i * gap + (gap - bar_w) / 2
        bar_h = (val / max_val) * chart_h
        y = margin["top"] + chart_h - bar_h
        bars.append(f"""
        <rect x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="{bar_h:.1f}"
              fill="#2E86AB" rx="2" class="bar"/>
        <text x="{x + bar_w/2:.1f}" y="{y - 6:.1f}" text-anchor="middle"
              font-size="12" fill="#333">{val}</text>
        <text x="{x + bar_w/2:.1f}" y="{margin['top'] + chart_h + 16:.1f}"
              text-anchor="middle" font-size="11" fill="#555">{cat}</text>""")

    html = f"""<!DOCTYPE html>
<html lang="zh">
<head><meta charset="UTF-8">
<style>
  body {{ font-family: 'SimSun', 'Microsoft YaHei', 'Times New Roman', serif; margin: 0; padding: 0; }}
  .title {{ text-align: center; font-size: 14px; font-weight: bold; margin: 20px 0 10px; }}
  .svg-container {{ text-align: center; }}
  .source {{ text-align: center; font-size: 10px; color: #888; margin-top: 5px; }}
</style></head>
<body>
<div class="title">{title}</div>
<div class="svg-container">
<svg width="{svg_width}" height="{svg_height}">
  <line x1="{margin['left']}" y1="{margin['top'] + chart_h}"
        x2="{margin['left'] + chart_w}" y2="{margin['top'] + chart_h}"
        stroke="#aaa" stroke-width="1"/>
  <line x1="{margin['left']}" y1="{margin['top']}"
        x2="{margin['left']}" y2="{margin['top'] + chart_h}"
        stroke="#aaa" stroke-width="1"/>
  <text x="{margin['left'] - 8}" y="{margin['top'] + chart_h/2}"
        text-anchor="middle" transform="rotate(-90, {margin['left'] - 8}, {margin['top'] + chart_h/2})"
        font-size="12">{y_label}</text>
  <text x="{margin['left'] + chart_w/2}" y="{svg_height - 10}"
        text-anchor="middle" font-size="12">{x_label}</text>
  {''.join(bars)}
</svg>
</div>
<div class="source">{source_note}</div>
</body></html>"""

    with open(filename, "w", encoding="utf-8") as f:
        f.write(html)
    return filename


def main():
    parser = argparse.ArgumentParser(description="HTML 图表 → PNG 渲染工具")
    parser.add_argument("inputs", nargs="+", help="HTML 文件路径或目录")
    parser.add_argument("--output", "-o", help="输出路径（单文件）或输出目录（批量）")
    parser.add_argument("--width", "-w", type=int, default=1200, help="视口宽度（默认 1200）")
    parser.add_argument("--dpr", type=float, default=2.0, help="设备像素比（默认 2.0 = Retina）")
    parser.add_argument("--batch", "-b", action="store_true", help="批量模式：输入为目录")
    parser.add_argument("--timeout", type=int, default=15000, help="加载超时 ms（默认 15000）")
    parser.add_argument("--tight", action="store_true", default=True,
                        help="紧贴 SVG 元素裁剪，去掉页面空白（默认开启）")
    parser.add_argument("--no-tight", dest="tight", action="store_false",
                        help="关闭紧贴裁剪，使用 full_page 截图")
    parser.add_argument("--target-width", type=int, default=1200,
                        help="目标 SVG 宽度（逻辑像素，默认 1200，仅 --tight 时生效）")

    args = parser.parse_args()

    if not check_playwright():
        print("[ERROR] 未安装 Playwright。请运行：", file=sys.stderr)
        print("  pip install playwright", file=sys.stderr)
        print("  playwright install chromium", file=sys.stderr)
        sys.exit(1)

    if args.batch or (len(args.inputs) == 1 and os.path.isdir(args.inputs[0])):
        # 批量模式
        input_dir = args.inputs[0]
        output_dir = args.output or input_dir
        batch_render(input_dir, output_dir, width=args.width, dpr=args.dpr,
                     tight_crop=args.tight, target_width=args.target_width)
    else:
        # 单文件模式
        for input_path in args.inputs:
            if not os.path.exists(input_path):
                print(f"[ERROR] 文件不存在: {input_path}", file=sys.stderr)
                continue
            out = args.output if args.output and len(args.inputs) == 1 else None
            render_html_to_png(input_path, out, width=args.width, dpr=args.dpr,
                              timeout=args.timeout, tight_crop=args.tight,
                              target_width=args.target_width)


if __name__ == "__main__":
    main()
