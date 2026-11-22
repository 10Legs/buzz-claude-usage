// Claude usage — Buzz plugin API v1 module. Reads the read-only usage server (see README).
// __SERVER_ORIGIN__ is filled in by scripts/build_plugin.py. The access token is pasted once and kept in this app's localStorage.
export const inject = ["react", "pages", "host"];

const ORIGIN = "__SERVER_ORIGIN__";
const TOKEN_KEY = "buzz.claude-usage.token";

function fmt(n) {
  if (n >= 1e6) return (n / 1e6).toFixed(1) + "M";
  if (n >= 1e3) return Math.round(n / 1e3) + "k";
  return String(n);
}

function until(iso) {
  const ms = new Date(iso).getTime() - Date.now();
  if (!(ms > 0)) return "now";
  const m = Math.round(ms / 60000), d = Math.floor(m / 1440), hr = Math.floor((m % 1440) / 60);
  return d > 0 ? `${d}d ${hr}h` : hr > 0 ? `${hr}h ${m % 60}m` : `${m}m`;
}

export function apply(ctx) {
  const React = ctx.react;
  const h = React.createElement;

  function Usage() {
    const [token, setToken] = React.useState(() => { try { return localStorage.getItem(TOKEN_KEY) || ""; } catch (e) { return ""; } });
    const [draft, setDraft] = React.useState("");
    const [days, setDays] = React.useState(7);
    const [data, setData] = React.useState(null);
    const [error, setError] = React.useState(null);
    const [loading, setLoading] = React.useState(false);

    const seq = React.useRef(0);
    const load = React.useCallback(async () => {
      if (!token) return;
      const mine = ++seq.current;  // ignore responses that arrive after a newer request
      setLoading(true);
      setError(null);
      try {
        const res = await ctx.host.request({ url: `${ORIGIN}/usage?days=${days}`, method: "GET", headers: { Authorization: "Bearer " + token }, timeoutMs: 20000 });
        if (res.status === 401) throw new Error("the server rejected the token (use Change token)");
        if (res.status !== 200) throw new Error(`server returned ${res.status}`);
        const body = typeof res.body === "string" ? res.body : new TextDecoder().decode(res.body);
        if (mine === seq.current) setData(JSON.parse(body));
      } catch (e) {
        if (mine === seq.current) setError(String(e && e.message ? e.message : e));
      } finally {
        if (mine === seq.current) setLoading(false);
      }
    }, [days, token]);

    React.useEffect(() => { load(); }, [load]);

    const agents = data
      ? Object.entries(data.by_agent).sort((a, b) => (b[1].output + b[1].cache_write) - (a[1].output + a[1].cache_write))
      : [];
    const dayRows = data ? Object.entries(data.by_day).sort((a, b) => (a[0] < b[0] ? 1 : -1)) : [];
    const maxDay = Math.max(1, ...dayRows.map(([, t]) => t.output + t.cache_write));
    const cell = (v, right) => h("td", { style: { padding: "4px 8px", textAlign: right ? "right" : "left" } }, v);
    const th = (v, right) => h("th", { style: { padding: "4px 8px", textAlign: right ? "right" : "left" } }, v);

    const limitBar = (label, w) => w && h("div", { key: label, style: { margin: "4px 0" } },
      h("div", { style: { display: "flex", justifyContent: "space-between" } },
        h("span", null, label), h("span", null, `${w.used_pct}% used · resets in ${until(w.resets_at)}`)),
      h("div", { style: { height: "10px", background: "rgba(128,128,128,0.25)" } },
        h("div", { style: { height: "10px", width: Math.min(100, w.used_pct) + "%", background: "currentColor", opacity: w.used_pct >= 90 ? 0.9 : 0.5 } })));
    const session = data && data.session
      ? Object.entries(data.session.by_agent).sort((a, b) => (b[1].output + b[1].cache_write) - (a[1].output + a[1].cache_write))
      : [];

    const saveToken = () => { const t = draft.trim(); try { localStorage.setItem(TOKEN_KEY, t); } catch (e) { /* keep in memory only */ } setToken(t); setDraft(""); };
    const clearToken = () => { try { localStorage.removeItem(TOKEN_KEY); } catch (e) { /* ignore */ } setToken(""); setData(null); };

    if (!token) {
      return h("section", { className: "ui-card", style: { padding: "var(--space-panel-inset)" } },
        h("h1", null, "Claude usage"),
        h("p", null, "Paste the access token for the Claude usage server."),
        h("input", { type: "password", value: draft, onChange: (e) => setDraft(e.target.value), placeholder: "token", style: { width: "100%" } }),
        " ",
        h("button", { onClick: saveToken, disabled: !draft.trim() }, "Save"));
    }

    return h("section", { className: "ui-card", style: { padding: "var(--space-panel-inset)" } },
      h("h1", null, "Claude usage"),
      h("p", null, "Tokens used by your Claude Code agents. Cache reads are cheap re-reads of conversation history; output and cache writes are the main cost drivers."),
      h("label", null, "Window: ",
        h("select", { value: days, onChange: (e) => setDays(Number(e.target.value)) },
          [1, 3, 7, 14, 30].map((d) => h("option", { key: d, value: d }, `${d} day${d > 1 ? "s" : ""}`))),
        " ",
        h("button", { onClick: load, disabled: loading }, loading ? "Loading…" : "Refresh"),
        " ",
        h("button", { onClick: clearToken }, "Change token")),
      error && h("p", { role: "alert" }, `Couldn't load usage: ${error}`),
      data && data.limits && h("h2", null, "Plan limits"),
      data && data.limits && limitBar("5-hour session", data.limits.five_hour),
      data && data.limits && limitBar("Weekly", data.limits.seven_day),
      data && session.length > 0 && h("h2", null, "This session by agent"),
      data && session.length > 0 && h("table", { style: { width: "100%", borderCollapse: "collapse" } },
        h("thead", null, h("tr", null, th("Agent"), th("Msgs", true), th("Output", true), th("Cache write", true))),
        h("tbody", null, session.map(([name, t]) =>
          h("tr", { key: name }, cell(name), cell(fmt(t.messages), true), cell(fmt(t.output), true), cell(fmt(t.cache_write), true))))),
      data && h("h2", null, "By agent"),
      data && h("table", { style: { width: "100%", borderCollapse: "collapse" } },
        h("thead", null, h("tr", null, th("Agent"), th("Msgs", true), th("Output", true), th("Cache write", true), th("Cache read", true))),
        h("tbody", null, agents.map(([name, t]) =>
          h("tr", { key: name }, cell(name), cell(fmt(t.messages), true), cell(fmt(t.output), true), cell(fmt(t.cache_write), true), cell(fmt(t.cache_read), true))))),
      data && h("h2", null, "By day"),
      data && h("div", null, dayRows.map(([day, t]) => {
        const top = Object.entries(data.by_agent_day[day] || {}).sort((a, b) => (b[1].output + b[1].cache_write) - (a[1].output + a[1].cache_write))[0];
        const w = Math.round(((t.output + t.cache_write) / maxDay) * 100);
        return h("div", { key: day, style: { display: "flex", alignItems: "center", gap: "8px", margin: "2px 0" } },
          h("span", { style: { width: "6.5em" } }, day),
          h("span", { style: { flex: 1 } }, h("span", { style: { display: "block", height: "10px", width: w + "%", background: "currentColor", opacity: 0.5 } })),
          h("span", { style: { width: "16em", textAlign: "right" } }, `out ${fmt(t.output)} · cw ${fmt(t.cache_write)} · top ${top ? top[0] : "-"}`));
      })),
      data && h("p", { role: "status" }, `Generated ${data.generated}`));
  }

  ctx.pages.register({ id: "main", title: "Claude usage", primary: true, component: Usage });
}
