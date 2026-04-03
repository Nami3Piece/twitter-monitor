# Common Issues & Diagnostic Checklist

> Accumulated from real incidents. Check this before debugging.

---

## Issue 1: Code changes not taking effect after deploy

**Symptom**: You git push and restart the service, but the site still shows old code.

**Root cause**: The systemd service runs from `/opt/twitter-monitor/`, but most git operations target `/var/www/twitter-monitor/`. They are two separate clones.

**Fix**:
```bash
cd /opt/twitter-monitor && sudo git pull origin main
cd /var/www/twitter-monitor && sudo git pull origin main
sudo fuser -k 8080/tcp 2>/dev/null
sleep 1
sudo systemctl restart twitter-monitor-web
```

**Verify which path the process is using**:
```bash
PID=$(sudo lsof -ti :8080)
ls /proc/$PID/cwd -la
```

---

## Issue 2: White background despite dark theme CSS

**Symptom**: Site renders with white/browser-default background even though CSS sets `background:var(--bg)`.

**Root cause**: CSS custom properties defined without a selector become invalid rules. Browser ignores them, so all `var(--bg)` fall back to `transparent` → white.

**Bad** (no selector — variables never defined):
```css
<style>{--bg:#0A0A0A; --card:#141414;}
```

**Good** (`:root` selector required):
```css
<style>:root{--bg:#0A0A0A; --card:#141414;}
```

In Python f-strings: `{{` → `{`, so write `:root{{--bg:...}}`.

**Quick diagnostic**:
```bash
curl -s http://127.0.0.1:8080/ | grep -o ':root{--bg'
# Should return: :root{--bg
# If empty → CSS variables broken
```

---

## Issue 3: AI Draft returns "Failed to generate drafts"

**Symptom**: Clicking "Generate Drafts" shows error toast.

**Checklist**:
1. Check v2code.ai balance (recharge at v2code.ai dashboard)
2. Verify correct API proxy URL: `https://dev.v2code.ai` (not `code.ai2api.dev`)
3. Model must be `claude-sonnet-4-6` — other models return 503/524
4. Test API directly:
```bash
curl -X POST "https://dev.v2code.ai/v1/messages" \
  -H "x-api-key: YOUR_KEY" \
  -H "anthropic-version: 2023-06-01" \
  -H "Content-Type: application/json" \
  -d '{"model":"claude-sonnet-4-6","max_tokens":20,"messages":[{"role":"user","content":"hi"}]}'
```

**Common error messages**:
| Message | Cause | Fix |
|---------|-------|-----|
| `没有可用token` | Balance depleted | Recharge v2code.ai |
| `无效的令牌` | Wrong API key | Update `.env` |
| `model_not_found` | Wrong model name | Use `claude-sonnet-4-6` only |
| `524 timeout` | Used opus model | Switch to sonnet |

---

## Issue 4: 502 Bad Gateway

**Symptom**: Nginx returns 502, site unreachable.

**Fix**:
```bash
sudo lsof -i :8080          # check if process exists
sudo fuser -k 8080/tcp      # kill stale process
sudo systemctl restart twitter-monitor-web
sudo journalctl -u twitter-monitor-web -n 50   # check logs if still failing
```

---

## Deployment Checklist (Standard)

- [ ] Pull to `/opt/twitter-monitor/` (service runs here)
- [ ] Pull to `/var/www/twitter-monitor/` (keep in sync)
- [ ] Kill port 8080 old process
- [ ] Restart `twitter-monitor-web` service
- [ ] Purge Cloudflare cache
- [ ] Hard refresh browser (Cmd+Shift+R)
