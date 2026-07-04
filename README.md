# OPS_QuickBuild 😎

one command → a whole **osu! private server** (osu!stable, standard mode + friends).

it installs and wires up **everything**: [bancho.py](https://github.com/osuAkatsuki/bancho.py) (the server), MariaDB (all databases + schema), Redis (live rankings), nginx (all the osu! subdomains), SSL certs, your accounts, a working ranking system, a full pp recalculator, and a leaderboard. you do the last 10% (pointing your DNS at the box) and it literally tells you how.

runs on **Debian / Ubuntu**. players connect with plain **osu!stable** using `-devserver`.

---

## quickstart

on your Debian/Ubuntu server:

```bash
git clone <this repo> OPS_QuickBuild && cd OPS_QuickBuild
chmod +x ops-quickbuild

# optional but recommended: prep your accounts first
cp accounts.example.txt accounts.txt && nano accounts.txt

sudo ./ops-quickbuild
```

answer the questions (server name, domain, accounts file, SSL choice), then go touch grass for a few minutes. when it's done it prints your exact DNS records, the firewall ports, and the start command.

**start the server** (this is the command it gives you, in tmux like god intended):

```bash
tmux new -s myserver /opt/myserver/start_server.sh
# detach: ctrl+b then d • reattach: tmux attach -t myserver
# screen version: screen -S myserver /opt/myserver/start_server.sh
```

**players connect** by adding `-devserver yourdomain.com` to their osu!.exe shortcut target. that's it, no patched client needed.

---

## what you feed it

| input | what it's for |
|---|---|
| server name | branding + bot name + install dir naming |
| domain | the thing players type after `-devserver` |
| accounts file | `username:password` per line (`:admin` third field = staff) — see [accounts.example.txt](accounts.example.txt) |
| SSL choice | Let's Encrypt wildcard (real), self-signed (testing), or bring-your-own |
| osu! api key | optional, helps beatmap lookups |

## 📦 what it builds

- **bancho.py** cloned + python env via `uv`, configured through a generated `.env`
- **MariaDB**: database, dedicated db user w/ random password, full schema imported
- **Redis**: live pp leaderboards (this is the "working ranking system" — bancho.py keeps global + per-country rankings in Redis sorted sets, updated on every score)
- **nginx**: one config routing `osu. c. ce. c4. c5. c6. a. b. api. assets.` → the server
- **GeoLite2** db so country flags work
- **accounts** imported with real bancho password hashing (bcrypt of md5, what osu!stable sends)
- **ops toolkit** in `<install>/`:
  - `start_server.sh` — the thing you run in tmux
  - `recalc-pp.sh` — recalculates **every score's pp** + rebuilds totals + refreshes leaderboards
  - `leaderboard.sh` — pretty CLI leaderboard (`--mode osu --top 25`)
  - `import-accounts.sh` — add more users any time
- **report file** `<install>/ops-quickbuild-report.txt` with every credential and command (chmod 600, keep it secret 🤫)

## custom pp calculation

default = **bancho's current pp system**, byte-for-byte what bancho.py computes (rosu-pp under the hood). zero changes out of the box.

want your own meta? edit **`<install>/bancho.py/ops_custom_pp.py`** — one function:

```python
def modify_pp(pp: float, ctx: dict) -> float:
    # examples: flat 1.2x, relax buffs, pp caps, softcap curves...
    return pp  # default: pure bancho
```

it's applied in two places:
1. **live** — the installer patches a tiny fail-open hook into bancho.py's performance module, so every score submission runs through your profile
2. **offline** — `recalc-pp.sh` rewrites pp on all existing scores with the same profile

after editing: run `./recalc-pp.sh`, then restart the server. rankings update everywhere (scores → player totals with bancho's weighted top-100 curve → Redis leaderboards).

## leaderboard

```bash
/opt/myserver/leaderboard.sh --mode osu --top 25
# modes: osu taiko catch mania rx!osu rx!taiko rx!catch ap!osu
```

medals for top 3, restricted players hidden, reads straight from the db 🥇

## 📡 the 10% you do yourself (DNS)

point these **A records** at your server's IP (or just one wildcard `*` record):

```
@  osu  c  ce  c4  c5  c6  a  b  api  assets
```

osu!stable **requires https**, hence the wildcard cert step. with self-signed mode, every player must install the generated `cert.pem` as a Trusted Root cert (fine for you + the homies, not for a public server).

## faq

- **server won't start?** `tmux attach -t <name>` and read the error. usually DNS/cert stuff or MariaDB not running (`systemctl status mariadb redis-server nginx`).
- **osu! says can't connect?** check DNS has propagated (`ping osu.yourdomain.com`), ports 80/443 open, and the cert is trusted by the client.
- **pp hook didn't patch?** (installer warns you) bancho.py upstream moved things around — live scores use stock pp, but `recalc-pp.sh` still applies your custom math. run it on a cron if you want.
- **update bancho.py?** `cd bancho.py && git pull && uv sync`, rerun `ops/patch_pp_hook.py`, restart.
- **where are the db creds??** `<install>/ops-quickbuild-report.txt`

---

built different 😎
gl on the farm
