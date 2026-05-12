# INFERO Debugger Probe

A minimal, isolated test extension to verify that **chrome.debugger +
Runtime.evaluate** is a viable code-execution path for INFERO Hook
on the Chrome Web Store. Mirrors the pattern Anthropic's "Claude for
Chrome" uses (verified by inspecting their 1.0.70 bundle).

Scope: matches only `https://dev.infero.net/*`. Will not touch any
other site. Safe to install alongside the main INFERO Hook extension.

## Files

| File | World | Purpose |
|---|---|---|
| `manifest.json` | — | MV3 manifest. `debugger` permission, narrow host match. |
| `service-worker.js` | background | Owns `chrome.debugger.attach` + `Runtime.evaluate`. |
| `content-bridge.js` | content (ISOLATED) | Forwards page CustomEvent → `chrome.runtime.sendMessage` → service worker. Returns result back via CustomEvent. |
| `main-world-api.js` | content (MAIN) | Exposes `window.inferoProbeExec(code)` to the page / DevTools console. |

## Install

1. `chrome://extensions/` → Developer mode ON.
2. **Load unpacked** → select this folder.
3. Open `https://dev.infero.net/genesis/` (or any other path on that origin).

## What you should see

1. The yellow Chrome banner at the top of the tab on the first
   `inferoProbeExec` call:
   > **"INFERO Debugger Probe" started debugging this browser.** [Cancel]
2. In the DevTools console:
   ```js
   const r = await inferoProbeExec('1 + 1');
   r.result.result.value === 2;  // true
   ```
3. Service-worker console (open via `chrome://extensions/` → "service worker"):
   ```
   [infero-probe] service worker ready
   [infero-probe] debugger attached to tab <N>
   ```

## What this proves

- `chrome.debugger.sendCommand(tabId, 'Runtime.evaluate', { expression })`
  is the **policy-sanctioned** way to execute arbitrary code on the
  user's behalf in MV3 — the RHC ban explicitly carves out the Debugger
  API and User Scripts API as exceptions.
- An extension that goes this route can ship to Chrome Web Store even
  while running AI-generated code, the same way Claude for Chrome,
  OpenAI Codex Chrome, Browser Use, and Nanobrowser do.
- The UX cost is the persistent debugger banner — irritating but
  legal. Users can cancel it; we re-attach on the next call.

## What to test next

After basic eval works:

- [ ] Async code with `await` (Promise return; `awaitPromise: true`).
- [ ] Multi-statement code (use IIFE).
- [ ] Error capture: `1/0`, syntax errors, thrown Errors.
- [ ] Network fetches initiated from inside the evaluated expression.
- [ ] Does the banner re-appear on every page navigation? (Probably yes —
      attach is per-tab, navigation kills the target.)
- [ ] Co-existence with INFERO Hook's nonce-script path: install both;
      both should work independently.

## If this works

The INFERO Hook can ship a Web-Store-compatible build by:
1. Adding `debugger` permission to its manifest.
2. Moving `execJsCSP` from "inject `<script nonce>`" to
   "postMessage → service-worker → `chrome.debugger.sendCommand`".
3. Keeping the current nonce-script path as a fallback for the
   dev-mode build where the banner is unwelcome.
