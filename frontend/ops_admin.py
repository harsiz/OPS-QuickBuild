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

# variables a formula can read (loaded into the generated modify_pp locals)
PP_VARS = {
    'pp', 'new_pp', 'ranked_pp', 'acc', 'stars', 'combo', 'nmiss', 'miss',
    'mods', 'mode', 'pp_aim', 'pp_speed', 'pp_acc', 'pp_flashlight',
    'speed_share', 'aim_share', 'is_relax', 'is_dt', 'is_hd', 'is_hr',
    'is_ez', 'is_fl',
}
# functions a formula can call: name -> arity (-1 = variadic, >=1)
PP_FUNCS = {
    'min': -1, 'max': -1, 'abs': 1, 'sqrt': 1, 'floor': 1, 'ceil': 1,
    'clamp': 3, 'lerp': 3, 'ramp': 2, 'iif': 3, 'has_mod': 1,
}
_USERVAR_RE = re.compile(r'^[a-z_][a-z0-9_]{0,24}$')
_RESERVED = {'and', 'or', 'not', 'iif', 'true', 'false', 'none'} | set(PP_FUNCS)

# the default flow shown to new/hand-written installs (== the auraelia v3 system)
DEFAULT_FLOW = [
    {'t': 'setvar', 'name': 'pv', 'x': 'ramp(pp, 500)'},
    {'t': 'comment', 'text': 'base multiplier 1.5x -> 3.5x, front-loaded'},
    {'t': 'mul', 'x': '1.5 + 2.0 * (pv ** 0.45)'},
    {'t': 'comment', 'text': 'stream buff / aim nerf from the aim-speed split'},
    {'t': 'if', 'cond': 'speed_share > 0.40',
     'then': [{'t': 'mul', 'x': '1 + 0.5 * clamp((speed_share - 0.40) / 0.25, 0, 1)'}],
     'else': [{'t': 'mul', 'x': '1 - 0.15 * clamp((0.40 - speed_share) / 0.25, 0, 1)'}]},
    {'t': 'if', 'cond': 'is_ez', 'then': [{'t': 'mul', 'x': '1 + 0.45 * pv'}], 'else': []},
    {'t': 'if', 'cond': 'is_relax', 'then': [{'t': 'mul', 'x': '0.35'}], 'else': []},
    {'t': 'clampmax', 'x': '4000'},
]

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
                ctx["combo"] = getattr(sp, "combo", None)
                ctx["nmiss"] = getattr(sp, "nmiss", None)
                acc = getattr(sp, "acc", None)
                ctx["acc"] = acc if acc is not None else _acc_from_counts(sp)
            _apply_one(result, ctx)
    except Exception:
        pass
    return results


def _acc_from_counts(sp):
    """Compute accuracy the same way osu! does, from hit counts."""
    try:
        g = lambda name: getattr(sp, name, 0) or 0
        mode = g("mode")
        n300, n100, n50 = g("n300"), g("n100"), g("n50")
        nmiss, ngeki, nkatu = g("nmiss"), g("ngeki"), g("nkatu")
        if mode == 0:
            total = n300 + n100 + n50 + nmiss
            return 100.0 * (300 * n300 + 100 * n100 + 50 * n50) / (300 * total) if total else None
        if mode == 1:
            total = n300 + n100 + nmiss
            return 100.0 * (n300 + 0.5 * n100) / total if total else None
        if mode == 2:
            total = n300 + n100 + n50 + nkatu + nmiss
            return 100.0 * (n300 + n100 + n50) / total if total else None
        if mode == 3:
            total = n300 + n100 + n50 + ngeki + nkatu + nmiss
            return (100.0 * (300 * (n300 + ngeki) + 200 * nkatu + 100 * n100 + 50 * n50)
                    / (300 * total)) if total else None
    except Exception:
        pass
    return None


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
    diff = None
    if isinstance(result, dict):
        perf = result.get("performance", result)
        diff = result.get("difficulty")
    else:
        perf = getattr(result, "performance", result)
        diff = getattr(result, "difficulty", None)

    for key in ("pp_acc", "pp_aim", "pp_speed", "pp_flashlight"):
        val = perf.get(key) if isinstance(perf, dict) else getattr(perf, key, None)
        if isinstance(val, (int, float)):
            ctx[key] = float(val)

    stars = diff.get("stars") if isinstance(diff, dict) else getattr(diff, "stars", None)
    if isinstance(stars, (int, float)):
        ctx["stars"] = float(stars)

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


class PPError(Exception):
    """Raised when a user formula/flow is invalid (shown to the admin)."""


def _tokenize(s: str):
    tokens, i, n = [], 0, len(s)
    ops = ('**', '>=', '<=', '==', '!=', '>', '<', '+', '-', '*', '/', '(', ')', ',')
    while i < n:
        c = s[i]
        if c.isspace():
            i += 1
            continue
        if c.isdigit() or (c == '.' and i + 1 < n and s[i + 1].isdigit()):
            j = i
            while j < n and (s[j].isdigit() or s[j] == '.'):
                j += 1
            tokens.append(('num', s[i:j]))
            i = j
            continue
        if c.isalpha() or c == '_':
            j = i
            while j < n and (s[j].isalnum() or s[j] == '_'):
                j += 1
            word = s[i:j]
            tokens.append(('kw', word) if word in ('and', 'or', 'not') else ('name', word))
            i = j
            continue
        for op in ops:
            if s.startswith(op, i):
                tokens.append(('op', op))
                i += len(op)
                break
        else:
            raise PPError(f"unexpected character {c!r}")
    tokens.append(('end', ''))
    return tokens


def _emit_call(name: str, a: list) -> str:
    if name in ('min', 'max'):
        return f"{name}({', '.join(a)})"
    if name == 'abs':
        return f"abs({a[0]})"
    if name == 'sqrt':
        return f"math.sqrt(max({a[0]}, 0.0))"
    if name == 'floor':
        return f"float(math.floor({a[0]}))"
    if name == 'ceil':
        return f"float(math.ceil({a[0]}))"
    if name == 'clamp':
        return f"max(min({a[0]}, {a[2]}), {a[1]})"
    if name == 'lerp':
        return f"({a[0]} + ({a[1]} - {a[0]}) * ({a[2]}))"
    if name == 'ramp':
        return f"min(_sd(max({a[0]}, 0.0), {a[1]}), 1.0)"
    if name == 'iif':
        return f"(({a[1]}) if ({a[0]}) else ({a[2]}))"
    if name == 'has_mod':
        return f"(1 if (mods & int({a[0]})) else 0)"
    raise PPError(f"unhandled function {name!r}")


class _Parser:
    """Recursive-descent parser: formula text -> validated python expression.

    Never eval()s. Only whitelisted variables and functions are emitted, so a
    hostile string can at worst produce a formula that returns a wrong number.
    """

    def __init__(self, text: str, allowed: set):
        self.toks = _tokenize(text)
        self.i = 0
        self.allowed = allowed

    def _peek(self):
        return self.toks[self.i]

    def _next(self):
        t = self.toks[self.i]
        self.i += 1
        return t

    def _eat(self, val):
        if self._next()[1] != val:
            raise PPError(f"expected {val!r}")

    def parse(self) -> str:
        e = self._or()
        if self._peek()[0] != 'end':
            raise PPError(f"unexpected {self._peek()[1]!r}")
        return e

    def _or(self):
        left = self._and()
        while self._peek() == ('kw', 'or'):
            self._next()
            left = f"({left} or {self._and()})"
        return left

    def _and(self):
        left = self._not()
        while self._peek() == ('kw', 'and'):
            self._next()
            left = f"({left} and {self._not()})"
        return left

    def _not(self):
        if self._peek() == ('kw', 'not'):
            self._next()
            return f"(not {self._not()})"
        return self._cmp()

    def _cmp(self):
        left = self._add()
        if self._peek()[0] == 'op' and self._peek()[1] in ('>', '<', '>=', '<=', '==', '!='):
            op = self._next()[1]
            return f"({left} {op} {self._add()})"
        return left

    def _add(self):
        left = self._mul()
        while self._peek()[0] == 'op' and self._peek()[1] in ('+', '-'):
            op = self._next()[1]
            left = f"({left} {op} {self._mul()})"
        return left

    def _mul(self):
        left = self._pow()
        while self._peek()[0] == 'op' and self._peek()[1] in ('*', '/'):
            op = self._next()[1]
            right = self._pow()
            left = f"_sd({left}, {right})" if op == '/' else f"({left} * {right})"
        return left

    def _pow(self):
        left = self._unary()
        if self._peek() == ('op', '**'):
            self._next()
            return f"({left} ** {self._pow()})"
        return left

    def _unary(self):
        if self._peek() == ('op', '-'):
            self._next()
            return f"(-{self._unary()})"
        if self._peek() == ('op', '+'):
            self._next()
            return self._unary()
        return self._atom()

    def _atom(self):
        t = self._next()
        if t[0] == 'num':
            return repr(float(t[1]))
        if t == ('op', '('):
            e = self._or()
            self._eat(')')
            return f"({e})"
        if t[0] == 'name':
            name = t[1]
            if self._peek() == ('op', '('):
                return self._call(name)
            if name in self.allowed:
                return name
            raise PPError(f"unknown variable {name!r}")
        raise PPError(f"unexpected {t[1]!r}")

    def _call(self, name):
        if name not in PP_FUNCS:
            raise PPError(f"unknown function {name!r}()")
        self._eat('(')
        args = []
        if self._peek() != ('op', ')'):
            args.append(self._or())
            while self._peek() == ('op', ','):
                self._next()
                args.append(self._or())
        self._eat(')')
        arity = PP_FUNCS[name]
        if arity == -1 and not args:
            raise PPError(f"{name}() needs at least one argument")
        if arity != -1 and len(args) != arity:
            raise PPError(f"{name}() takes {arity} arguments, got {len(args)}")
        return _emit_call(name, args)


def _cf(text, allowed: set) -> str:
    """Compile one formula field to a python expression string."""
    if text is None or not str(text).strip():
        raise PPError("empty formula")
    return _Parser(str(text), allowed).parse()


def _collect_vars(stmts, acc: set) -> set:
    for st in stmts or []:
        if st.get('t') == 'setvar':
            name = (st.get('name') or '').strip()
            if _USERVAR_RE.match(name):
                acc.add(name)
        elif st.get('t') == 'if':
            _collect_vars(st.get('then'), acc)
            _collect_vars(st.get('else'), acc)
    return acc


def _compile_stmts(stmts, allowed: set, indent: int) -> str:
    pad = '    ' * indent
    lines = []
    for st in stmts or []:
        t = st.get('t')
        if t == 'comment':
            lines.append(f"{pad}# {str(st.get('text', ''))[:120].splitlines()[0] if st.get('text') else ''}")
        elif t == 'mul':
            lines.append(f"{pad}new_pp = new_pp * ({_cf(st.get('x'), allowed)})")
        elif t == 'add':
            lines.append(f"{pad}new_pp = new_pp + ({_cf(st.get('x'), allowed)})")
        elif t == 'set':
            lines.append(f"{pad}new_pp = ({_cf(st.get('x'), allowed)})")
        elif t == 'setvar':
            name = (st.get('name') or '').strip()
            if not _USERVAR_RE.match(name) or name.lower() in _RESERVED or name in PP_VARS:
                raise PPError(f"invalid variable name {name!r}")
            lines.append(f"{pad}{name} = ({_cf(st.get('x'), allowed)})")
        elif t == 'clampmax':
            lines.append(f"{pad}new_pp = min(new_pp, ({_cf(st.get('x'), allowed)}))")
        elif t == 'clampmin':
            lines.append(f"{pad}new_pp = max(new_pp, ({_cf(st.get('x'), allowed)}))")
        elif t == 'softcap':
            cap = _cf(st.get('cap'), allowed)
            exp = _cf(st.get('exp') or '0.8', allowed)
            lines.append(f"{pad}_cap = ({cap})")
            lines.append(f"{pad}new_pp = new_pp if new_pp <= _cap else _cap + (new_pp - _cap) ** ({exp})")
        elif t == 'if':
            lines.append(f"{pad}if {_cf(st.get('cond'), allowed)}:")
            body = _compile_stmts(st.get('then'), allowed, indent + 1)
            lines.append(body if body.strip() else f"{pad}    pass")
            if st.get('else'):
                lines.append(f"{pad}else:")
                els = _compile_stmts(st.get('else'), allowed, indent + 1)
                lines.append(els if els.strip() else f"{pad}    pass")
        else:
            raise PPError(f"unknown block type {t!r}")
    return '\n'.join(lines)


def _generate_profile_from_flow(flow, flags: dict) -> str:
    allowed = set(PP_VARS) | _collect_vars(flow, set())
    body = _compile_stmts(flow, allowed, 1)
    preamble = [
        '    if not pp or pp <= 0:',
        '        return 0.0',
        '    new_pp = float(pp)',
        '    ranked_pp = float(pp)',
        '    mods = ctx.get("mods") or 0',
        '    mode = ctx.get("mode") or 0',
        '    acc = _num(ctx.get("acc"), 100.0)',
        '    stars = _num(ctx.get("stars"), 0.0)',
        '    combo = _num(ctx.get("combo"), 0.0)',
        '    nmiss = _num(ctx.get("nmiss"), 0.0)',
        '    miss = nmiss',
        '    pp_aim = _num(ctx.get("pp_aim"), 0.0)',
        '    pp_speed = _num(ctx.get("pp_speed"), 0.0)',
        '    pp_acc = _num(ctx.get("pp_acc"), 0.0)',
        '    pp_flashlight = _num(ctx.get("pp_flashlight"), 0.0)',
        '    speed_share = _sd(pp_speed, pp_aim + pp_speed)',
        '    aim_share = 1.0 - speed_share',
        '    is_relax = 1 if ((mods & 128) or mode in (4, 5, 6)) else 0',
        '    is_dt = 1 if (mods & 64) else 0',
        '    is_hd = 1 if (mods & 8) else 0',
        '    is_hr = 1 if (mods & 16) else 0',
        '    is_ez = 1 if (mods & 2) else 0',
        '    is_fl = 1 if (mods & 1024) else 0',
    ]
    tail = [
        '    if new_pp != new_pp or new_pp < 0:  # NaN or negative guard',
        '        return 0.0',
        '    return float(new_pp)',
    ]
    return '\n'.join([
        '"""pp profile — generated by the OPS admin PP flow builder 😎',
        '',
        'edit visually at /admin/pp on the website. hand edits here get',
        'overwritten by the next builder save (a .bak of the old file is kept).',
        '"""',
        '# OPS_PP_FLOW: ' + json.dumps(flow, separators=(',', ':')),
        'from __future__ import annotations',
        '',
        'import math',
        '',
        '',
        'def _num(v, default=0.0):',
        '    try:',
        '        f = float(v)',
        '        return f if f == f else default',
        '    except (TypeError, ValueError):',
        '        return default',
        '',
        '',
        'def _sd(a, b):',
        '    try:',
        '        return a / b if b else 0.0',
        '    except Exception:',
        '        return 0.0',
        '',
        '',
        'def modify_pp(pp: float, ctx: dict) -> float:',
        *preamble,
        body if body.strip() else '    pass',
        *tail,
        '',
        '',
        '# server behavior flags (preserved across builder saves)',
        f"RELAX_PLAYS_AS_VANILLA = {flags.get('rx_vanilla', False)}",
        f"MERGE_RELAX_INTO_VANILLA = {flags.get('merge', False)}",
        PP_PLUMBING,
    ])


def _flow_to_markdown(flow) -> str:
    def walk(stmts, depth):
        out, ind = [], '  ' * depth
        for st in stmts or []:
            t = st.get('t')
            if t == 'comment':
                out.append(f"{ind}- 💬 *{st.get('text', '')}*")
            elif t == 'mul':
                out.append(f"{ind}- ✖️ multiply pp by `{st.get('x', '')}`")
            elif t == 'add':
                out.append(f"{ind}- ➕ add `{st.get('x', '')}` to pp")
            elif t == 'set':
                out.append(f"{ind}- 🟰 set pp = `{st.get('x', '')}`")
            elif t == 'setvar':
                out.append(f"{ind}- 📦 let `{st.get('name', '')}` = `{st.get('x', '')}`")
            elif t == 'clampmax':
                out.append(f"{ind}- 🧢 cap pp at `{st.get('x', '')}` (hard max)")
            elif t == 'clampmin':
                out.append(f"{ind}- ⬆️ floor pp at `{st.get('x', '')}` (min)")
            elif t == 'softcap':
                out.append(f"{ind}- 🪶 softcap past `{st.get('cap', '')}` (exponent `{st.get('exp', '0.8')}`)")
            elif t == 'if':
                out.append(f"{ind}- ❓ **if** `{st.get('cond', '')}`:")
                out += walk(st.get('then'), depth + 1)
                if st.get('else'):
                    out.append(f"{ind}- ↪️ **else**:")
                    out += walk(st.get('else'), depth + 1)
            else:
                out.append(f"{ind}- ⚠️ unknown block `{t}`")
        return out

    lines = walk(flow, 0)
    return '\n'.join(lines) if lines else '_(empty flow — pp passes through unchanged)_'


def _compile_and_check(flow, flags: dict) -> str:
    """Generate the profile, syntax-check it, and smoke-test modify_pp."""
    source = _generate_profile_from_flow(flow, flags)
    try:
        code = compile(source, 'ops_custom_pp.py', 'exec')
    except SyntaxError as exc:
        raise PPError(f"generated code failed to compile: {exc}")
    ns = {}
    try:
        exec(code, ns)
        for sample in ({'mods': 0}, {'mods': 64 + 128, 'acc': 92.0, 'nmiss': 4,
                                     'stars': 6.2, 'pp_aim': 80.0, 'pp_speed': 40.0}):
            out = ns['modify_pp'](200.0, sample)
            float(out)
    except Exception as exc:
        raise PPError(f"profile crashed on a test score: {exc}")
    return source


def _load_flow():
    """Return (flow, handwritten, flags) from the current profile."""
    p = _pp_paths()
    flow, handwritten = list(DEFAULT_FLOW), False
    flags = {'rx_vanilla': False, 'merge': False}
    if p['profile'].exists():
        src = p['profile'].read_text()
        flags['rx_vanilla'] = 'RELAX_PLAYS_AS_VANILLA = True' in src
        flags['merge'] = 'MERGE_RELAX_INTO_VANILLA = True' in src
        m = re.search(r'^# OPS_PP_FLOW: (.+)$', src, re.M)
        if m:
            try:
                flow = json.loads(m.group(1))
            except Exception:
                handwritten = True
        else:
            handwritten = True
    return flow, handwritten, flags


def _flow_from_request(raw):
    """Validate the incoming flow JSON shape (defensive, bounded depth/size)."""
    try:
        flow = json.loads(raw)
    except Exception:
        raise PPError("couldn't read the flow (invalid JSON)")
    if not isinstance(flow, list):
        raise PPError("flow must be a list of blocks")

    def check(stmts, depth):
        if depth > 12:
            raise PPError("blocks nested too deep")
        if len(stmts) > 200:
            raise PPError("too many blocks")
        for st in stmts:
            if not isinstance(st, dict) or 't' not in st:
                raise PPError("malformed block")
            if st['t'] == 'if':
                check(st.get('then') or [], depth + 1)
                check(st.get('else') or [], depth + 1)

    check(flow, 0)
    return flow


@ops_admin.route('/pp')
async def pp_builder():
    fail = _guard()
    if fail:
        return await flash('error', 'You have insufficient privileges.', fail)

    flow, handwritten, _flags = _load_flow()
    p = _pp_paths()
    log_tail = ''
    if p['log'].exists():
        try:
            log_tail = '\n'.join(p['log'].read_text().splitlines()[-8:])
        except Exception:
            pass

    return await render_template(
        'admin/pp.html', flow_json=json.dumps(flow),
        pp_vars=sorted(PP_VARS), pp_funcs=sorted(PP_FUNCS),
        handwritten=handwritten, log_tail=log_tail,
        msg=request.args.get('msg'), st=request.args.get('st', 'success'))


@ops_admin.route('/pp/guide')
async def pp_guide():
    fail = _guard()
    if fail:
        return await flash('error', 'You have insufficient privileges.', fail)
    return await render_template(
        'admin/pp_guide.html',
        pp_vars=sorted(PP_VARS), pp_funcs=sorted(PP_FUNCS))


@ops_admin.route('/pp/compile', methods=['POST'])
async def pp_compile():
    fail = _guard()
    if fail:
        return {'ok': False, 'error': 'insufficient privileges'}, 403

    body = await request.get_json(force=True, silent=True) or {}
    try:
        flow = _flow_from_request(json.dumps(body.get('flow', [])))
        _, _, flags = _load_flow()
        source = _compile_and_check(flow, flags)
    except PPError as exc:
        return {'ok': False, 'error': str(exc)}
    return {'ok': True, 'python': source, 'markdown': _flow_to_markdown(flow)}


@ops_admin.route('/pp/save', methods=['POST'])
async def pp_save():
    fail = _guard()
    if fail:
        return await flash('error', 'You have insufficient privileges.', fail)

    form = await request.form
    p = _pp_paths()
    _, _, flags = _load_flow()
    try:
        flow = _flow_from_request(form.get('flow_json', '[]'))
        source = _compile_and_check(flow, flags)
    except PPError as exc:
        return redirect(f"/admin/pp?st=error&msg=couldn't save: {exc}")

    old_src = p['profile'].read_text() if p['profile'].exists() else ''
    if old_src:
        (p['profile'].parent / 'ops_custom_pp.py.bak').write_text(old_src)
    p['profile'].write_text(source)

    with open(p['log'], 'a') as f:
        f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] 🧮 pp flow saved — "
                'recalc started (takes a few minutes)...\n')
    install, slug = p['install'], p['slug']
    cmd = (f'"{install}/recalc-pp.sh" --quiet >> "{p["log"]}" 2>&1; '
           f'tmux kill-session -t "{slug}" 2>/dev/null; '
           f'tmux new -d -s "{slug}" "{install}/start_server.sh"; '
           f'echo "[$(date "+%Y-%m-%d %H:%M:%S")] ✅ recalc done + game server '
           f'restarted — new pp is live" >> "{p["log"]}"')
    subprocess.Popen(['bash', '-c', cmd], start_new_session=True)

    return redirect('/admin/pp?msg=saved! recalcing all scores + restarting the '
                    'server in the background — refresh to watch the log')


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
