import asyncio, os, random, time, aiohttp
from aiohttp import web
import discord
from discord.ext import commands

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip().strip('"').strip("'")
ALERT_CH_ID = int(os.getenv("ALERT_CHANNEL_ID", "1568991830245253263"))
START_TIME, BOT_STATUS = time.time(), "Starting..."

SCAN = {
    "run": True, "min_id": 1000, "max_id": 35000000,
    "min_roles": 1, "ch": ALERT_CH_ID,
    "checked": 0, "unowned": 0, "claimable": 0, "closed": 0, "locked": 0,
}
QUEUE = asyncio.Queue()
HITS = []

async def run_web():
    app = web.Application()
    app.router.add_get("/", lambda r: web.Response(text=f"Group Role Scanner 24/7 | Checked: {SCAN['checked']} | Claimable: {SCAN['claimable']} | Status: {BOT_STATUS}"))
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

async def inspect_group(s: aiohttp.ClientSession, gid: int) -> dict | None:
    try:
        async with s.get(f"https://groups.roblox.com/v1/groups/{gid}", timeout=10) as r:
            if r.status == 429: return {"rl": True}
            if r.status != 200: return None
            d = await r.json()
    except Exception: return None

    no_owner = d.get("owner") is None
    is_open = d.get("publicEntryAllowed") is True
    is_locked = bool(d.get("isLocked", False))
    claimable = no_owner and is_open and not is_locked

    roles = []
    if no_owner:
        try:
            async with s.get(f"https://groups.roblox.com/v1/groups/{gid}/roles", timeout=10) as rr:
                if rr.status == 200:
                    rd = await rr.json()
                    roles = [x.get("name", "Role") for x in rd.get("roles", []) if x.get("rank", 0) > 0]
        except Exception: pass

    rc = len(roles) or 3
    saved_robux = max(0, (rc - 3) * 25)
    return {
        "id": gid, "name": d.get("name", f"Group {gid}"), "members": d.get("memberCount", 0),
        "no_owner": no_owner, "open": is_open, "locked": is_locked, "claimable": claimable,
        "role_count": rc, "roles": roles, "robux": saved_robux,
        "url": f"https://www.roblox.com/groups/{gid}",
    }

async def v2_scanner():
    async with aiohttp.ClientSession() as s:
        while True:
            if not SCAN["run"] or QUEUE.qsize() >= 35:
                await asyncio.sleep(1.5); continue
            base = random.randint(SCAN["min_id"] // 100, max(SCAN["min_id"] // 100 + 1, SCAN["max_id"] // 100)) * 100
            ids = ",".join(str(i) for i in range(base, base + 100))
            try:
                async with s.get(f"https://groups.roblox.com/v2/groups?groupIds={ids}", timeout=10) as r:
                    if r.status == 200:
                        SCAN["checked"] += 100
                        for g in (await r.json()).get("data", []):
                            if g.get("owner") is None and g.get("id"):
                                SCAN["unowned"] += 1
                                await QUEUE.put(g["id"])
                    elif r.status == 429: await asyncio.sleep(4)
            except Exception: pass
            await asyncio.sleep(0.8)

async def v1_verifier(bot: commands.Bot):
    await bot.wait_until_ready()
    async with aiohttp.ClientSession() as s:
        while not bot.is_closed():
            if not SCAN["run"]: await asyncio.sleep(1.5); continue
            try: gid = await asyncio.wait_for(QUEUE.get(), timeout=2.0)
            except asyncio.TimeoutError: continue
            info = await inspect_group(s, gid)
            if info and info.get("rl"):
                await QUEUE.put(gid); QUEUE.task_done(); await asyncio.sleep(5); continue
            QUEUE.task_done()
            if not info or not info["no_owner"]:
                await asyncio.sleep(0.75); continue

            if info["claimable"]:
                SCAN["claimable"] += 1
                if info["role_count"] >= SCAN["min_roles"]:
                    HITS.append(info)
                    ch = bot.get_channel(SCAN["ch"])
                    if ch:
                        r_list = ", ".join(info["roles"][:12]) or "Default Roles"
                        tag = "💎 **HIGH-ROLE JACKPOT!**" if info["role_count"] >= 6 else "🔓 **CLAIMABLE GROUP!**"
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
            elif info["locked"]: SCAN["locked"] += 1
            else: SCAN["closed"] += 1
            await asyncio.sleep(0.8)

def dash_embed() -> discord.Embed:
    st = "🟢 **SCANNING 24/7**" if SCAN["run"] else "⏸️ **PAUSED**"
    emb = discord.Embed(
        title="👑 Roblox Open Group & Role Scanner",
        description=(
            f"📡 **Status:** {st}  |  🎭 **Min Roles Filter:** `{SCAN['min_roles']}+ roles`\n"
            f"🎯 **ID Range:** `{SCAN['min_id']:,}` → `{SCAN['max_id']:,}`\n"
            f"📊 **Scanned:** `{SCAN['checked']:,}` | **Unowned:** `{SCAN['unowned']:,}` | **Queue:** `{QUEUE.qsize()}`\n"
            f"🔓 **Open & Claimable:** **`{SCAN['claimable']}`** | 🔒 **Closed:** `{SCAN['closed']}` | 🚫 **Locked:** `{SCAN['locked']}`"
        ),
        color=discord.Color.green() if SCAN["run"] else discord.Color.gold(),
    )
    if HITS:
        top = sorted(HITS, key=lambda x: x["role_count"], reverse=True)[:6]
        lines = [f"• **[{g['name'][:24]}]({g['url']})** — **`{g['role_count']} Roles`** (`{g['robux']} R$` • `{g['members']} 👥`)" for g in top]
        emb.add_field(name="🏆 Top Claimable Groups (Sorted by Most Roles)", value="\n".join(lines), inline=False)
    return emb

class FilterModal(discord.ui.Modal, title="🎭 Set Min Roles & ID Range"):
    min_r = discord.ui.TextInput(label="Minimum Roles Required (1 = All, 5+ = Large)", default=str(SCAN["min_roles"]), required=True)
    min_i = discord.ui.TextInput(label="Min Group ID", default=str(SCAN["min_id"]), required=True)
    max_i = discord.ui.TextInput(label="Max Group ID", default=str(SCAN["max_id"]), required=True)
    async def on_submit(self, i: discord.Interaction):
        try:
            SCAN["min_roles"] = max(1, int(self.min_r.value.strip()))
            SCAN["min_id"] = max(1, int(self.min_i.value.replace(",", "").strip()))
            SCAN["max_id"] = max(SCAN["min_id"] + 100, int(self.max_i.value.replace(",", "").strip()))
        except Exception: pass
        await i.response.edit_message(embed=dash_embed(), view=ScanView())

class ScanView(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)
    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.primary, emoji="🔄", custom_id="rs:ref")
    async def ref_b(self, i: discord.Interaction, b: discord.ui.Button):
        await i.response.edit_message(embed=dash_embed(), view=self)

    @discord.ui.button(label="Pause / Resume", style=discord.ButtonStyle.success, emoji="⏯️", custom_id="rs:tog")
    async def tog_b(self, i: discord.Interaction, b: discord.ui.Button):
        SCAN["run"] = not SCAN["run"]; SCAN["ch"] = i.channel.id
        await i.response.edit_message(embed=dash_embed(), view=self)

    @discord.ui.button(label="Set Role Filter & Range", style=discord.ButtonStyle.secondary, emoji="🎭", custom_id="rs:flt")
    async def flt_b(self, i: discord.Interaction, b: discord.ui.Button):
        await i.response.send_modal(FilterModal())

class ScannerBot(commands.Bot):
    def __init__(self, msg_intent: bool = True):
        intents = discord.Intents.default(); intents.message_content = msg_intent
        super().__init__(command_prefix="!", intents=intents, help_command=None)
    async def setup_hook(self):
        self.add_view(ScanView()); await self.tree.sync()
    async def on_ready(self):
        global BOT_STATUS; BOT_STATUS = f"ONLINE as {self.user}"
        print(f"✅ Group Role Scanner ONLINE as {self.user}")

def reg_cmds(b: ScannerBot):
    @b.tree.command(name="scanner", description="Open the 24/7 Roblox Claimable Group & Role Scanner")
    async def s_scan(i: discord.Interaction):
        SCAN["ch"] = i.channel.id
        await i.response.send_message(embed=dash_embed(), view=ScanView())

    @b.tree.command(name="checkgroup", description="Check any Roblox Group ID for Owner, Open Status, and Roles")
    async def s_chk(i: discord.Interaction, group_id: int):
        await i.response.defer()
        async with aiohttp.ClientSession() as s:
            info = await inspect_group(s, group_id)
        if not info or info.get("rl"): return await i.followup.send("⚠️ Could not fetch group (or rate limited).")
        st = "🔓 **CLAIMABLE NOW!**" if info["claimable"] else ("🔒 Closed/Locked" if info["no_owner"] else "👤 Already Owned")
        r_txt = ", ".join(info["roles"][:15]) or "Default"
        emb = discord.Embed(title=f"🔍 {info['name']} (#{info['id']})", description=f"📌 **Status:** {st}\n👥 **Members:** `{info['members']:,}`\n🎭 **Roles ({info['role_count']}):** `{r_txt}`\n💰 **Role Value:** `~{info['robux']} Robux`\n🔗 **[Open on Roblox]({info['url']})**", color=discord.Color.green() if info["claimable"] else discord.Color.orange())
        await i.followup.send(embed=emb)

async def main():
    global BOT_STATUS
    await run_web(); asyncio.create_task(pinger())
    if not BOT_TOKEN:
        BOT_STATUS = "MISSING BOT_TOKEN IN RENDER ENVIRONMENT"
        while True: await asyncio.sleep(3600)
    bot = ScannerBot(True); reg_cmds(bot)
    asyncio.create_task(v2_scanner()); asyncio.create_task(v1_verifier(bot))
    try: await bot.start(BOT_TOKEN)
    except discord.PrivilegedIntentsRequired:
        await bot.close(); bot = ScannerBot(False); reg_cmds(bot)
        asyncio.create_task(v1_verifier(bot)); await bot.start(BOT_TOKEN)

if __name__ == "__main__":
    asyncio.run(main())
