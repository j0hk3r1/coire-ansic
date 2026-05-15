// dashboard.js — BIFROST live dashboard reactivity layer.
// Boots Alpine.js component over server-rendered jinja paint.

const DASH_NS = 'bifrost:';
const STORAGE = {
  get: (k, d) => { try { const v = localStorage.getItem(DASH_NS + k); return v ? JSON.parse(v) : d; } catch { return d; } },
  set: (k, v) => { try { localStorage.setItem(DASH_NS + k, JSON.stringify(v)); } catch {} },
};

function bifrostApp() {
  return {
    state: window.__BIFROST_INITIAL__ || null,
    timeRange: STORAGE.get('timeRange', 24),
    theme: STORAGE.get('theme', 'cyber'),
    compact: STORAGE.get('compact', false),
    collapsed: STORAGE.get('collapsed', {}),
    starredPools: STORAGE.get('starredPools', []),
    mutedPools: STORAGE.get('mutedPools', []),
    drawer: { open: false, kind: null, key: null },
    helpOpen: false,
    feedFilters: { errors: { text: '', chips: {} }, successes: { text: '', chips: {} } },
    activeTab: STORAGE.get('activeTab', 'overview'),
    status: null,
    usageEstimates: null,
    latency: null,
    weightDrift: null,
    pollInterval: null,
    slowPollInterval: null,
    backoff: 5000,
    lastSeen: { errors: null, successes: null },
    online: true,
    toasts: [],

    init() {
      document.body.classList.toggle('theme-clean', this.theme === 'clean');
      document.body.classList.toggle('compact', this.compact);
      this.startPolling();
      this.bindShortcuts();
      this.bindVisibility();
      const params = new URLSearchParams(location.search);
      const h = parseInt(params.get('h') || this.timeRange, 10);
      if ([1, 6, 24, 168].includes(h)) this.timeRange = h;
      // Tab from URL hash overrides storage
      const hash = (location.hash || '').replace('#', '');
      if (['overview','stream','pools','providers','curator','latency'].includes(hash)) this.activeTab = hash;
      this.fetchStatus();
      this.fetchUsage();
      this.fetchLatency();
      this.fetchDrift();
      setInterval(() => { this.fetchStatus(); }, 15000);
      setInterval(() => { this.fetchUsage(); }, 60000);
      setInterval(() => { this.fetchLatency(); }, 30000);
      setInterval(() => { this.fetchDrift(); }, 60000);
    },

    setTab(t) {
      this.activeTab = t;
      STORAGE.set('activeTab', t);
      history.replaceState(null, '', '#' + t);
    },

    async fetchStatus() {
      try {
        const r = await fetch('/api/health_status', { cache: 'no-store' });
        if (r.ok) this.status = await r.json();
      } catch (e) { /* keep stale */ }
    },

    async fetchUsage() {
      try {
        const r = await fetch('/api/usage_estimates', { cache: 'no-store' });
        if (r.ok) this.usageEstimates = await r.json();
      } catch (e) { /* keep stale */ }
    },

    async fetchLatency() {
      try {
        const r = await fetch('/api/latency', { cache: 'no-store' });
        if (r.ok) this.latency = await r.json();
      } catch (e) { /* keep stale */ }
    },

    async fetchDrift() {
      try {
        const r = await fetch('/api/weight_drift', { cache: 'no-store' });
        if (r.ok) this.weightDrift = await r.json();
      } catch (e) { /* keep stale */ }
    },

    // CB demote-type classification → CSS class for row coloring.
    cbBadge(d) {
      if (d.pruned) return { cls: 'badge-red', label: 'PRUNED' };
      if (d.daily_quota) return { cls: 'badge-yellow', label: 'daily-quota' };
      if ((d.fail_count || 0) > 0) return { cls: 'badge-orange', label: `slow-retry ${d.fail_count}` };
      return { cls: 'badge-blue', label: 'cooldown' };
    },

    // Color latency P95 by absolute ms thresholds (visual cue, not auto-action).
    latencyClass(p95) {
      if (p95 == null) return '';
      if (p95 > 10000) return 'lat-bad';
      if (p95 > 3000)  return 'lat-warn';
      if (p95 > 1000)  return 'lat-ok';
      return 'lat-fast';
    },

    fmtMs(ms) {
      if (ms == null) return '-';
      if (ms < 1000) return `${ms}ms`;
      return `${(ms/1000).toFixed(1)}s`;
    },

    driftClass(status) {
      if (status === 'ok') return 'drift-ok';
      if (status.startsWith('MISSING')) return 'drift-bad';
      if (status === 'extra (not in plan)') return 'drift-extra';
      if (status.startsWith('under')) return 'drift-under';
      if (status === 'over') return 'drift-over';
      return 'drift-warn';
    },

    driftSummary(pool) {
      const counts = { ok: 0, drifted: 0, missing: 0, extra: 0 };
      for (const t of (pool?.targets || [])) {
        if (t.status === 'ok') counts.ok++;
        else if (t.status.startsWith('MISSING')) counts.missing++;
        else if (t.status === 'extra (not in plan)') counts.extra++;
        else counts.drifted++;
      }
      return counts;
    },

    async restoreTarget(provider, model) {
      try {
        const r = await fetch('/api/circuit_breaker/restore', {
          method: 'POST', headers: {'Content-Type':'application/json'},
          body: JSON.stringify({ provider, model })
        });
        const d = await r.json();
        this.toasts.push({ id: Date.now(), msg: d.ok ? `restoring ${provider}/${model}` : `fail: ${d.error}`, kind: d.ok ? 'ok' : 'err' });
        setTimeout(() => this.fetchState(), 35000);
      } catch (e) { this.toasts.push({ id: Date.now(), msg: e.message, kind: 'err' }); }
    },

    async pruneTarget(provider, model) {
      if (!confirm(`Permanently prune ${provider}/${model}?`)) return;
      try {
        const r = await fetch('/api/circuit_breaker/prune', {
          method: 'POST', headers: {'Content-Type':'application/json'},
          body: JSON.stringify({ provider, model })
        });
        const d = await r.json();
        this.toasts.push({ id: Date.now(), msg: d.ok ? `pruned ${provider}/${model}` : `fail: ${d.error}`, kind: d.ok ? 'ok' : 'err' });
        this.fetchState();
      } catch (e) { this.toasts.push({ id: Date.now(), msg: e.message, kind: 'err' }); }
    },

    async fetchState() {
      try {
        const r = await fetch(`/api/stream_state?h=${this.timeRange}`, { cache: 'no-store' });
        if (!r.ok) throw new Error(`http ${r.status}`);
        const next = await r.json();
        this.applyState(next);
        this.online = true;
        if (this.backoff !== 5000) {
          this.backoff = 5000;
          this.scheduleNextPoll();
        }
      } catch (e) {
        this.online = false;
        this.backoff = Math.min(this.backoff * 2, 60000);
        this.scheduleNextPoll();
      }
    },

    applyState(next) {
      this.detectThresholds(this.state, next);
      // Mark new error/success rows.
      const stamp = (rows) => new Set((rows || []).map(r => `${r.ts}|${r.pool}|${r.provider}|${r.model}`));
      const oldErr = stamp(this.state?.recent_errors?.errors);
      const oldOk = stamp(this.state?.recent_successes?.successes);
      (next.recent_errors?.errors || []).forEach(r => {
        r._fresh = !oldErr.has(`${r.ts}|${r.pool}|${r.provider}|${r.model}`);
      });
      (next.recent_successes?.successes || []).forEach(r => {
        r._fresh = !oldOk.has(`${r.ts}|${r.pool}|${r.provider}|${r.model}`);
      });
      this.state = next;
    },

    detectThresholds(prev, next) {
      if (!prev) return;
      const stamp = (rows) => new Set((rows || []).map(r => `${r.ts}|${r.pool}|${r.provider}|${r.model}|${r.status_code || ''}`));
      const prevSet = stamp(prev.recent_errors?.errors);
      const newRows = (next.recent_errors?.errors || []).filter(r => {
        const k = `${r.ts}|${r.pool}|${r.provider}|${r.model}|${r.status_code || ''}`;
        return !prevSet.has(k);
      });
      newRows.forEach(r => this.addToast({
        level: 'error',
        title: 'new error',
        body: `${r.pool} · ${r.provider}/${(r.model || '').slice(0, 24)} · ${r.status_code || 'err'}`,
      }));
      const prevDemoted = prev.circuit_breaker?.demoted_count || 0;
      const nextDemoted = next.circuit_breaker?.demoted_count || 0;
      if (nextDemoted > prevDemoted) {
        this.addToast({ level: 'warn', title: 'breaker demote', body: `${nextDemoted} target(s) on cooldown` });
      }
      const prevPools = prev.pool_health?.pools || [];
      const nextPools = next.pool_health?.pools || [];
      nextPools.forEach(np => {
        const op = prevPools.find(p => p.pool === np.pool);
        if (op && op.rate_pct >= 60 && np.rate_pct < 60) {
          this.addToast({ level: 'error', title: 'pool degraded', body: `${np.pool} dropped to ${np.rate_pct}%` });
        }
      });
    },
    addToast(t) {
      const sig = `${t.title}|${t.body}`;
      if (!this._recentToasts) this._recentToasts = new Map();
      const now = Date.now();
      // prune entries older than 30s
      for (const [k, ts] of this._recentToasts) {
        if (now - ts > 30000) this._recentToasts.delete(k);
      }
      if (this._recentToasts.has(sig)) return;
      this._recentToasts.set(sig, now);
      t.id = now + Math.random();
      this.toasts.push(t);
      setTimeout(() => { this.toasts = this.toasts.filter(x => x.id !== t.id); }, 3000);
    },

    startPolling() {
      this.fetchState();
      this.pollInterval = setInterval(() => this.fetchState(), this.backoff);
    },

    scheduleNextPoll() {
      clearInterval(this.pollInterval);
      this.pollInterval = setInterval(() => this.fetchState(), this.backoff);
    },

    bindShortcuts() {
      document.addEventListener('keydown', (e) => {
        const tag = (e.target.tagName || '').toLowerCase();
        if (tag === 'input' || tag === 'textarea') return;
        if (e.key === 'r') this.fetchState();
        else if (e.key === '?') this.helpOpen = !this.helpOpen;
        else if (e.key === '/') { e.preventDefault(); this.focusFilter(); }
        else if (e.key === 'Escape') { this.drawer.open = false; this.helpOpen = false; }
        else if (e.key === 'c') { this.compact = !this.compact; STORAGE.set('compact', this.compact); document.body.classList.toggle('compact', this.compact); }
        else if (e.key === 't') {
          const opts = [1, 6, 24, 168];
          this.timeRange = opts[(opts.indexOf(this.timeRange) + 1) % opts.length];
          STORAGE.set('timeRange', this.timeRange);
          this.fetchState();
        }
        else if (/^[1-9]$/.test(e.key)) {
          const sections = document.querySelectorAll('section.panel');
          const idx = parseInt(e.key, 10) - 1;
          if (sections[idx]) sections[idx].scrollIntoView({ behavior: 'smooth', block: 'start' });
        }
      });
    },

    bindVisibility() {
      document.addEventListener('visibilitychange', () => {
        if (document.hidden) clearInterval(this.pollInterval);
        else { this.backoff = 5000; this.startPolling(); }
      });
    },

    setTimeRange(h) {
      this.timeRange = h;
      STORAGE.set('timeRange', h);
      const url = new URL(location.href);
      url.searchParams.set('h', h);
      history.replaceState(null, '', url);
      this.fetchState();
    },

    toggleTheme() {
      this.theme = this.theme === 'cyber' ? 'clean' : 'cyber';
      STORAGE.set('theme', this.theme);
      document.body.classList.toggle('theme-clean', this.theme === 'clean');
    },

    toggleCollapse(id) {
      this.collapsed[id] = !this.collapsed[id];
      STORAGE.set('collapsed', this.collapsed);
    },

    isCollapsed(id) {
      return !!this.collapsed[id];
    },

    toggleStar(pool) {
      const i = this.starredPools.indexOf(pool);
      if (i >= 0) this.starredPools.splice(i, 1); else this.starredPools.push(pool);
      STORAGE.set('starredPools', this.starredPools);
    },
    toggleMute(pool) {
      const i = this.mutedPools.indexOf(pool);
      if (i >= 0) this.mutedPools.splice(i, 1); else this.mutedPools.push(pool);
      STORAGE.set('mutedPools', this.mutedPools);
    },
    sortPools(pools) {
      return (pools || []).slice().sort((a, b) => {
        const aS = this.starredPools.includes(a.pool) ? -1 : 0;
        const bS = this.starredPools.includes(b.pool) ? -1 : 0;
        return aS - bS;
      }).filter(p => !this.mutedPools.includes(p.pool));
    },

    openDrawer(kind, key) {
      this.drawer = { open: true, kind, key };
    },

    closeDrawer() {
      this.drawer.open = false;
    },

    filteredFeed(kind) {
      const list = kind === 'errors'
        ? (this.state?.recent_errors?.errors || [])
        : (this.state?.recent_successes?.successes || []);
      const f = this.feedFilters[kind];
      const text = (f.text || '').toLowerCase();
      const activeChips = Object.entries(f.chips).filter(([_, v]) => v).map(([k]) => k);
      return list.filter(r => {
        if (text && !`${r.pool} ${r.provider} ${r.model} ${r.err || ''}`.toLowerCase().includes(text)) return false;
        if (activeChips.length === 0) return true;
        return activeChips.some(c => {
          const [type, val] = c.split(':');
          if (type === 'pool') return r.pool === val;
          if (type === 'provider') return r.provider === val;
          if (type === 'status') return String(r.status_code) === val || (val === 'cancelled' && r.cancelled);
          return false;
        });
      });
    },
    chipSet(kind) {
      const list = kind === 'errors'
        ? (this.state?.recent_errors?.errors || [])
        : (this.state?.recent_successes?.successes || []);
      const pools = new Set(), providers = new Set(), statuses = new Set();
      list.forEach(r => {
        if (r.pool) pools.add(r.pool);
        if (r.provider) providers.add(r.provider);
        if (r.cancelled) statuses.add('cancelled');
        else if (r.status_code) statuses.add(String(r.status_code));
      });
      return { pools: [...pools], providers: [...providers], statuses: [...statuses] };
    },
    toggleChip(kind, chipKey) {
      this.feedFilters[kind].chips[chipKey] = !this.feedFilters[kind].chips[chipKey];
    },
    focusFilter() {
      const el = document.querySelector('input[data-filter-active]');
      if (el) el.focus();
    },

    poolRadarPoints(p) {
      const ax = [
        (p.rate_pct || 0) / 100,
        p.p50 ? Math.min(1, 1 / p.p50) : 0,
        p.total ? Math.min(1, p.fallback_rescues / p.total) : 0,
        p.total ? Math.min(1, (new Set([p.top_model]).size) / 1) : 0,
      ];
      const cx = 40, cy = 40, r = 28;
      return ax.map((v, i) => {
        const angle = (Math.PI * 2 * i / ax.length) - Math.PI / 2;
        return `${cx + r * v * Math.cos(angle)},${cy + r * v * Math.sin(angle)}`;
      }).join(' ');
    },

    providerDonut() {
      const rows = this.state?.bifrost_metrics?.by_provider || [];
      const total = rows.reduce((a, r) => a + (r.input || 0) + (r.output || 0), 0);
      if (!total) return { slices: [], total: 0 };
      let acc = 0;
      const colors = ['#ff2d92', '#00f0ff', '#a855f7', '#b6ff00', '#ffb627', '#ff3360'];
      const slices = rows.map((r, i) => {
        const v = (r.input || 0) + (r.output || 0);
        const start = (acc / total) * 360;
        acc += v;
        const end = (acc / total) * 360;
        return { name: r.provider, color: colors[i % colors.length], start, end, pct: Math.round((v / total) * 100) };
      });
      return { slices, total };
    },
    arcPath(cx, cy, r, startAngle, endAngle) {
      const toRad = (a) => (a - 90) * Math.PI / 180;
      const x0 = cx + r * Math.cos(toRad(startAngle));
      const y0 = cy + r * Math.sin(toRad(startAngle));
      const x1 = cx + r * Math.cos(toRad(endAngle));
      const y1 = cy + r * Math.sin(toRad(endAngle));
      const large = endAngle - startAngle > 180 ? 1 : 0;
      return `M ${cx} ${cy} L ${x0} ${y0} A ${r} ${r} 0 ${large} 1 ${x1} ${y1} Z`;
    },

    drawerEvents() {
      if (!this.state || !this.drawer.open) return [];
      const errs = (this.state.recent_errors?.errors || []).map(e => ({...e, status: 'err'}));
      const oks = (this.state.recent_successes?.successes || []).map(e => ({...e, status: 'ok'}));
      const all = [...errs, ...oks].sort((a, b) => (b.ts || '').localeCompare(a.ts || ''));
      const matches = (r) => {
        if (this.drawer.kind === 'pool') return r.pool === this.drawer.key;
        if (this.drawer.kind === 'provider') return r.provider === this.drawer.key;
        if (this.drawer.kind === 'model') {
          const [p, m] = this.drawer.key.split('/');
          return r.provider === p && r.model === m;
        }
        return false;
      };
      return all.filter(matches).slice(0, 100);
    },

    exportSection(rows, name, format) {
      if (!rows || rows.length === 0) return;
      let blob;
      if (format === 'json') {
        blob = new Blob([JSON.stringify(rows, null, 2)], { type: 'application/json' });
      } else {
        const cols = Object.keys(rows[0]);
        const esc = (v) => `"${String(v ?? '').replace(/"/g, '""')}"`;
        const csv = [cols.join(','), ...rows.map(r => cols.map(c => esc(r[c])).join(','))].join('\n');
        blob = new Blob([csv], { type: 'text/csv' });
      }
      const ts = new Date().toISOString().replace(/[:.]/g, '').slice(0, 13);
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = `bifrost-${name}-${ts}.${format}`;
      a.click();
      URL.revokeObjectURL(a.href);
    },

    tickerItems() {
      const s = this.state;
      if (!s) return [];
      const rpmTotal = (s.requests_per_minute || []).reduce((a, b) => a + b, 0);
      const topPool = (s.pool_health?.pools || []).slice().sort((a, b) => (b.total || 0) - (a.total || 0))[0];
      const latestErr = (s.recent_errors?.errors || [])[0];
      const items = [
        ['req/60m', rpmTotal],
        ['top pool', topPool ? `${topPool.pool} ${topPool.rate_pct}%` : '—'],
        ['cooldown', s.circuit_breaker?.demoted_count || 0],
        ['providers', s.provider_status?.providers?.length || 0],
        ['aa models', s.curator_recommendations?.total_models || 0],
        ['latest err', latestErr ? `${latestErr.provider}/${(latestErr.model || '').slice(0,20)} ${latestErr.status_code}` : 'none'],
      ];
      return items;
    },
  };
}

window.bifrostApp = bifrostApp;
window.STORAGE = STORAGE;
