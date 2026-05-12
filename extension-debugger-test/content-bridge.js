// Content script (ISOLATED world). Bridges page MAIN-world API requests
// (via CustomEvent) to the service worker (via chrome.runtime.sendMessage).
//
// The MAIN-world side calls:
//     window.dispatchEvent(new CustomEvent('infero-probe-exec', {
//         detail: { id, code, timeout }
//     }))
// Then listens for:
//     'infero-probe-result/' + id
// with detail.{ok, result, error}.

window.addEventListener('infero-probe-exec', (ev) => {
  const { id, code, timeout } = ev.detail || {};
  chrome.runtime.sendMessage(
    { type: 'infero-probe-exec', code, timeout },
    (response) => {
      const lastErr = chrome.runtime.lastError;
      const payload = lastErr ? { ok: false, error: lastErr.message } : response;
      window.dispatchEvent(new CustomEvent('infero-probe-result/' + id, { detail: payload }));
    }
  );
});

console.log('[infero-probe] content bridge ready');
