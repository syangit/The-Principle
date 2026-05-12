// Service worker — owns chrome.debugger. Content script can't call this API
// directly (debugger is extension-only), so we proxy via message-passing.
//
// Lifecycle: attach lazily on first eval request per tab, keep attached until
// tab closes or detach is requested. (Re-attaching on each call would flash
// the yellow bar; one persistent attach is the canonical pattern.)

const attached = new Map();  // tabId -> true

async function ensureAttached(tabId) {
  if (attached.get(tabId)) return;
  await chrome.debugger.attach({ tabId }, '1.3');
  attached.set(tabId, true);
  console.log('[infero-probe] debugger attached to tab', tabId);
}

async function evalInTab(tabId, expression, opts = {}) {
  await ensureAttached(tabId);
  const result = await chrome.debugger.sendCommand(
    { tabId },
    'Runtime.evaluate',
    {
      expression,
      returnByValue: true,
      awaitPromise: true,
      timeout: opts.timeout || 10000,
      userGesture: false,
    }
  );
  return result;
}

chrome.debugger.onDetach.addListener(({ tabId }, reason) => {
  console.log('[infero-probe] debugger detached from tab', tabId, 'reason:', reason);
  attached.delete(tabId);
});

chrome.tabs.onRemoved.addListener((tabId) => attached.delete(tabId));

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg?.type !== 'infero-probe-exec') return;
  const tabId = sender.tab?.id;
  if (!tabId) { sendResponse({ ok: false, error: 'no tab id' }); return false; }
  evalInTab(tabId, msg.code, { timeout: msg.timeout })
    .then(result => sendResponse({ ok: true, result }))
    .catch(err => sendResponse({ ok: false, error: err.message || String(err) }));
  return true;  // keep channel open for async response
});

console.log('[infero-probe] service worker ready');
