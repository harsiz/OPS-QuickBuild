# Auraelia changelog 😎

the running history of [Auraelia](https://justharsiz.online) (`-devserver justharsiz.online`) — pp reworks, site updates, everything.

> using OPS_QuickBuild for your own server? this folder is Auraelia-specific, feel free to ignore or delete it.

---

## 2026-07-04 — PP builder v3: visual flow editor 🧩

/admin/pp is now a full drag-and-drop, block-based pp programming tool:

- **visual flow blocks** you stack and reorder: multiply / set / add pp,
  if-else conditions (nestable), set-variable, cap, floor, softcap,
  comments — runs top to bottom, live "readable flow" preview beside it
- **a real formula language** in every block: variables (`pp`, `acc`,
  `stars`, `nmiss`, `combo`, `speed_share`, `is_dt`, `is_relax`, …),
  functions (`ramp`, `clamp`, `lerp`, `iif`, `min/max`, `has_mod`, …),
  full arithmetic + comparisons + `and/or/not`. safely parsed and
  compiled to Python — no `eval`, injection-proof
- **Compile** button: validates + shows the exact generated Python;
  **Save and Update** recalcs every score and restarts the server
- **13 searchable presets**: Bancho Classic, Auraelia v3, DT Priority,
  Farm Heaven, Stream Dream, Tryhard, Accuracy Meta, Anti-Farm,
  High Ceiling, Nomod Andy, and more — one click to load & tweak
- admin panel UI cleaned up across the board (tabs, tables, dashboard)

## 2026-07-04 — PP builder v2: full tinker mode + presets ✨

/admin/pp got a massive upgrade:

- **6 one-click presets**: bancho classic, auraelia v3, DT priority,
  farm heaven, stream dream, and tryhard (competitive) — fills the whole
  form, save when happy
- **accuracy rules**: high-acc bonus (ramping to 100%), low-acc penalty
  — works identically live and on recalc (accuracy is now computed from
  hit counts exactly like osu! does)
- **miss punishment**: -% per miss with a cap
- **star rating rules**: bonus per star above a threshold (capped) and a
  low-star farm nerf
- **softcap option**: diminishing returns past the cap instead of a wall,
  with adjustable harshness
- **fine-tuning**: custom curve exponent, stream/aim neutral point and
  sensitivity, final global multiplier, more mods (NF, SD, SO)

## 2026-07-04 — no-code PP builder + server stats 🧮📊

- **/admin/pp — the PP calculation builder**: change the entire pp system
  from the website, zero code. base multiplier range + curve shape, per-mod
  bonuses/nerfs (flat or scaling with play value), stream buff / aim nerf,
  pp cap, relax multiplier. hitting **Save and Update** generates the
  profile, backs up the old one, recalculates every score and restarts the
  game server automatically (progress log shown on the page)
- **/admin/stats**: live server stats — players/online/banned, scores
  submitted (all-time/7d/24h), total plays, maps in db/ranked/loved,
  **pp economy** (sum of all active bests), pp awarded in the last 24h/7d,
  highest pp plays, most played maps

## 2026-07-04 — admin panel powers 🛠️

the website admin panel does real things now (staff accounts only):

- **/admin/users**: search players, ban/unban (leaderboards update
  instantly), silence with custom duration, rename, full score wipes,
  supporter and nominator toggles
- **/admin/maps**: rank / love / unrank any beatmap from the browser —
  whole mapset or single difficulty, statuses frozen so they stick
- footer github link now points to the server's toolkit repo

## 2026-07-04 — pp v3.2: "one leaderboard, for real this time" 🤝⚡

full recalc + score migration.

- relax is now **natively vanilla**: relax plays are stored as vanilla
  scores with RX as a regular mod (like HD/HR), so the main leaderboard,
  player totals and **map leaderboards** all update **live** — no more
  periodic merge, no more separate relax board
- relax pp reduction (65%, keeps 35%) unchanged from v3.1
- existing relax scores migrated into the vanilla modes with
  personal-best dedup (best score per map wins, mods shown honestly)

## 2026-07-04 — pp v3.1: "one leaderboard" 🤝

full recalc.

- **relax now counts on the main vanilla leaderboard** — everyone ranks
  together on one board
- the price of admission: **relax pp is reduced by 65%** (relax keeps 35%
  of its vanilla-equivalent value)
- the separate relax leaderboard still exists and is unaffected
- everything else from v3 unchanged

## 2026-07-04 — pp system v3: "the rhythm update" 🥁

full recalc. the meta is streams now.

- **base multiplier: 1.5x → 3.5x**, ramping with raw play value (maxes at 500 raw pp). the curve is front-loaded — a mid play already sits around the ~70% mark (~2.9x), and the last stretch to 3.5x is reserved for genuinely cracked plays
- **stream/burst maps buffed HARD**: the calculator's own aim/speed breakdown decides what kind of map it is. speed-dominant plays earn up to **+50%** extra
- **aim maps slightly nerfed**: aim-dominant plays lose up to **-15%**
- **EZ is finally respected 🙏**: up to **+45%** more pp with EZ, scaling with play value (it is NOT easy)
- **DT priority removed** (v2's headline feature, rip)
- still in effect: relax = 0.75x of vanilla-equivalent value, hard cap 4000pp per play

## 2026-07-04 — pp system v2: "DT priority"

full recalc.

- base multiplier reworked to **3.5x → 5x**, ramping with raw play value
- **DT/NC bonus: +25% up to +75%**, scaling with the same ramp
- relax 0.75x and the 4000pp cap carried over from v1

## 2026-07-04 — pp system v1: "the big bang" 💥

first custom pp economy, applied via full recalc.

- every score worth **4x-7x** bancho pp (ramping with raw play value, full 7x at 500+ raw pp)
- **relax nerf**: 0.75x of the vanilla-equivalent value
- hard cap: **4000pp** per play

## 2026-07-04 — website launched 🌐

- frontend live at https://justharsiz.online (profiles, leaderboards, settings, admin panel)
- avatar uploads via site settings; avatars served on `a.justharsiz.online`
- custom theme (purple hue), custom mascot + favicon
- fixed: cross-origin api headers so profile stats actually render

## 2026-07-04 — server launched 🚀

- Auraelia is live: osu!stable via `-devserver justharsiz.online`
- bancho.py + MariaDB + Redis + nginx, wildcard Let's Encrypt SSL
- accounts imported, rankings + leaderboards operational
- built with OPS_QuickBuild v1.0.0
