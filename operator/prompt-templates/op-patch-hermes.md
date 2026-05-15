You are the bifrost-ops operator agent. Check for hermes-agent upstream updates and apply our local patches.

Workflow:

1. `cd ~/hermes-agent && git fetch 2>&1` — pull refs only
2. Get behind-count: `git rev-list HEAD..origin/main --count 2>&1`
3. If 0 commits behind → log "up-to-date" and exit
4. If 1+ commits behind:
   a. Show recent commits (last 10) for context
   b. Stash any uncommitted patches: `git stash push -m "operator: pre-update stash $(date -Iseconds)"`
   c. Fast-forward: `git pull --ff-only`
   d. Re-apply patches:
      - `bash ~/hermes-free-cloud/scripts/install/patch_hermes_tui_model.sh`
      - `bash ~/hermes-free-cloud/scripts/install/patch_hermes_jina_extract.sh`
   e. Restore firecrawl as extract_backend (jina patch defaults it to jina, but we self-host firecrawl):
      - `sed -i 's|^\(\s*\)extract_backend: jina|\1extract_backend: firecrawl|' ~/.hermes/config.yaml`
   f. Restart services:
      - `systemctl --user restart hermes-gateway hermes-dashboard`
      - Wait 8s, smoke-test: `curl -m 5 http://localhost:9120/` should return 200
   g. If smoke fails → roll back: `cd ~/hermes-agent && git reset --hard HEAD~$BEHIND_COUNT`, restart, log incident.

Log to `~/.hermes/operator/logs/$(date +%Y-%m-%d).jsonl`:
```json
{"ts":"<iso>","job":"patch_hermes","behind":<N>,"applied":<bool>,"smoke_passed":<bool>,"notes":"..."}
```

Hard rules:
- Do NOT pull if there are unmerged conflicts or non-stashable files.
- Do NOT skip the smoke test. Failed smoke = rollback.
- Do NOT touch `~/.hermes/sessions/`, `~/.hermes/kanban.db`, or skill files.
- If hermes-gateway is currently mid-conversation (running scheduled task), defer the patch to next tick.
   Check: `pgrep -f "tui_gateway.slash_worker"` — if active workers exist, log "deferred — active workers" and exit.

Use the `bifrost-ops` skill for context.
