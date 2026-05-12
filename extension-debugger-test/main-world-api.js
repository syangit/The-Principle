// Main-world script — exposes a clean async API to the page.
//
// Usage from page or DevTools console:
//   const r = await window.inferoProbeExec('1 + 1');
//   console.log(r);  // { ok: true, result: { result: { value: 2, type: 'number' } } }
//
//   const r = await window.inferoProbeExec(`(async () => {
//       const res = await fetch('https://api.github.com/repos/torvalds/linux');
//       const data = await res.json();
//       return { stars: data.stargazers_count };
//   })()`);
//
// The string is sent to the service worker, which runs it via
// chrome.debugger.sendCommand(..., 'Runtime.evaluate', { expression }).
// Yellow "debugger" banner should appear at the top of the tab on the
// first call and persist until tab closes.

window.inferoProbeExec = function (code, opts = {}) {
  return new Promise((resolve) => {
    const id = 'p_' + Math.random().toString(36).slice(2);
    const handler = (ev) => {
      window.removeEventListener('infero-probe-result/' + id, handler);
      resolve(ev.detail);
    };
    window.addEventListener('infero-probe-result/' + id, handler, { once: true });
    window.dispatchEvent(new CustomEvent('infero-probe-exec', {
      detail: { id, code, timeout: opts.timeout || 10000 }
    }));
  });
};

console.log('[infero-probe] window.inferoProbeExec(code) ready — try: await inferoProbeExec("1+1")');
