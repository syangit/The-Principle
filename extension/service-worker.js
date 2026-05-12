// INFERO Hook — service worker. Owns the chrome.debugger API.
//
// Why: Chrome Web Store's RHC policy forbids running strings via eval()
// or injected <script> tags, but explicitly carves out chrome.debugger
// as a sanctioned exception. Anthropic's "Claude for Chrome" uses this
// same path (verified by inspecting their 1.0.70 bundle: they call
// chrome.debugger.sendCommand(tabId, 'Runtime.evaluate', { expression }).
//
// Lifecycle: attach lazily on first exec request per tab, keep attached
// until the tab closes or the user clicks "Cancel" on the debugger
// banner. Re-attaching every call would flash the banner.

const attached = new Map();  // tabId -> true

async function ensureAttached(tabId) {
  if (attached.get(tabId)) return;
  await chrome.debugger.attach({ tabId }, '1.3');
  attached.set(tabId, true);
  console.log('[infero-hook] debugger attached to tab', tabId);
}

async function evalInTab(tabId, expression, opts = {}) {
  await ensureAttached(tabId);
  return chrome.debugger.sendCommand(
    { tabId },
    'Runtime.evaluate',
    {
      expression,
      returnByValue: true,
      awaitPromise: true,
      timeout: opts.timeout || 30000,
      userGesture: false,
    }
  );
}

chrome.debugger.onDetach.addListener(({ tabId }, reason) => {
  console.log('[infero-hook] debugger detached from tab', tabId, 'reason:', reason);
  attached.delete(tabId);
});

chrome.tabs.onRemoved.addListener((tabId) => attached.delete(tabId));

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg?.type !== 'infero-exec') return;
  const tabId = sender.tab?.id;
  if (!tabId) { sendResponse({ ok: false, error: 'no tab id' }); return false; }
  evalInTab(tabId, msg.code, { timeout: msg.timeout })
    .then(cdp => {
      // Normalize { result, exceptionDetails } → { ok, stdout, error }.
      // Our wrapper in hook.js always returns { stdout, error } via returnByValue;
      // exceptionDetails covers syntax errors / things the wrapper can't catch.
      if (cdp?.exceptionDetails) {
        const ex = cdp.exceptionDetails;
        const msg = ex.exception?.description || ex.text || 'unknown CDP exception';
        sendResponse({ ok: false, stdout: '', error: msg });
        return;
      }
      const wrapped = cdp?.result?.value;
      if (wrapped && typeof wrapped === 'object' && ('stdout' in wrapped || 'error' in wrapped)) {
        sendResponse({ ok: !wrapped.error, stdout: wrapped.stdout || '', error: wrapped.error });
      } else {
        sendResponse({ ok: true, stdout: '', error: undefined });
      }
    })
    .catch(err => sendResponse({ ok: false, stdout: '', error: err.message || String(err) }));
  return true;  // keep channel open for async response
});

console.log('[infero-hook] service worker ready');
