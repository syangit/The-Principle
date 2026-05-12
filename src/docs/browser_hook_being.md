# Browser-Hook Being

A 200-line script that turns any third-party chat web app (DeepSeek, Claude.ai,
ChatGPT, Gemini, ...) into a full INFERO Being container — same `/exec` protocol,
same Skill Hub, same identity, same device-relay shell access as Genesis itself.

The host site provides the LLM and its UI. We parasitize the page, capture
its output stream, execute fenced JS blocks, and feed results back through the
same chat UI. The host never sees the difference; we never run a backend.

```
┌───────────────────────────────────────────────────────────────────┐
│  Host chat page (chat.deepseek.com / claude.ai / ...)             │
│                                                                   │
│  ┌────────────┐         ┌────────────────────┐                    │
│  │   Input    │  type   │   AI's message     │  /exec + code      │
│  │  (editor)  │ ←──────┐│   (streaming DOM)  │ ───────┐           │
│  └─────┬──────┘        │└────────────────────┘        │           │
│        │ send btn      │                              │           │
│        ▼               │                              ▼           │
│   ┌─────────────────────────┐                  ┌──────────────┐   │
│   │   Host's chat backend   │                  │ MutationObs  │   │
│   │   (closed)              │                  │ + Stop-btn   │   │
│   └─────────────────────────┘                  │   gate       │   │
│        ▲                                       └──────┬───────┘   │
│        │ trigger() inserts text + click send          │           │
│        │                                              ▼           │
│   ┌─────────────────────────────────────────────────────────┐    │
│   │  Hook (this code)                                       │    │
│   │  - extract /exec code blocks                            │    │
│   │  - hash-dedup within 5 min                              │    │
│   │  - execJsCSP(code)  → main-world script tag with nonce  │    │
│   │  - exposes: trigger, infero.*, DB.*, currentBeingId     │    │
│   └─────────────────────────────────────────────────────────┘    │
└───────────────────────────────────────────────────────────────────┘
```

## Pieces

### 1. `__inferoHook.observer` — stream-end detection

A single `MutationObserver` on `document.body { childList, subtree, characterData }`.
Each mutation:

1. If the host's "Stop response" button exists and is enabled, do nothing —
   the AI is still streaming.
2. Otherwise debounce 600 ms on the last assistant-message DOM element.
3. After debounce fires, double-check Stop-button absence, then call
   `onAssistantDone(msgEl)`.

The Stop-button gate is the canonical "stream finished" signal. Pure time
debounce is brittle — Claude streams in chunks with occasional > 1 s gaps that
are *not* end-of-stream.

### 2. `onAssistantDone(msgEl)` — `/exec` protocol

Walk the message DOM in order. Track an `armed` flag. Any text node containing
`/exec` (or `/browser exec`) sets `armed = true`. The next `<pre>` consumes it:

- If `armed`: extract its `<code>`'s `textContent`, push to `toExec`. Clear
  `armed`.
- If not `armed`: skip — this code block is recap/illustration, not a command.

`armed` is consumed per-block, so each `/exec` only triggers exactly one
following code block.

Then for each code in `toExec`:

1. Compute `FNV-1a` hash of `code.trim()`.
2. Check `recentHashes`: if seen in the last 5 minutes, skip (Claude often
   echoes earlier code while explaining — we don't want to re-run it).
3. **Wait 1000 ms** (let the page fully settle; especially React/tiptap can
   keep re-rendering 100-500 ms after stream ends).
4. `await execJsCSP(code)`.
5. Log result.

### 3. `execJsCSP(code)` — CSP-safe execution

Claude.ai has `script-src` without `'unsafe-eval'`, so `eval()` and
`new Function()` are blocked. But the CSP includes `'strict-dynamic'` and
emits a per-request nonce. Trick:

1. Read the nonce from any existing inline script: `script.nonce` (the
   attribute is stripped to `""` by the browser, the IDL property still
   exposes it).
2. Wrap the user code in `(async () => { try { <code> } catch (e) { error =
   e.message; } window.dispatchEvent(new CustomEvent(id, { detail: { stdout,
   error } })); })();` so it returns stdout/error via event, not a sync
   return.
3. Create `<script nonce="<the-nonce>">` with that wrapped source as
   `textContent`. Append to `document.head`. Strict-dynamic allows it.
4. Wait for the custom event. Resolve with `{ ok, stdout, error }`. Remove
   the `<script>` tag.

For sites without strict CSP (DeepSeek), we fall back to plain `eval()`. The
same `execJsCSP` API hides the difference.

### 4. `window.trigger(text)` — send back through chat UI

The reverse direction: skill code calls `await trigger("System - [Browser] -
Result: …")` to talk back to the LLM.

```
1. save draft  = editor.innerText.trim()        (don't lose user's typing)
2. focus editor; selectAll + delete; insertText(text)
3. wait 1000 ms  (let ProseMirror commit the transaction)
4. click button[aria-label="Send message"]      (programmatic click;
                                                 NOT dispatch Enter — synthetic
                                                 keydown often has isTrusted=false
                                                 and tiptap ignores it)
5. wait 400 ms  (let host's submit handler run)
6. selectAll + delete the editor                (host may not clear it on
                                                 programmatic click — we do)
7. if draft: insertText(draft)                  (restore user's typing)
```

Calls are serialized through a Promise queue, so rapid fire-and-forget
(`onclick="trigger('click')"` pattern) works without races.

The two `sleep(1000)` calls — one before exec, one before send — are *load-bearing*. Without them the system bounces:
- Without the pre-exec wait: hook tries to eval while React is still mid-render → React errors / partial DOM.
- Without the pre-send wait: ProseMirror hasn't committed our `insertText` yet → send button submits an empty (or previous) buffer.

### 5. Persistence layer — IndexedDB + localStorage

Reuses Genesis's schema verbatim, so any Genesis-native skill (`identity_seed`,
`nostr_comms`, `hub_install`, ...) drops in unchanged.

| Storage | Key | Value |
|---|---|---|
| IndexedDB `GenesisDB`/`beings` | `<beingId>` | `{ name, createdAt, host }` |
| same | `<beingId>/identity` | `{ mnemonic, version, ... }` |
| same | `<beingId>/skill/<name>` | skill record |
| same | `<beingId>/core_mem.md` | `{ value: <markdown> }` |
| localStorage | `infero_being_id` | the beingId string |
| localStorage | `infero_skills_v1` | hook's parallel skill store |
| localStorage | `infero_relay/<beingId>/devices` | paired devices `{ name: { keyB64, ... } }` |

Origin-isolated: each chat host (`chat.deepseek.com`, `claude.ai`, ...) holds
an *independent* Being, with its own beingId, identity, paired devices, and
skill set. Skills are shared across origins via the Hub.

### 6. Device-relay client (skill `device_relay_client`)

A self-contained port of Genesis's browser-side device relay. Hook gives it:
WebCrypto, fetch, localStorage. It gives the Being: encrypted shell access to
any paired `infero-dev` agent (macOS / Linux / Windows).

```
Pair:
   browser → POST /pair/create { instance_id, browser_pub }
          ← { code }
   user runs `infero-dev pair <code>` on the device
   relay → browser (WSS): device_status { fresh_pair, device_pub }
   browser derives ECDH P-256 → HKDF → AES-256-GCM key,
       stores in localStorage:infero_relay/<beingId>/devices

Exec:
   skill code → window.relay.exec("localhost-hbw0", "ls -la")
   browser encrypts {cmd} with AES, sends via WSS
   relay forwards to paired agent
   agent decrypts, run_shell_detached, encrypts result, sends back
   browser decrypts, resolves the Promise with {stdout, stderr, exit_code}
```

E2E encrypted. Relay sees only ciphertext. Host (Claude.ai etc.) never sees
the WSS to the relay — it's a separate connection from the page.

## Per-host adaptation

The hook's selectors are the only host-specific bits. Adding a new site:

```js
const HOST_RULES = {
    'chat.deepseek.com': {
        inputSelector: 'textarea[placeholder="Message DeepSeek"]',
        inputType: 'textarea',
        sendSelector: '...',           // optional; falls back to Enter
        messageSelector: '.ds-assistant-message-main-content',
        cspMode: 'eval',               // permissive; plain eval works
    },
    'claude.ai': {
        inputSelector: '.ProseMirror[contenteditable="true"]',
        inputType: 'prosemirror',      // use execCommand insertText
        sendSelector: 'button[aria-label="Send message"]',
        messageSelector: '.font-claude-response',
        cspMode: 'nonce',              // strict-dynamic + nonce required
    },
    'chatgpt.com': { ... },
    'gemini.google.com': { ... },
}
```

Per-host detection at boot picks the right rule. CSP mode auto-falls-back
(`eval` first, on failure switch to `nonce`).

## What this is *not*

- Not a Genesis replacement. Genesis still controls system prompt, model
  choice, context caching, cross-host portability, etc. — see
  `docs/infero_being_skill.md` for the home-base view.
- Not a Claude subscription proxy (à la sub2api / OpenClaw). The host's chat
  service is used as a chat service, not exfiltrated as an API. ToS-safe.
- Not bot automation. The host's anti-bot (DeepSeek PoW, Claude rate limits)
  is left intact; the hook just plumbs the AI's natural output back into
  itself.

## What this *is*

A 200-line proof that the Being abstraction is portable beyond Genesis:
any browser tab with a chat UI + an LLM willing to follow the `/exec`
protocol can host a Being. The Skill Hub becomes the connective tissue;
device_relay_client makes the local shell reachable; identity_seed gives
each Being a durable BIP-39 root.

The hook is the *plumbing*. Genesis is still the *home*.
