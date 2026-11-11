#!/usr/bin/env python3
"""One-time snapshot of profile stats, rendered as SVG cards for the README.

Usage:
  python3 scripts/snapshot.py            # render SVGs from data/snapshot.json
  python3 scripts/snapshot.py --collect  # re-collect data first (needs `gh` + local repos)

Collect reads (never publishes) private data:
  - GitHub GraphQL via `gh api`: contribution calendar, repo languages
  - local `git log` of the product repos: commit counts, me vs. agents, hour of day
Only aggregated numbers end up in data/snapshot.json and the SVGs.
"""
import collections
import json
import pathlib
import subprocess
import sys
from datetime import date, datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "snapshot.json"
ASSETS = ROOT / "assets"

PROJECTS = pathlib.Path.home() / "CODE" / "Projects"
REPOS = {"Bimbelio": PROJECTS / "Bimbelio", "Jutif": PROJECTS / "Jutif", "Otaknologi": PROJECTS / "Otaknologi"}
ME = {"syamilji", "Syamil Jihad"}
AGENTS = {"T3 Code"}

# Business impact, from the LinkedIn profile (not computed).
IMPACT = [
    ("5,000+", "monthly active users, 12 mo", "Bimbelio"),
    ("90%", "core features shipped with AI", "after 3 devs left · Bimbelio"),
    ("< IDR 15K", "acquisition cost per user", "Bimbelio"),
    ("3h → 12m", "analytics refresh, automated", "GoTo Financial"),
]


# ---------------------------------------------------------------- collect

def gh_graphql(query, paginate=False):
    cmd = ["gh", "api", "graphql", "-f", f"query={query}"]
    if paginate:
        cmd.insert(3, "--paginate")
    out = subprocess.run(cmd, check=True, capture_output=True, text=True).stdout
    dec, i, pages = json.JSONDecoder(), 0, []
    while (rest := out[i:].lstrip()):
        obj, n = dec.raw_decode(rest)
        pages.append(obj)
        i = len(out) - len(rest) + n
    return pages


def collect():
    cal = gh_graphql("query{viewer{contributionsCollection{contributionCalendar{totalContributions "
                     "weeks{contributionDays{date contributionCount weekday}}}}}}")[0]
    cal = cal["data"]["viewer"]["contributionsCollection"]["contributionCalendar"]
    days = [d for w in cal["weeks"] for d in w["contributionDays"]]
    counts = [d["contributionCount"] for d in days]
    best = max(days, key=lambda d: d["contributionCount"])
    since, until = days[0]["date"], days[-1]["date"]

    pages = gh_graphql(
        "query($endCursor:String){viewer{repositories(first:100,after:$endCursor,"
        "ownerAffiliations:[OWNER,ORGANIZATION_MEMBER,COLLABORATOR]){pageInfo{hasNextPage endCursor} "
        "nodes{isFork isPrivate languages(first:10,orderBy:{field:SIZE,direction:DESC})"
        "{edges{size node{name color}}}}}}}", paginate=True)
    repos = [n for p in pages for n in p["data"]["viewer"]["repositories"]["nodes"] if not n["isFork"]]
    size, color = collections.Counter(), {}
    for r in repos:
        for e in r["languages"]["edges"]:
            size[e["node"]["name"]] += e["size"]
            color[e["node"]["name"]] = e["node"]["color"] or "#6e7681"
    total = sum(size.values())
    langs = [{"name": k, "color": color[k], "pct": round(100 * v / total, 1)} for k, v in size.most_common(6)]
    langs.append({"name": "Other", "color": "#6e7681", "pct": round(100 - sum(l["pct"] for l in langs), 1)})

    products, hours = [], [0] * 24
    for name, path in REPOS.items():
        log = subprocess.run(["git", "-C", str(path), "log", "--all", f"--since={since}", "--no-merges",
                              "--format=%an|%ad", "--date=format:%H"],
                             check=True, capture_output=True, text=True).stdout.splitlines()
        me = agents = 0
        for line in log:
            author, hour = line.rsplit("|", 1)
            if author in ME:
                me += 1
            elif author in AGENTS:
                agents += 1
            else:
                continue  # teammates and servers are not counted
            hours[int(hour)] += 1
        products.append({"name": name, "me": me, "agents": agents})

    snap = {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "window": {"since": since, "until": until},
        "contributions": cal["totalContributions"],
        "active_days": sum(1 for c in counts if c), "days": len(counts),
        "best_day": {"date": best["date"], "count": best["contributionCount"]},
        "repos": len(repos), "private_repos": sum(r["isPrivate"] for r in repos),
        "languages": langs, "products": sorted(products, key=lambda p: -(p["me"] + p["agents"])),
        "hours": hours,
    }
    DATA.parent.mkdir(parents=True, exist_ok=True)
    DATA.write_text(json.dumps(snap, indent=2) + "\n")
    return snap


# ---------------------------------------------------------------- render

THEMES = {
    "dark": dict(tx="#e6edf3", tx2="#9198a1", bd="#3d444d", track="#262c36", me="#e6edf3", ag="#8b7bff", bar="#3d444d"),
    "light": dict(tx="#1f2328", tx2="#59636e", bd="#d1d9e0", track="#eff2f5", me="#1f2328", ag="#8250df", bar="#d1d9e0"),
}
SANS = "-apple-system,BlinkMacSystemFont,'Segoe UI','Noto Sans',Helvetica,Arial,sans-serif"
MONO = "ui-monospace,SFMono-Regular,'SF Mono',Menlo,Consolas,monospace"


def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def svg(w, h, t, body, title):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
            f'role="img" aria-label="{esc(title)}"><title>{esc(title)}</title>'
            f'<rect x="0.5" y="0.5" width="{w-1}" height="{h-1}" rx="6" fill="none" stroke="{t["bd"]}"/>'
            f'{body}</svg>\n')


def text(x, y, s, t, size=12, color="tx", weight=400, font=SANS, anchor="start"):
    return (f'<text x="{x}" y="{y}" font-family="{font}" font-size="{size}" font-weight="{weight}" '
            f'fill="{t[color]}" text-anchor="{anchor}">{esc(s)}</text>')


def tiles(items, t, title, w=840, h=96):
    """Row of big-number tiles. items: (value, label, footnote, accent?)"""
    n, body = len(items), ""
    cw = w / n
    for i, (val, label, foot, accent) in enumerate(items):
        x = i * cw
        if i:
            body += f'<line x1="{x:.1f}" y1="14" x2="{x:.1f}" y2="{h-14}" stroke="{t["bd"]}"/>'
        body += text(x + 18, 42, val, t, 26, "ag" if accent else "tx", 600)
        body += text(x + 18, 62, label, t, 12, "tx2")
        body += text(x + 18, 79, foot, t, 10.5, "tx2", font=MONO)
    return svg(w, h, t, body, title)


def card_head(t, title, sub):
    return text(18, 28, title, t, 13, "tx", 600) + text(18, 46, sub, t, 11, "tx2")


def render_commits(s, t, w=840):
    rows = s["products"]
    h = 70 + 26 * len(rows) + 30
    top = max(p["me"] + p["agents"] for p in rows)
    body = card_head(t, "Where the commits went", "Me vs. the coding agents I orchestrated, per product")
    x0, x1 = 110, w - 80
    for i, p in enumerate(rows):
        y, tot = 70 + 26 * i, p["me"] + p["agents"]
        full = (x1 - x0) * tot / top
        mw = full * p["me"] / tot
        body += text(18, y + 10, p["name"], t, 12)
        body += f'<rect x="{x0}" y="{y}" width="{x1-x0}" height="12" rx="2" fill="{t["track"]}"/>'
        body += f'<rect x="{x0}" y="{y}" width="{mw:.1f}" height="12" rx="2" fill="{t["me"]}"/>'
        body += f'<rect x="{x0+mw+1:.1f}" y="{y}" width="{full-mw-1:.1f}" height="12" rx="2" fill="{t["ag"]}"/>'
        body += text(w - 18, y + 10, f"{tot:,}", t, 11.5, "tx2", font=MONO, anchor="end")
    me = sum(p["me"] for p in rows); ag = sum(p["agents"] for p in rows)
    ly = h - 18
    body += f'<rect x="18" y="{ly-9}" width="10" height="10" rx="2" fill="{t["me"]}"/>' + text(34, ly, f"me · {me:,}", t, 11, "tx2")
    body += f'<rect x="130" y="{ly-9}" width="10" height="10" rx="2" fill="{t["ag"]}"/>' + text(146, ly, f"agents · {ag:,}", t, 11, "tx2")
    return svg(w, h, t, body, "Commits per product, me vs agents")


def render_languages(s, t, w=414, h=176):
    body = card_head(t, "Languages", f"By bytes across {s['repos']} private repos")
    x, bw = 18, w - 36
    body += f'<clipPath id="r"><rect x="18" y="62" width="{bw}" height="10" rx="5"/></clipPath><g clip-path="url(#r)">'
    for l in s["languages"]:
        lw = bw * l["pct"] / 100
        body += f'<rect x="{x:.1f}" y="62" width="{max(lw - 1.5, 1):.1f}" height="10" fill="{l["color"]}"/>'
        x += lw
    body += "</g>"
    for i, l in enumerate(s["languages"][:6]):
        cx, cy = 18 + (i % 2) * (w / 2 - 6), 98 + (i // 2) * 24
        body += f'<circle cx="{cx + 5}" cy="{cy - 4}" r="5" fill="{l["color"]}"/>'
        body += text(cx + 16, cy, l["name"], t, 12)
        body += text(cx + w / 2 - 34, cy, f'{l["pct"]}%', t, 11.5, "tx2", font=MONO, anchor="end")
    return svg(w, h, t, body, "Languages by bytes")


def render_hours(s, t, w=414, h=176):
    hrs = s["hours"]
    peak = max(range(24), key=lambda i: hrs[i])
    body = card_head(t, "When things shipped", f"Commits by hour (UTC+7) · peak {peak:02d}:00")
    x0, x1, base, top = 18, w - 18, 146, 64
    gap, bw, m = 3, (w - 36 - 23 * 3) / 24, max(hrs)
    for i, v in enumerate(hrs):
        bh = (base - top) * v / m
        body += (f'<rect x="{x0 + i*(bw+gap):.1f}" y="{base-bh:.1f}" width="{bw:.1f}" height="{bh:.1f}" rx="1.5" '
                 f'fill="{t["ag"] if i == peak else t["bar"]}"/>')
    for i in (0, 6, 12, 18, 23):
        body += text(x0 + i * (bw + gap) + bw / 2, base + 16, f"{i:02d}", t, 10, "tx2", font=MONO, anchor="middle")
    return svg(w, h, t, body, "Commits by hour of day")


def fmt_k(n):
    return f"{n/1000:.1f}k" if n >= 10000 else f"{n:,}"


def main():
    s = collect() if "--collect" in sys.argv or not DATA.exists() else json.loads(DATA.read_text())
    commits = sum(p["me"] + p["agents"] for p in s["products"])
    agent_pct = round(100 * sum(p["agents"] for p in s["products"]) / commits)
    best = s["best_day"]
    best_label = date.fromisoformat(best["date"]).strftime("%-d %b %Y")
    window = (f'{date.fromisoformat(s["window"]["since"]).strftime("%b %Y")} – '
              f'{date.fromisoformat(s["window"]["until"]).strftime("%b %Y")}')
    ASSETS.mkdir(exist_ok=True)
    for name, t in THEMES.items():
        files = {
            "numbers": tiles([
                (f'{s["contributions"]:,}', "GitHub contributions", window, False),
                (fmt_k(commits), "commits shipped, 3 products", "me + agents", False),
                (f"{agent_pct}%", "written by agents I ran", "the rest by hand", True),
                (str(s["active_days"]), "active days", f'best: {best["count"]} on {best_label}', False),
            ], t, "By the numbers"),
            "impact": tiles([(v, l, f, i == 0) for i, (v, l, f) in enumerate(IMPACT)], t, "Business impact"),
            "commits": render_commits(s, t),
            "languages": render_languages(s, t),
            "hours": render_hours(s, t),
        }
        for k, v in files.items():
            (ASSETS / f"{k}-{name}.svg").write_text(v)
    print(f"rendered {len(files) * len(THEMES)} SVGs from snapshot {s['generated']} "
          f"({s['contributions']:,} contributions, {commits:,} commits, {agent_pct}% agents)")


if __name__ == "__main__":
    main()
