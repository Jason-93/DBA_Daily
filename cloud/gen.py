#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DBA Daily 云端晨报/专题生成器。

用法:
    python cloud/gen.py --mode daily --date 2026-10-28
    python cloud/gen.py --mode weekly --date 2026-10-26

流程: 幂等检查 -> 抓取候选 -> 近期标题去重 -> 调用 Kimi API 产出结构化 JSON
      -> Python 渲染 md + html(版式固定,SEO 头固定) -> 写入仓库。
凭证: 环境变量 KIMI_API_KEY(GitHub Secrets 注入)。
"""
import argparse
import json
import re
import subprocess
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent          # cloud/
REPO = ROOT.parent                               # 仓库根
API_URL = "https://api.moonshot.cn/v1/chat/completions"
MODEL = "kimi-k2.6"
TZ_LABEL = "Asia/Shanghai"

DAILY_CSS = """
  :root { --bg:#0f1115; --card:#1a1e26; --text:#e8eaf0; --dim:#9aa3b2;
          --accent:#6ea8fe; --tech:#4cc38a; --phil:#c792ea; --news:#f78c6c; --sci:#82aaff; }
  * { margin:0; padding:0; box-sizing:border-box; }
  body { background:var(--bg); color:var(--text); font-family:-apple-system,"PingFang SC","Helvetica Neue",sans-serif;
         line-height:1.7; padding:16px; max-width:720px; margin:0 auto; }
  header { padding:18px 4px 10px; }
  h1 { font-size:22px; } h1 small { font-size:13px; color:var(--dim); font-weight:normal; }
  .meta { color:var(--dim); font-size:12.5px; margin-top:6px; }
  nav { background:var(--card); border-radius:12px; padding:12px 16px; margin:14px 0 20px; font-size:14px; }
  nav a { color:var(--accent); text-decoration:none; margin-right:16px; }
  nav a.home { color:var(--text); font-weight:600; }
  h2 { font-size:16px; margin:26px 4px 12px; padding-left:10px; border-left:3px solid var(--accent); }
  h2.tech { border-color:var(--tech); } h2.phil { border-color:var(--phil); }
  h2.news { border-color:var(--news); } h2.sci { border-color:var(--sci); }
  .card { background:var(--card); border-radius:12px; padding:16px 18px; margin-bottom:12px; }
  .card h3 { font-size:15.5px; line-height:1.5; }
  .card h3 a { color:var(--text); text-decoration:none; }
  .card h3 a:hover { color:var(--accent); text-decoration:underline; }
  .orig { color:var(--dim); font-size:12.5px; font-style:italic; margin-top:2px; }
  .tags { margin:8px 0; font-size:12px; color:var(--dim); }
  .score { color:#ffd479; font-weight:600; }
  .abstract { font-size:13px; margin:6px 0 2px; color:#c9cfda; }
  .abstract b { color:var(--accent); }
  .action { font-size:13px; margin:2px 0 2px; color:#c9cfda; }
  .action b { color:#7ee0a3; }
  footer { color:var(--dim); font-size:12.5px; margin-top:28px; padding:14px 4px 30px;
           border-top:1px solid #262c38; }
  footer p { margin:6px 0; }
""".strip()

WEEKLY_CSS = """
  :root { --bg:#0f1115; --card:#1a1e26; --text:#e8eaf0; --dim:#9aa3b2;
          --accent:#6ea8fe; --radar:#4cc38a; --debate:#c792ea; --paper:#82aaff; }
  * { margin:0; padding:0; box-sizing:border-box; }
  body { background:var(--bg); color:var(--text); font-family:-apple-system,"PingFang SC","Helvetica Neue",sans-serif;
         line-height:1.7; padding:16px; max-width:720px; margin:0 auto; }
  header { padding:18px 4px 10px; }
  h1 { font-size:22px; } h1 small { font-size:13px; color:var(--dim); font-weight:normal; }
  .meta { color:var(--dim); font-size:12.5px; margin-top:6px; }
  nav { background:var(--card); border-radius:12px; padding:12px 16px; margin:14px 0 20px; font-size:14px; }
  nav a { color:var(--accent); text-decoration:none; margin-right:16px; }
  nav a.home { color:var(--text); font-weight:600; }
  h2 { font-size:16px; margin:26px 4px 12px; padding-left:10px; border-left:3px solid var(--accent); }
  h2.radar { border-color:var(--radar); } h2.debate { border-color:var(--debate); }
  h2.paper { border-color:var(--paper); }
  h3 { font-size:15.5px; margin:18px 4px 8px; }
  .card { background:var(--card); border-radius:12px; padding:16px 18px; margin-bottom:12px; font-size:14px; }
  .card p { margin:6px 0; }
  .judge { color:var(--radar); font-size:13.5px; }
  .judge::before { content:"趋势判断："; font-weight:600; }
  ul, ol { margin:6px 0 6px 22px; font-size:14px; }
  li { margin:4px 0; }
  a.link { color:var(--accent); font-size:12.5px; word-break:break-all; text-decoration:none; display:block; margin:3px 0; }
  a.link::before { content:"🔗 "; }
  .note { background:var(--card); border-radius:12px; padding:12px 16px; color:var(--dim); font-size:12.5px; margin-bottom:12px; }
  footer { color:var(--dim); font-size:12.5px; margin-top:28px; padding:14px 4px 30px; border-top:1px solid #262c38; }
""".strip()

PROFILE = """读者画像：途虎 DBA，9 年经验（美团/bigo/建信金科/字节），ramp up 阶段；痛点是深度饥渴+技术视野变窄，不缺热点。
技术兴趣优先级：1 AI4DB（大模型做数据库运维/诊断/调优/Text2SQL/自治库）2 DB4AI（向量数据库、RAG 底座、混合检索）3 稳定性工程（SRE/故障治理/可观测）4 vibe coding/AI 工具链 5 云原生与分布式数据库 6 职业转型。
辩论：多年辩龄，偏好价值辩/哲理辩，关注「矛盾处境中的具体的人」；高价值素材=真实两难案例+哲学框架。
哲学：康德黑格尔到存在主义有底子，阅读顺序 伯林→阿伦特→加缪→海德格尔；反感纯概念游戏。
科学：物理/生命科学/认知科学均可，尽量附原始论文链接。
负面清单（不入选）：名人人物专访/访谈体；娱乐八卦。"""

RUBRIC = """五维打分（0-5）：相关度35% 深度25% 可操作性20% 信源10% 时效与新颖10%；总分≥3.4 入选。
摘要写法：先 1-2 句概括内容本身（做了什么/核心观点/关键数据），再 1-2 句点明对读者的价值（结合画像：可带回团队的手段、职业判断锚点、辩题切口），连成一段，≤120 字。
落地建议：一条可执行动作（精读某部分/抄进 runbook/存素材库/装工具试用/写一页备忘），必须具体到读者团队场景，一句话；纯资讯可写「扫读即可」级。
硬性规则：只允许用候选清单里真实抓取到的条目及其真实链接，禁止编造；某层无达标候选就留空并说明，不得凑数。"""


def esc(s):
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def call_api(prompt, api_key, max_tokens=12000, retries=3):
    payload = json.dumps({
        "model": MODEL, "temperature": 1, "max_tokens": max_tokens, "reasoning_effort": "low",
        "messages": [{"role": "system", "content": "你是严谨的信息编辑，只输出合法的 JSON，不输出任何其他文字。"},
                     {"role": "user", "content": prompt}],
    }).encode()
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(API_URL, data=payload, headers={
                "Authorization": f"Bearer {api_key}", "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=300) as r:
                data = json.loads(r.read())
            return data["choices"][0]["message"]["content"]
        except Exception as e:  # noqa
            last = e
            print(f"API attempt {i+1} failed: {e}", file=sys.stderr)
    raise RuntimeError(f"Kimi API failed after {retries} attempts: {last}")


def parse_json(text):
    text = text.strip()
    text = re.sub(r"^```(json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object in model output")
    return json.loads(text[start:end + 1])


def fetch_candidates(hours, out):
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "fetch_candidates.py"),
                        "--hours", str(hours), "--out", str(out)],
                       cwd=str(ROOT), capture_output=True, text=True)
    print(r.stdout[-500:] if r.stdout else "", r.stderr[-500:] if r.stderr else "")
    data = json.loads(Path(out).read_text())
    items = data if isinstance(data, list) else data.get("items") or data.get("candidates")
    return items, data


def recent_context(mode, date_str):
    """返回近 8 期晨报的标题/链接(去重用) + 本周 md 摘要(weekly 素材)。"""
    ctx, week_md = [], []
    pages = sorted(REPO.glob("2*.html"), reverse=True)[:8]
    for p in pages:
        for m in re.finditer(r'<h3><a href="([^"]+)"[^>]*>(?:\d+\.\s*)?([^<]+)</a></h3>', p.read_text()):
            ctx.append({"title": m.group(2).strip(), "url": m.group(1)})
    if mode == "weekly":
        monday = datetime.strptime(date_str, "%Y-%m-%d")
        iso = monday.isocalendar()
        week_no = iso[1]
        for md in sorted((REPO / "briefs").glob("*.md"), reverse=True)[:10]:
            try:
                d = datetime.strptime(md.stem, "%Y-%m-%d")
            except ValueError:
                continue
            if d.isocalendar()[1] == week_no:
                txt = md.read_text()
                items_txt = "\n".join(re.findall(r"### .*", txt)) + "\n" + "\n".join(
                    line for line in txt.splitlines() if line.startswith("**摘要**"))[:6000]
                week_md.append(f"== {md.stem} ==\n{items_txt[:6500]}")
    return ctx[:120], week_md


def head_html(title, desc, url, css, date_published):
    return f"""<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{esc(title)}</title>
<meta name="description" content="{esc(desc)}">
<meta name="robots" content="index,follow">
<link rel="canonical" href="{esc(url)}">
<meta property="og:type" content="article">
<meta property="og:site_name" content="DBA Daily">
<meta property="og:title" content="{esc(title)}">
<meta property="og:description" content="{esc(desc)}">
<meta property="og:url" content="{esc(url)}">
<meta property="og:locale" content="zh_CN">
<meta name="twitter:card" content="summary">
<meta name="twitter:title" content="{esc(title)}">
<meta name="twitter:description" content="{esc(desc)}">
<script type="application/ld+json">
{{
  "@context": "https://schema.org",
  "@type": "BlogPosting",
  "headline": "{esc(title)}",
  "description": "{esc(desc)}",
  "url": "{esc(url)}",
  "datePublished": "{date_published}",
  "inLanguage": "zh-CN",
  "author": {{ "@type": "Organization", "name": "DBA Daily" }}
}}
</script>"""


def render_daily(date_str, weekday, meta_line, result):
    tiers = [("tech", "tech", "🛠 技术（AI4DB / DB4AI / 稳定性 / vibe coding）"),
             ("phil", "phil", "🏛 哲学与辩论"),
             ("sci", "sci", "🔬 科学"),
             ("news", "news", "⚡ 热点保底")]
    by_tier = {k: [] for k, _, _ in tiers}
    for i, it in enumerate(result["items"], 1):
        by_tier.setdefault(it["tier"], []).append((i, it))
    title = f"晨报 · {date_str}"
    url = f"https://jason-93.github.io/DBA_Daily/{date_str}.html"
    n = len(result["items"])
    desc = f"每日晨报 · {date_str}：入选 {n} 条精选，附相关度评分与内容摘要。"

    nav = ['<a href="./" class="home">← 主页</a>']
    body, md = [], []
    md.append(f"# 每日晨报 · {date_str}（{weekday}）\n")
    md.append(f"> {meta_line}\n> 打分权重：相关度 35% · 深度 25% · 可操作性 20% · 信源 10% · 时效与新颖 10%（≥3.4 入选）\n> 本页由云端定时任务生成。\n")
    num = 0
    for key, cls, label in tiers:
        items = by_tier.get(key, [])
        if not items:
            continue
        nav.append(f'<a href="#{key}">{label.split(" ")[0]} {len(items)}</a>')
        body.append(f'\n<h2 class="{cls}" id="{key}">{esc(label)}</h2>')
        md.append(f"\n## {label} · {len(items)} 条\n")
        for _, it in items:
            num += 1
            orig = f'\n  <div class="orig">{esc(it.get("orig", ""))}</div>' if it.get("orig") else ""
            body.append(f'''<div class="card">
  <h3><a href="{esc(it["url"])}">{num}. {esc(it["title_zh"])}</a></h3>{orig}
  <div class="tags">{esc(it.get("source", ""))} · {esc(it.get("tags", ""))} · 约 {it.get("minutes", 5)} 分钟 · <span class="score">★{it["score"]:.1f}</span></div>
  <p class="abstract"><b>摘要:</b>{esc(it["abstract"])}</p>
  <p class="action"><b>落地:</b>{esc(it["action"])}</p>
</div>''')
            md.append(f'### {num}. [{it["title_zh"]}]({it["url"]})')
            if it.get("orig"):
                md.append(f'**{it["orig"]}**')
            md.append(f'标签：{it.get("tags", "")} · 约 {it.get("minutes", 5)} 分钟')
            md.append(f'评分：**{it["score"]:.1f}**')
            md.append(f'**摘要**：{it["abstract"]}')
            md.append(f'**落地建议**：{it["action"]}\n')
    absent = result.get("absent_sources") or []
    note = result.get("meta_note") or ""
    foot_parts = []
    if absent:
        foot_parts.append(f'<p><b>缺席源:</b>{"；".join(esc(a) for a in absent)}。</p>')
    if note:
        foot_parts.append(f'<p><b>缺席说明:</b>{esc(note)}</p>')
    foot_parts.append('<p><b>反馈:</b>回复"晨报反馈:条目序号 + 有用/无用 + 一句话"</p>')
    footer = "\n".join(foot_parts)
    md.append("\n---\n\n## 缺席源\n\n" + ("\n".join(f"- {a}" for a in absent) if absent else "无。"))
    if note:
        md.append(f"\n## 缺席说明\n\n{note}")
    md.append('\n## 反馈\n\n回复「晨报反馈：条目序号 + 有用/无用 + 一句话」，将计入后续选题调整。')

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
{head_html(title, desc, url, DAILY_CSS, date_str)}
<style>
{DAILY_CSS}
</style>
</head>
<body>

<header>
  <h1>每日晨报 <small>{date_str} {weekday}</small></h1>
  <div class="meta">{esc(meta_line)} · 已对照近期晨报去重</div>
</header>

<nav>
  {" ".join(nav)}
</nav>
{"".join(body)}

<footer>
{footer}
</footer>

</body>
</html>"""
    return "\n".join(md) + "\n", html


def render_weekly(week_id, range_label, meta_line, result):
    title = f"周日专题 · {week_id}"
    url = f"https://jason-93.github.io/DBA_Daily/weekly/{week_id}.html"
    desc = f"周日专题 {week_id}：本周 AI4DB/DB4AI/稳定性雷达、辩题素材包、论文精读推荐与反馈回顾。"
    today = datetime.now().strftime("%Y-%m-%d")

    body = ['\n<h2 class="radar" id="radar">一、本周 AI4DB / DB4AI / 稳定性雷达</h2>']
    md = [f"# 周日专题 · {week_id}（{range_label}）\n", f"> {meta_line}\n"]
    md.append("\n## 一、本周 AI4DB / DB4AI / 稳定性雷达\n")
    for i, r in enumerate(result["radar"], 1):
        refs = "".join(f'\n  <a class="link" href="{esc(x["url"])}">{esc(x["label"])}</a>' for x in r.get("refs", []))
        body.append(f'''<div class="card">
  <p><b>{i}. {esc(r["title"])}</b></p>
  <p>{esc(r["body"])}</p>
  <p class="judge">{esc(r["judge"])}</p>{refs}
</div>''')
        md.append(f'### {i}. {r["title"]}\n\n{r["body"]}\n\n**趋势判断**：{r["judge"]}\n')
        for x in r.get("refs", []):
            md.append(f'- {x["label"]}: {x["url"]}')

    d = result["debate"]
    body.append(f'\n<h2 class="debate" id="debate">二、辩题素材包：{esc(d["title"])}</h2>')
    md.append(f'\n## 二、辩题素材包：{d["title"]}\n')
    case_refs = "".join(f'\n  <a class="link" href="{esc(x["url"])}">{esc(x["label"])}</a>' for x in d.get("case_refs", []))
    body.append(f'''<div class="card">
  <p><b>案例（来源已核实）</b></p>
  <p>{esc(d["case"])}</p>{case_refs}
  {f'<p>{esc(d["extra_note"])}</p>' if d.get("extra_note") else ""}
</div>''')
    md.append(f'**案例**：{d["case"]}\n')
    for x in d.get("case_refs", []):
        md.append(f'- {x["label"]}: {x["url"]}')
    if d.get("extra_note"):
        md.append(f'\n{d["extra_note"]}')
    for side, key in [("正方立论框架", "affirmative"), ("反方立论框架", "negative")]:
        lis = "".join(f"<li>{esc(x)}</li>" for x in d[key])
        body.append(f'<div class="card">\n  <p><b>{side}</b></p>\n  <ol>{lis}</ol>\n</div>')
        md.append(f'\n**{side}**\n' + "\n".join(f'{j}. {x}' for j, x in enumerate(d[key], 1)))
    phis = "".join(f"<li>{esc(x)}</li>" for x in d["philosophy"])
    body.append(f'<div class="card">\n  <p><b>关联哲学文本</b></p>\n  <ul>{phis}</ul>\n</div>')
    md.append('\n**关联哲学文本**\n' + "\n".join(f'- {x}' for x in d["philosophy"]))

    p = result["paper"]
    body.append('\n<h2 class="paper" id="paper">三、论文精读推荐</h2>')
    body.append(f'''<div class="card">
  <p><b>{esc(p["title"])}</b></p>
  <a class="link" href="{esc(p["url"])}">{esc(p["url"].replace("https://", ""))}</a>
  <p>{esc(p.get("meta_line", ""))}</p>
  <p><b>导读：</b>{esc(p["guide"])}</p>
  <p><b>怎么读：</b>{esc(p["howto"])}</p>
</div>''')
    md.append(f'\n## 三、论文精读推荐\n\n**{p["title"]}**\n链接：{p["url"]} · {p.get("meta_line", "")}\n\n**导读**：{p["guide"]}\n\n**怎么读**：{p["howto"]}\n')

    body.append('\n<h2 id="feedback">四、反馈回顾</h2>')
    body.append(f'<div class="note">{esc(result["feedback_note"])}</div>')
    md.append(f'\n## 四、反馈回顾\n\n{result["feedback_note"]}\n')

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
{head_html(title, desc, url, WEEKLY_CSS, today)}
<style>
{WEEKLY_CSS}
</style>
</head>
<body>

<header>
  <h1>周日专题 <small>{week_id}（{range_label}）</small></h1>
  <div class="meta">{esc(meta_line)}</div>
</header>

<nav>
  <a href="../" class="home">← 主页</a>
  <a href="#radar">📡 雷达 {len(result["radar"])}</a>
  <a href="#debate">⚖️ 辩题素材包</a>
  <a href="#paper">📄 论文精读</a>
  <a href="#feedback">💬 反馈回顾</a>
</nav>
{"".join(body)}

<footer>DBA Daily · 周日专题由云端定时任务生成</footer>

</body>
</html>"""
    return "\n".join(md) + "\n", html


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["daily", "weekly"], required=True)
    ap.add_argument("--date", help="Asia/Shanghai 的日期 YYYY-MM-DD，默认今天")
    args = ap.parse_args()
    api_key = __import__("os").environ.get("KIMI_API_KEY")
    if not api_key:
        sys.exit("KIMI_API_KEY missing")

    now = datetime.now()
    date_str = args.date or now.strftime("%Y-%m-%d")
    d = datetime.strptime(date_str, "%Y-%m-%d")
    weekday = "周一 周二 周三 周四 周五 周六 周日".split()[d.weekday()]

    if args.mode == "daily":
        md_path = REPO / "briefs" / f"{date_str}.md"
        if md_path.exists():
            print(f"SKIP: {md_path} already exists")
            return
        hours = 30
        items, raw = fetch_candidates(hours, ROOT / "candidates.json")
        if len(items) < 8:
            items, raw = fetch_candidates(54, ROOT / "candidates.json")
        failed = raw.get("sources_failed") if isinstance(raw, dict) else []
        absent = [f"{f.get('name')}（{f.get('error', '抓取失败')}）" for f in (failed or [])]
        ctx, _ = recent_context("daily", date_str)
        cand = [{"title": i.get("title"), "url": i.get("url"), "source": i.get("source"),
                 "tier": i.get("tier"), "published": (i.get("published") or "")[:16],
                 "snippet": (i.get("snippet") or "")[:300]} for i in items]
        prompt = f"""{PROFILE}

{RUBRIC}

今天是 {date_str} {weekday}。以下是过去 {hours} 小时抓取到的候选条目（JSON）：
{json.dumps(cand, ensure_ascii=False)}

以下是近 8 期晨报已选过的标题与链接（不得重复入选）：
{json.dumps(ctx, ensure_ascii=False)}

抓取失败的源：{json.dumps(absent, ensure_ascii=False)}

请完成今日晨报选稿，只输出 JSON（不要输出任何其他文字），格式：
{{
  "items": [
    {{"tier": "tech|phil|sci|news", "title_zh": "中文标题(英文条目必须翻译)",
      "orig": "英文原标题(中文条目填空字符串)", "url": "必须严格等于候选中的某个 url",
      "source": "来源名", "tags": "标签1 / 标签2", "minutes": 8,
      "score": 3.8, "abstract": "按摘要写法,≤120字", "action": "落地建议一句话"}}
  ],
  "absent_sources": ["缺席源一句话(含原因),没有则空数组"],
  "meta_note": "去重说明或某层留空的原因,没有则空字符串"
}}
配额：技术 4-6 条、哲学 0-2 条、科学 0-2 条、热点 ≤2 条；宁缺毋滥，每层只选真正达 3.4 的。
条目编号按最终顺序，tech 在前。评分严格按五维加权，不要给候选条目里不存在的内容打分。"""
        print("calling Kimi API (daily selection)...")
        result = parse_json(call_api(prompt, api_key))
        cand_urls = {c["url"] for c in cand}
        bad = [i["url"] for i in result["items"] if i["url"] not in cand_urls]
        if bad:
            sys.exit(f"model invented urls not in candidates: {bad}")
        meta_line = f"抓取窗口 {hours} 小时 · 候选 {len(items)} 条 · 入选 {len(result['items'])} 条"
        md, html = render_daily(date_str, weekday, meta_line, result)
        md_path.parent.mkdir(exist_ok=True)
        md_path.write_text(md)
        (REPO / f"{date_str}.html").write_text(html)
        (REPO / ".generated").write_text(date_str)
        print(f"OK daily: briefs/{date_str}.md + {date_str}.html")
    else:
        iso = d.isocalendar()
        week_id = f"{d.year}-W{iso[1]:02d}"
        monday = d.fromisocalendar(d.year, iso[1], 1)
        sunday = monday.fromisocalendar(d.year, iso[1], 7)
        range_label = f"{monday.strftime('%m-%d')} ~ {sunday.strftime('%m-%d')}"
        md_path = REPO / "weekly" / f"{week_id}.md"
        if md_path.exists():
            print(f"SKIP: {md_path} already exists")
            return
        items, raw = fetch_candidates(200, ROOT / "candidates-weekly.json")
        failed = raw.get("sources_failed") if isinstance(raw, dict) else []
        absent = [f"{f.get('name')}（{f.get('error', '抓取失败')}）" for f in (failed or [])]
        _, week_md = recent_context("weekly", date_str)
        cand = [{"title": i.get("title"), "url": i.get("url"), "source": i.get("source"),
                 "tier": i.get("tier"), "published": (i.get("published") or "")[:16],
                 "snippet": (i.get("snippet") or "")[:300]} for i in items]
        prompt = f"""{PROFILE}

{RUBRIC}

今天是 {date_str}。本周区间：{range_label}。请生成周日专题，只输出 JSON：
{{
  "radar": [{{"title": "雷达条目标题(含判断)", "body": "支撑材料:本周发生了什么的具体描述,提及对应晨报日期",
              "judge": "一句趋势判断", "refs": [{{"label": "来源说明", "url": "真实链接"}}]}}],
  "debate": {{"title": "辩题一句话", "case": "真实两难案例描述(100-180字,含出处)",
              "case_refs": [{{"label": "来源说明", "url": "真实链接"}}],
              "extra_note": "案例补充说明,没有则空字符串",
              "affirmative": ["立论1", "立论2", "立论3"],
              "negative": ["立论1", "立论2", "立论3"],
              "philosophy": ["文本1(含为何关联,优先伯林/阿伦特/加缪/海德格尔+SEP词条方向)"]}},
  "paper": {{"title": "论文标题(含 arXiv 号)", "url": "真实链接",
             "meta_line": "出处与评分一句话", "guide": "200字导读", "howto": "阅读路径+落地动作"}},
  "feedback_note": "反馈回顾一段话(feedback.md 无新反馈则说明不调整权重并邀请反馈)"
}}
雷达 3-6 条，每条 refs 里的 url 必须来自候选清单或本周晨报内容；辩题案例与论文 url 同样必须真实。
所有事实必须来自下方素材或候选清单，禁止编造。

本周各期晨报摘录：
{"\n\n".join(week_md)}

200h 候选清单：
{json.dumps(cand, ensure_ascii=False)}

抓取失败的源：{json.dumps(absent, ensure_ascii=False)}"""
        print("calling Kimi API (weekly)...")
        result = parse_json(call_api(prompt, api_key, max_tokens=20000))
        meta_line = (f"素材：本周晨报 + 200h 候选 {len(items)} 条"
                     + (f"（缺席源：{'；'.join(absent)}）" if absent else ""))
        md, html = render_weekly(week_id, range_label, meta_line, result)
        md_path.parent.mkdir(exist_ok=True)
        md_path.write_text(md)
        (REPO / "weekly" / f"{week_id}.html").write_text(html)
        (REPO / ".generated").write_text(week_id)
        print(f"OK weekly: weekly/{week_id}.md + weekly/{week_id}.html")


if __name__ == "__main__":
    main()
