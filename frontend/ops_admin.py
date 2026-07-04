# -*- coding: utf-8 -*-
"""OPS_QuickBuild — guweb admin extension: real moderation actions 😎

Installed into guweb by ops-upgrade-admin. Adds:
  /admin/users — search, ban/unban, silence, rename, score wipe,
                 supporter + nominator toggles
  /admin/maps  — rank / love / unrank beatmaps (whole set or single diff)

All writes go straight to the bancho.py database (and Redis leaderboards
where relevant). bancho.py caches online players in memory, so bans and
priv changes fully apply when the target next logs in (or on restart).
"""

__all__ = ()

import json
import re
import subprocess
import time
from pathlib import Path

from quart import Blueprint
from quart import redirect
from quart import request
from quart import render_template
from quart import session

import config
from objects import glob
from objects.utils import flash

ops_admin = Blueprint('ops_admin', __name__)

# bancho.py privilege bits
PRIV_UNRESTRICTED = 1 << 0
PRIV_SUPPORTER = 1 << 4
PRIV_PREMIUM = 1 << 5
PRIV_NOMINATOR = 1 << 11
PRIV_STAFF = (1 << 12) | (1 << 13) | (1 << 14)

USERNAME_RE = re.compile(r'^[\w \[\]-]{2,15}$')
ALL_MODES = (0, 1, 2, 3, 4, 5, 6, 8)

MAP_ACTIONS = {'rank': 2, 'love': 5, 'unrank': 0}
MAP_STATUS_LABELS = {
    -1: 'not submitted', 0: 'pending', 1: 'update available',
    2: 'ranked', 3: 'approved', 4: 'qualified', 5: 'loved',
}


def _guard():
    """Return a redirect target when the visitor isn't staff, else None."""
    if 'authenticated' not in session:
        return 'login'
    if not session['user_data']['is_staff']:
        return 'home'
    return None


def _redis():
    try:
        import redis as redis_lib
        return redis_lib.Redis(host='127.0.0.1', port=6379, socket_timeout=2)
    except Exception:
        return None


def _boards_remove(user_id: int, country: str) -> None:
    r = _redis()
    if r is None:
        return
    try:
        for mode in ALL_MODES:
            r.zrem(f'bancho:leaderboard:{mode}', user_id)
            r.zrem(f'bancho:leaderboard:{mode}:{country}', user_id)
    except Exception:
        pass


async def _boards_restore(user_id: int, country: str) -> None:
    r = _redis()
    if r is None:
        return
    try:
        rows = await glob.db.fetchall(
            'SELECT mode, pp FROM stats WHERE id = %s AND pp > 0', [user_id])
        for row in rows or []:
            r.zadd(f'bancho:leaderboard:{row["mode"]}', {str(user_id): int(row['pp'])})
            r.zadd(f'bancho:leaderboard:{row["mode"]}:{country}',
                   {str(user_id): int(row['pp'])})
    except Exception:
        pass


def _flag_user(u: dict, now: int) -> dict:
    priv = u['priv']
    u['is_banned'] = not priv & PRIV_UNRESTRICTED
    u['is_silenced'] = (u.get('silence_end') or 0) > now
    u['is_staff'] = bool(priv & PRIV_STAFF)
    u['is_donor'] = bool(priv & (PRIV_SUPPORTER | PRIV_PREMIUM))
    u['is_nominator'] = bool(priv & PRIV_NOMINATOR)
    return u


# ─── users ───────────────────────────────────────────────────────────────────

@ops_admin.route('/users')
async def users():
    fail = _guard()
    if fail:
        return await flash('error', 'You have insufficient privileges.', fail)

    q = request.args.get('q', '').strip()
    base = ('SELECT u.id, u.name, u.priv, u.country, u.silence_end, '
            'u.latest_activity, '
            '(SELECT pp FROM stats WHERE id = u.id AND mode = 0) pp '
            'FROM users u ')
    if q:
        rows = await glob.db.fetchall(
            base + 'WHERE u.name LIKE %s OR u.id = %s ORDER BY u.id DESC LIMIT 50',
            [f'%{q}%', q])
    else:
        rows = await glob.db.fetchall(base + 'ORDER BY u.id DESC LIMIT 50')

    now = int(time.time())
    users = [_flag_user(dict(r), now) for r in (rows or [])]
    return await render_template(
        'admin/users.html', users=users, q=q,
        msg=request.args.get('msg'), st=request.args.get('st', 'success'))


@ops_admin.route('/users/action', methods=['POST'])
async def users_action():
    fail = _guard()
    if fail:
        return await flash('error', 'You have insufficient privileges.', fail)

    form = await request.form
    try:
        user_id = int(form['user_id'])
        action = form['action']
    except (KeyError, ValueError):
        return redirect('/admin/users?st=error&msg=bad request')

    q = form.get('q', '')

    def done(message: str, st: str = 'success'):
        return redirect(f'/admin/users?q={q}&st={st}&msg={message}')

    user = await glob.db.fetch(
        'SELECT id, name, priv, country FROM users WHERE id = %s', [user_id])
    if not user:
        return done('user not found', 'error')
    if user_id == 1:
        return done('leave the bot alone 😭', 'error')
    if user_id == session['user_data']['id'] and action in ('ban', 'wipe'):
        return done("you can't do that to yourself 💀", 'error')

    now = int(time.time())

    if action == 'ban':
        await glob.db.execute(
            'UPDATE users SET priv = priv & ~%s WHERE id = %s',
            [PRIV_UNRESTRICTED, user_id])
        _boards_remove(user_id, user['country'])
        return done(f"'{user['name']}' banned — off the leaderboards, "
                    'fully applies on their next login')

    if action == 'unban':
        await glob.db.execute(
            'UPDATE users SET priv = priv | %s WHERE id = %s',
            [PRIV_UNRESTRICTED, user_id])
        await _boards_restore(user_id, user['country'])
        return done(f"'{user['name']}' unbanned — back on the boards")

    if action == 'silence':
        try:
            hours = max(1, min(24 * 365, int(form.get('hours', 24))))
        except ValueError:
            return done('silence hours must be a number', 'error')
        await glob.db.execute(
            'UPDATE users SET silence_end = %s WHERE id = %s',
            [now + hours * 3600, user_id])
        return done(f"'{user['name']}' silenced for {hours}h")

    if action == 'unsilence':
        await glob.db.execute(
            'UPDATE users SET silence_end = 0 WHERE id = %s', [user_id])
        return done(f"'{user['name']}' unsilenced")

    if action == 'rename':
        new_name = form.get('new_name', '').strip()
        if not USERNAME_RE.match(new_name):
            return done('invalid username (2-15 chars, letters/numbers/_ -[])', 'error')
        safe = new_name.lower().replace(' ', '_')
        clash = await glob.db.fetch(
            'SELECT id FROM users WHERE safe_name = %s AND id != %s', [safe, user_id])
        if clash:
            return done('that name is taken', 'error')
        await glob.db.execute(
            'UPDATE users SET name = %s, safe_name = %s WHERE id = %s',
            [new_name, safe, user_id])
        return done(f"'{user['name']}' renamed to '{new_name}'")

    if action == 'wipe':
        await glob.db.execute('DELETE FROM scores WHERE userid = %s', [user_id])
        await glob.db.execute(
            'UPDATE stats SET tscore=0, rscore=0, pp=0, plays=0, playtime=0, '
            'acc=0, max_combo=0, total_hits=0, replay_views=0, xh_count=0, '
            'x_count=0, sh_count=0, s_count=0, a_count=0 WHERE id = %s', [user_id])
        _boards_remove(user_id, user['country'])
        return done(f"'{user['name']}' wiped — all scores and stats gone")

    if action == 'donor':
        await glob.db.execute(
            'UPDATE users SET priv = priv | %s, donor_end = %s WHERE id = %s',
            [PRIV_SUPPORTER, now + 10 * 365 * 86400, user_id])
        return done(f"'{user['name']}' is now a supporter 💖")

    if action == 'undonor':
        await glob.db.execute(
            'UPDATE users SET priv = priv & ~%s, donor_end = 0 WHERE id = %s',
            [PRIV_SUPPORTER | PRIV_PREMIUM, user_id])
        return done(f"'{user['name']}' supporter removed")

    if action == 'nominator':
        await glob.db.execute(
            'UPDATE users SET priv = priv | %s WHERE id = %s',
            [PRIV_NOMINATOR, user_id])
        return done(f"'{user['name']}' can now rank maps (!map + web panel)")

    if action == 'unnominator':
        await glob.db.execute(
            'UPDATE users SET priv = priv & ~%s WHERE id = %s',
            [PRIV_NOMINATOR, user_id])
        return done(f"'{user['name']}' nominator removed")

    return done('unknown action', 'error')


# ─── maps ────────────────────────────────────────────────────────────────────

@ops_admin.route('/maps')
async def maps():
    fail = _guard()
    if fail:
        return await flash('error', 'You have insufficient privileges.', fail)

    q = request.args.get('q', '').strip()
    base = ('SELECT id, set_id, artist, title, version, creator, status, frozen '
            'FROM maps ')
    if q:
        rows = await glob.db.fetchall(
            base + 'WHERE artist LIKE %s OR title LIKE %s OR set_id = %s OR id = %s '
                   'ORDER BY id DESC LIMIT 30',
            [f'%{q}%', f'%{q}%', q, q])
    else:
        rows = await glob.db.fetchall(base + 'ORDER BY id DESC LIMIT 15')

    maps_ = [dict(r) for r in (rows or [])]
    for m in maps_:
        m['status_label'] = MAP_STATUS_LABELS.get(m['status'], str(m['status']))
    return await render_template(
        'admin/maps.html', maps=maps_, q=q,
        msg=request.args.get('msg'), st=request.args.get('st', 'success'))


@ops_admin.route('/maps/action', methods=['POST'])
async def maps_action():
    fail = _guard()
    if fail:
        return await flash('error', 'You have insufficient privileges.', fail)

    form = await request.form
    action = form.get('action', '')
    target = form.get('target', 'set')
    q = form.get('q', '')

    def done(message: str, st: str = 'success'):
        return redirect(f'/admin/maps?q={q}&st={st}&msg={message}')

    if action not in MAP_ACTIONS:
        return done('unknown action', 'error')
    try:
        ident = int(form['ident'])
    except (KeyError, ValueError):
        return done('enter a numeric beatmap(set) id', 'error')

    col = 'set_id' if target == 'set' else 'id'
    count_row = await glob.db.fetch(
        f'SELECT COUNT(*) c FROM maps WHERE {col} = %s', [ident])
    count = count_row['c'] if count_row else 0
    if not count:
        return done(f'no maps in the db with {col} = {ident} — someone needs to '
                    'play/download it in-game once first', 'error')

    await glob.db.execute(
        f'UPDATE maps SET status = %s, frozen = 1 WHERE {col} = %s',
        [MAP_ACTIONS[action], ident])
    label = {'rank': 'RANKED', 'love': 'LOVED', 'unrank': 'unranked'}[action]
    return done(f'{count} difficulty(s) set to {label} (frozen so bancho.py '
                "won't overwrite it) — players see it after map cache refresh/restart")


# ─── pp builder ──────────────────────────────────────────────────────────────

CURVE_EASE = {'front': 0.45, 'linear': 1.0, 'back': 2.0}
MOD_CHOICES = [
    (2, 'EZ'), (8, 'HD'), (16, 'HR'), (64, 'DT/NC'),
    (256, 'HT'), (1024, 'FL'), (8192, 'AP'),
]
PP_DEFAULTS = {
    'base_min': 1.5, 'base_max': 3.5, 'full_at': 500.0, 'curve': 'front',
    'stream_buff': 50.0, 'aim_nerf': 15.0, 'cap': 4000.0,
    'relax_factor': 0.35, 'mod_rules': [],
}

# the fail-open hook machinery appended to every generated profile
PP_PLUMBING = '''

# ─────────────────────────────────────────────────────────────────────────────
# plumbing below — called by the live hook inside bancho.py; fails open.
# ─────────────────────────────────────────────────────────────────────────────

def apply_to_results(results, args=None, kwargs=None):
    try:
        score_params = _extract_score_params(args, kwargs)
        for i, result in enumerate(results or []):
            ctx = {"source": "live"}
            if score_params is not None and i < len(score_params):
                sp = score_params[i]
                ctx["mode"] = getattr(sp, "mode", None)
                ctx["mods"] = getattr(sp, "mods", None)
                ctx["acc"] = getattr(sp, "acc", None)
                ctx["combo"] = getattr(sp, "combo", None)
            _apply_one(result, ctx)
    except Exception:
        pass
    return results


def _extract_score_params(args, kwargs):
    try:
        candidates = []
        if kwargs:
            candidates.append(kwargs.get("scores"))
        if args:
            candidates.extend(args)
        for cand in candidates:
            if cand is None or isinstance(cand, (str, bytes)):
                continue
            items = list(cand)
            if items and hasattr(items[0], "mods"):
                return items
    except Exception:
        pass
    return None


def _apply_one(result, ctx):
    perf = None
    if isinstance(result, dict):
        perf = result.get("performance", result)
    elif hasattr(result, "performance"):
        perf = result.performance

    for key in ("pp_acc", "pp_aim", "pp_speed", "pp_flashlight"):
        val = perf.get(key) if isinstance(perf, dict) else getattr(perf, key, None)
        if isinstance(val, (int, float)):
            ctx[key] = float(val)

    if isinstance(perf, dict) and isinstance(perf.get("pp"), (int, float)):
        try:
            new_pp = float(modify_pp(float(perf["pp"]), dict(ctx)))
            if new_pp >= 0 and new_pp == new_pp:
                perf["pp"] = new_pp
        except Exception:
            pass
    elif perf is not None and isinstance(getattr(perf, "pp", None), (int, float)):
        try:
            new_pp = float(modify_pp(float(perf.pp), dict(ctx)))
            if new_pp >= 0 and new_pp == new_pp:
                object.__setattr__(perf, "pp", new_pp)
        except Exception:
            pass
'''


def _pp_paths() -> dict:
    gulag = Path(config.path_to_gulag.rstrip('/'))
    install = gulag.parent
    return {
        'profile': gulag / 'ops_custom_pp.py',
        'install': install,
        'slug': install.name,
        'log': install / 'pp-update.log',
    }


def _generate_profile(cfg: dict, flags: dict) -> str:
    ease = CURVE_EASE[cfg['curve']]
    L = [
        '"""pp profile — generated by the OPS admin PP builder 😎',
        '',
        'edit via /admin/pp on the website. hand edits here get overwritten by',
        'the next builder save (a .bak of the previous file is kept).',
        '"""',
        '# OPS_PP_BUILDER: ' + json.dumps(cfg),
        'from __future__ import annotations',
        '',
        '',
        'def modify_pp(pp: float, ctx: dict) -> float:',
        '    if not pp or pp <= 0:',
        '        return 0.0',
        f"    t = min(pp / {cfg['full_at']!r}, 1.0)",
        f"    eased = t ** {ease!r}",
        f"    new_pp = pp * ({cfg['base_min']!r} + ({cfg['base_max']!r} - {cfg['base_min']!r}) * eased)",
        '    mods = ctx.get("mods") or 0',
    ]
    for rule in cfg['mod_rules']:
        factor = rule['pct'] / 100.0
        scale = 'eased' if rule['scale'] == 'ramp' else '1.0'
        L.append(f"    if mods & {rule['bit']}:  # {rule['label']} {rule['pct']:+g}%"
                 f"{' (scales with play value)' if rule['scale'] == 'ramp' else ''}")
        L.append(f"        new_pp *= 1.0 + {factor!r} * {scale}")
    if cfg['stream_buff'] or cfg['aim_nerf']:
        L += [
            '    pp_aim = ctx.get("pp_aim")',
            '    pp_speed = ctx.get("pp_speed")',
            '    if pp_aim is not None and pp_speed is not None and (pp_aim + pp_speed) > 0:',
            '        share = pp_speed / (pp_aim + pp_speed)',
            '        skew = max(-1.0, min(1.0, (share - 0.40) / 0.25))',
            '        if skew > 0:',
            f"            new_pp *= 1.0 + {cfg['stream_buff'] / 100.0!r} * skew",
            '        else:',
            f"            new_pp *= 1.0 + {cfg['aim_nerf'] / 100.0!r} * skew",
        ]
    L += [
        '    if (mods & 128) or ctx.get("mode") in (4, 5, 6):  # relax',
        f"        new_pp *= {cfg['relax_factor']!r}",
    ]
    if cfg.get('cap'):
        L.append(f"    return min(new_pp, {cfg['cap']!r})")
    else:
        L.append('    return new_pp')
    L += [
        '',
        '',
        '# server behavior flags (preserved across builder saves)',
        f"RELAX_PLAYS_AS_VANILLA = {flags.get('rx_vanilla', False)}",
        f"MERGE_RELAX_INTO_VANILLA = {flags.get('merge', False)}",
        PP_PLUMBING,
    ]
    return '\n'.join(L)


@ops_admin.route('/pp')
async def pp_builder():
    fail = _guard()
    if fail:
        return await flash('error', 'You have insufficient privileges.', fail)

    p = _pp_paths()
    cfg = dict(PP_DEFAULTS)
    handwritten = False
    if p['profile'].exists():
        src = p['profile'].read_text()
        m = re.search(r'^# OPS_PP_BUILDER: (.+)$', src, re.M)
        if m:
            try:
                cfg.update(json.loads(m.group(1)))
            except Exception:
                handwritten = True
        else:
            handwritten = True

    log_tail = ''
    if p['log'].exists():
        try:
            log_tail = '\n'.join(p['log'].read_text().splitlines()[-8:])
        except Exception:
            pass

    return await render_template(
        'admin/pp.html', cfg=cfg, mod_choices=MOD_CHOICES,
        handwritten=handwritten, log_tail=log_tail,
        msg=request.args.get('msg'), st=request.args.get('st', 'success'))


@ops_admin.route('/pp/save', methods=['POST'])
async def pp_save():
    fail = _guard()
    if fail:
        return await flash('error', 'You have insufficient privileges.', fail)

    form = await request.form

    def fnum(name, default, lo, hi):
        try:
            val = float(form.get(name, default))
        except (TypeError, ValueError):
            val = default
        return max(lo, min(hi, val))

    cfg = {
        'base_min': fnum('base_min', 1.0, 0.1, 100.0),
        'base_max': fnum('base_max', 1.0, 0.1, 100.0),
        'full_at': fnum('full_at', 500.0, 1.0, 100000.0),
        'curve': form.get('curve') if form.get('curve') in CURVE_EASE else 'front',
        'stream_buff': fnum('stream_buff', 0.0, 0.0, 500.0),
        'aim_nerf': fnum('aim_nerf', 0.0, 0.0, 90.0),
        'cap': fnum('cap', 0.0, 0.0, 10.0 ** 9) or None,
        'relax_factor': fnum('relax_factor', 1.0, 0.0, 5.0),
        'mod_rules': [],
    }
    cfg['base_max'] = max(cfg['base_max'], cfg['base_min'])

    labels = dict(MOD_CHOICES)
    seen_bits = set()
    for bit, pct, scale in zip(form.getlist('mod_bit'),
                               form.getlist('mod_pct'),
                               form.getlist('mod_scale')):
        if not bit:
            continue
        try:
            bit, pct = int(bit), float(pct)
        except ValueError:
            continue
        if bit not in labels or bit in seen_bits or pct == 0:
            continue
        seen_bits.add(bit)
        cfg['mod_rules'].append({
            'bit': bit, 'pct': max(-90.0, min(500.0, pct)),
            'scale': 'ramp' if scale == 'ramp' else 'flat',
            'label': labels[bit],
        })

    p = _pp_paths()
    old_src = p['profile'].read_text() if p['profile'].exists() else ''
    flags = {
        'rx_vanilla': 'RELAX_PLAYS_AS_VANILLA = True' in old_src,
        'merge': 'MERGE_RELAX_INTO_VANILLA = True' in old_src,
    }

    source = _generate_profile(cfg, flags)
    try:
        compile(source, 'ops_custom_pp.py', 'exec')
    except SyntaxError as exc:
        return redirect(f'/admin/pp?st=error&msg=generated profile failed to compile: {exc}')

    if old_src:
        (p['profile'].parent / 'ops_custom_pp.py.bak').write_text(old_src)
    p['profile'].write_text(source)

    # background: full recalc, then restart the game server tmux
    with open(p['log'], 'a') as f:
        f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] 🧮 profile saved — "
                'recalc started (takes a few minutes)...\n')
    install, slug = p['install'], p['slug']
    cmd = (f'"{install}/recalc-pp.sh" --quiet >> "{p["log"]}" 2>&1; '
           f'tmux kill-session -t "{slug}" 2>/dev/null; '
           f'tmux new -d -s "{slug}" "{install}/start_server.sh"; '
           f'echo "[$(date "+%Y-%m-%d %H:%M:%S")] ✅ recalc done + game server '
           f'restarted — new pp is live" >> "{p["log"]}"')
    subprocess.Popen(['bash', '-c', cmd], start_new_session=True)

    return redirect('/admin/pp?msg=saved! recalcing all scores + restarting the '
                    'server in the background — refresh this page to watch the log')


# ─── stats ───────────────────────────────────────────────────────────────────

@ops_admin.route('/stats')
async def stats():
    fail = _guard()
    if fail:
        return await flash('error', 'You have insufficient privileges.', fail)

    async def one(query, args=None):
        row = await glob.db.fetch(query, args or [])
        return (list(row.values())[0] or 0) if row else 0

    now = int(time.time())
    tiles = {
        'users': await one('SELECT COUNT(*) c FROM users'),
        'online': await one(
            'SELECT COUNT(*) c FROM users WHERE latest_activity > %s', [now - 300]),
        'banned': await one('SELECT COUNT(*) c FROM users WHERE NOT priv & 1'),
        'scores_all': await one('SELECT COUNT(*) c FROM scores'),
        'scores_24h': await one(
            'SELECT COUNT(*) c FROM scores WHERE play_time > NOW() - INTERVAL 1 DAY'),
        'scores_7d': await one(
            'SELECT COUNT(*) c FROM scores WHERE play_time > NOW() - INTERVAL 7 DAY'),
        'plays_total': await one('SELECT COALESCE(SUM(plays), 0) c FROM stats'),
        'maps_total': await one('SELECT COUNT(*) c FROM maps'),
        'maps_ranked': await one('SELECT COUNT(*) c FROM maps WHERE status IN (2, 3)'),
        'maps_loved': await one('SELECT COUNT(*) c FROM maps WHERE status = 5'),
        'pp_economy': int(await one(
            'SELECT COALESCE(SUM(pp), 0) c FROM scores WHERE status = 2')),
        'pp_24h': int(await one(
            'SELECT COALESCE(SUM(pp), 0) c FROM scores '
            'WHERE play_time > NOW() - INTERVAL 1 DAY')),
        'pp_7d': int(await one(
            'SELECT COALESCE(SUM(pp), 0) c FROM scores '
            'WHERE play_time > NOW() - INTERVAL 7 DAY')),
    }

    top_scores = await glob.db.fetchall(
        'SELECT s.pp, s.acc, s.mods, u.name uname, u.id uid, '
        'm.artist, m.title, m.version '
        'FROM scores s JOIN users u ON u.id = s.userid '
        'JOIN maps m ON m.md5 = s.map_md5 '
        'WHERE s.status = 2 AND u.priv & 1 ORDER BY s.pp DESC LIMIT 5')

    top_maps = await glob.db.fetchall(
        'SELECT m.id, m.artist, m.title, m.version, COUNT(*) plays '
        'FROM scores s JOIN maps m ON m.md5 = s.map_md5 '
        'GROUP BY s.map_md5 ORDER BY plays DESC LIMIT 5')

    return await render_template(
        'admin/stats.html', t=tiles,
        top_scores=top_scores or [], top_maps=top_maps or [])
