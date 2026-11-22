# buzz-claude-usage

See how many tokens your [Claude Code](https://docs.claude.com/en/docs/claude-code/overview) agents use, by agent and by day, inside the
[Buzz](https://github.com/block/buzz-app) desktop app.

It has two parts:

| Part | What it is |
| --- | --- |
| **Server** (`server/`) | A small read-only HTTP service in Python 3 (standard library only). It reads the session transcripts Claude Code writes under `~/.claude/projects` and adds up the token counts. |
| **Plugin** (`plugin/`) | A Buzz plugin that adds a "Claude usage" page to the desktop app: a time window picker, a table by agent, and a bar per day showing the busiest agent. |

```
Buzz desktop app  ──(HTTPS, bearer token)──▶  claude_usage_server.py  ──▶  ~/.claude/projects/*/*.jsonl
 (Claude usage plugin)                         (read-only, 127.0.0.1)        (Claude Code transcripts)
```

It counts **tokens only**. It does not estimate prices, because what you pay depends on your plan, model and discounts.

> **Status.** The report and server are covered by tests (`python3 -m unittest discover tests`). The plugin is written against the Buzz
> plugin documentation, which describes the API (v1) as a preview that can change. Expect rough edges and please open an issue if
> something breaks.

## Contents

- [Requirements](#requirements)
- [Quick start](#quick-start)
- [Configuration](#configuration)
- [Naming your agents](#naming-your-agents)
- [Command-line report](#command-line-report)
- [Exposing the server over HTTPS](#exposing-the-server-over-https)
- [Building and installing the plugin](#building-and-installing-the-plugin)
- [Using the plugin](#using-the-plugin)
- [Reading the numbers](#reading-the-numbers)
- [Running the server as a service](#running-the-server-as-a-service)
- [Security model](#security-model)
- [HTTP API](#http-api)
- [Troubleshooting](#troubleshooting)
- [Limitations](#limitations)
- [Project layout](#project-layout)
- [Development](#development)
- [License](#license)

## Requirements

- **Server host:** the machine where your Claude Code agents run, with Python 3.9 or newer. No packages to install. Run the server as the
  same user as the agents, because it reads that user's `~/.claude/projects`.
- **HTTPS in front of the server.** Buzz only lets a plugin call an exact HTTPS origin. A private-network option such as
  [Tailscale Serve](https://tailscale.com/kb/1242/tailscale-serve) works well; a reverse proxy with a TLS certificate also works.
- **A Buzz desktop app with plugin support** (Settings → Plugins). The plugin system is a preview, so check that your version has it.

You don't need Buzz agents specifically. Anything that runs Claude Code on that machine (agents, scripts, your own sessions) shows up.

## Quick start

```sh
git clone https://github.com/10Legs/buzz-claude-usage.git
cd buzz-claude-usage

# 1. Configure: name your agents and create a token
cp config.example.json config.json
$EDITOR config.json                 # map agent names to their working directories (see "Naming your agents")
openssl rand -hex 24 > token && chmod 600 token

# 2. Check the numbers from the command line
python3 server/claude_usage_report.py --config config.json --days 7

# 3. Run the server (listens on 127.0.0.1:8792)
CLAUDE_USAGE_CONFIG=config.json python3 server/claude_usage_server.py

# 4. In another terminal: expose it over HTTPS on your private network (example uses Tailscale Serve)
tailscale serve --bg --https=10445 http://127.0.0.1:8792

# 5. Build the plugin for the HTTPS origin you just created
python3 scripts/build_plugin.py https://your-host.your-tailnet.ts.net:10445
```

Then install `dist/claude-usage` in the Buzz app (see [Building and installing the plugin](#building-and-installing-the-plugin)) and paste
the contents of `token` when the plugin asks for it.

## Configuration

The server reads a JSON file. Point to it with the `CLAUDE_USAGE_CONFIG` environment variable. Every key is optional; start from
[`config.example.json`](config.example.json).

| Key | Default | Meaning |
| --- | --- | --- |
| `projects_dir` | `~/.claude/projects` | Where Claude Code keeps its session transcripts. |
| `agents` | `{}` | Map of agent name → working directory. See [Naming your agents](#naming-your-agents). |
| `launcher_glob` | `null` | Optional glob of launcher scripts to discover agents from, for example `~/buzz/agents/*-launch`. |
| `unmatched_label` | `"(other)"` | Label for usage that doesn't match any agent (manual sessions, other tools). |
| `max_days` | `60` | Largest window the server will compute. Longer requests are clamped. |
| `cache_seconds` | `60` | How long a computed report is reused before the transcripts are read again. |
| `bind_host` | `127.0.0.1` | Address to listen on. Keep it on localhost and put HTTPS in front. |
| `port` | `8792` | Port to listen on. |
| `plan_limits` | `false` | Optional. When `true`, `/usage` also returns your plan's 5-hour session and weekly limits and a per-agent breakdown of the current session. See [Plan limits](#plan-limits). |
| `credentials_file` | `~/.claude/.credentials.json` | Where the Claude Code login lives, used only when `plan_limits` is on. |
| `token_file` | `token` | File holding the access token, relative to the config file (or to the current directory when no config is set). Must contain at least 24 characters. |

You can supply the token through the `CLAUDE_USAGE_TOKEN` environment variable instead of a file. It takes precedence over `token_file`.

Both `config.json` and `token` are in `.gitignore`. Don't commit them.

## Naming your agents

Claude Code stores each session in a folder named after the session's working directory, with `/` and `.` replaced by `-`. For example,
a session started in `/srv/agents/reviewer` is stored in `~/.claude/projects/-srv-agents-reviewer/`. The report uses that to give each
folder an agent name.

**Option 1: list them.** Map each agent to the directory it runs in:

```json
{
  "agents": {
    "reviewer": "/srv/agents/reviewer",
    "docs-bot": "~/work/docs"
  }
}
```

**Option 2: discover them from launcher scripts.** If each agent has a launcher script named `<agent>-launch` (or `<agent>.sh`) whose first
`cd <dir>` line sets its working directory, as Buzz agent launchers usually do, point `launcher_glob` at them:

```json
{ "launcher_glob": "~/buzz/agents/*-launch" }
```

You can use both. Entries under `agents` win when both name the same directory.

Matching rules:

- **Subfolders and worktrees count.** A session in `/srv/agents/reviewer-wt-fix-123` or `/srv/agents/reviewer/sub` is counted as
  `reviewer`, because its folder name starts with the agent's.
- **The longest match wins**, so `alpha-two` isn't counted as `alpha`.
- Everything else goes under `unmatched_label`.

## Command-line report

The same numbers are available without the server or the plugin:

```sh
python3 server/claude_usage_report.py --config config.json --days 7          # table
python3 server/claude_usage_report.py --config config.json --days 7 --json   # same JSON the server returns
```

Example output (numbers are illustrative):

```
Claude Code usage, last 7 days (tokens; cache_read is cheap re-reads of history)
agent                        msgs   output   input  cache_wr  cache_rd
reviewer                     1840     1.2M      4k      9.8M    410.2M
docs-bot                      612     380k      1k      2.1M     88.0M
(other)                       201      90k     512      700k     21.4M

by day:
  2026-11-20  msgs=  402 output=  260k cache_wr=  2.0M cache_rd= 90.1M  top=reviewer
```

## Exposing the server over HTTPS

The server speaks plain HTTP on localhost on purpose. Put a TLS-terminating proxy in front of it, reachable only by your own devices.

**Tailscale Serve** (private to your tailnet):

```sh
tailscale serve --bg --https=10445 http://127.0.0.1:8792
tailscale serve status                      # shows the https:// URL
tailscale serve --https=10445 off           # undo
```

**Reverse proxy** (Caddy example), only if you also restrict who can reach it (VPN, IP allowlist, mTLS):

```
usage.example.net {
    reverse_proxy 127.0.0.1:8792
}
```

Do not expose the server directly to the open internet. The token is the only login.

## Building and installing the plugin

Buzz asks the owner to approve exactly which network address a plugin may call, and that address is fixed in the plugin manifest. So the
plugin is generated for your server's address:

```sh
python3 scripts/build_plugin.py https://usage.example.net:10445
```

The argument must be an HTTPS origin: scheme, host and optional port, no path. The script writes `dist/claude-usage/manifest.json` and
`dist/claude-usage/plugin.js`. Rebuild whenever the address changes.

Install it in the Buzz desktop app:

1. Copy `dist/claude-usage` to the machine that runs the app (if it is a different machine).
2. Open **Settings → Plugins → Load from folder** and choose `dist/claude-usage`.
3. Choose **Install plugin**, review the single network permission it requests, and confirm.
4. Enable the plugin. A "Claude usage" tab appears.
5. Open the tab and paste the access token. It is saved in the app's local storage.

To remove it, disable the plugin and delete it in the same Settings page.

## Using the plugin

- **Window:** pick 1, 3, 7, 14 or 30 days. The page reloads automatically.
- **Refresh:** re-fetch now. The server reuses a computed report for `cache_seconds` (default 60), so refreshing faster than that returns
  the same numbers.
- **By agent:** one row per agent, sorted by output plus cache writes (the main cost drivers).
- **By day:** newest first. The bar length is that day's output plus cache writes relative to the busiest day, and "top" names the agent
  that used the most.
- **Change token:** forget the saved token and ask for a new one.

## Reading the numbers

| Column | What it is | Why it matters |
| --- | --- | --- |
| **Msgs** | API responses (each counted once) | Rough measure of how busy an agent was |
| **Output** | Tokens the model generated | Usually the most expensive tokens |
| **Cache write** | Prompt tokens written to the prompt cache | Charged at a premium; grows with long or changing context |
| **Cache read** | Prompt tokens re-read from the cache | Very large but cheap; it's the conversation history being reused |
| **Input** (CLI only) | Uncached prompt tokens | Usually small when caching works |

A busy agent with a long conversation shows huge cache reads. That's normal. Watch output and cache writes when you're looking for the
agents that cost the most.

## Running the server as a service

An example systemd user unit is in [`deploy/claude-usage.service.example`](deploy/claude-usage.service.example):

```sh
cp deploy/claude-usage.service.example ~/.config/systemd/user/claude-usage.service
$EDITOR ~/.config/systemd/user/claude-usage.service     # set the two paths
systemctl --user daemon-reload
systemctl --user enable --now claude-usage
journalctl --user -u claude-usage -f
```

Run it as the user whose agents you want to measure. To keep it running after you log out, enable lingering:
`loginctl enable-linger $USER`.

## Plan limits

Off by default. Token counts can't tell you how much of your plan is left, so with `"plan_limits": true` the server asks Anthropic for the
account's limits and adds them to `/usage`:

```json
"limits":  { "five_hour": { "used_pct": 42.0, "resets_at": "2026-11-22T08:39:59+00:00" }, "seven_day": { "used_pct": 55.0, "resets_at": "..." } },
"session": { "started": "2026-11-22T03:39:59+00:00", "by_agent": { "reviewer": { "output": 1200, "...": 0 } } }
```

The plugin shows two bars ("5-hour session" and "Weekly", with percent used and time to reset) and a "This session by agent" table.
`session.by_agent` counts transcript tokens since the 5-hour window began (its reset time minus 5 hours).

- **How it works.** It calls `https://api.anthropic.com/api/oauth/usage` with the Claude Code login token from `credentials_file`. That
  endpoint is not documented and may change; when the call fails, `limits` is simply left out and the rest of the report still works.
  The reason is logged (never the token).
- **Account-wide.** The limits belong to the logged-in account, so they cover everything that account does, not just the agents you named.
- **Token expiry.** Claude Code refreshes the login file while it runs. If the file's token has expired the server doesn't refresh it and
  reports no limits until Claude Code has run again.
- **Security.** This is the one place the server reads a credential. The token is held in memory for that call, never logged, written or
  returned to the plugin. Turn it on only on a host where that is acceptable.

## Security model

**What the server exposes:** agent names (from your config), daily token totals and message counts. It never sends prompts, responses,
file contents, tool calls or model credentials, even though the transcripts it reads contain them. (With `plan_limits` on it reads the
Claude login token to ask Anthropic for your limits, but still never returns it; see [Plan limits](#plan-limits).)

**What the server enforces**

- **Authentication.** Every request except `/healthz` needs `Authorization: Bearer <token>`. The comparison is constant-time. Use a long
  random token (`openssl rand -hex 24`).
- **Read-only.** It only reads transcripts and never writes, deletes or changes anything.
- **Localhost by default.** It listens on `127.0.0.1`. Reaching it from another device requires the HTTPS layer you choose.
- **Bounded work.** The window is clamped to `max_days`, and results are cached so repeated requests don't re-read every transcript.
- **Quiet logging.** It doesn't log requests, and it never logs the token.

**What it does not do**

- It does not encrypt traffic. That is the job of the HTTPS layer you put in front of it.
- One shared token, no per-user accounts and no rate limiting.
- Activity patterns are information too: anyone with the token can see when each agent was busy.
- The plugin keeps the token in the app's local storage, readable by anything that runs inside the app. Plugins are trusted code. Install
  only plugins you trust.

To report a vulnerability, please open a private security advisory on the GitHub repository rather than a public issue.

## HTTP API

| Request | Auth | Response |
| --- | --- | --- |
| `GET /healthz` | none | `200 ok` |
| `GET /usage?days=N` | bearer | `200` JSON report for the last N days (1 to `max_days`, default 7) |

Errors are JSON `{"error": "..."}` with `400` (bad `days`), `401` (missing or wrong token), `404` (unknown path) or `500` (the report
failed; details are in the server log).

Report shape:

```json
{
  "days": 7,
  "generated": "2026-11-21T09:00:00-05:00",
  "by_agent":     { "reviewer": { "input": 4000, "output": 1200000, "cache_read": 410200000, "cache_write": 9800000, "messages": 1840 } },
  "by_day":       { "2026-11-20": { "input": 600, "output": 260000, "cache_read": 90100000, "cache_write": 2000000, "messages": 402 } },
  "by_agent_day": { "2026-11-20": { "reviewer": { "input": 400, "output": 200000, "cache_read": 70000000, "cache_write": 1500000, "messages": 300 } } }
}
```

Days are in the server's local time zone. The window is rolling (N × 24 hours back from now), so a 7-day report can show 8 day rows, the oldest one partial.

## Troubleshooting

| Symptom | Likely cause and fix |
| --- | --- |
| Plugin says the server rejected the token | The token in the app doesn't match the server's. Use **Change token** and paste the contents of `token` again. |
| Plugin can't reach the server, or the request is blocked | The plugin was built for a different address, or the HTTPS endpoint is down. Rebuild with the exact origin from `tailscale serve status` (or your proxy) and reinstall. |
| Everything shows as `(other)` | Agent working directories don't match. Run the CLI report and compare `ls ~/.claude/projects` with the paths in your config. |
| An agent is missing | It hasn't run in the chosen window, or it runs as a different user. The server only sees its own user's `~/.claude/projects`. |
| Server exits with "no token" | Create the token file next to `config.json`, or set `CLAUDE_USAGE_TOKEN`. |
| Numbers lag by up to a minute | That's the cache (`cache_seconds`). |

## Limitations

- Tokens only. No prices or cost estimates.
- One machine and one user per server. Run a server per machine if your agents are spread out.
- It depends on the transcript format Claude Code writes today (`type`, `timestamp`, `message.id`, `message.usage`). A future Claude Code
  release could change it.
- Transcripts you have deleted or that Claude Code has cleaned up can't be counted.
- Single shared token, no per-user access control.
- The plugin API is a preview and can change between Buzz releases.

## Project layout

```
server/claude_usage_report.py   reads transcripts and builds the report (also a CLI)
server/claude_usage_server.py   read-only HTTP server (standard library only)
plugin/manifest.template.json   plugin manifest, origin filled in by the build script
plugin/plugin.template.js       plugin code, origin filled in by the build script
scripts/build_plugin.py         builds dist/claude-usage for your server's HTTPS origin
deploy/claude-usage.service.example   systemd user unit
config.example.json             example configuration
tests/test_report.py            report and server tests
```

## Development

```sh
python3 -m unittest discover tests          # report and server tests
python3 scripts/build_plugin.py https://usage.example.net:10445
node --check dist/claude-usage/plugin.js    # syntax check (copy to .mjs if your Node needs it)
```

Contributions are welcome. Keep the server dependency-free and read-only, never return transcript content, and add a test for any change
to the counting or matching rules.

## License

MIT. See [`LICENSE`](LICENSE).
