#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""信息初筛 · 候选抓取脚本

用法:
    python tools/fetch_candidates.py --hours 30 --out work/candidates.json

读取 sources.yaml,抓取所有配置了 feed 的源(RSS/Atom/RDF 均可),
按时间窗过滤、全局去重后输出 JSON 候选清单。
单个源抓取失败不会中断脚本,失败信息记录在 sources_failed 中,
供下游在报告里如实列出"缺席源"。
"""
import argparse
import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
import xml.etree.ElementTree as ET

import requests
import yaml
from bs4 import BeautifulSoup

UA = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/124.0 Safari/537.36 info-digest/0.1"
}
TIMEOUT = 15


def localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def child(elem, name):
    for c in elem:
        if localname(c.tag) == name:
            return c
    return None


def child_text(elem, *names) -> str:
    for n in names:
        c = child(elem, n)
        if c is not None and c.text and c.text.strip():
            return c.text.strip()
    return ""


def parse_date(s):
    if not s:
        return None
    s = s.strip()
    try:
        dt = parsedate_to_datetime(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        pass
    t = s.replace("Z", "+00:00")
    m = re.search(r"([+-]\d{2})(\d{2})$", t)
    if m:
        t = t[: m.start()] + m.group(1) + ":" + m.group(2)
    try:
        dt = datetime.fromisoformat(t)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        pass
    m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)),
                        tzinfo=timezone.utc)
    return None


def clean_html(text, limit=300):
    if not text:
        return ""
    plain = BeautifulSoup(text, "html.parser").get_text(" ", strip=True)
    return re.sub(r"\s+", " ", plain)[:limit]


def norm_key(title: str) -> str:
    return re.sub(r"\W+", "", title.lower())[:80]


def parse_xml(content: bytes):
    """优先标准库解析;失败时(如未转义的 & 等不规范 XML)用 lxml 容错模式兜底。
    成功返回根元素,失败返回 None。"""
    try:
        return ET.fromstring(content)
    except ET.ParseError:
        pass
    try:
        from lxml import etree as LET
        return LET.fromstring(content, parser=LET.XMLParser(recover=True))
    except Exception:
        return None


def fetch_source(src, since, max_per_source):
    """返回 (items, error_or_None)"""
    try:
        resp = requests.get(src["feed"], headers=UA, timeout=TIMEOUT)
        resp.raise_for_status()
    except Exception as e:
        return [], f"{type(e).__name__}: {e}"
    root = parse_xml(resp.content)
    if root is None:
        return [], "ParseError: 标准库与 lxml 容错模式均解析失败"

    items = []
    for el in root.iter():
        if localname(el.tag) not in ("item", "entry"):
            continue
        title = child_text(el, "title")
        if not title:
            continue
        link = ""
        link_el = child(el, "link")
        if link_el is not None:
            link = (link_el.get("href") or (link_el.text or "")).strip()
        pub = parse_date(child_text(
            el, "pubDate", "published", "updated", "date", "created"))
        snippet = clean_html(child_text(
            el, "description", "summary", "encoded", "content"))
        items.append({
            "title": re.sub(r"\s+", " ", title),
            "url": link,
            "source": src["name"],
            "tier": src.get("tier", ""),
            "lang": src.get("lang", ""),
            "tags": src.get("tags", []),
            "source_weight": src.get("weight", 3),
            "published": pub.isoformat() if pub else None,
            "_dt": pub,
            "snippet": snippet,
        })

    dated = sorted((x for x in items if x["_dt"]),
                   key=lambda x: x["_dt"], reverse=True)
    undated = [x for x in items if not x["_dt"]]
    kept = [x for x in (dated + undated)
            if x["_dt"] is None or x["_dt"] >= since][:max_per_source]
    for x in kept:
        x.pop("_dt", None)
    return kept, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=int, default=30,
                    help="时间窗(小时),晨报用 30,周报用 200")
    ap.add_argument("--out", default="work/candidates.json")
    ap.add_argument("--max-per-source", type=int, default=20)
    ap.add_argument("--config", default=str(
        Path(__file__).resolve().parent.parent / "sources.yaml"))
    args = ap.parse_args()

    with open(args.config, encoding="utf-8") as f:
        sources = (yaml.safe_load(f) or {}).get("sources", [])

    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=args.hours)

    all_items, ok, failed, no_feed = [], [], [], []
    feed_sources = []
    for src in sources:
        if not src.get("feed"):
            no_feed.append(src["name"])
            continue
        feed_sources.append(src)

    # 并发抓取(约 19 个 feed,单源超时 15s;串行总时长会超过外层 290s 限制)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(
            lambda s: fetch_source(s, since, args.max_per_source),
            feed_sources))
    for src, (items, err) in zip(feed_sources, results):
        if err:
            failed.append({"name": src["name"], "error": err})
        else:
            ok.append(src["name"])
            all_items.extend(items)

    seen, deduped = set(), []
    for x in all_items:
        k = norm_key(x["title"])
        if k and k not in seen:
            seen.add(k)
            deduped.append(x)

    result = {
        "generated_at": now.isoformat(),
        "window_hours": args.hours,
        "sources_ok": ok,
        "sources_failed": failed,
        "sources_no_feed": no_feed,
        "count": len(deduped),
        "items": deduped,
    }
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    print(f"OK sources={len(ok)} failed={len(failed)} "
          f"items={len(deduped)} -> {out_path}")
    for f_ in failed:
        print(f"  FAILED: {f_['name']}: {f_['error']}")


if __name__ == "__main__":
    main()
