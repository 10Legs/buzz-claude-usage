"""Tests for the usage report and server. Run: python3 -m unittest discover tests"""
import json, os, sys, tempfile, threading, unittest, urllib.error, urllib.request
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
import claude_usage_report as r  # noqa: E402
import claude_usage_server as s  # noqa: E402
import claude_limits as lim  # noqa: E402

NOW = datetime.now(timezone.utc)


def msg(mid, ts, out=10, inp=1, cr=100, cw=20, kind="assistant"):
    return json.dumps({"type": kind, "timestamp": ts.isoformat().replace("+00:00", "Z"),
                       "message": {"id": mid, "model": "claude-test", "usage": {
                           "input_tokens": inp, "output_tokens": out,
                           "cache_read_input_tokens": cr, "cache_creation_input_tokens": cw}}})


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.projects = os.path.join(self.tmp.name, "projects")
        self.cfg = r.load_config()
        self.cfg.update(projects_dir=self.projects, agents={"alpha": "/srv/agents/alpha", "alpha-two": "/srv/agents/alpha-two"},
                        unmatched_label="(other)", _config_dir=self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, workdir, lines, name="s.jsonl"):
        d = os.path.join(self.projects, r.encode_dir(workdir))
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, name), "w") as f:
            f.write("\n".join(lines) + "\n")


class ReportTests(Fixture):
    def test_encode_dir_matches_claude_code(self):
        self.assertEqual(r.encode_dir("/home/u/my.repo/"), "-home-u-my-repo")

    def test_dedup_keeps_last_entry_per_message(self):
        self.write("/srv/agents/alpha", [msg("m1", NOW, out=1), msg("m1", NOW, out=50), msg("m2", NOW, out=5)])
        by_agent, _, _ = r.summarize(r.collect(self.cfg, 7))
        self.assertEqual(by_agent["alpha"]["messages"], 2)
        self.assertEqual(by_agent["alpha"]["output"], 55)

    def test_longest_prefix_wins_and_worktrees_match(self):
        self.write("/srv/agents/alpha-two", [msg("a", NOW)])
        self.write("/srv/agents/alpha-wt-feature", [msg("b", NOW)])
        self.write("/somewhere/else", [msg("c", NOW)])
        by_agent, _, _ = r.summarize(r.collect(self.cfg, 7))
        self.assertEqual(by_agent["alpha-two"]["messages"], 1)
        self.assertEqual(by_agent["alpha"]["messages"], 1)
        self.assertEqual(by_agent["(other)"]["messages"], 1)

    def test_window_and_noise_are_ignored(self):
        self.write("/srv/agents/alpha", [msg("old", NOW - timedelta(days=10)), msg("new", NOW),
                                         msg("u", NOW, kind="user"), "not json {", json.dumps({"type": "assistant"})])
        rows = r.collect(self.cfg, 7)
        self.assertEqual([x["input"] for x in rows], [1])

    def test_launcher_glob(self):
        ldir = os.path.join(self.tmp.name, "launchers")
        os.makedirs(ldir)
        with open(os.path.join(ldir, "beta-launch"), "w") as f:
            f.write("#!/bin/sh\nexport X=1\ncd /srv/agents/beta || exit 1\nexec agent\n")
        self.cfg["launcher_glob"] = os.path.join(ldir, "*-launch")
        self.write("/srv/agents/beta", [msg("x", NOW)])
        by_agent, _, _ = r.summarize(r.collect(self.cfg, 7))
        self.assertIn("beta", by_agent)

    def test_report_shape(self):
        self.write("/srv/agents/alpha", [msg("m", NOW)])
        doc = json.loads(json.dumps(r.report(self.cfg, 3)))
        self.assertEqual(doc["days"], 3)
        self.assertEqual(set(doc), {"days", "generated", "by_agent", "by_day", "by_agent_day"})
        day = next(iter(doc["by_day"]))
        self.assertEqual(doc["by_agent_day"][day]["alpha"]["cache_write"], 20)


class LimitsTests(Fixture):
    def test_parse_keeps_only_valid_windows(self):
        doc = {"five_hour": {"utilization": 42.04, "resets_at": "2026-11-22T08:39:59+00:00"},
               "seven_day": {"utilization": None, "resets_at": "2026-11-24T15:59:59+00:00"},
               "seven_day_opus": {"utilization": 9, "resets_at": "2026-11-24T15:59:59+00:00"}}
        self.assertEqual(lim.parse(doc), {"five_hour": {"used_pct": 42.0, "resets_at": "2026-11-22T08:39:59+00:00"}})
        self.assertEqual(lim.parse(None), {})

    def test_disabled_by_default_and_failures_return_nothing(self):
        self.assertEqual(lim.safe_fetch({}), {})
        self.assertEqual(lim.safe_fetch({"plan_limits": True, "credentials_file": os.path.join(self.tmp.name, "missing.json")}), {})

    def test_expired_token_is_refused(self):
        path = os.path.join(self.tmp.name, "c.json")
        with open(path, "w") as f:
            json.dump({"claudeAiOauth": {"accessToken": "t", "expiresAt": 1000}}, f)
        with self.assertRaises(RuntimeError):
            lim.fetch(path)

    def test_report_adds_limits_and_session_by_agent(self):
        resets = NOW + timedelta(hours=2)  # window began 3 hours ago
        self.write("/srv/agents/alpha", [msg("in", NOW - timedelta(hours=1), out=7), msg("before", NOW - timedelta(hours=4), out=100)])
        limits = {"five_hour": {"used_pct": 10.0, "resets_at": resets.isoformat()}}
        doc = json.loads(json.dumps(r.report(self.cfg, 3, limits)))
        self.assertEqual(doc["limits"], limits)
        self.assertEqual(doc["session"]["by_agent"]["alpha"]["output"], 7)
        self.assertNotIn("limits", r.report(self.cfg, 3))


class ServerTests(Fixture):
    TOKEN = "t" * 32

    def setUp(self):
        super().setUp()
        self.write("/srv/agents/alpha", [msg("m", NOW)])
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), s.make_handler(self.cfg, self.TOKEN))
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"

    def tearDown(self):
        self.srv.shutdown()
        super().tearDown()

    def get(self, path, token=None):
        req = urllib.request.Request(self.base + path, headers={"Authorization": f"Bearer {token}"} if token else {})
        try:
            with urllib.request.urlopen(req) as res:
                return res.status, res.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def test_health_needs_no_token(self):
        self.assertEqual(self.get("/healthz")[0], 200)

    def test_token_required(self):
        self.assertEqual(self.get("/usage")[0], 401)
        self.assertEqual(self.get("/usage", "wrong")[0], 401)

    def test_usage_and_clamping(self):
        code, body = self.get("/usage?days=999", self.TOKEN)
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)["days"], 60)
        self.assertEqual(self.get("/usage?days=abc", self.TOKEN)[0], 400)
        self.assertEqual(self.get("/nope", self.TOKEN)[0], 404)


if __name__ == "__main__":
    unittest.main()
