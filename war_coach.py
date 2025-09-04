# war_coach.py  — FULL PRODUCTION VERSION
# Usage:
#   export DISCORD_TOKEN="YOUR_TOKEN"
#   python3 war_coach.py
# First bind channel in Discord:  type  ->  !here
#
# AM report (by 07:30):  !am distance=8.2 steps=12345 kcal=640
# PM report (by 22:00):  !pm wake=05:30 strength=Y calories=1700 protein=195 steps=15200 sleep=8 indulgence=N discipline=9
# See punishments:       !punish
# See today’s status:    !status
# Adjust targets:        !set cal=1800 protein=190 steps=12000 cardio=600 sleep=7.5 discipline=8

import os, json, re, datetime, asyncio
import discord
from discord.ext import commands, tasks

# ================== CONFIG (defaults; can be changed with !set) ==================
DEFAULTS = {
    "CAL_TARGET": 1800,       # max kcals/day
    "PROTEIN_TARGET": 190,    # g/day
    "STEPS_TARGET": 12000,    # steps/day
    "CARDIO_KCAL_TARGET": 600,# morning fasted cardio kcal
    "SLEEP_TARGET": 7.5,      # hours
    "DISCIPLINE_MIN": 8       # 1..10
}
WAKE      = "05:30"
AM_CHECK  = "07:30"
PROTEIN_PINGS = ["09:00","13:00","17:00","21:00"]
MIDDAY    = "13:00"
PM_AUDIT  = "22:00"
PM_GRADE  = "22:10"
SUNDAY_AUDIT_TIME = "21:00"      # Sundays only

STATE_FILE  = "war_state.json"   # daily logs & punishments
CONFIG_FILE = "war_config.json"  # channel id + targets

# ================== ENV ==================
TOKEN = os.environ["DISCORD_TOKEN"]

# ================== UTIL ==================
def today_str():
    return datetime.datetime.now().strftime("%Y-%m-%d")

def now_hhmm():
    return datetime.datetime.now().strftime("%H:%M")

def load_json(path, default):
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                return json.load(f)
        except:
            pass
    return default

def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)

def load_config():
    cfg = load_json(CONFIG_FILE, {})
    # ensure targets exist
    if "targets" not in cfg:
        cfg["targets"] = DEFAULTS.copy()
    else:
        for k,v in DEFAULTS.items():
            cfg["targets"].setdefault(k, v)
    cfg.setdefault("channel_id", 0)
    return cfg

def save_config(cfg):
    save_json(CONFIG_FILE, cfg)

def load_state():
    return load_json(STATE_FILE, {})

def save_state(state):
    save_json(STATE_FILE, state)

def ensure_day(state, day):
    if day not in state:
        state[day] = {
            "am": None,            # {"distance": float, "steps": int, "kcal": float, "ts": "..."}
            "pm": None,            # {"wake": "05:30", "strength":"Y/N", "calories": int, "protein": int, "steps": int, "sleep": float, "indulgence":"Y/N", "discipline": int, "ts": "..."}
            "compliance": None,    # int %
            "punishment_next_day": []
        }
    return state

def parse_kv(text):
    kv = {}
    for part in re.split(r"[,\s]+", text.strip()):
        if "=" in part:
            k, v = part.split("=", 1)
            kv[k.lower()] = v
    return kv

def compute_compliance(day_data, targets):
    """
    7 checks (each 1 point):
      1) Calories <= CAL_TARGET
      2) Protein >= PROTEIN_TARGET
      3) Steps >= STEPS_TARGET
      4) Strength/HIIT = Y
      5) Cardio kcal (AM) >= CARDIO_KCAL_TARGET
      6) Sleep hours >= SLEEP_TARGET
      7) Discipline >= DISCIPLINE_MIN
    """
    am = day_data.get("am") or {}
    pm = day_data.get("pm") or {}

    total = 7
    score = 0

    # 1 calories
    cals = pm.get("calories")
    if cals is not None and cals <= targets["CAL_TARGET"]:
        score += 1

    # 2 protein
    prot = pm.get("protein")
    if prot is not None and prot >= targets["PROTEIN_TARGET"]:
        score += 1

    # 3 steps
    steps = pm.get("steps")
    if steps is not None and steps >= targets["STEPS_TARGET"]:
        score += 1

    # 4 strength
    strength = (pm.get("strength") or "").upper()
    if strength == "Y":
        score += 1

    # 5 morning cardio kcal
    kcal = am.get("kcal")
    if kcal is not None and kcal >= targets["CARDIO_KCAL_TARGET"]:
        score += 1

    # 6 sleep
    sleep = pm.get("sleep")
    if sleep is not None and sleep >= targets["SLEEP_TARGET"]:
        score += 1

    # 7 discipline
    disc = pm.get("discipline", targets["DISCIPLINE_MIN"])
    if disc >= targets["DISCIPLINE_MIN"]:
        score += 1

    pct = round((score/total)*100)
    return pct

def add_punishments(day_data, punish_list):
    existing = set(day_data.get("punishment_next_day", []))
    for p in punish_list:
        existing.add(p)
    day_data["punishment_next_day"] = sorted(list(existing))
    return day_data

def is_sunday():
    # Monday=0 ... Sunday=6
    return datetime.datetime.now().weekday() == 6

# ================== DISCORD ==================
cfg = load_config()
CHANNEL_ID = int(cfg.get("channel_id", 0))
targets = cfg["targets"]

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

async def get_bound_channel():
    global CHANNEL_ID
    if not CHANNEL_ID:
        return None
    ch = bot.get_channel(CHANNEL_ID)
    if ch is None:
        try:
            ch = await bot.fetch_channel(CHANNEL_ID)
        except:
            ch = None
    return ch

@bot.event
async def on_ready():
    print(f"⚔️ War Coach online as {bot.user} (ID: {bot.user.id})")
    print("Guilds I’m in:")
    for g in bot.guilds:
        print(" -", g.name, g.id)
    ch = await get_bound_channel()
    if ch:
        try:
            await ch.send("⚔️ War Coach reporting for duty. Bound channel ready. Use **!am** and **!pm** today. Type **!status** any time.")
            print("✅ Posted to bound channel:", CHANNEL_ID)
        except Exception as e:
            print("❌ Could not post to bound channel:", e)
    else:
        print("⚠️ No channel bound. In your target channel type:  !here")
    scheduler.start()

# ------------------ COMMANDS ------------------
@bot.command()
async def here(ctx):
    """Bind the bot to the current channel (saved)."""
    global CHANNEL_ID, cfg
    CHANNEL_ID = ctx.channel.id
    cfg["channel_id"] = CHANNEL_ID
    save_config(cfg)
    await ctx.send("✅ Bound to this channel. I’ll post here on schedule.")

@bot.command()
async def set(ctx, *, args:str):
    """Adjust targets. Example: !set cal=1800 protein=190 steps=12000 cardio=600 sleep=7.5 discipline=8"""
    global targets, cfg
    kv = parse_kv(args)
    mapping = {
        "cal":"CAL_TARGET", "calories":"CAL_TARGET",
        "protein":"PROTEIN_TARGET",
        "steps":"STEPS_TARGET",
        "cardio":"CARDIO_KCAL_TARGET", "cardiokcal":"CARDIO_KCAL_TARGET",
        "sleep":"SLEEP_TARGET",
        "discipline":"DISCIPLINE_MIN"
    }
    changed = []
    for k,v in kv.items():
        key = mapping.get(k.lower())
        if not key: continue
        try:
            if key in ["SLEEP_TARGET"]:
                targets[key] = float(v)
            else:
                targets[key] = int(float(v))
            changed.append(f"{key}={targets[key]}")
        except:
            pass
    cfg["targets"] = targets
    save_config(cfg)
    if changed:
        await ctx.send("✅ Targets updated: " + ", ".join(changed))
    else:
        await ctx.send("No valid keys found. Try: cal=, protein=, steps=, cardio=, sleep=, discipline=")

@bot.command()
async def am(ctx, *, args:str):
    """Morning cardio report. Example: !am distance=8.2 steps=12034 kcal=640"""
    kv = parse_kv(args)
    try:
        distance = float(kv.get("distance", "0"))
        steps = int(kv.get("steps", "0"))
        kcal = float(kv.get("kcal", "0"))
    except:
        await ctx.send("Format error. Example: `!am distance=8.2 steps=12034 kcal=640`")
        return

    state = load_state()
    day = today_str()
    ensure_day(state, day)
    state[day]["am"] = {
        "distance": distance,
        "steps": steps,
        "kcal": kcal,
        "ts": datetime.datetime.now().isoformat(timespec="seconds")
    }
    save_state(state)

    verdict = "PASS ✅" if (distance >= 8.0 and kcal >= targets["CARDIO_KCAL_TARGET"]) else "CHECK ❗"
    await ctx.send(f"AM logged: {distance:.2f} km, {steps} steps, {kcal:.0f} kcal — {verdict}")

@bot.command()
async def pm(ctx, *, args:str):
    """Night audit report. Example: !pm wake=05:30 strength=Y calories=1700 protein=195 steps=15200 sleep=8 indulgence=N discipline=9"""
    kv = parse_kv(args)
    wake = kv.get("wake", "")
    strength = (kv.get("strength","")).upper()
    try:
        calories = int(kv.get("calories"))
        protein  = int(kv.get("protein"))
        steps    = int(kv.get("steps"))
        sleep    = float(kv.get("sleep"))
    except:
        await ctx.send("Format error. Example: `!pm wake=05:30 strength=Y calories=1700 protein=195 steps=15200 sleep=8 indulgence=N discipline=9`")
        return
    indulgence = (kv.get("indulgence","N")).upper()
    discipline = int(kv.get("discipline", targets["DISCIPLINE_MIN"]))

    state = load_state()
    day = today_str()
    ensure_day(state, day)
    state[day]["pm"] = {
        "wake": wake, "strength": strength, "calories": calories, "protein": protein,
        "steps": steps, "sleep": sleep, "indulgence": indulgence,
        "discipline": discipline, "ts": datetime.datetime.now().isoformat(timespec="seconds")
    }

    comp = compute_compliance(state[day], targets)
    state[day]["compliance"] = comp

    punish_msg = None
    if comp < 80:
        add_punishments(state[day], ["+30 min morning cardio", "24h carb cut"])
        punish_msg = "❌ **FAIL** — Compliance < 80%. Punishment set for tomorrow: +30 min morning cardio + 24h carb cut."

    save_state(state)

    am_kcal = (state[day]["am"] or {}).get("kcal", 0)
    await ctx.send(
        f"PM logged. **Compliance: {comp}%** "
        f"(cal≤{targets['CAL_TARGET']}, prot≥{targets['PROTEIN_TARGET']}, steps≥{targets['STEPS_TARGET']}, strength=Y, "
        f"cardio≥{targets['CARDIO_KCAL_TARGET']}kcal[{am_kcal}], sleep≥{targets['SLEEP_TARGET']}, discipline≥{targets['DISCIPLINE_MIN']}).\n"
        + (punish_msg or "PASS ✅ — No new punishment tonight.")
    )

@bot.command()
async def punish(ctx):
    """Show tomorrow's punishments (based on today's failures)."""
    state = load_state()
    day = today_str()
    ensure_day(state, day)
    punish = state[day].get("punishment_next_day", [])
    if punish:
        await ctx.send("🔴 **Punishments queued for tomorrow:** " + "; ".join(punish))
    else:
        await ctx.send("🟢 No punishments queued for tomorrow. Keep it that way.")

@bot.command()
async def status(ctx):
    """Show today's AM/PM and compliance."""
    state = load_state()
    day = today_str()
    ensure_day(state, day)
    d = state[day]
    lines = [f"📅 **{day}**"]
    if d["am"]:
        lines.append(f"AM: {d['am'].get('distance','?')} km, {d['am'].get('steps','?')} steps, {d['am'].get('kcal','?')} kcal")
    else:
        lines.append("AM: (no report)")
    if d["pm"]:
        pm = d["pm"]
        lines.append(f"PM: wake {pm['wake']}, strength {pm['strength']}, cal {pm['calories']}, prot {pm['protein']}, steps {pm['steps']}, sleep {pm['sleep']}, ind {pm['indulgence']}, disc {pm['discipline']}")
    else:
        lines.append("PM: (no report)")
    if d["compliance"] is not None:
        lines.append(f"Compliance: **{d['compliance']}%**")
    pun = d.get("punishment_next_day", [])
    if pun:
        lines.append("Punishments queued: " + "; ".join(pun))
    await ctx.send("\n".join(lines))

# ================== SCHEDULER ==================
@tasks.loop(minutes=1)
async def scheduler():
    ch = await get_bound_channel()
    if not ch:
        return
    t = now_hhmm()
    state = load_state()
    day = today_str()
    ensure_day(state, day)

    # Wake-up
    if t == WAKE:
        await ch.send("⚔️ **WAKE UP.** Report awake. If cardio not logged by **07:30**, you FAIL → **2 hrs tomorrow + 50% carb cut**.")

    # Protein pings
    if t in PROTEIN_PINGS:
        await ch.send("⚔️ **Protein feed** NOW: ≥40 g. Don’t underfeed or tomorrow burns harder.")

    # Midday strike
    if t == MIDDAY:
        await ch.send("⚔️ **Midday check** — Protein ≥100 g by now? Calories logging started? Steps ≥6k? Report if behind.")

    # Morning deadline
    if t == AM_CHECK:
        am = state[day]["am"]
        kcal_target = targets["CARDIO_KCAL_TARGET"]
        if (not am) or (am.get("distance",0) < 8.0 or am.get("kcal",0) < kcal_target):
            msg = "❌ **FAIL** — "
            if not am:
                msg += "No morning cardio report."
            else:
                msg += f"Morning below standard (distance {am.get('distance','?')} km, kcal {am.get('kcal','?')})."
            add_punishments(state[day], ["Double cardio tomorrow", "50% carb cut for 24h"])
            save_state(state)
            await ch.send(msg + " **Punishment set for tomorrow: Double cardio + 50% carb cut.**")

    # PM audit reminder
    if t == PM_AUDIT:
        await ch.send("⚔️ **Night Audit** time. Post: `!pm wake=HH:MM strength=Y/N calories=#### protein=### steps=##### sleep=# indulgence=Y/N discipline=#`")

    # Auto-grade if missed
    if t == PM_GRADE:
        pm = state[day]["pm"]
        if not pm:
            add_punishments(state[day], ["+30 min morning cardio", "24h carb cut"])
            save_state(state)
            await ch.send("❌ **FAIL** — No Night Audit. **Punishment set for tomorrow: +30 min morning cardio + 24h carb cut.**")

    # Sunday weekly audit reminder
    if t == SUNDAY_AUDIT_TIME and is_sunday():
        await ch.send("📸 **Weekly Audit** (Sunday): Upload front/side/back photos, morning weight, waist, and 7-day averages. If loss < 2 kg/week → **FAIL** → Double cardio Monday + 48h carb cut.")

@bot.command()
async def helpme(ctx):
    """Show how to use War Coach commands."""
    msg = (
        "⚔️ **War Coach Command Guide** ⚔️\n\n"
        "__**Setup**__\n"
        "`!here` → Bind the bot to this channel.\n"
        "`!set cal=1800 protein=190 steps=12000 cardio=600 sleep=7.5 discipline=8` → Adjust targets.\n\n"
        "__**Daily Logs**__\n"
        "`!am distance=8.2 steps=12034 kcal=640` → Morning cardio report (by 07:30).\n"
        "`!pm wake=05:30 strength=Y calories=1700 protein=195 steps=15200 sleep=8 indulgence=N discipline=9` → Night audit (by 22:00).\n\n"
        "__**Checks**__\n"
        "`!status` → Show today’s AM/PM logs, compliance %, punishments.\n"
        "`!punish` → Show punishments queued for tomorrow.\n\n"
        "__**Utility**__\n"
        "`!ping` → Check if War Coach is alive.\n"
        "`!helpme` → Show this help message again.\n\n"
        "⚔️ **Compliance Rule:** If <80% or any field missing → **FAIL** → Punishment = +30 min cardio next morning + 24h carb cut."
    )
    await ctx.send(msg)

# ================== RUN ==================
bot.run(TOKEN)
