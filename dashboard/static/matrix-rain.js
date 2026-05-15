// matrix-rain.js — fixed-position canvas, 5 fps, neon-cyan falling chars.
// Disabled by prefers-reduced-motion or .theme-clean on body.
(function () {
  if (matchMedia('(prefers-reduced-motion: reduce)').matches) return;

  const c = document.createElement('canvas');
  c.style.cssText = 'position:fixed;inset:0;z-index:-1;opacity:0.12;pointer-events:none';
  document.body.appendChild(c);
  const ctx = c.getContext('2d');
  const chars = 'アァカサタナハマヤラワABCDEFGHIJKLMNOPQR0123456789'.split('');
  let cols = [], W = 0, H = 0, raf = null;

  function resize() {
    W = c.width = innerWidth;
    H = c.height = innerHeight;
    cols = Array.from({ length: Math.floor(W / 14) }, () => Math.random() * H);
  }

  function draw() {
    if (document.body.classList.contains('theme-clean')) return;
    ctx.fillStyle = 'rgba(4,6,15,0.18)';
    ctx.fillRect(0, 0, W, H);
    ctx.fillStyle = '#00f0ff';
    ctx.font = '12px "JetBrains Mono"';
    cols.forEach((y, i) => {
      const ch = chars[Math.floor(Math.random() * chars.length)];
      ctx.fillText(ch, i * 14, y);
      cols[i] = y > H + Math.random() * 600 ? 0 : y + 14;
    });
  }

  let last = 0;
  function loop(t) {
    if (document.hidden) { raf = requestAnimationFrame(loop); return; }
    if (t - last > 200) { draw(); last = t; }
    raf = requestAnimationFrame(loop);
  }

  addEventListener('resize', resize);
  resize();
  loop(0);
})();
