#!/usr/bin/env python3
"""
论文字数统计工具

统计中文论文的字数：
- 中文字符 + 标点 = 1 字
- 英文单词 = 1 字（按空格/token 估算）
- 数字序列 = 按实际位数
- 排除 Markdown 标记和代码块

用法：
    python count_words.py paper.md
    python count_words.py paper.md --chapters
"""

import argparse
import re
import sys


def strip_markdown(text: str) -> str:
    """移除 Markdown 标记，保留纯文本"""
    # 移除代码块
    text = re.sub(r'```[\s\S]*?```', '', text)
    # 移除行内代码
    text = re.sub(r'`[^`]+`', '', text)
    # 移除图片
    text = re.sub(r'!\[.*?\]\(.*?\)', '', text)
    # 移除链接，保留文字
    text = re.sub(r'\[([^\]]*)\]\([^)]+\)', r'\1', text)
    # 移除引用标记 >
    text = re.sub(r'^>\s?', '', text, flags=re.MULTILINE)
    # 移除 HTML 注释
    text = re.sub(r'<!--.*?-->', '', text, flags=re.DOTALL)
    # 移除粗体/斜体标记 ** __ * _
    text = re.sub(r'\*{1,3}|_{1,3}', '', text)
    # 移除水平线
    text = re.sub(r'^[-*_]{3,}\s*$', '', text, flags=re.MULTILINE)
    # 移除多余的空白
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def count_words(text: str) -> int:
    """统计中英文混合文本的字数"""
    text = strip_markdown(text)

    # 分离中文和英文部分
    # 中文字符（包括中文标点）
    chinese_chars = len(re.findall(r'[一-鿿　-〿＀-￯]', text))

    # 英文单词（连续字母序列）
    english_words = len(re.findall(r'[a-zA-Z]+', text))

    # 数字（连续数字序列算 1 字）
    numbers = len(re.findall(r'\d+', text))

    return chinese_chars + english_words + numbers


def count_chapters(markdown_text: str) -> list:
    """按章节统计字数"""
    chapters = []
    # 匹配一级标题
    pattern = r'(?:(?:^|\n)#\s+.+?\n)'
    sections = re.split(pattern, "\n" + markdown_text)

    # 找到所有标题
    headers = re.findall(r'^#\s+(.+?)$', markdown_text, re.MULTILINE)

    # 引言前的部分是"前置"（摘要等）
    pre = sections[0] if sections else ""
    if pre.strip():
        chapters.append(("前置（摘要/Abstract）", count_words(pre)))

    for i, (header, section) in enumerate(zip(headers, sections[1:]), 1):
        wc = count_words(section)
        chapters.append((header, wc))

    return chapters


def main():
    parser = argparse.ArgumentParser(description="论文中文/英文混合字数统计")
    parser.add_argument("file", help="Markdown 文件路径")
    parser.add_argument("--chapters", "-c", action="store_true", help="按章节分别统计")
    parser.add_argument("--plain", "-p", help="统计纯文本文件")

    args = parser.parse_args()

    if args.plain:
        with open(args.plain, "r", encoding="utf-8") as f:
            text = f.read()
        print(f"字数: {count_words(text)}")
        return

    with open(args.file, "r", encoding="utf-8") as f:
        content = f.read()

    if args.chapters:
        print(f"文件: {args.file}")
        print("-" * 50)
        total = 0
        chapters = count_chapters(content)
        for name, wc in chapters:
            print(f"  {name:30s} {wc:>6} 字")
            total += wc
        print("-" * 50)
        print(f"  {'总计':30s} {total:>6} 字")
    else:
        wc = count_words(content)
        print(f"文件: {args.file}")
        print(f"字数: {wc}")


if __name__ == "__main__":
    main()
