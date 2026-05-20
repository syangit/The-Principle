#!/usr/bin/env python3
"""infero deployment regression suite (end-to-end, hits live servers).

Covers the deployed surface: genesis static assets, API relay auth gate,
hub (list / open-read / server-rendered page / gated write / health),
device-relay bot filter, and an optional headless-CDP genesis boot.

Usage:
    python3 src/tests/regression_e2e.py                 # http tests, both envs
    python3 src/tests/regression_e2e.py --env prod      # prod only
    python3 src/tests/regression_e2e.py --cdp           # + headless genesis boot
    python3 src/tests/regression_e2e.py --ssh           # + systemd unit health (needs key)
    python3 src/tests/regression_e2e.py --env dev --cdp --ssh

Exit code 0 = all pass, 1 = any fail. No third-party deps for HTTP/SSH tests;
--cdp needs `websockets` and a local Google Chrome.
"""
import argparse
import json
import socket
import ssl
import subprocess
import sys
import time
import urllib.request
import urllib.error

ENVS = {
    "prod": {"base": "https://infero.net",     "dev_mode": False},
    "dev":  {"base": "https://dev.infero.net", "dev_mode": True},
}
SSH = ["ssh", "-i", "~/.ssh/ec2_tokyo_2023.pem", "ubuntu@3.114.3.152"]
SYSTEMD_UNITS = ["api-relay-prod", "api-relay-dev", "device-relay",
                 "device-relay-dev", "hub-prod", "hub-dev"]

_results = []  # (group, name, ok, detail)


def check(group, name, ok, detail=""):
    _results.append((group, name, bool(ok), detail))
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {name}" + (f"  — {detail}" if detail and not ok else ""))
    return ok


def http(url, method="GET", headers=None, body=None, timeout=30):
    """Return (status, body_text, headers_dict). Never raises on HTTP errors."""
    req = urllib.request.Request(url, method=method, data=body,
                                 headers=headers or {})
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            return r.status, r.read().decode("utf-8", "replace"), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), dict(e.headers or {})
    except Exception as e:
        return -1, f"{type(e).__name__}: {e}", {}


# ── HTTP suite ──────────────────────────────────────────────────────────────
def test_http(env_name):
    cfg = ENVS[env_name]
    base = cfg["base"]
    g = f"http/{env_name}"
    print(f"\n== {g} ({base}) ==")

    # genesis static
    s, b, _ = http(f"{base}/genesis/")
    check(g, "genesis index 200 + INFERO", s == 200 and "INFERO" in b, f"status={s}")

    s, b, _ = http(f"{base}/genesis/models.js")
    check(g, "models.js 200 + window.MODELS + gemini-3.5-flash",
          s == 200 and "window.MODELS" in b and "gemini-3.5-flash" in b, f"status={s}")

    s, b, _ = http(f"{base}/genesis/i18n.js")
    check(g, "i18n.js 200", s == 200, f"status={s}")

    # api relay auth gate (endpoint alive, not 404/502)
    s, _, _ = http(f"{base}/api/v1/models")
    check(g, "api relay /api/v1/models alive (401/200)", s in (200, 401, 403), f"status={s}")

    # hub list
    s, b, _ = http(f"{base}/hub/list")
    total, skill_name = 0, None
    if s == 200:
        try:
            d = json.loads(b)
            total = d.get("total", 0)
            if d.get("skills"):
                skill_name = d["skills"][0]["name"]
        except Exception:
            pass
    check(g, "hub/list 200 + total>=1", s == 200 and total >= 1, f"status={s} total={total}")

    if not skill_name:
        skill_name = "intellectual_honesty"

    # hub skill read is OPEN (no identity) — the regression for the read-gate fix
    s, b, _ = http(f"{base}/hub/skill/{skill_name}")
    has_instr = False
    if s == 200:
        try:
            has_instr = bool(json.loads(b).get("instruction"))
        except Exception:
            pass
    check(g, "hub/skill read open w/o identity (200 + instruction)",
          s == 200 and has_instr, f"status={s}")

    # hub server-rendered detail page — curl-able, no JS
    s, b, h = http(f"{base}/hub/{skill_name}")
    ctype = h.get("Content-Type", "")
    check(g, "hub/<name> server-rendered HTML (curl-able)",
          s == 200 and "text/html" in ctype and "<h1>" in b and "Instruction" in b,
          f"status={s} ctype={ctype!r}")

    # hub catalog index still served (hub.html)
    s, b, _ = http(f"{base}/hub")
    check(g, "hub catalog index 200", s == 200 and len(b) > 500, f"status={s}")

    # hub write is GATED for anonymous (prod=429; dev DEV_MODE=1 won't 429 but must not 200)
    s, _, _ = http(f"{base}/hub/submit", method="POST",
                   headers={"Content-Type": "application/json"}, body=b"{}")
    if cfg["dev_mode"]:
        check(g, "hub/submit anon not accepted", s != 200, f"status={s}")
    else:
        check(g, "hub/submit anon gated (429)", s == 429, f"status={s}")

    # hub health
    s, b, _ = http(f"{base}/hub/health")
    ok_health = False
    if s == 200:
        try:
            ok_health = json.loads(b).get("ok") is True
        except Exception:
            pass
    check(g, "hub/health ok", s == 200 and ok_health, f"status={s}")

    # device-relay bot filter: bot UA must NOT consume code (204), PS UA reaches relay
    s_bot, _, _ = http(f"{base}/device-relay/pair/ZZZZ/ps1",
                       headers={"User-Agent": "Slackbot-LinkExpanding"})
    check(g, "device-relay bot UA blocked (204)", s_bot == 204, f"status={s_bot}")
    s_ps, _, _ = http(f"{base}/device-relay/pair/ZZZZ/ps1",
                      headers={"User-Agent": "Mozilla/5.0 (Windows NT) WindowsPowerShell/5.1"})
    check(g, "device-relay PS UA reaches relay (200 not-found body)", s_ps == 200, f"status={s_ps}")


# ── CDP suite: boot genesis in headless chrome ──────────────────────────────
def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _chrome_bin():
    import os
    for p in ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
              "/usr/bin/google-chrome", "/usr/bin/chromium",
              "/usr/bin/chromium-browser"):
        if os.path.exists(p):
            return p
    return None


def test_cdp(env_name):
    g = f"cdp/{env_name}"
    print(f"\n== {g} (headless genesis boot) ==")
    base = ENVS[env_name]["base"]
    try:
        from websockets.sync.client import connect
    except Exception as e:
        check(g, "websockets available", False, f"import: {e}")
        return
    chrome = _chrome_bin()
    if not chrome:
        check(g, "chrome binary found", False, "no Google Chrome / chromium")
        return

    port = _free_port()
    profile = f"/tmp/infero-regress-{port}"
    proc = subprocess.Popen(
        [chrome, "--headless=new", f"--remote-debugging-port={port}",
         f"--user-data-dir={profile}", "--no-first-run",
         "--no-default-browser-check", "--disable-gpu", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        # wait for CDP up
        ver = None
        for _ in range(30):
            try:
                ver = json.load(urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/json/version", timeout=2))
                break
            except Exception:
                time.sleep(0.5)
        if not check(g, "headless CDP endpoint up", bool(ver),
                     "no /json/version"):
            return

        # open genesis tab
        tab = json.load(urllib.request.urlopen(
            urllib.request.Request(
                f"http://127.0.0.1:{port}/json/new?{base}/genesis/", method="PUT"),
            timeout=10))
        ws = connect(tab["webSocketDebuggerUrl"], open_timeout=10)

        def cmd(i, method, params=None):
            ws.send(json.dumps({"id": i, "method": method, "params": params or {}}))
            while True:
                m = json.loads(ws.recv())
                if m.get("id") == i:
                    return m

        cmd(1, "Runtime.enable")
        cmd(2, "Page.enable")
        time.sleep(4)  # let the SPA boot + fetch models.js
        r = cmd(3, "Runtime.evaluate", {
            "expression": "JSON.stringify({title:document.title,"
                          "hasMODELS:typeof window.MODELS!=='undefined',"
                          "hasIDB:!!window.indexedDB,"
                          "models:(window.MODELS&&window.MODELS.models||[]).length})",
            "returnByValue": True})
        val = json.loads(r["result"]["result"]["value"])
        check(g, "genesis SPA boots headless (title INFERO)", val["title"] == "INFERO", str(val))
        check(g, "window.MODELS loaded (>0 models)", val["hasMODELS"] and val["models"] > 0, str(val))
        check(g, "IndexedDB available", val["hasIDB"], str(val))

        # persistence: close client, browser must survive
        ws.close()
        time.sleep(1)
        try:
            tabs = json.load(urllib.request.urlopen(
                f"http://127.0.0.1:{port}/json", timeout=5))
            survived = any("genesis" in t["url"] for t in tabs if t["type"] == "page")
        except Exception:
            survived = False
        check(g, "browser survives client disconnect (CDP persistence)", survived)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


# ── SSH suite: systemd unit health ──────────────────────────────────────────
def test_ssh():
    import os
    g = "ssh/systemd"
    print(f"\n== {g} (service health) ==")
    units = " ".join(SYSTEMD_UNITS)
    cmd = SSH[:1] + [os.path.expanduser(SSH[2])] if False else \
        ["ssh", "-i", os.path.expanduser("~/.ssh/ec2_tokyo_2023.pem"),
         "ubuntu@3.114.3.152", f"systemctl is-active {units}"]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        states = out.stdout.split()
    except Exception as e:
        check(g, "ssh reachable", False, str(e))
        return
    for unit, state in zip(SYSTEMD_UNITS, states + ["?"] * len(SYSTEMD_UNITS)):
        check(g, f"{unit} active", state == "active", f"state={state}")


# ── main ────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", choices=["prod", "dev", "both"], default="both")
    ap.add_argument("--cdp", action="store_true", help="add headless genesis boot test")
    ap.add_argument("--ssh", action="store_true", help="add systemd unit health (needs key)")
    args = ap.parse_args()

    envs = ["prod", "dev"] if args.env == "both" else [args.env]
    for e in envs:
        test_http(e)
    if args.cdp:
        for e in envs:
            test_cdp(e)
    if args.ssh:
        test_ssh()

    total = len(_results)
    failed = [r for r in _results if not r[2]]
    print("\n" + "=" * 60)
    print(f"RESULT: {total - len(failed)}/{total} passed")
    if failed:
        print("FAILED:")
        for grp, name, _, detail in failed:
            print(f"  - [{grp}] {name}  ({detail})")
    print("=" * 60)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
