# Multi-being isolation in one browser

Beings that share one Infero browser weren't isolated. The loop, the save path and skill code acted
on whichever being was **on screen**, not the being that started the work. Switching beings, or
opening a second tab, could write one being's work into another, lose it, or wake the other being up.

Issues 1–4 are fixed on branch `fix/multi-being-isolation`. Issue 5 needs a design decision and is
left open. `multi_being_isolation_test.py` in this folder reproduces all five: on `dev` at `a1fad55`
it fails every check; with the fixes, everything except issue 5 passes.

| # | Problem | Severity | Status |
|---|---|---|---|
| 1 | Switching mid-loop wrote A's turn into B | High | **Fixed** (commit 1) |
| 2 | Switching within ~3 s of a write lost it | Medium | **Fixed** (commit 1) |
| 3 | A's skill code kept running after the switch | Medium | **Fixed** (commit 2) |
| 4 | Multiple tabs: settings overwritten, no way to choose a being, same being clobbered | Medium | **Fixed** (commits 2, 3) |
| 5 | No access boundary between beings | By design today | Open: needs a per-being origin or encryption |

Line numbers below refer to `src/index.html` at `a1fad55` and are approximate.

## Issues

### 1. Switching mid-loop writes A's turn into B

- `switchBeing` (~2368) and `newBeing` (~2411) replaced the globals `currentBeingId` and
  `consciousness`. They didn't stop a running `loop()`, and the being picker (~1365) was never disabled.
- The loop writes through `appendConsciousness()` (~656) to the global `consciousness`; `saveBeing()`
  (~2266) saves it under the current `currentBeingId`. The rest of A's cycle (reply, exec results,
  `/view` markers) was appended to B's history and saved under B, and the loop carried on as B.
- Not reset on switch: `currentUserInput` (input typed to A was answered as B), `dispatchTimer`,
  `_pendingHandoff` (~41), `window._overloadModel`.

**Manual repro:** host = this browser. Give A a slow task, switch to B while it's inferring.
Before the fix, B's chat ends with A's reply and A is missing that turn. After: B is untouched and A
keeps the prompt; the cancelled turn reruns when A is woken.

### 2. Switching within ~3 s of a write loses it

- Saves are debounced 3 s (`scheduleSaveBeing`, ~2258) and `switchBeing` didn't flush a pending save,
  so A's last writes, held only in memory, were dropped. When the timer fired it saved B's (already
  loaded) consciousness under B, so nothing of A's survived.
- Race (not tested): `switchBeing` set `currentBeingId = B` first, then awaited several IndexedDB
  reads before swapping `consciousness` and `#html-div`. A save in that gap stored A's consciousness
  or UI snapshot (`${id}/snapshot`) under B. Gone now: the switch saves first, then reloads.

**Manual repro:** let A finish a reply, switch to B within 1 s, switch back. Before the fix, A's last
reply is gone.

### 3. A's skill code keeps running after the switch

- Skill JS and `/exec browser` code run in the shared page via `(0, eval)`. `switchBeing` evaluated
  B's enabled skills but never unloaded A's.
- Cleaned on switch: `requestAnimationFrame` loops (`_rafIds`, ~2188); the canvas was cleared and
  `#html-div` emptied.
- Not cleaned: `window.*` globals, `setInterval` / `setTimeout`, `MutationObserver`s, `window` /
  `document` listeners, sockets, prototype patches. Any leftover callback calling `trigger(value)`
  (~4307) started the loop as B.
- Seen in practice with the hub skill `infero_agentic_ui_zh`: its `MutationObserver` on `#html-div`
  calls `window.trigger(...)` on English text and woke B with a translation task.

**Manual repro:** in A, run `setTimeout(() => trigger('hello from A'), 5000)` (or enable
`infero_agentic_ui_zh`), switch to B. Before the fix, B receives the prompt and answers it.

### 4. Multiple tabs

- **Settings:** each tab read `localStorage.genesis_settings` once and later wrote back its whole copy
  (`saveSettingsToStorage`, ~1563, ~20 callers, including every `device_status` message). A device
  paired in tab 1 was erased by tab 2's next save; model, key or host changes reverted the same way.
- **New tabs:** a new tab always opened the most recently updated being (`initApp`, ~2507), usually the
  one another tab was running. There was no URL parameter to choose a being, and boot evaluated the
  being's skills twice.
- **Same being in two tabs:** each tab saved its own in-memory consciousness, so the last save won and
  the other tab's turns were lost.

**Manual repro:** open two tabs. Pair a device in tab 1, then change the model in tab 2: before the
fix, the device is gone after reload. Or chat with the same being in both tabs: only one tab's turns
remain.

### 5. No access boundary between beings (open)

IndexedDB keys are prefixed `<beingId>/` by convention only. Any being's code can list, read and
overwrite other beings' records, identity keys included.

Why it isn't fixed here: the browser has no boundary inside one origin. Every being's code runs with
the page's full privileges, and IndexedDB belongs to the origin (`infero.net`), not to a being. A
`ctx.db` wrapper that enforces the prefix is easy, but skill code can call
`indexedDB.open('GenesisDB')` directly and bypass it; same-origin iframes share the same storage.
A real boundary needs either:

- **an origin per being** (for example `<being>.beings.infero.net`), which changes hosting, nginx,
  TLS and how beings are switched; or
- **per-being encryption** of stored records with a key other beings' code can't reach, which is hard
  while all code shares one page.

The fixes above remove the *accidental* case the original write-up described (A's leftover code
writing into B after a switch): only one being's code runs per page now. Deliberate cross-being
access remains possible.

## The fix

Switching **pauses** a being: its loop stops, and it resumes when it's woken again. Different beings
in different tabs still run at the same time, since each tab is a separate page.

| Commit | Change | Fixes |
|---|---|---|
| 1 `fix: stop and save the current being before switching` | `leaveBeing()` disables the picker, cancels pending dispatch and auto-infer, aborts the in-flight request, waits for `loop()` to end (the loop now checks `_stopRequested` between steps, since abort alone didn't stop it after `act()`), resets pending input, handoff and overload state, and saves immediately. User actions (`switchBeing`, `newBeing`, duplicate, import, delete) leave first; boot uses the split-out `loadBeing` / `createBeing`. | 1, 2 |
| 2 `fix: reload the page when switching beings; open a being by ?being=<id>` | After leaving, switching reloads the page as `?being=<id>` (`?new` for a new being), so nothing from the old being's code survives. Boot reads `?being=` and keeps it in the URL; skills are evaluated once at boot. Remote hosts are unaffected: on reconnect the relay already syncs and hands off the current being. | 3, 4 (choose a being) |
| 3 `fix: keep settings in sync across tabs; one tab per being` | A `storage` listener adopts settings saved by other tabs. Each tab holds a Web Lock (`navigator.locks`) on its being for the life of the page. Picking a being open in another tab is refused with a notice; a tab asking for one opens the next free being, or a new one, and says so. | 4 |

Trade-offs:

- A switch costs a page load (about a second) and a relay reconnect.
- Switching mid-reply cancels that reply (the prompt is kept). Switching while the being is running
  exec code waits for it to finish (up to the 15 s exec timeout); the status shows "Stopping...".
- An ordinary reload (F5) still drops a save that's pending in the 3 s debounce; that's unchanged.

Not done (from the original proposal): a `Being` object per being so beings keep running in the
background, per-being skill resource tracking, and an iframe per being. Pausing makes them
unnecessary for now; they'd matter if background running is wanted later.

## The automated test

```bash
pip install playwright            # plus Playwright's Chromium, or set CHROME=/path/to/chromium
python3 src/tests/multi_being_isolation/multi_being_isolation_test.py [--json results.json]
```

How it works:

- Serves `src/` on `127.0.0.1:8765` (`PORT` to change) and runs it in headless Chromium in a fresh
  profile, with `provider=custom` pointing at a fake LLM on the same server.
- The fake LLM answers the newest `TASK-<x>` marker in the request with `ANSWER-<x> …/call_for_human`
  in Gemini SSE format, so every saved answer can be traced to its prompt. `TASK-SLOW*` prompts stream
  over 6 s; others over 0.3 s.
- Chromium resolves only `127.0.0.1`, jsDelivr, bootcdn and esm.sh (page libraries); every other host
  fails DNS. The device relay URL points at a dead port, so no relay traffic.
- Setup creates two fresh beings, A and B, via `?new` and the New Being button. Switching goes through
  the being picker, like a user, and works whether or not the switch reloads the page, so the same
  test runs on the old code too.
- Each check asserts the **correct** behaviour. Issue 5's checks are marked known failures. Exit code
  0 = no failures other than known ones, 1 = any other failure.

Harness notes: the test blocks hosts with Chromium's `--host-resolver-rules` rather than
`ctx.route()`, because Playwright's sync API only serves intercepted requests while Python is inside a
Playwright call, so routed requests stall during `time.sleep()`. It polls on a timer rather than with
`wait_for_function`'s default, because the page's `cancelAllAnimations()` cancels every
`requestAnimationFrame`, including Playwright's.

| Check | Steps | Pass condition |
|---|---|---|
| 1 | On A, send `TASK-SLOW1`; after 4 s (mid-stream, past the save debounce) switch to B; wait for the loop to end | `ANSWER-SLOW1` not in B, and `TASK-SLOW1` stored in A only |
| 2 | On A, send `TASK-FAST2`; as soon as the loop ends switch to B; wait 4 s; switch back | `ANSWER-FAST2` stored in A |
| 3 | On A, eval `setTimeout(() => trigger('TASK-LEAK3', 0), 3000)`; switch to B; wait 5 s | `TASK-LEAK3` not in B |
| 4a | With tab 1 on A, open tab 2 at `index.html?being=<B>` | tab 2 shows B |
| 4a | Reload tab 2 | tab 2 still shows B |
| 4b | Tab 1 adds `devices.TestDevice` and saves settings; tab 2 calls `saveSettingsToStorage(settings)` | `TestDevice` still in `localStorage` |
| 4c | Tab 2 (on B) picks A in the being picker while tab 1 has A open | tab 2 stays on B |
| 4d | Open tab 3 at `index.html?being=<A>` while tab 1 has A open | tab 3 opens another being |
| 5 | From B, list keys under `<A>/`; write `<A>/probe` | B can neither read nor write A's records (known failure) |

Not covered: a real `device_status` message triggering the settings save (4b calls the save
directly), and remote-host handoff after a switch (needs a paired device). Check 3 uses a one-line
timer instead of a real hub skill; the mechanism is the same. Duplicate, delete, first-visit boot and
the Abort button were checked separately with a one-off script.

## Results

Run 2026-10-09, Chromium 154, Linux.

| Check | `dev` at `a1fad55` (before) | `fix/multi-being-isolation` (after) |
|---|---|---|
| 1 | FAIL: loop still running at switch; A's reply saved in **B** | PASS: reply cancelled, saved nowhere; prompt kept in A |
| 2 | FAIL: save pending at switch; A's reply saved **nowhere** | PASS: reply saved in A |
| 3 | FAIL: A's timer prompt landed in **B**, and B answered it | PASS: landed nowhere |
| 4a `?being=` | FAIL: tab 2 opened **A** | PASS: tab 2 opened B |
| 4a reload | FAIL: tab 2 reopened **A** | PASS: tab 2 reopened B |
| 4b | FAIL: saved devices after tab 2's save: `[]` | PASS: `['TestDevice']` |
| 4c | FAIL: tab 2 switched to A, no notice | PASS: tab 2 stayed on B, notice shown |
| 4d | FAIL: tab 3 opened **A** | PASS: tab 3 opened a new being (A and B both open) |
| 5 read | KNOWN: B read all 12 of A's records, incl. `identity` | KNOWN: same |
| 5 write | KNOWN: B wrote `<A>/probe` | KNOWN: same |
| **Total** | 0/10 passed, 8 failures, exit 1 | 8/10 passed, 2 known, exit 0 |

The "after" result was the same in two separate runs.
