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

import re
import time

from quart import Blueprint
from quart import redirect
from quart import request
from quart import render_template
from quart import session

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
