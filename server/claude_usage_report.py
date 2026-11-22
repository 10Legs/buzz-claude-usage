#!/usr/bin/env python3
"""claude_usage_report — Claude Code token usage by agent and by day.

Reads the session transcripts Claude Code writes under ~/.claude/projects/<project>/*.jsonl and adds up the
token counts recorded on each assistant message. Tokens only; no prices.

  python3 server/claude_usage_report.py [--days N] [--json] [--config config.json]

Each API message is counted once: transcripts can repeat a message while it streams, so entries are
de-duplicated by message id and the last entry (which carries the final counts) wins.

A project folder is labelled with an agent name when its working directory matches one in the config,
either listed under "agents" or discovered from launcher scripts ("launcher_glob"). Worktrees and
subdirectories of a matched directory get the same label. Anything else is "unmatched_label".
"""
import argparse, glob, json, os, re, sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

DEFAULTS = {
    "projects_dir": "~/.claude/projects",
    "agents": {},
    "launcher_glob": None,
    "unmatched_label": "(other)",
    "max_days": 60,
    "cache_seconds": 60,
    "bind_host": "127.0.0.1",
    "port": 8792,
    "token_file": "token",
}
KEYS = ("input", "output", "cache_read", "cache_write")


def load_config(path=None):
    """Read the JSON config (optional for the report). Missing keys fall back to DEFAULTS."""
    cfg = dict(DEFAULTS)
    path = path or os.environ.get("CLAUDE_USAGE_CONFIG")
    if path:
        with open(path) as f:
            cfg.update(json.load(f))
        cfg["_config_dir"] = os.path.dirname(os.path.abspath(path))
    else:
        cfg["_config_dir"] = os.getcwd()
    cfg["projects_dir"] = os.path.expanduser(cfg["projects_dir"])
    return cfg


def encode_dir(path):
    """Claude Code names a project folder after its working directory, with '/' and '.' replaced by '-'."""
    return os.path.expanduser(path).rstrip("/").replace("/", "-").replace(".", "-")


def agent_map(cfg):
    """Map encoded working directory -> agent name, from the config and from launcher scripts."""
    m = {}
    if cfg.get("launcher_glob"):
        # Launcher convention: the file is named <agent>-launch (or <agent>.sh) and its first `cd <dir>` line
        # sets the agent's working directory.
        for f in glob.glob(os.path.expanduser(cfg["launcher_glob"])):
            name = re.sub(r"(-launch|\.sh)$", "", os.path.basename(f))
            with open(f, errors="replace") as fh:
                for line in fh:
                    r = re.match(r"\s*cd\s+(\S+)", line)
                    if r:
                        m[encode_dir(r.group(1).rstrip(";&|").strip("'\""))] = name
                        break
    for name, workdir in (cfg.get("agents") or {}).items():
        m[encode_dir(workdir)] = name
    return m


def agent_for(projdir, amap, unmatched="(other)"):
    """Longest matching prefix wins, so /a/b-c is not mistaken for /a/b."""
    for enc, name in sorted(amap.items(), key=lambda kv: -len(kv[0])):
        if projdir == enc or projdir.startswith(enc + "-"):
            return name
    return unmatched


def collect(cfg, days, now=None):
    """Return one row per API message in the last `days` days (a rolling window of days x 24 hours)."""
    amap = agent_map(cfg)
    labels = {}
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)
    seen = {}
    for f in glob.glob(os.path.join(cfg["projects_dir"], "*", "*.jsonl")):
        proj = os.path.basename(os.path.dirname(f))
        if os.path.getmtime(f) < cutoff.timestamp() - 86400:  # untouched for a day before the window: skip
            continue
        with open(f, errors="replace") as fh:
            for line in fh:
                if '"usage"' not in line:
                    continue
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if d.get("type") != "assistant":
                    continue
                m = d.get("message") or {}
                u = m.get("usage")
                if not u or not m.get("id"):
                    continue
                try:
                    ts = datetime.fromisoformat(d["timestamp"].replace("Z", "+00:00"))
                except (KeyError, ValueError, AttributeError):
                    continue
                if ts.tzinfo is None:  # Claude Code writes UTC ("...Z"); treat a bare timestamp the same way
                    ts = ts.replace(tzinfo=timezone.utc)
                if ts < cutoff:
                    continue
                seen[m["id"]] = (ts, proj, m.get("model", "?"), u)  # last entry for a message has the final counts
    rows = []
    for ts, proj, model, u in seen.values():
        if proj not in labels:
            labels[proj] = agent_for(proj, amap, cfg.get("unmatched_label", "(other)"))
        rows.append(dict(day=ts.astimezone().strftime("%Y-%m-%d"), agent=labels[proj], model=model,
                         input=u.get("input_tokens", 0) or 0, output=u.get("output_tokens", 0) or 0,
                         cache_read=u.get("cache_read_input_tokens", 0) or 0,
                         cache_write=u.get("cache_creation_input_tokens", 0) or 0))
    return rows


def _total():
    return dict.fromkeys(KEYS, 0) | {"messages": 0}


def summarize(rows):
    by_agent, by_day = defaultdict(_total), defaultdict(_total)
    by_agent_day = defaultdict(lambda: defaultdict(_total))
    for r in rows:
        for t in (by_agent[r["agent"]], by_day[r["day"]], by_agent_day[r["day"]][r["agent"]]):
            for k in KEYS:
                t[k] += r[k]
            t["messages"] += 1
    return by_agent, by_day, by_agent_day


def report(cfg, days):
    """The JSON document served at GET /usage."""
    by_agent, by_day, by_agent_day = summarize(collect(cfg, days))
    return {"days": days, "generated": datetime.now().astimezone().isoformat(),
            "by_agent": by_agent, "by_day": by_day, "by_agent_day": by_agent_day}


def fmt(n):
    return f"{n/1e6:.1f}M" if n >= 1e6 else f"{n/1e3:.0f}k" if n >= 1e3 else str(n)


def main():
    ap = argparse.ArgumentParser(description="Claude Code token usage by agent and day.")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--json", action="store_true", help="print the same JSON the server returns")
    ap.add_argument("--config", help="config file (default: $CLAUDE_USAGE_CONFIG, else built-in defaults)")
    a = ap.parse_args()
    cfg = load_config(a.config)
    if a.json:
        json.dump(report(cfg, a.days), sys.stdout)
        return
    by_agent, by_day, by_agent_day = summarize(collect(cfg, a.days))
    print(f"Claude Code usage, last {a.days} days (tokens; cache_read is cheap re-reads of history)")
    print(f"{'agent':26}{'msgs':>7}{'output':>9}{'input':>8}{'cache_wr':>10}{'cache_rd':>10}")
    for n, t in sorted(by_agent.items(), key=lambda kv: -(kv[1]["output"] + kv[1]["cache_write"])):
        print(f"{n:26}{t['messages']:>7}{fmt(t['output']):>9}{fmt(t['input']):>8}"
              f"{fmt(t['cache_write']):>10}{fmt(t['cache_read']):>10}")
    print("\nby day:")
    for d in sorted(by_day):
        t = by_day[d]
        top = max(by_agent_day[d].items(), key=lambda kv: kv[1]["output"] + kv[1]["cache_write"])[0]
        print(f"  {d}  msgs={t['messages']:>5} output={fmt(t['output']):>6} cache_wr={fmt(t['cache_write']):>6} "
              f"cache_rd={fmt(t['cache_read']):>6}  top={top}")


if __name__ == "__main__":
    main()
