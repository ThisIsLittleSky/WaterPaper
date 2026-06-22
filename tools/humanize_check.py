#!/usr/bin/env python3
"""降AI效果验证脚本 — 检测论文中的 AI 生成痕迹。

自包含脚本，纯标准库依赖。可独立使用或集成到 WaterPaper 工作流。

检测项：
  - 句长分布标准差（过低 → AI 单峰钟形分布）
  - AI 高频连接词密度
  - 长横线分隔符（强 AI 信号）
  - humanize_matrix.md 覆盖度（如有）

用法：
  python tools/humanize_check.py paper.md --markdown
  python tools/humanize_check.py paper.md --json
  python tools/humanize_check.py paper.md --write  # 写入 humanize_report.md
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from dataclasses import dataclass, field
from pathlib import Path


# ---------------------------------------------------------------------------
# AI 模式检测词库（整合 thesis-optimizer 三级词汇体系）
# ---------------------------------------------------------------------------

# 红色高风险词 — 出现即警告
AI_CONNECTORS_RED = [
    "首先", "其次", "再次", "最后",
    "综上所述", "总而言之", "总的来说",
    "此外", "另外", "不仅如此",
    "值得注意的是", "需要指出的是", "不容忽视的是",
    "具有重要意义", "具有重要的理论意义", "具有重要的现实意义",
    "实现了良好效果", "具有较高价值", "具有重要的参考价值",
    "为……奠定基础", "在……的过程中",
    # thesis-optimizer 补充
    "至关重要", "不可或缺", "极其重要",
    "深远影响", "革命性突破",
    "深入探讨", "全面分析", "系统梳理",
    "充分体现", "淋漓尽致地展现",
    "宝贵的经验", "充满活力的",
    "在这一背景下", "在当今时代背景下",
    "随着……的快速发展", "随着……的不断深入",
    "具有深远意义", "发挥至关重要的作用",
]

# 黄色中风险词 — 密度过高时警告
AI_CONNECTORS_YELLOW = [
    "然而", "因此", "鉴于此", "基于此",
    "揭示了", "阐明了",
    "构建", "打造", "提供",
    "值得注意的是", "有必要指出",
]

# 综合连接词列表（兼容原有逻辑）
AI_CONNECTORS_ZH = AI_CONNECTORS_RED + AI_CONNECTORS_YELLOW

AI_PATTERNS_ZH = [
    # 原有
    (r"首先.{1,30}其次.{1,30}再次.{1,30}最后", "序数词连环（首先→其次→再次→最后）"),
    (r"不仅.{1,20}而且", "不仅……而且……句型"),
    (r"既.{1,15}又", "既……又……句型"),
    (r"随着.{1,30}的(快速|不断|持续)发展", "随着……的发展 套话"),
    (r"在当今.{1,20}(时代|社会)背景下", "在当今……背景下 套话"),
    (r"具有重要的.{1,20}(意义|价值)", "具有重要的……意义/价值 套话"),
    # thesis-optimizer 补充 — 内容模式
    (r"标志着.{1,30}(重要|关键|新)转折", "C01 夸大意义——'标志着……转折'"),
    (r"有学者指(出|出，).{0,30}(?!\[\d)", "C02 模糊归因——'有学者指出'无具体引用"),
    (r"从而(凸显|彰显|体现)了", "C06 肤浅分析结尾——'从而凸显了……'"),
    (r"为未来.{1,20}(发展|研究)指(明|出)了方向", "C07 通用积极结论"),
    # thesis-optimizer 补充 — 语言模式
    (r"(高效|鲁棒|直观)(、|，|且)(高效|鲁棒|直观)(、|，|且)", "L04 三段式法则——形容词并列"),
    (r"(全面|客观|科学)(、|，|且)(全面|客观|科学)(、|，|且)", "L04 三段式法则——形容词并列"),
    (r"不可否认的是", "L07 填充短语——'不可否认的是'"),
    # thesis-optimizer 补充 — 结构模式
    (r"\(\s*1\s*\).{1,50}\(\s*2\s*\).{1,50}\(\s*3\s*\)", "S01 规整编号排比 (1)(2)(3)"),
    (r"接下来.{1,10}(本章|本节|我们)将(介绍|探讨|讨论)", "S04 生硬路标过渡——'接下来本章将……'"),
    # thesis-optimizer 补充 — 语气模式
    (r"(随着|伴随).{1,20}(发展|进步|深入|推进)", "T02 万能宣告式开头——'随着…的发展'"),
    (r"根据(目前|现有|近年).{1,15}(资料|研究|实践)", "T03 知识截止免责腔调"),
    # thesis-optimizer 补充 — 格式模式
    (r"[—]{3,}", "F01 长横线分隔符（强 AI 信号）"),
    (r"——.{1,50}——.{1,50}——", "F01 破折号过度使用"),
]

AI_PATTERNS_EN = [
    (r"firstly.{5,50}secondly.{5,50}finally", "firstly/secondly/finally chain"),
    (r"it is worth noting that", "it is worth noting that"),
    (r"has significant implications", "has significant implications"),
    (r"plays a crucial role", "plays a crucial role"),
]

# 术语保护 — 检测被通俗化/错误翻译的专业术语
# 注意：标准中文术语（如"卷积神经网络""循环神经网络"）是正常表述，不在此列
# 以下仅匹配明确错误的翻译变体
TERM_PROTECTION_VIOLATIONS = [
    (r"变换器模型", "Transformer 可能被误改为'变换器模型'（应保留英文原名）"),
    (r"自我关注(机制|力)", "Self-Attention 可能被误改为'自我关注'（应保留英文原名）"),
    (r"时序推测", "'时间序列预测'可能被误改为'时序推测'"),
    (r"知识浓缩", "'知识蒸馏'可能被误改为'知识浓缩'"),
    (r"梯度下撤", "'梯度下降'可能被误改为'梯度下撤'"),
    (r"预训练.{0,5}(生成|变换)器", "GPT/BERT 等模型名可能被过度翻译"),
]

DETECTION_DIMS = (
    "sentence structure", "paragraph similarity", "information density",
    "connector frequency", "term-context matching",
)

DETECTION_DIMS_ZH = (
    "句长分布", "段落结构", "信息密度",
    "连接词频率", "术语语境",
)


# ---------------------------------------------------------------------------
# tunable thresholds
# ---------------------------------------------------------------------------

MIN_PARAGRAPH_CHARS = 50        # 短于此的段落不参与覆盖率计算
MIN_COVERAGE_RATIO = 0.5        # 矩阵行数/段落数 最低比例
COVERAGE_MIN_PARAGRAPHS = 2     # 段落数超过此值才检查覆盖率
SENTENCE_MIN_CHARS = 5          # 句长下限
SENTENCE_MAX_CHARS = 300        # 句长上限
MIN_SENTENCE_LENGTH_STDDEV = 6  # 句长标准差低于此值 → AI 信号
MAX_CONNECTOR_DENSITY = 8       # 每千字连接词超过此值 → AI 信号
MAX_SHORT_SENTENCE_RATIO = 0.10 # 短句比例低于此值 → AI 信号
MIN_SHORT_SENTENCE_RATIO = 0.15 # 短句比例至少达到此值（medium 档）


# ---------------------------------------------------------------------------
# data structures
# ---------------------------------------------------------------------------

@dataclass
class HumanizeCheckResult:
    path: str
    ok: bool = True
    matrix_rows: int = 0
    manuscript_paragraphs: int = 0
    coverage_ratio: float = 0.0
    sentence_length_stddev: float = 0.0
    short_sentence_ratio: float = 0.0
    connector_count: int = 0
    connector_density: float = 0.0
    connector_red: int = 0
    connector_yellow: int = 0
    connector_red_detail: dict = field(default_factory=dict)
    connector_yellow_detail: dict = field(default_factory=dict)
    term_violations: list[str] = field(default_factory=list)
    density_flat_runs: list[int] = field(default_factory=list)
    findings: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# text analysis helpers
# ---------------------------------------------------------------------------

def sentence_lengths(text: str) -> list[int]:
    """提取所有有效句子的长度"""
    sents = re.split(r"[.。!！?？;；\n]+", text)
    return [
        len(s.strip())
        for s in sents
        if SENTENCE_MIN_CHARS < len(s.strip()) < SENTENCE_MAX_CHARS
    ]


def count_connectors(text: str, lang: str = "zh") -> int:
    """统计 AI 高频连接词出现次数"""
    pool = AI_CONNECTORS_ZH if lang == "zh" else []
    return sum(text.count(c) for c in pool)


def find_ai_patterns(text: str, lang: str = "zh") -> list[str]:
    """扫描 AI 高频句式模式"""
    patterns = AI_PATTERNS_ZH if lang == "zh" else AI_PATTERNS_EN
    found = []
    for pat, desc in patterns:
        if re.search(pat, text):
            found.append(desc)
    return found


def check_term_protection(text: str) -> list[str]:
    """检测术语是否被误改为通俗化表述"""
    violations = []
    for pat, desc in TERM_PROTECTION_VIOLATIONS:
        if re.search(pat, text):
            violations.append(f"术语保护: {desc}")
    return violations


def count_connectors_risk(text: str, lang: str = "zh") -> dict:
    """按风险等级分类统计连接词命中"""
    if lang != "zh":
        return {"red": 0, "yellow": 0, "total": 0}
    red_hits = {}
    yellow_hits = {}
    for c in AI_CONNECTORS_RED:
        n = text.count(c)
        if n > 0:
            red_hits[c] = n
    for c in AI_CONNECTORS_YELLOW:
        n = text.count(c)
        if n > 0:
            yellow_hits[c] = n
    return {
        "red": sum(red_hits.values()),
        "yellow": sum(yellow_hits.values()),
        "total": sum(red_hits.values()) + sum(yellow_hits.values()),
        "red_detail": red_hits,
        "yellow_detail": yellow_hits,
    }


def check_info_density(text: str) -> dict | None:
    """检查连续段落信息密度波动（实验性）"""
    paragraphs = split_paragraphs(text)
    if len(paragraphs) < 4:
        return None
    densities = []
    for p in paragraphs:
        chars = len(p)
        if chars == 0:
            densities.append(0)
            continue
        # 2026-06: 跳过参考文献段 — 结构相同，密度自然均匀
        if re.match(r'^\s*\[\d+\]', p):
            densities.append(-1)  # sentinel, excluded from flat-run check
            continue
        proper_nouns = len(re.findall(r'[A-Z]{2,}', p))
        # 2026-06: 密度公式不变，中文专有名词通过 CAPS 缩写间接覆盖（NR-V2X, URLLC 等）
        numbers = len(re.findall(r'\d+', p))
        refs = len(re.findall(r'\[\d+(?:[,，\s]*\d+)*\]', p))
        density = (proper_nouns * 3 + numbers * 2 + refs * 2) / (chars / 100)
        densities.append(round(density, 1))
    flat_runs = []
    for i in range(len(densities) - 2):
        # 2026-06: 阈值从 2 放宽到 3，降低误报；跳过含 sentinel(-1) 的窗口
        if -1 in densities[i:i+3]:
            continue
        if max(densities[i:i+3]) - min(densities[i:i+3]) < 3:
            flat_runs.append(i + 1)
    if flat_runs:
        return {"densities": densities, "flat_runs": flat_runs}
    return None


def split_paragraphs(text: str) -> list[str]:
    """将文本按空行拆分为段落"""
    parts = re.split(r"\n\s*\n+", text)
    return [
        re.sub(r"\s+", " ", p).strip()
        for p in parts
        if len(p.strip()) > MIN_PARAGRAPH_CHARS
    ]


def strip_markdown(text: str) -> str:
    """移除 Markdown 标记保留纯文本"""
    text = re.sub(r'```[\s\S]*?```', '', text)
    text = re.sub(r'`[^`]+`', '', text)
    text = re.sub(r'!\[.*?\]\(.*?\)', '', text)
    text = re.sub(r'\[([^\]]*)\]\([^)]+\)', r'\1', text)
    text = re.sub(r'<!--.*?-->', '', text, flags=re.DOTALL)
    text = re.sub(r'\*{1,3}|_{1,3}', '', text)
    text = re.sub(r'^>\s?', '', text, flags=re.MULTILINE)
    text = re.sub(r'^#+\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


# ---------------------------------------------------------------------------
# table parsing (for humanize_matrix.md)
# ---------------------------------------------------------------------------

def _split_table_line(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _is_sep(cells: list[str]) -> bool:
    return bool(cells) and all(c and set(c) <= {"-", ":", " "} for c in cells)


def _table_rows(text: str) -> tuple[list[str], list[list[str]]]:
    rows: list[list[str]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not (line.startswith("|") and line.endswith("|")):
            continue
        cells = _split_table_line(line)
        if _is_sep(cells):
            continue
        rows.append(cells)
    return (rows[0], rows[1:]) if rows else ([], [])


# ---------------------------------------------------------------------------
# main check logic
# ---------------------------------------------------------------------------

def check_text(text: str, lang: str = "zh",
               matrix_path: Path = None) -> HumanizeCheckResult:
    """对论文文本执行完整的 AI 痕迹检测"""
    result = HumanizeCheckResult("input text", True)

    plain_text = strip_markdown(text)

    # -- 段落统计 --
    paragraphs = split_paragraphs(plain_text)
    result.manuscript_paragraphs = len(paragraphs)

    # -- D1: 句长分布 --
    lengths = sentence_lengths(plain_text)
    if len(lengths) > 2:
        result.sentence_length_stddev = round(statistics.stdev(lengths), 2)
        if result.sentence_length_stddev < MIN_SENTENCE_LENGTH_STDDEV:
            result.findings.append(
                f"D1 句长标准差 = {result.sentence_length_stddev}（阈值 ≥ {MIN_SENTENCE_LENGTH_STDDEV}）"
                f" — 句长过于均匀，呈现 AI 单峰钟形分布特征。"
                f"建议：增加短句（≤10字）和长句（≥35字）。"
            )
            result.ok = False

        short_count = sum(1 for l in lengths if l <= 12)
        result.short_sentence_ratio = round(short_count / len(lengths), 2) if lengths else 0
        if result.short_sentence_ratio < MAX_SHORT_SENTENCE_RATIO:
            result.findings.append(
                f"D1 短句比例 = {result.short_sentence_ratio:.0%}（阈值 ≥ {MAX_SHORT_SENTENCE_RATIO:.0%}）"
                f" — 短句太少，AI 文本通常缺少碎片化短句。"
            )
            result.ok = False
    else:
        result.warnings.append("文本过短，无法分析句长分布")

    # -- D4: 连接词密度 --
    char_count = len(plain_text)
    conn_count = count_connectors(plain_text, lang)
    result.connector_count = conn_count
    if char_count > 0:
        result.connector_density = round(conn_count / (char_count / 1000), 2)
        if result.connector_density > MAX_CONNECTOR_DENSITY:
            result.findings.append(
                f"D4 连接词密度 = {result.connector_density}/千字（阈值 ≤ {MAX_CONNECTOR_DENSITY}/千字）"
                f" — 检测到 {conn_count} 个 AI 高频连接词。"
                f"建议：删除'首先/其次/最后''综上所述'等套话，用自然逻辑过渡。"
            )
            result.ok = False

    # -- D4 增强: 三级词汇风险报告 --
    risk = count_connectors_risk(plain_text, lang)
    result.connector_red = risk["red"]
    result.connector_yellow = risk["yellow"]
    result.connector_red_detail = risk.get("red_detail", {})
    result.connector_yellow_detail = risk.get("yellow_detail", {})
    if risk["red"] > 0:
        red_words = ", ".join(f"{w}×{c}" for w, c in risk.get("red_detail", {}).items())
        result.findings.append(
            f"D4 红色高风险词: {risk['red']} 次 — {red_words}。"
            f"建议删除或替换这些词汇。"
        )
        result.ok = False
    if risk["yellow"] > 3:
        yellow_words = ", ".join(f"{w}×{c}" for w, c in risk.get("yellow_detail", {}).items())
        result.warnings.append(
            f"D4 黄色中风险词: {risk['yellow']} 次 — {yellow_words}。"
            f"密度较高，建议分散或替换部分词汇。"
        )

    # -- AI 高频句式扫描 --
    pattern_hits = find_ai_patterns(plain_text, lang)
    for p in pattern_hits:
        result.findings.append(f"AI 句式检测: {p}")
        result.ok = False

    # -- 长横线检测 --
    dash_match = re.search(r"[—\-—―]{3,}", plain_text)
    if dash_match:
        result.findings.append(
            "检测到长横线分隔符（如'————'）。这是强 AI 生成信号，"
            "请用空白行或章节标题替代。"
        )
        result.ok = False

    # -- 术语保护检查 --
    term_violations = check_term_protection(plain_text)
    if term_violations:
        result.term_violations = term_violations
        for v in term_violations:
            result.findings.append(v)
        result.ok = False

    # -- D3 增强: 信息密度波动检查 --
    density_result = check_info_density(plain_text)
    if density_result and density_result["flat_runs"]:
        result.density_flat_runs = density_result["flat_runs"]
        result.findings.append(
            f"D3 信息密度波动不足：段落 {density_result['flat_runs']} "
            f"附近连续段落密度过于均匀，呈现 AI 文本特征。"
            f"建议：增加过渡段拉大密度差异，形成'高-低-高'交替。"
        )
        result.ok = False

    # -- humanize_matrix.md 覆盖度检查（如有） --
    if matrix_path and matrix_path.exists():
        matrix_text = matrix_path.read_text(encoding="utf-8", errors="ignore")
        header, rows = _table_rows(matrix_text)
        if header:
            result.matrix_rows = len(rows)
            if result.manuscript_paragraphs > 0:
                result.coverage_ratio = result.matrix_rows / result.manuscript_paragraphs
            if (
                result.coverage_ratio < MIN_COVERAGE_RATIO
                and result.manuscript_paragraphs > COVERAGE_MIN_PARAGRAPHS
            ):
                result.findings.append(
                    f"矩阵覆盖度 = {result.coverage_ratio:.0%}（阈值 ≥ {MIN_COVERAGE_RATIO:.0%}）"
                    f" — {result.matrix_rows} 行 / {result.manuscript_paragraphs} 段。"
                )
                result.ok = False

            header_text = " ".join(c.lower() for c in header)
            required_cols = ("ai pattern", "detection dim", "severity", "applied change")
            for col in required_cols:
                if col not in header_text:
                    result.findings.append(f"矩阵缺少必填列: {col}")
                    result.ok = False

            empty_rows = [
                i for i, row in enumerate(rows, start=1)
                if any(not c.strip() for c in row)
            ]
            if empty_rows:
                result.findings.append(
                    f"矩阵存在空单元格的行: {empty_rows[:8]}"
                )
                result.ok = False
        else:
            result.warnings.append("humanize_matrix.md 无有效表格")

    return result


# ---------------------------------------------------------------------------
# output formatters
# ---------------------------------------------------------------------------

def to_markdown(result: HumanizeCheckResult) -> str:
    icon = "PASS" if result.ok else "FAIL"
    lines = [
        "# 降AI检查报告",
        "",
        f"**状态**: {icon}",
        "",
        "## 统计指标",
        "",
        f"| 指标 | 值 | 阈值 | 状态 |",
        f"|------|----|------|------|",
    ]

    def _cell(v, threshold, compare: str):
        if compare == "gte":
            ok = v >= threshold
        elif compare == "lte":
            ok = v <= threshold
        else:
            ok = True
        status = "OK" if ok else "WARN"
        return f"| {v} | {threshold} | {status} |"

    # 句长标准差
    sl_ok = result.sentence_length_stddev >= MIN_SENTENCE_LENGTH_STDDEV
    lines.append(
        f"| 句长标准差 | {result.sentence_length_stddev} | "
        f"≥ {MIN_SENTENCE_LENGTH_STDDEV} | {'OK' if sl_ok else 'WARN'} |"
    )

    # 短句比例
    sr_ok = result.short_sentence_ratio >= MAX_SHORT_SENTENCE_RATIO
    lines.append(
        f"| 短句比例 | {result.short_sentence_ratio:.0%} | "
        f"≥ {MAX_SHORT_SENTENCE_RATIO:.0%} | {'OK' if sr_ok else 'WARN'} |"
    )

    # 连接词密度
    cd_ok = result.connector_density <= MAX_CONNECTOR_DENSITY
    lines.append(
        f"| 连接词密度 | {result.connector_density}/千字 | "
        f"≤ {MAX_CONNECTOR_DENSITY}/千字 | {'OK' if cd_ok else 'WARN'} |"
    )

    # 覆盖度
    if result.matrix_rows > 0:
        cov_ok = result.coverage_ratio >= MIN_COVERAGE_RATIO
        lines.append(
            f"| 矩阵覆盖度 | {result.coverage_ratio:.0%} | "
            f"≥ {MIN_COVERAGE_RATIO:.0%} | {'OK' if cov_ok else 'WARN'} |"
        )

    lines.append("")
    lines.append(f"- 段落数: {result.manuscript_paragraphs}")
    lines.append(f"- 矩阵行数: {result.matrix_rows}")
    lines.append(f"- 连接词总命中: {result.connector_count}")
    lines.append(f"  - 红色高风险词: {result.connector_red}")
    lines.append(f"  - 黄色中风险词: {result.connector_yellow}")
    if result.term_violations:
        lines.append(f"- 术语保护违规: {len(result.term_violations)}")
    if result.density_flat_runs:
        lines.append(f"- 密度平坦段落组: {result.density_flat_runs}")
    lines.append("")

    lines.append("## 发现的问题" if not result.ok else "## 问题")
    lines.append("")
    if result.findings:
        for f in result.findings:
            lines.append(f"- [FIX] {f}")
    else:
        lines.append("- 未发现 AI 痕迹 ")

    if result.warnings:
        lines.append("")
        lines.append("## 提示")
        lines.append("")
        for w in result.warnings:
            lines.append(f"- [NOTE] {w}")

    lines.append("")
    return "\n".join(lines)


def to_json(result: HumanizeCheckResult) -> str:
    return json.dumps({
        "ok": result.ok,
        "paragraphs": result.manuscript_paragraphs,
        "matrix_rows": result.matrix_rows,
        "coverage": result.coverage_ratio,
        "sentence_stddev": result.sentence_length_stddev,
        "short_sentence_ratio": result.short_sentence_ratio,
        "connector_count": result.connector_count,
        "connector_density": result.connector_density,
        "connector_red": result.connector_red,
        "connector_yellow": result.connector_yellow,
        "connector_red_detail": result.connector_red_detail,
        "connector_yellow_detail": result.connector_yellow_detail,
        "term_violations": result.term_violations,
        "density_flat_runs": result.density_flat_runs,
        "findings": result.findings,
        "warnings": result.warnings,
    }, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="降AI效果验证 — 检测论文中的 AI 生成痕迹"
    )
    parser.add_argument(
        "input", nargs="?", default=None,
        help="论文 Markdown/纯文本文件路径（或 --text 直接输入文本）"
    )
    parser.add_argument(
        "--text", "-t",
        help="直接输入文本（不使用文件）"
    )
    parser.add_argument(
        "--matrix", "-m",
        help="humanize_matrix.md 路径（默认在 input 同目录查找）"
    )
    parser.add_argument(
        "--output-dir", "-d", default="paper_rewriting_output",
        help="输出目录（默认当前目录 paper_rewriting_output）"
    )
    parser.add_argument("--json", action="store_true", help="JSON 格式输出")
    parser.add_argument("--markdown", action="store_true", help="Markdown 格式输出")
    parser.add_argument("--write", "-w", action="store_true", help="写入 humanize_report.md")
    parser.add_argument("--lang", default="zh", help="语言 (zh/en)")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    # 获取文本
    if args.text:
        text = args.text
        input_path = None
        matrix_path = None
    elif args.input:
        input_path = Path(args.input)
        if not input_path.exists():
            print(f"[ERROR] 文件不存在: {args.input}", file=sys.stderr)
            return 2
        text = input_path.read_text(encoding="utf-8", errors="ignore")
        matrix_path = None
        # 自动查找 humanize_matrix.md
        if args.matrix:
            matrix_path = Path(args.matrix)
        else:
            candidate = input_path.parent / "humanize_matrix.md"
            if candidate.exists():
                matrix_path = candidate
            else:
                out_dir = Path(args.output_dir)
                candidate2 = out_dir / "humanize_matrix.md"
                if candidate2.exists():
                    matrix_path = candidate2
    else:
        print("[ERROR] 请指定输入文件或使用 --text", file=sys.stderr)
        return 2

    result = check_text(text, args.lang, matrix_path)

    # 输出
    if args.json:
        print(to_json(result))
    else:
        print(to_markdown(result))

    if args.write:
        out_dir = Path(args.output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        report_path = out_dir / "humanize_report.md"
        report_path.write_text(to_markdown(result), encoding="utf-8")
        print(f"\n[OK] 报告已写入: {report_path}", file=sys.stderr)

    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
