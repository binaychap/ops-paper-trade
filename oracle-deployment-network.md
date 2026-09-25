# Oracle Deployment — Network Setup & Troubleshooting

Notes from exposing the OpsPaperTrade FastAPI backend running on an Oracle Cloud
compute instance so the iOS app can reach it over the internet. Written 2026-09-23.

> Never commit real API keys or server IPs to this repo. Use `<IOS_API_KEY>` and
> `<oracle-public-ip>` placeholders below. The instance's ephemeral public IP can
> change on restart — verify with `curl -s ifconfig.me` on the server.

## 1. Run the API so it's reachable

In the bot's `.env` on the server:

- `IOS_API_KEY=<long random string>` — the iOS app's password for the trading API. Not the Optionomics key.
- `DRY_RUN=true` — keep on while testing.

Start uvicorn bound to all interfaces (the default is localhost-only):

```bash
cd ~/ops-paper-trade
PYTHONPATH=. uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Keep it alive after SSH disconnects (use `>>` so restarts append instead of wiping the old log):

```bash
PYTHONPATH=. nohup uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 >> uvicorn.log 2>&1 &
```

Logs: uvicorn has no default log file — it writes to stdout/stderr, which the
command above captures in `~/ops-paper-trade/uvicorn.log`. Watch it live with
`tail -f uvicorn.log`. To keep a copy of the old log before restarting,
`mv uvicorn.log uvicorn.log.bak` first.

Verify it's listening on all interfaces — want `0.0.0.0:8000`, not `127.0.0.1:8000`:

```bash
ss -tlnp | grep 8000
```

Smoke-test from the server itself:

```bash
curl -H "Authorization: Bearer <IOS_API_KEY>" http://127.0.0.1:8000/api/trading/account
```

## 2. Open the firewall — two layers on Oracle

### Layer 1: instance iptables

```bash
sudo iptables -I INPUT -p tcp --dport 8000 -j ACCEPT
sudo iptables -L INPUT -n --line-numbers | head -10   # confirm the rule is there
sudo service iptables save                             # persist across reboots
```

### Layer 2: OCI security list

OCI Console → Networking → Virtual Cloud Networks → subnet → Security list → Add Ingress Rules:

| Field | Value |
|---|---|
| Source CIDR | `0.0.0.0/0` |
| IP Protocol | TCP |
| Source port range | All |
| Destination port range | `8000` |

Source port = the client's ephemeral port (leave as All). Destination port = the port the app listens on.

Also confirm:

- The subnet your instance is in actually uses the security list you edited.
- No Network Security Groups are attached to the VNIC (check the VNIC details page). If any are, add the same ingress rule to the NSG too.
- Don't confuse "Add security attributes" on the VNIC page (Zero Trust Packet Routing tags) with firewall rules — that's the wrong dialog.

## 3. Issues hit and fixes

1. **uvicorn bound to `127.0.0.1:8000` only.** Symptom: `ss` showed localhost-only; remote curl got "couldn't connect". Fix: `kill <pid>`, restart with `--host 0.0.0.0`.
2. **OCI "source port range" vs "destination port range" confusion.** Destination = `8000`, source = All.
3. **Wrong console dialog.** "Add security attributes" is for ZPR tags, not firewall rules. Firewall rules live under security lists / NSGs.
4. **Still refused with every layer open (investigating).** iptables ACCEPT present, correct security list attached to the subnet, no NSGs, correct ephemeral IP — yet curl from the Mac fails fast. Suspect `firewalld` running alongside iptables on Oracle Linux:
   ```bash
   sudo firewall-cmd --state
   # if it says "running":
   sudo firewall-cmd --permanent --add-port=8000/tcp
   sudo firewall-cmd --reload
   ```
   Cross-check: try `http://<oracle-public-ip>:8000/` from the iPhone on cellular to isolate Mac-side network issues.

## 4. End-to-end check from the Mac

```bash
curl -H "Authorization: Bearer <IOS_API_KEY>" http://<oracle-public-ip>:8000/api/trading/account
```

## 5. iOS app settings

Settings tab → Server URL `http://<oracle-public-ip>:8000` → paste the same `IOS_API_KEY` → Save API key to Keychain → Test connection. No same-Wi-Fi needed; the server has a public IP.
