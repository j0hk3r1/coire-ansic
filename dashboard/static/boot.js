// boot.js — typewriter intro overlay, sessionStorage gated, Esc skips.
(function () {
  function start() {
    if (sessionStorage.getItem('bifrost:bootShown') === '1') return;
    if (matchMedia('(prefers-reduced-motion: reduce)').matches) return;

    const overlay = document.createElement('div');
    overlay.className = 'boot-overlay';
    overlay.innerHTML = `
      <div class="boot-skip">esc / click to skip</div>
      <pre id="boot-text"></pre>
    `;
    document.body.appendChild(overlay);

    const lines = [
      '> BIFROST v2.0',
      '> linking pools .......... [OK]',
      '> curator online ......... [OK]',
      '> circuit breaker armed .. [OK]',
      '> rendering grid ......... [OK]',
    ];
    const target = overlay.querySelector('#boot-text');
    let li = 0, ci = 0;
    let done = false;

    function finish() {
      if (done) return;
      done = true;
      sessionStorage.setItem('bifrost:bootShown', '1');
      overlay.style.transition = 'opacity 200ms';
      overlay.style.opacity = '0';
      setTimeout(() => overlay.remove(), 220);
    }

    overlay.addEventListener('click', finish);
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape') finish(); }, { once: true });

    function tick() {
      if (li >= lines.length) { setTimeout(finish, 200); return; }
      const line = lines[li];
      target.textContent += line[ci];
      ci++;
      if (ci >= line.length) {
        target.textContent += '\n';
        li++; ci = 0;
        setTimeout(tick, 60);
      } else {
        setTimeout(tick, 12);
      }
    }
    tick();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();
