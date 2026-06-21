#!/usr/bin/env python3
"""
多源学术文献采集工具

支持的数据源：
1. CrossRef API — 英文文献（免费，无需 API Key，速率限制 50 req/s）
2. Semantic Scholar API — 英文文献（免费，需 API Key 可选）
3. 百度学术 — 中文文献（Web 抓取）
4. CNKI — 中文文献（Web 抓取，反爬严格）

用法：
    python literature_scraper.py "数字化转型 企业绩效" --sources crossref,semantic,baidu --limit 10
    python literature_scraper.py "machine learning healthcare" --sources crossref,semantic --lang en

输出：JSON 数组，每项包含 title, authors, year, source, doi, url, abstract, citation_count
"""

import argparse
import json
import re
import sys
import time
import hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Optional
from urllib.parse import quote, urlencode

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def safe_get(url: str, headers: dict = None, params: dict = None,
              timeout: int = 15, retries: int = 2) -> Optional[requests.Response]:
    """带重试的 HTTP GET，失败返回 None"""
    default_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    }
    if headers:
        default_headers.update(headers)

    for attempt in range(retries + 1):
        try:
            resp = requests.get(url, headers=default_headers, params=params,
                                timeout=timeout)
            resp.raise_for_status()
            return resp
        except requests.RequestException as e:
            if attempt < retries:
                time.sleep(1 + attempt)
            else:
                print(f"[WARN] 请求失败 ({url}): {e}", file=sys.stderr)
                return None

def deduplicate(results: list) -> list:
    """按标题相似度去重，保留首次出现的条目"""
    seen = set()
    out = []
    for r in results:
        key = hashlib.md5(r.get("title", "").lower().strip().encode()).hexdigest()
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out

def truncate(s: str, n: int = 300) -> str:
    return s[:n] + "..." if s and len(s) > n else (s or "")

# ---------------------------------------------------------------------------
# Source 1: CrossRef API
# ---------------------------------------------------------------------------

CROSSREF_API = "https://api.crossref.org/works"

def search_crossref(query: str, limit: int = 10) -> list:
    """通过 CrossRef API 搜索文献"""
    results = []
    params = {
        "query": query,
        "rows": min(limit, 100),
        "sort": "relevance",
        "filter": "type:journal-article",
    }
    resp = safe_get(CROSSREF_API, params=params)
    if resp is None:
        return results

    data = resp.json()
    items = data.get("message", {}).get("items", [])

    for item in items:
        try:
            title = item.get("title", [""])[0] if item.get("title") else ""
            authors = []
            for a in item.get("author", [])[:6]:
                family = a.get("family", "")
                given = a.get("given", "")
                if family:
                    authors.append(f"{family} {given}".strip())
            year = None
            published = item.get("published-print") or item.get("published-online")
            if published and "date-parts" in published:
                parts = published["date-parts"][0]
                year = parts[0] if parts else None

            doi = item.get("DOI", "")
            source = item.get("container-title", [""])[0] if item.get("container-title") else ""
            abstract = item.get("abstract", "")
            citation_count = item.get("is-referenced-by-count", 0)

            results.append({
                "title": title,
                "authors": authors,
                "year": year,
                "source": source,
                "source_type": "journal",
                "doi": f"https://doi.org/{doi}" if doi else "",
                "url": f"https://doi.org/{doi}" if doi else "",
                "abstract": truncate(abstract, 300),
                "citation_count": citation_count,
                "origin": "CrossRef",
                "language": "en",
                "verified": True,
            })
        except Exception:
            continue

    print(f"[INFO] CrossRef: 返回 {len(results)} 条", file=sys.stderr)
    return results


# ---------------------------------------------------------------------------
# Source 2: Semantic Scholar API
# ---------------------------------------------------------------------------

SEMANTIC_API = "https://api.semanticscholar.org/graph/v1/paper/search"

def search_semantic_scholar(query: str, limit: int = 10, api_key: str = None) -> list:
    """通过 Semantic Scholar API 搜索文献"""
    results = []
    headers = {}
    if api_key:
        headers["x-api-key"] = api_key

    params = {
        "query": query,
        "limit": min(limit, 100),
        "fields": "title,authors,year,abstract,externalIds,url,journal,citationCount,publicationTypes",
    }
    resp = safe_get(SEMANTIC_API, headers=headers, params=params, timeout=20)
    if resp is None:
        return results

    data = resp.json()
    papers = data.get("data", [])

    for p in papers:
        try:
            authors = [a.get("name", "") for a in p.get("authors", [])[:6]]
            doi = p.get("externalIds", {}).get("DOI", "")
            journal = p.get("journal", {})
            journal_name = journal.get("name", "") if journal else ""
            pub_types = p.get("publicationTypes", [])
            source_type = "journal" if "JournalArticle" in pub_types else (
                "conference" if "ConferencePaper" in pub_types else "other"
            )

            results.append({
                "title": p.get("title", ""),
                "authors": authors,
                "year": p.get("year"),
                "source": journal_name,
                "source_type": source_type,
                "doi": f"https://doi.org/{doi}" if doi else "",
                "url": p.get("url", ""),
                "abstract": truncate(p.get("abstract", ""), 300),
                "citation_count": p.get("citationCount", 0) or 0,
                "origin": "SemanticScholar",
                "language": "en",
                "verified": True,
            })
        except Exception:
            continue

    print(f"[INFO] Semantic Scholar: 返回 {len(results)} 条", file=sys.stderr)
    return results


# ---------------------------------------------------------------------------
# Source 3: 百度学术
# ---------------------------------------------------------------------------

BAIDU_SCHOLAR = "https://xueshu.baidu.com/s"

def search_baidu_scholar(query: str, limit: int = 10) -> list:
    """通过百度学术搜索中文文献（Web 抓取）"""
    results = []
    params = {
        "wd": query,
        "rsv_bp": "0",
        "tn": "SE_baiduxueshu_c1g0upa",
    }
    headers = {
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    resp = safe_get(BAIDU_SCHOLAR, headers=headers, params=params, timeout=15)
    if resp is None:
        return results

    soup = BeautifulSoup(resp.text, "html.parser")
    items = soup.select(".sc_content") or soup.select(".result-item") or soup.select("[class*='result']")

    count = 0
    for item in items:
        if count >= limit:
            break
        try:
            # 标题
            title_el = item.select_one("h3 a") or item.select_one(".title a") or item.select_one("a.title")
            title = title_el.get_text(strip=True) if title_el else ""

            # 作者/来源/年份 — 百度学术通常放在一行摘要里
            info_el = item.select_one(".sc_info") or item.select_one(".info") or item.select_one("[class*='abstract']")
            info_text = info_el.get_text(strip=True) if info_el else ""

            # 提取作者（通常是前几个名字）
            authors = []
            year = None
            source = ""

            # 尝试从 info_text 中提取年份
            year_match = re.search(r'(\d{4})', info_text)
            if year_match:
                year = int(year_match.group(1))

            # 尝试提取来源
            source_match = re.search(r'《(.+?)》|-\s*([^-]+)$', info_text)
            if source_match:
                source = source_match.group(1) or source_match.group(2)

            # 链接
            link = title_el.get("href", "") if title_el else ""

            if title:
                results.append({
                    "title": title,
                    "authors": authors,
                    "year": year,
                    "source": source.strip(),
                    "source_type": "journal",
                    "doi": "",
                    "url": link if link.startswith("http") else f"https://xueshu.baidu.com{link}",
                    "abstract": truncate(info_text, 200),
                    "citation_count": 0,
                    "origin": "百度学术",
                    "language": "zh",
                    "verified": False,  # 百度学术抓取结果需人工核验
                })
                count += 1
        except Exception:
            continue

    print(f"[INFO] 百度学术: 返回 {len(results)} 条", file=sys.stderr)
    return results


# ---------------------------------------------------------------------------
# Source 4: CNKI（中国知网）
# ---------------------------------------------------------------------------

CNKI_SEARCH = "https://kns.cnki.net/kns8s/search"

def search_cnki(query: str, limit: int = 10) -> list:
    """通过 CNKI 搜索中文文献（Web 抓取 — 反爬严格，可能失败）"""
    results = []
    # CNKI 反爬非常严格，需要处理验证码和动态加载
    # 这里使用简化路径：尝试获取基础搜索结果
    params = {
        "classid": "YSTT4HG0",
        "kw": query,
        "kwd": query,
    }
    headers = {
        "Accept": "text/html",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": "https://www.cnki.net/",
    }
    resp = safe_get(CNKI_SEARCH, headers=headers, params=params, timeout=20, retries=1)
    if resp is None:
        print("[WARN] CNKI: 请求失败（可能需要处理验证码）", file=sys.stderr)
        return results

    soup = BeautifulSoup(resp.text, "html.parser")
    # CNKI 页面结构频繁变化，这里做最大努力提取
    rows = soup.select("tr") or soup.select(".result-item") or soup.select("[class*='result']")

    count = 0
    for row in rows:
        if count >= limit:
            break
        try:
            # 提取标题
            title_el = row.select_one("a") or row.select_one(".title")
            if not title_el:
                continue
            title = title_el.get_text(strip=True)

            # 提取其他信息
            text = row.get_text(strip=True)

            # 年份
            year_match = re.search(r'(\d{4})', text)
            year = int(year_match.group(1)) if year_match else None

            if title and len(title) > 5:  # 过滤太短的标题
                results.append({
                    "title": title,
                    "authors": [],
                    "year": year,
                    "source": "",
                    "source_type": "journal",
                    "doi": "",
                    "url": title_el.get("href", ""),
                    "abstract": truncate(text, 200),
                    "citation_count": 0,
                    "origin": "CNKI",
                    "language": "zh",
                    "verified": False,  # CNKI 抓取结果需人工核验
                })
                count += 1
        except Exception:
            continue

    print(f"[INFO] CNKI: 返回 {len(results)} 条", file=sys.stderr)
    return results


# ---------------------------------------------------------------------------
# 主采集器
# ---------------------------------------------------------------------------

SOURCE_MAP = {
    "crossref": search_crossref,
    "semantic": search_semantic_scholar,
    "baidu": search_baidu_scholar,
    "cnki": search_cnki,
}


class LiteratureCollector:
    """多源文献采集器"""

    def __init__(self, sources: list = None, max_per_source: int = 10):
        self.sources = sources or ["crossref", "semantic", "baidu"]
        self.max_per_source = max_per_source
        self.results = []

    def search(self, query: str) -> list:
        """针对单个查询串行搜索所有数据源，返回合并去重结果"""
        all_results = []
        for src_name in self.sources:
            if src_name not in SOURCE_MAP:
                print(f"[WARN] 未知数据源: {src_name}", file=sys.stderr)
                continue
            try:
                func = SOURCE_MAP[src_name]
                results = func(query, self.max_per_source)
                all_results.extend(results)
            except Exception as e:
                print(f"[ERROR] {src_name} 搜索异常: {e}", file=sys.stderr)

        return deduplicate(all_results)

    def search_all(self, queries: list) -> list:
        """针对多组关键词并行搜索，返回合并去重结果"""
        all_results = []
        with ThreadPoolExecutor(max_workers=min(len(queries), 4)) as executor:
            futures = {executor.submit(self.search, q): q for q in queries}
            for future in as_completed(futures):
                q = futures[future]
                try:
                    results = future.result()
                    all_results.extend(results)
                    print(f"[INFO] 关键词 '{q}' → {len(results)} 条", file=sys.stderr)
                except Exception as e:
                    print(f"[ERROR] 关键词 '{q}' 处理失败: {e}", file=sys.stderr)

        return deduplicate(all_results)


# ---------------------------------------------------------------------------
# 结果过滤与排序
# ---------------------------------------------------------------------------

def filter_by_year(results: list, min_year: int = 2019) -> list:
    """过滤：只保留 min_year 及之后的文献"""
    return [r for r in results if r.get("year") and r["year"] >= min_year]


def filter_by_type(results: list, source_type: str = "journal") -> list:
    """过滤：只保留指定类型的文献"""
    return [r for r in results if r.get("source_type") == source_type]


def sort_by_citations(results: list) -> list:
    """按引用数降序排列"""
    return sorted(results, key=lambda r: r.get("citation_count", 0) or 0, reverse=True)


def sort_by_year(results: list) -> list:
    """按年份降序排列"""
    return sorted(results, key=lambda r: r.get("year", 0) or 0, reverse=True)


# ---------------------------------------------------------------------------
# 输出格式化
# ---------------------------------------------------------------------------

def format_as_table(results: list) -> str:
    """格式化为 Markdown 表格"""
    if not results:
        return "（无结果）"

    lines = ["| # | 标题 | 作者 | 年份 | 来源 | 引用 | 数据源 |",
             "|---|------|------|------|------|------|--------|"]
    for i, r in enumerate(results, 1):
        authors = ", ".join(r.get("authors", [])[:3])
        if len(r.get("authors", [])) > 3:
            authors += " 等"
        title = truncate(r.get("title", ""), 40)
        source = truncate(r.get("source", ""), 25)
        year = r.get("year", "?")
        citations = r.get("citation_count", 0)
        origin = r.get("origin", "")
        verified = "✅" if r.get("verified") else "⚠️"
        lines.append(f"| {i} | {title} | {authors} | {year} | {source} | {citations} | {origin} {verified} |")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="多源学术文献采集工具")
    parser.add_argument("query", nargs="+", help="搜索关键词（支持多组，空格分隔）")
    parser.add_argument("--sources", "-s", default="crossref,baidu",
                        help="数据源：crossref,semantic,baidu,cnki（逗号分隔，默认 crossref,baidu）")
    parser.add_argument("--limit", "-n", type=int, default=10,
                        help="每个数据源最大返回条数（默认 10）")
    parser.add_argument("--min-year", "-y", type=int, default=2019,
                        help="最早年份（默认 2019）")
    parser.add_argument("--output", "-o", default="literature_results.json",
                        help="输出文件路径（默认当前目录 literature_results.json）")
    parser.add_argument("--format", "-f", choices=["json", "table"], default="json",
                        help="输出格式（默认 json）")
    parser.add_argument("--sort", choices=["year", "citations", "none"], default="year",
                        help="排序方式（默认 year）")
    parser.add_argument("--semantic-api-key", help="Semantic Scholar API Key（可选）")

    args = parser.parse_args()

    # 合并所有 query 参数为一个关键词列表
    queries = [" ".join(args.query)] if len(args.query) == 1 else args.query

    sources = [s.strip() for s in args.sources.split(",")]

    collector = LiteratureCollector(sources=sources, max_per_source=args.limit)
    results = collector.search_all(queries)

    # 过滤
    results = filter_by_year(results, args.min_year)

    # 排序
    if args.sort == "year":
        results = sort_by_year(results)
    elif args.sort == "citations":
        results = sort_by_citations(results)

    print(f"\n[RESULT] 共采集到 {len(results)} 条去重文献\n", file=sys.stderr)

    # 输出
    if args.format == "json":
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        print(f"结果已保存到: {args.output}")
        # 同时输出 JSON 到 stdout 供管道使用
        json.dump(results, sys.stdout, ensure_ascii=False, indent=2)
    else:
        print(format_as_table(results))


if __name__ == "__main__":
    main()
