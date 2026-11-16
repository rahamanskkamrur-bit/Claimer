import asyncio, os, random, time, aiohttp
from aiohttp import web
import discord
from discord.ext import commands

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip().strip('"').strip("'")
ALERT_CH_ID = int(os.getenv("ALERT_CHANNEL_ID", "1568991830245253263"))
START_TIME, BOT_STATUS = time.time(), "Starting..."

# Valid Roblox Group ID ranges (skips the 17.3M - 32M dead zone where 0 groups exist!)
VALID_RANGES = [(100000, 17300000), (32000000, 35900000)]

SCAN = {
    "run": True, "min_roles": 1, "ch": ALERT_CH_ID,
    "checked": 0, "unowned": 0, "claimable": 0, "closed": 0, "locked": 0, "rl": 0,
    "last_group": "None yet",
}
QUEUE = asyncio.Queue(maxsize=120)
SEEN = set()
HITS = []

async def run_web():
    app = web.Application()
    app.router.add_get("/", lambda r: web.Response(text=f"Scanner 24/7 | Checked: {SCAN['checked']} | Unowned: {SCAN['unowned']} | Claimable: {SCAN['claimable']} | Status: {BOT_STATUS}"))
    runner = web.AppRunner(app); await runner.setup()
    try: await web.TCPSite(runner, "0.0.0.0", int(os.environ.get("PORT", 10000))).start()
    except Exception: pass

async def pinger():
    await asyncio.sleep(20)
    url = os.environ.get("RENDER_EXTERNAL_URL") or f"http://127.0.0.1:{os.environ.get('PORT', 10000)}/"
    async with aiohttp.ClientSession() as s:
        while True:
            try: await s.get(url, timeout=10)
            except Exception: pass
            await asyncio.sleep(240)

def pick_smart_batch() -> int:
    lo, hi = random.choices(VALID_RANGES, weights=[75, 25], k=1)[0]
    return random.randint(lo // 100, hi // 100) * 100

async def inspect_group(s: aiohttp.ClientSession, gid: int) -> dict | None:
    try:
        async with s.get(f"https://groups.roblox.com/v1/groups/{gid}", timeout=10) as r:
            if r.status == 429:
                SCAN["rl"] += 1
                return {"rl": True}
            if r.status != 200: return None
            d = await r.json()
    except Exception: return None

    no_owner = d.get("owner") is None
    is_open = d.get("publicEntryAllowed") is True
    is_locked = bool(d.get("isLocked", False))
    claimable = no_owner and is_open and not is_locked
    name = d.get("name", f"Group {gid}")
    SCAN["last_group"] = f"{name[:20]} (#{gid})"

    # CRITICAL FIX: Only fetch /roles if the group is ACTUALLY claimable!
    # (Cuts API calls in half and prevents constant HTTP 429 rate-limits)
    roles = []
    if claimable:
        try:
            async with s.get(f"https://groups.roblox.com/v1/groups/{gid}/roles", timeout=10) as rr:
                if rr.status == 200:
                    rd = await rr.json()
                    roles = [x.get("name", "Role") for x in rd.get("roles", []) if x.get("rank", 0) > 0]
        except Exception: pass

    rc = len(roles) or 3
    return {
        "id": gid, "name": name, "members": d.get("memberCount", 0),
        "no_owner": no_owner, "open": is_open, "locked": is_locked, "claimable": claimable,
        "role_count": rc, "roles": roles, "robux": max(0, (rc - 3) * 25),
        "url": f"https://www.roblox.com/groups/{gid}",
    }

async def post_claimable_alert(bot: commands.Bot, info: dict):
    HITS.append(info)
    ch = bot.get_channel(SCAN["ch"])
    if not ch:
        try: ch = await bot.fetch_channel(SCAN["ch"])
        except Exception: return
    r_list = ", ".join(info["roles"][:12]) or "Default Roles"
    tag = "💎 **HIGH-ROLE JACKPOT!**" if info["role_count"] >= 6 else "🔓 **CLAIMABLE GROUP FOUND!**"
    emb = discord.Embed(
        title=f"🎉 {info['name']} ({info['role_count']} Roles!)",
        description=(
            f"🆔 **ID:** `{info['id']}`  |  👥 **Members:** `{info['members']:,}`\n"
            f"👑 **Owner:** `None`  |  🚪 **Status:** `Open & Unlocked`\n"
            f"🎭 **Roles ({info['role_count']}):** `{r_list}`\n"
            f"💰 **Est. Role Value:** `~{info['robux']} Robux saved`\n\n"
            f"👉 **[CLICK HERE TO JOIN & CLAIM GROUP]({info['url']})**"
        ),
        color=discord.Color.gold() if info["role_count"] >= 6 else discord.Color.green(),
    )
    v = discord.ui.View()
    v.add_item(discord.ui.Button(label=f"Claim Group ({info['role_count']} Roles)", style=discord.ButtonStyle.link, url=info["url"], emoji="👑"))
    try: await ch.send(content=f"@here {tag}", embed=emb, view=v)
    except Exception: pass

async def v2_scanner():
    async with aiohttp.ClientSession() as s:
        while True:
            if not SCAN["run"] or QUEUE.qsize() >= 80:
                await asyncio.sleep(1.0); continue
            base = pick_smart_batch()
            if base in SEEN:
                await asyncio.sleep(0.1); continue
            SEEN.add(base)
            if len(SEEN) > 5000: SEEN.clear()
            ids = ",".join(str(i) for i in range(base, base + 100))
            try:
                async with s.get(f"https://groups.roblox.com/v2/groups?groupIds={ids}", timeout=10) as r:
                    if r.status == 200:
                        SCAN["checked"] += 100
                        for g in (await r.json()).get("data", []):
                            if g.get("owner") is None and g.get("id"):
                                SCAN["unowned"] += 1
                                if not QUEUE.full():
                                    await QUEUE.put(g["id"])
                    elif r.status == 429:
                        SCAN["rl"] += 1; await asyncio.sleep(3)
            except Exception: pass
            await asyncio.sleep(0.5)

async def v1_verifier(bot: commands.Bot):
    await bot.wait_until_ready()
    async with aiohttp.ClientSession() as s:
        while not bot.is_closed():
            if not SCAN["run"]: await asyncio.sleep(1.0); continue
            try: gid = await asyncio.wait_for(QUEUE.get(), timeout=2.0)
            except asyncio.TimeoutError: continue
            info = await inspect_group(s, gid)
            if info and info.get("rl"):
                await QUEUE.put(gid); QUEUE.task_done(); await asyncio.sleep(3.5); continue
            QUEUE.task_done()
            if not info or not info["no_owner"]:
                await asyncio.sleep(0.4); continue

            if info["claimable"]:
                SCAN["claimable"] += 1
                if info["role_count"] >= SCAN["min_roles"]:
                    await post_claimable_alert(bot, info)
            elif info["locked"]: SCAN["locked"] += 1
            else: SCAN["closed"] += 1
            await asyncio.sleep(0.45)

def dash_embed() -> discord.Embed:
    st = "🟢 **TURBO SCANNING (DEAD-ZONE SKIPPED)**" if SCAN["run"] else "⏸️ **PAUSED**"
    emb = discord.Embed(
        title="👑 Roblox Claimable Group & Role Scanner v2",
        description=(
            f"📡 **Status:** {st}\n"
            f"🎭 **Min Roles Filter:** `{SCAN['min_roles']}+ roles`  |  📍 **Alert Ch:** <#{SCAN['ch']}>\n"
            f"🎯 **Smart Ranges:** `100K–17.3M` & `32M–35.9M` *(Skips 17.3M–32M Empty Gap)*\n"
            f"📊 **Scanned:** `{SCAN['checked']:,}` | **Unowned Found:** `{SCAN['unowned']:,}` | **Queue:** `{QUEUE.qsize()}`\n"
            f"🔓 **Claimable:** **`{SCAN['claimable']}`** | 🔒 **Closed:** `{SCAN['closed']:,}` | 🚫 **Locked:** `{SCAN['locked']:,}`\n"
            f"🔎 **Last Inspected:** `{SCAN['last_group']}` *(429 Cooldowns: `{SCAN['rl']}`)*"
        ),
        color=discord.Color.green() if SCAN["run"] else discord.Color.gold(),
    )
    if HITS:
        top = sorted(HITS, key=lambda x: x["role_count"], reverse=True)[:6]
        lines = [f"• **[{g['name'][:24]}]({g['url']})** — **`{g['role_count']} Roles`** (`{g['robux']} R$` • `{g['members']} 👥`)" for g in top]
        emb.add_field(name="🏆 Claimable Groups Found", value="\n".join(lines), inline=False)
    return emb

class FilterModal(discord.ui.Modal, title="🎭 Set Minimum Roles Filter"):
    min_r = discord.ui.TextInput(label="Min Roles (Set to 1 to catch EVERY open group)", default=str(SCAN["min_roles"]), required=True)
    async def on_submit(self, i: discord.Interaction):
        try: SCAN["min_roles"] = max(1, int(self.min_r.value.strip()))
        except Exception: pass
        await i.response.edit_message(embed=dash_embed(), view=ScanView())

class ScanView(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)
    @discord.ui.button(label="Refresh Stats", style=discord.ButtonStyle.primary, emoji="🔄", custom_id="rs:ref")
    async def ref_b(self, i: discord.Interaction, b: discord.ui.Button):
        await i.response.edit_message(embed=dash_embed(), view=self)

    @discord.ui.button(label="Pause / Resume", style=discord.ButtonStyle.success, emoji="⏯️", custom_id="rs:tog")
    async def tog_b(self, i: discord.Interaction, b: discord.ui.Button):
        SCAN["run"] = not SCAN["run"]; SCAN["ch"] = i.channel.id
        await i.response.edit_message(embed=dash_embed(), view=self)

    @discord.ui.button(label="Role Filter", style=discord.ButtonStyle.secondary, emoji="🎭", custom_id="rs:flt")
    async def flt_b(self, i: discord.Interaction, b: discord.ui.Button):
        await i.response.send_modal(FilterModal())

    @discord.ui.button(label="Set Alert Channel Here", style=discord.ButtonStyle.secondary, emoji="🔔", custom_id="rs:test")
    async def test_b(self, i: discord.Interaction, b: discord.ui.Button):
        SCAN["ch"] = i.channel.id
        await i.response.edit_message(embed=dash_embed(), view=self)
        await i.followup.send(f"✅ Alert channel locked to {i.channel.mention}! All claimable groups will ping here.", ephemeral=True)

class ScannerBot(commands.Bot):
    def __init__(self, msg_intent: bool = True):
        intents = discord.Intents.default(); intents.message_content = msg_intent
        super().__init__(command_prefix="!", intents=intents, help_command=None)
    async def setup_hook(self):
        self.add_view(ScanView()); await self.tree.sync()
    async def on_ready(self):
        global BOT_STATUS; BOT_STATUS = f"ONLINE as {self.user}"
        print(f"✅ Group Scanner v2 ONLINE as {self.user}")

def reg_cmds(b: ScannerBot):
    @b.tree.command(name="scanner", description="Open the 24/7 Roblox Claimable Group & Role Scanner")
    async def s_scan(i: discord.Interaction):
        SCAN["ch"] = i.channel.id
        await i.response.send_message(embed=dash_embed(), view=ScanView())

    @b.tree.command(name="keyword_scan", description="Search Roblox groups by keyword (e.g. Police, SWAT, Studio) for claimable groups")
    async def s_kw(i: discord.Interaction, keyword: str):
        await i.response.defer()
        found = []
        async with aiohttp.ClientSession() as s:
            try:
                async with s.get(f"https://groups.roblox.com/v1/groups/search?keyword={keyword}&limit=25", timeout=10) as r:
                    data = (await r.json()).get("data", []) if r.status == 200 else []
            except Exception: data = []
            for g in data:
                gid = g.get("id")
                if gid:
                    info = await inspect_group(s, gid)
                    if info and info.get("claimable"):
                        found.append(info)
                        await post_claimable_alert(b, info)
                    await asyncio.sleep(0.3)
        if found:
            await i.followup.send(f"🎉 Found **`{len(found)}`** claimable group(s) for keyword **`{keyword}`**! Posted above.")
        else:
            await i.followup.send(f"🔎 Checked top `{len(data)}` groups for **`{keyword}`** — none were both unowned & open right now.")

    @b.tree.command(name="checkgroup", description="Inspect any Roblox Group ID for Owner, Open Status, and Roles")
    async def s_chk(i: discord.Interaction, group_id: int):
        await i.response.defer()
        async with aiohttp.ClientSession() as s:
            info = await inspect_group(s, group_id)
        if not info or info.get("rl"): return await i.followup.send("⚠️ Could not fetch group (or rate limited).")
        st = "🔓 **CLAIMABLE NOW!**" if info["claimable"] else ("🔒 Closed/Locked" if info["no_owner"] else "👤 Already Owned")
        emb = discord.Embed(title=f"🔍 {info['name']} (#{info['id']})", description=f"📌 **Status:** {st}\n👥 **Members:** `{info['members']:,}`\n🎭 **Roles ({info['role_count']}):** `{', '.join(info['roles'][:12]) or 'Default'}`\n🔗 **[Open on Roblox]({info['url']})**", color=discord.Color.green() if info["claimable"] else discord.Color.orange())
        await i.followup.send(embed=emb)

async def main():
    global BOT_STATUS
    await run_web(); asyncio.create_task(pinger())
    if not BOT_TOKEN:
        BOT_STATUS = "MISSING BOT_TOKEN"
        while True: await asyncio.sleep(3600)
    bot = ScannerBot(True); reg_cmds(bot)
    asyncio.create_task(v2_scanner())
    # Run 2 parallel verifier workers for 2x faster queue processing
    asyncio.create_task(v1_verifier(bot)); asyncio.create_task(v1_verifier(bot))
    try: await bot.start(BOT_TOKEN)
    except discord.PrivilegedIntentsRequired:
        await bot.close(); bot = ScannerBot(False); reg_cmds(bot)
        asyncio.create_task(v1_verifier(bot)); await bot.start(BOT_TOKEN)

if __name__ == "__main__":
    asyncio.run(main())
    
