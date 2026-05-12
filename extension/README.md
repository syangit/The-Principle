# INFERO Hook — Browser Extension

Manifest V3 content-script. Auto-injects on supported chat hosts; turns
each page into a Being container with `/exec` protocol, hub-shared skills,
encrypted device relay (via `device_relay_client` skill), durable BIP-39
identity (via `identity_seed` skill).

Architecture: see `src/docs/browser_hook_being.md`.

## Currently supported hosts

| Host | Status | Notes |
|---|---|---|
| `chat.deepseek.com` | ✓ verified | textarea + Enter |
| `claude.ai` | ✓ verified | ProseMirror + `[aria-label="Send message"]`, CSP `'strict-dynamic'` + nonce |
| `chatgpt.com` | best-effort | selectors may shift; verify on first install |
| `gemini.google.com` | best-effort | selectors may shift |

Adding a new host = adding one entry to `HOST_RULES` in `hook.js`.

## Install (developer mode)

1. Open `chrome://extensions/` (or `edge://extensions/`, `brave://extensions/`)
2. Toggle **Developer mode** (top-right)
3. **Load unpacked** → select this directory
4. Open one of the supported chat sites
5. DevTools console should print:
   ```
   [infero-hook] ready on <host> · being: being_xxx · boot: { loaded: [...], failed: [] }
   ```

## What gets exposed on `window`

| Name | Purpose |
|---|---|
| `window.trigger(text)` | Send `text` back through the host's chat UI. Serialized queue, 1s pre-send wait, draft preservation. |
| `window.__inferoHook` | Internal state: `observer`, `processed`, `recentHashes`, `log`, `execJsCSP`, `host`, `rules`. |
| `window.infero` | `{ installSkill, listSkills, searchSkills, bootSkills, initBeing }` |
| `window.DB` | `{ get(id), put(id, value), _raw }` — IndexedDB `GenesisDB`/`beings` helper |
| `window.currentBeingId` | The Being's persistent ID (per-origin, localStorage-backed) |

Skills installed from the Hub add their own globals — e.g. `identity_seed`
adds `window.identityInit/Export/Import`, `device_relay_client` adds
`window.relay.{pair,exec,connect,status}`.

## /exec protocol

The AI's reply must contain a `/exec` marker (anywhere in plain text) **before**
the JS code block it wants to run:

````
Sure, I'll check the time.

/exec
```javascript
const r = await window.relay.exec('localhost-hbw0', 'date');
await trigger(`Server time: ${r.stdout.trim()}`);
```
````

- Each `/exec` arms the *next* `<pre>` block only.
- Code blocks without a preceding `/exec` are treated as illustration / recap
  and **not** executed.
- Same code (by FNV-1a hash of trimmed text) won't re-run within a 5-minute
  window — protects against the LLM echoing earlier code while explaining.

## CSP modes

- `eval` (chat.deepseek.com): the page's CSP allows `'unsafe-eval'`. Plain
  `(0, eval)(code)` works.
- `nonce` (claude.ai, chatgpt.com, gemini): the CSP forbids `eval` but
  permits `'strict-dynamic'` + nonce. The hook reads the nonce from any
  existing inline `<script>` and creates a new `<script nonce="…">` with the
  AI's code as `textContent`. Strict-dynamic lets it run.

If neither applies, exec returns `{ ok: false, error: 'no CSP nonce' }`.

## Storage layout

All persistent state lives in:

| Where | Key | Content |
|---|---|---|
| IndexedDB `GenesisDB`/`beings` | `<beingId>` | `{ name, createdAt, host }` |
| same | `<beingId>/identity` | BIP-39 mnemonic record (when `identity_seed` is installed) |
| same | `<beingId>/skill/<name>` | Genesis-native skill record |
| same | `<beingId>/core_mem.md` | `{ value: <markdown> }` |
| localStorage | `infero_being_id` | the beingId string |
| localStorage | `infero_skills_v1` | hook's parallel skill cache |
| localStorage | `infero_relay/<beingId>/...` | device-relay pair keys, instance id |

Each chat host is its own origin → independent Being, independent identity,
independent paired devices. Skills cross-pollinate via the Hub
(`dev.infero.net/hub`).

## What this is *not*

- Not a subscription proxy. The host's chat service is used *as* a chat
  service; nothing is exfiltrated as a third-party API. ToS-safe.
- Not bot automation. The host's PoW, rate limits, anti-abuse — all left
  intact. The hook just plumbs the AI's natural output back into the same
  UI as user input.
- Not a Genesis replacement. Genesis still owns system-prompt control, model
  choice, context-cache optimization, cross-device handoff. See
  `src/docs/browser_hook_being.md` § "What this is *not*".

## Known limitations

- Host selector changes break the hook silently. Watch the console for
  `'[infero-hook] ready on …'` — if absent on a supported site, the selector
  drifted; update `HOST_RULES` in `hook.js`.
- Manifest V3 + `world: "MAIN"` requires Chrome ≥ 102, Firefox ≥ 128.
- No popup UI yet. Toggle on/off via Chrome's extension menu.
- Icons are placeholders. Drop your own PNGs in `icons/` (16, 48, 128).

## Future

- Per-host TestPlan in `test/` (selectors might drift; CI runs the basic
  pair-and-exec smoke test).
- Popup with: paired devices, installed skills, beingId, identity QR.
- Firefox-AMO listing (MV3 + main-world is supported since 128).
- Side-loaded import/export of the whole Being (mnemonic + skills) as
  a single JSON.
