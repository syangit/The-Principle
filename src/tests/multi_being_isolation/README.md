# Multi-being isolation in one browser

Beings that share one Infero browser aren't isolated. The loop, the save path and skill code act on
whichever being is **on screen**, not the being that started the work. Switching beings, or opening
a second tab, can write one being's work into another, lose it, or wake the other being up.

All five issues are reproduced by `multi_being_isolation_test.py` in this folder (0/8 checks pass on `dev` at
`a1fad55`, 2026-10-09). Line numbers refer to `src/index.html` at that commit and are approximate.

| # | Problem | Severity | Status |
|---|---|---|---|
| 1 | Switching mid-loop writes A's turn into B | High | Reproduced |
| 2 | Switching within ~3 s of a write loses it | Medium | Reproduced |
| 3 | A's skill code keeps running after the switch | Medium | Reproduced (also seen in practice) |
| 4 | Multiple tabs: settings overwritten, no way to choose a being, same being clobbered | Medium | Reproduced |
| 5 | No access boundary between beings | By design today | Reproduced |

## Issues

### 1. Switching mid-loop writes A's turn into B

- `switchBeing` (~2368) and `newBeing` (~2411) replace the globals `currentBeingId` and
  `consciousness`. They don't stop a running `loop()`, and the being picker (~1365) is never disabled.
- The loop writes through `appendConsciousness()` (~656) to the global `consciousness`; `saveBeing()`
  (~2266) saves it under the current `currentBeingId`. The rest of A's cycle (reply, exec results,
  `/view` markers) is appended to B's history and saved under B, and the loop carries on as B.
- Not reset on switch: `currentUserInput` (input typed to A is answered as B), `dispatchTimer`,
  `_pendingHandoff` (~41), `window._overloadModel`.

**Manual repro:** host = this browser. Give A a slow task, switch to B while it's inferring.
B's chat ends with A's reply; A is missing that turn.

### 2. Switching within ~3 s of a write loses it

- Saves are debounced 3 s (`scheduleSaveBeing`, ~2258) and `switchBeing` doesn't flush a pending
  save, so A's last writes, held only in memory, are dropped. When the timer fires it saves B's
  (already loaded) consciousness under B, so nothing of A's survives.
- Race (not tested): `switchBeing` sets `currentBeingId = B` first, then awaits several IndexedDB
  reads before swapping `consciousness` and `#html-div`. A save in that gap stores A's consciousness
  or UI snapshot (`${id}/snapshot`) under B.

**Manual repro:** let A finish a reply, switch to B within 1 s, switch back. A's last reply is gone.

### 3. A's skill code keeps running after the switch

- Skill JS and `/exec browser` code run in the shared page via `(0, eval)`. `switchBeing` evaluates
  B's enabled skills but never unloads A's.
- Cleaned on switch: `requestAnimationFrame` loops (`_rafIds`, ~2188); the canvas is cleared and
  `#html-div` emptied.
- Not cleaned: `window.*` globals, `setInterval` / `setTimeout`, `MutationObserver`s, `window` /
  `document` listeners, sockets, prototype patches. Any leftover callback calling `trigger(value)`
  (~4307) starts the loop as B.
- Seen in practice with the hub skill `infero_agentic_ui_zh`: its `MutationObserver` on `#html-div`
  calls `window.trigger(...)` on English text and wakes B with a translation task.

**Manual repro:** in A, run `setTimeout(() => trigger('hello from A'), 5000)` (or enable
`infero_agentic_ui_zh`), switch to B. B receives the prompt and answers it.

### 4. Multiple tabs

- **Settings:** each tab reads `localStorage.genesis_settings` once and later writes back its whole
  copy (`saveSettingsToStorage`, ~1563, ~20 callers, including every `device_status` message). A
  device paired in tab 1 is erased by tab 2's next save; model, key or host changes revert the same way.
- **New tabs:** a new tab always opens the most recently updated being (`initApp`, ~2507), usually the
  one another tab is running. There's no URL parameter to choose a being.
- **Same being in two tabs:** each tab saves its own in-memory consciousness, so the last save wins
  and the other tab's turns are lost. There's no `navigator.locks`, `BroadcastChannel` or `storage`
  listener.

**Manual repro:** open two tabs. Pair a device in tab 1, then change the model in tab 2: the device
is gone after reload. Or chat with the same being in both tabs: after reload only one tab's turns remain.

### 5. No access boundary between beings

IndexedDB keys are prefixed `<beingId>/` by convention only. Any being's code can list, read and
overwrite other beings' records, identity keys included. Skill writes build keys from the current
`currentBeingId` (for example `hubInstall`, ~1973), so a write from A's code after a switch lands in B.

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
- External requests are blocked except jsDelivr, bootcdn and esm.sh (page libraries). The device
  relay URL points at a dead port, so no relay traffic.
- Setup creates two fresh beings, A and B, via `?new` and `newBeing()`.
- Each check asserts the **correct** behaviour, so it fails while the bug exists. Exit code 0 = all pass, 1 = any
  fail.

| Check | Steps | Pass condition |
|---|---|---|
| 1 | On A, send `TASK-SLOW1`; after 4 s (mid-stream, past the save debounce) switch to B; wait for the loop to end | `ANSWER-SLOW1` stored in A only |
| 2 | On A, send `TASK-FAST2`; as soon as the loop ends switch to B; wait 4 s; switch back | `ANSWER-FAST2` stored in A |
| 3 | On A, eval `setTimeout(() => trigger('TASK-LEAK3', 0), 3000)`; switch to B; wait 5 s | `TASK-LEAK3` not in B |
| 4a | With tab 1 on A, open tab 2 at `index.html?being=<B>` | tab 2 shows B |
| 4b | Tab 1 adds `devices.TestDevice` and saves settings; tab 2 calls `saveSettingsToStorage(settings)` | `TestDevice` still in `localStorage` |
| 4c | Both tabs on A; tab 1 sends `TASK-P1`, then tab 2 sends `TASK-P2` (each saved) | A stores both answers |
| 5 | From B, list keys under `<A>/`; write `<A>/probe` | B can neither read nor write A's records |

Not covered: the save race inside `switchBeing` (issue 2), the double skill evaluation at boot, and
a real `device_status` message triggering the settings save (4b calls the save directly). Check 3
uses a one-line timer instead of a real hub skill; the mechanism is the same.

## Results

Run 2026-10-09 on `dev` at `a1fad55`, Chromium 154, Linux. **0/8 checks passed**, the same in two
separate runs.

| Check | Result | Observed |
|---|---|---|
| 1 | FAIL | Loop still running at switch; A's reply saved in **B** only |
| 2 | FAIL | Save pending at switch; A's reply saved **nowhere** |
| 3 | FAIL | A's timer prompt landed in **B**, and B answered it |
| 4a | FAIL | `?being=<B>` ignored; tab 2 opened **A**, the being tab 1 is on |
| 4b | FAIL | Saved devices after tab 2's save: `[]` (TestDevice erased) |
| 4c | FAIL | A keeps tab 2's turn but **not** tab 1's |
| 5 | FAIL | B read all 12 of A's records, incl. `identity` |
| 5 | FAIL | B wrote `<A>/probe` |

## Proposed fix

| Stage | Change | Fixes |
|---|---|---|
| 0 — stopgap | At the start of `switchBeing` / `newBeing`: abort and await a running loop (or disable the picker while running); cancel `_saveBeingTimer` and `await saveBeing()` before touching `currentBeingId`; reset `currentUserInput`, `dispatchTimer`, `_pendingHandoff`, `_overloadModel`. Optionally reload the page after a switch. | 1, 2 (3 if reloading) |
| 1 | A `Being` object per being holding consciousness, loop, pending input, save timer and abort controller; the UI only views one. Skills get `install(ctx)` with `ctx.trigger`, `ctx.db` (enforces the `<beingId>/` prefix), `ctx.beingId`. Mirror `relay/agent.py`'s `GenesisWorker` per being. | 1, 2, the `trigger` part of 3, the write part of 5 |
| 2 | Track skill timers, observers, listeners and sockets per being; dispose on switch. Skill `scope: "being" \| "app"`. | Most of 3 |
| 3 | iframe per being. | All of 3 |
| Tabs | `storage` listener or merge-on-save for settings; `navigator.locks` per being; `?being=<id>` URL parameter. | 4 |

Open decision: when switching A → B, should A keep running in the background or pause until viewed again?
