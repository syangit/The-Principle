#!/usr/bin/env python3
"""
Multi-being isolation tests: do beings that share one browser stay separate?

Boots the real src/index.html in headless Chromium against a local fake LLM
(Gemini-format SSE) and checks the five problems described in
README.md. Each check asserts the CORRECT (isolated) behaviour,
so on current code every check fails; after a fix they should pass.

    python3 src/tests/multi_being_isolation/multi_being_isolation_test.py
    CHROME=/usr/bin/chromium-browser python3 src/tests/multi_being_isolation/multi_being_isolation_test.py
    python3 src/tests/multi_being_isolation/multi_being_isolation_test.py --json /tmp/isolation.json

Needs: pip install playwright, and a Chromium/Chrome (Playwright's bundled one,
or set CHROME). Network: loads marked/html2canvas from jsDelivr and bip39 from
esm.sh; every other external request (hub, relay) is blocked.
Exit code 0 = all pass, 1 = any fail.
"""
import argparse
import json
import os
import re
import sys
import threading
import time
from functools import partial
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

from playwright.sync_api import sync_playwright

SRC = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PORT = int(os.environ.get("PORT", "8765"))
BASE = f"http://127.0.0.1:{PORT}"
ALLOWED_HOSTS = ("cdn.jsdelivr.net", "cdn.bootcdn.net", "esm.sh")
SLOW_REPLY_S = 6.0   # total stream time for a TASK-SLOW* prompt
FAST_REPLY_S = 0.3
SETTINGS = {"provider": "custom", "apiEndpoint": f"{BASE}/mock", "tokens": {"custom": "x"},
            "model": "gemini-3.1-pro", "relayUrl": "ws://127.0.0.1:9/ws",
            "hostDevice": "this browser", "devices": {}}


# --- Fake LLM + static server -------------------------------------------------
# Replies to the newest "TASK-<x>" marker in the request with "ANSWER-<x> ...",
# so the test can tell which prompt each saved answer belongs to.
class Handler(SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode("utf-8", "replace")
        if not self.path.startswith("/mock"):
            self.send_response(404); self.end_headers(); return
        marks = [(m.start(), m.group(1)) for m in re.finditer(r"TASK-([A-Za-z0-9_]+)", body)]
        mark = max(marks)[1] if marks else "NONE"
        delay = SLOW_REPLY_S if mark.startswith("SLOW") else FAST_REPLY_S
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        parts = [f"ANSWER-{mark} part1 ", "part2 ", "done.\n\n/call_for_human"]
        try:
            for p in parts:
                time.sleep(delay / len(parts))
                chunk = {"candidates": [{"content": {"parts": [{"text": p}]}}]}
                self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode()); self.wfile.flush()
            self.wfile.write(b'data: {"usageMetadata":{"promptTokenCount":10,"candidatesTokenCount":5}}\n\n')
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass  # the page aborted the stream (e.g. switching beings mid-reply)


def start_server():
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), partial(Handler, directory=SRC))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


# --- Page helpers -------------------------------------------------------------
def route_filter(route):
    u = route.request.url
    if u.startswith(BASE) or any(h in u for h in ALLOWED_HOSTS):
        return route.continue_()
    return route.abort()


def wait_idle(pg, timeout=30):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if pg.evaluate("() => !loopRunning && dispatchTimer === null"):
            return
        time.sleep(0.2)
    raise RuntimeError("loop never went idle")


def settle(pg):
    """Wait for the loop to stop, then flush the debounced save."""
    wait_idle(pg)
    pg.evaluate("() => saveBeing()")
    time.sleep(0.3)


def switch(pg, bid):
    pg.evaluate("id => switchBeing(id)", bid)


def stored(pg, bid):
    r = pg.evaluate("k => DB.get(k)", f"{bid}/consciousness.txt")
    return (r or {}).get("value", "")


def holders(pg, needle, ids):
    return [name for name, bid in ids.items() if needle in stored(pg, bid)]


def open_page(ctx, url):
    pg = ctx.new_page()
    pg.on("pageerror", lambda e: print("    [pageerror]", str(e)[:150]))
    pg.goto(url)
    pg.wait_for_function("() => typeof currentBeingId === 'string' && currentBeingId.length > 0",
                         timeout=30000)
    return pg


def launch(p):
    exe = os.environ.get("CHROME")
    if exe:
        return p.chromium.launch(executable_path=exe, headless=True)
    try:
        return p.chromium.launch(headless=True)
    except Exception:
        for exe in ("/usr/bin/chromium-browser", "/usr/bin/chromium", "/usr/bin/google-chrome"):
            if os.path.exists(exe):
                return p.chromium.launch(executable_path=exe, headless=True)
        raise


# --- Tests --------------------------------------------------------------------
checks = []


def check(num, name, ok, detail):
    checks.append({"issue": num, "check": name, "pass": bool(ok), "detail": detail})
    print(f"  [{'PASS' if ok else 'FAIL'}] {name} — {detail}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", help="write results to this file")
    args = ap.parse_args()

    srv = start_server()
    try:
        with sync_playwright() as p:
            br = launch(p)
            ctx = br.new_context()
            ctx.route("**/*", route_filter)
            ctx.add_init_script("if (!localStorage.getItem('genesis_settings')) "
                                f"localStorage.setItem('genesis_settings', {json.dumps(json.dumps(SETTINGS))});")

            # Setup: two fresh beings, A and B (each runs one auto-infer turn).
            pg = open_page(ctx, f"{BASE}/index.html?new")
            time.sleep(4); settle(pg)
            A = pg.evaluate("() => currentBeingId")
            pg.evaluate("() => newBeing()")
            time.sleep(4); settle(pg)
            B = pg.evaluate("() => currentBeingId")
            ids = {"A": A, "B": B}
            print(f"beings: A={A[:16]}…  B={B[:16]}…")

            print("\n[1] Switching mid-loop writes A's turn into B")
            switch(pg, A); settle(pg)
            pg.evaluate("() => trigger('TASK-SLOW1', 0)")
            time.sleep(4)   # past the 3 s save debounce, mid-stream
            running = pg.evaluate("() => loopRunning")
            switch(pg, B); settle(pg)
            # Switching pauses A: its in-flight reply is cancelled, its prompt stays in A.
            reply = holders(pg, "ANSWER-SLOW1", ids)
            prompt = holders(pg, "TASK-SLOW1", ids)
            check(1, "nothing of A's turn lands in B; A keeps its prompt",
                  "B" not in reply and prompt == ["A"],
                  f"loop running at switch={running}; reply saved in {reply or 'none'}, prompt in {prompt or 'none'}")

            print("\n[2] Switching within ~3 s of a write loses it")
            switch(pg, A); settle(pg)
            pg.evaluate("() => trigger('TASK-FAST2', 0)")
            wait_idle(pg)
            pending = pg.evaluate("() => _saveBeingTimer !== null")
            switch(pg, B)
            time.sleep(4)
            switch(pg, A)
            where = holders(pg, "ANSWER-FAST2", ids)
            check(2, "A's last reply survives an immediate switch", where == ["A"],
                  f"save pending at switch={pending}; reply saved in {where or 'none'}")

            print("\n[3] A's code keeps running after the switch")
            switch(pg, A); settle(pg)
            pg.evaluate("() => (0, eval)(\"window.__leak = setTimeout(() => trigger('TASK-LEAK3', 0), 3000)\")")
            switch(pg, B)
            time.sleep(5); settle(pg)
            where = holders(pg, "TASK-LEAK3", ids)
            check(3, "A's timer does not wake B", "B" not in where,
                  f"prompt from A's timer landed in {where or 'none'}")

            print("\n[4] Multiple tabs")
            switch(pg, A); settle(pg)
            pg2 = open_page(ctx, f"{BASE}/index.html?being={B}")
            time.sleep(2)
            opened = "A" if pg2.evaluate("() => currentBeingId") == A else "B"
            check("4a", "a new tab opened with ?being=B opens B", opened == "B",
                  f"new tab opened {opened}, the being tab 1 is on")
            pg.evaluate("() => { settings.devices = {TestDevice: {online: false}}; saveSettingsToStorage(settings); }")
            pg2.evaluate("() => saveSettingsToStorage(settings)")
            devices = pg.evaluate("() => Object.keys(JSON.parse(localStorage.genesis_settings).devices || {})")
            check("4b", "a device paired in tab 1 survives a settings save in tab 2", "TestDevice" in devices,
                  f"saved devices after tab 2's save: {devices}")
            switch(pg2, A); settle(pg2)
            pg.evaluate("() => trigger('TASK-P1', 0)"); settle(pg)
            pg2.evaluate("() => trigger('TASK-P2', 0)"); settle(pg2)
            final = stored(pg, A)
            p1, p2 = "ANSWER-P1" in final, "ANSWER-P2" in final
            check("4c", "same being in two tabs keeps both tabs' turns", p1 and p2,
                  f"A keeps tab 1's turn={p1}, tab 2's turn={p2}")
            pg2.close()

            print("\n[5] No access boundary between beings")
            switch(pg, B); settle(pg)
            keys = pg.evaluate("async a => (await DB.list()).map(r => r.id).filter(k => k.startsWith(a + '/'))", A)
            check(5, "B cannot read A's records", not keys,
                  f"B read {len(keys)} of A's records, incl. identity={any(k.endswith('/identity') for k in keys)}")
            wrote = pg.evaluate("async a => { await DB.put({id: a + '/probe', value: 'by-B'});"
                                " return (await DB.get(a + '/probe'))?.value === 'by-B'; }", A)
            check(5, "B cannot write A's records", not wrote, f"B wrote {A[:8]}…/probe={wrote}")
            br.close()
    finally:
        srv.shutdown()

    failed = sum(not c["pass"] for c in checks)
    print(f"\n{len(checks) - failed}/{len(checks)} checks passed")
    if args.json:
        with open(args.json, "w") as f:
            json.dump(checks, f, indent=2)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
