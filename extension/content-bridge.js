// INFERO Hook — content script (ISOLATED world). Bridges the MAIN-world
// hook's exec requests to the service worker, which owns chrome.debugger.
//
// MAIN world dispatches CustomEvent('infero-exec', { detail: { id, code, timeout } })
// and listens for CustomEvent('infero-exec-result/' + id, { detail: { ok, stdout, error } }).

window.addEventListener('infero-exec', (ev) => {
  const { id, code, timeout } = ev.detail || {};
  chrome.runtime.sendMessage(
    { type: 'infero-exec', code, timeout },
    (response) => {
      const lastErr = chrome.runtime.lastError;
      const payload = lastErr
        ? { ok: false, stdout: '', error: lastErr.message }
        : response;
      window.dispatchEvent(new CustomEvent('infero-exec-result/' + id, { detail: payload }));
    }
  );
});
