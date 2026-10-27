#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""站点索引更新: 首页列表/统计 + sitemap。由 GitHub Actions 在生成步骤后调用。

用法:
    python cloud/site_update.py --mode daily --slug 2026-10-28 --label 10-28 --title "晨报 · 2026-10-28（周三）"
    python cloud/site_update.py --mode weekly --slug 2026-W44 --label 2026-W44 --title "辩题：……一句话"
"""
import argparse
import re
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
INDEX = REPO / "index.html"
SITEMAP = REPO / "sitemap.xml"


def bump_stat(html, name, delta=1):
    m = re.search(rf'<div class="stat"><b>(\d+)</b><span>{name}</span></div>', html)
    if not m:
        raise SystemExit(f"stat not found: {name}")
    return html[:m.start(1)] + str(int(m.group(1)) + delta) + html[m.end(1):]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["daily", "weekly"], required=True)
    ap.add_argument("--slug", required=True, help="如 2026-10-28 或 2026-W44")
    ap.add_argument("--label", required=True, help="列表日期显示,如 10-28 / 2026-W44")
    ap.add_argument("--title", required=True)
    args = ap.parse_args()
    today = datetime.now().strftime("%Y-%m-%d")
    index = INDEX.read_text()
    sitemap = SITEMAP.read_text()

    if args.mode == "daily":
        href, ul_id, chip = f"{args.slug}.html", "daily", "new"
        stat_name = "期晨报"
        loc = f"https://jason-93.github.io/DBA_Daily/{args.slug}.html"
    else:
        href, ul_id, chip = f"weekly/{args.slug}.html", "weekly", "new"
        stat_name = "周日专题"
        loc = f"https://jason-93.github.io/DBA_Daily/weekly/{args.slug}.html"

    # 1) 只移除目标 ul 区块内的旧 NEW 芯片
    m = re.search(rf'(<ul class="list" id="{ul_id}">)(.*?)(</ul>)', index, re.S)
    if not m:
        raise SystemExit(f"ul not found: {ul_id}")
    block = m.group(2).replace('<span class="chip new">NEW</span>', "", 1)
    index = index[:m.start(2)] + block + index[m.end(2):]

    # 2) 插入新行
    new_li = (f'<li class="item"><a href="{href}"><span class="chip {chip}">'
              f'{"NEW" if chip == "new" else "专题"}</span><span class="date">{args.label}</span>'
              f'<span>{args.title}</span><span class="arr">→</span></a></li>')
    anchor = f'<ul class="list" id="{ul_id}">'
    if anchor not in index:
        raise SystemExit(f"ul not found: {anchor}")
    index = index.replace(anchor, anchor + "\n    " + new_li, 1)

    # 3) 统计与最近更新
    index = bump_stat(index, stat_name)
    if args.mode == "daily":
        index = re.sub(r'(<div class="stat"><b>)\d{2}-\d{2}(</b><span>最近更新</span></div>)',
                       rf"\g<1>{datetime.now().strftime('%m-%d')}\g<2>", index)

    # 4) sitemap
    entry = f'  <url><loc>{loc}</loc><lastmod>{today}</lastmod><priority>0.8</priority></url>\n'
    if args.mode == "daily":
        m = re.search(r'(  <url><loc>https://jason-93\.github\.io/DBA_Daily/2026-[^/]+\.html</loc>)', sitemap)
    else:
        m = re.search(r'(  <url><loc>https://jason-93\.github\.io/DBA_Daily/weekly/2026-[^/]+\.html</loc>)', sitemap)
    if not m:
        raise SystemExit("sitemap anchor not found")
    sitemap = sitemap[:m.start(1)] + entry + sitemap[m.start(1):]

    INDEX.write_text(index)
    SITEMAP.write_text(sitemap)
    print(f"OK site_update: {args.mode} {args.slug}")


if __name__ == "__main__":
    main()
