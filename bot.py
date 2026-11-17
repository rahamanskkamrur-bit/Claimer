import asyncio, json, os, random, time, aiohttp
from aiohttp import web
import discord
from discord.ext import commands

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip().strip('"').strip("'")
ALERT_CH_ID = int(os.getenv("ALERT_CHANNEL_ID", "1568991830245253263"))
VAULT_FILE = "claimable_vault.json"
START_TIME, BOT_STATUS = time.time(), "Starting..."

RANGES = [(12000000, 17300000), (32500000, 36000000)]
KEYWORDS = [
    "police", "swat", "sheriff", "state", "patrol", "academy", "rp", "roleplay",
    "studio", "studios", "dev", "games", "club", "fan", "official", "army",
    "clan", "squad", "team", "clothing", "dept", "department", "division",
    "bureau", "agency", "security", "tactical", "force", "guard", "corp"
]
HOSTS = ["https://groups.roblox.com", "https://groups.roproxy.com"]

SCAN = {"run": True, "min_roles": 1, "ch": ALERT_CH_ID, "checked": 0, "unowned": 0, "claimable": 0, "dead_skipped": 0}
QUEUE = asyncio.Queue(maxsize=150)
VAULT = []

def load_vault():
    global VAULT
    if os.path.exists(VAULT_FILE):
        try: VAULT = json.load(open(VAULT_FILE))
        except Exception: VAULT = []

def save_vault():
    try: json.dump(VAULT[-50:], open(VAULT_FILE, "w"))
    except Exception: pass

async def run_web():
    app = web.Application()
    app.router.add_get("/", lambda r: web.Response(text=f"Scanner 24/7 | Vault: {len(VAULT)} | Checked: {SCAN['checked']} | Status: {BOT_STATUS}"))
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
    """5-Layer Check to filter out Deactivated, Locked, 0-Member & Content-Deleted groups."""
    d = None
    for host in HOSTS:
        try:
            async with s.get(f"{host}/v1/groups/{gid}", timeout=8) as r:
                if r.status == 200:
                    d = await r.json(); break
        except Exception: pass
    if not d: return None

    name = (d.get("name") or "").strip()
    desc = (d.get("description") or "").strip()
    members = int(d.get("memberCount") or 0)

    # Layer 1: Must have no owner, be open, and NOT locked
    if d.get("owner") is not None or d.get("publicEntryAllowed") is not True or d.get("isLocked"):
        return None

    # Layer 2: Filter out [ Content Deleted ] or 0-member deactivated groups
    if not name or "content deleted" in name.lower() or "content deleted" in desc.lower() or members < 1:
        SCAN["dead_skipped"] += 1
        return None

    # Layer 3: Verify active member exists via /users (Deactivated groups have 0 real users!)
    has_active_user = False
    for host in HOSTS:
        try:
            async with s.get(f"{host}/v1/groups/{gid}/users?limit=10", timeout=8) as ur:
                if ur.status == 200:
                    users_data = (await ur.json()).get("data", [])
                    has_active_user = len(users_data) > 0
                    break
        except Exception: pass
    if not has_active_user:
        SCAN["dead_skipped"] += 1
        return None

    # Layer 4: Verify Roles & Ensure Rank 255 (Owner Role) has 0 members
    roles = []
    owner_role_occupied = False
    for host in HOSTS:
        try:
            async with s.get(f"{host}/v1/groups/{gid}/roles", timeout=8) as rr:
                if rr.status == 200:
                    raw_roles = (await rr.json()).get("roles", [])
                    for x in raw_roles:
                        if x.get("rank") == 255 and int(x.get("memberCount") or 0) > 0:
                            owner_role_occupied = True
                        if x.get("rank", 0) > 0:
                            roles.append(x.get("name", "Role"))
                    break
        except Exception: pass

    if owner_role_occupied or not roles:
        SCAN["dead_skipped"] += 1
        return None

    rc = len(roles)
    return {
        "id": gid, "name": name, "members": members, "claimable": True,
        "role_count": rc, "roles": roles, "robux": max(0, (rc - 3) * 25),
        "url": f"https://www.roblox.com/communities/{gid}",
    }

def make_group_embed(g: dict, prefix: str = "🔓 VERIFIED ACTIVE GROUP") -> discord.Embed:
    r_txt = ", ".join(g["roles"][:12])
    return discord.Embed(
        title=f"{prefix}: {g['name']} ({g['role_count']} Roles)",
        description=(
            f"🆔 **ID:** `{g['id']}`  |  👥 **Active Members:** `{g['members']:,}`\n"
            f"👑 **Owner:** `None (0 on Owner Rank)`  |  🟢 **Status:** `Active & Not Deactivated`\n"
            f"🎭 **Roles ({g['role_count']}):** `{r_txt}`\n"
            f"💰 **Role Value:** `~{g['robux']} Robux saved`\n\n"
            f"👉 **[JOIN & CLAIM COMMUNITY NOW]({g['url']})**"
        ),
        color=discord.Color.gold() if g["role_count"] >= 5 else discord.Color.green(),
    )

async def save_and_alert(bot: commands.Bot, info: dict):
    if not any(x["id"] == info["id"] for x in VAULT):
        VAULT.append(info); save_vault()
    SCAN["claimable"] += 1
    if info["role_count"] < SCAN["min_roles"]: return
    ch = bot.get_channel(SCAN["ch"])
    if not ch: return
    v = discord.ui.View()
    v.add_item(discord.ui.Button(label=f"Claim Active Group ({info['role_count']} Roles)", style=discord.ButtonStyle.link, url=info["url"], emoji="👑"))
    try: await ch.send(content="@here ⚡ **ACTIVE CLAIMABLE GROUP FOUND!**", embed=make_group_embed(info), view=v)
    except Exception: pass

async def harvester_loop():
    async with aiohttp.ClientSession() as s:
        while True:
            if not SCAN["run"] or QUEUE.qsize() >= 100:
                await asyncio.sleep(1.0); continue
            lo, hi = random.choice(RANGES)
            base = random.randint(lo // 100, hi // 100) * 100
            ids = ",".join(str(i) for i in range(base, base + 100))
            host = random.choice(HOSTS)
            try:
                async with s.get(f"{host}/v2/groups?groupIds={ids}", timeout=8) as r:
                    if r.status == 200:
                        SCAN["checked"] += 100
                        for g in (await r.json()).get("data", []):
                            if g.get("owner") is None and g.get("id") and not QUEUE.full():
                                SCAN["unowned"] += 1; await QUEUE.put(g["id"])
            except Exception: pass

            kw = random.choice(KEYWORDS)
            try:
                async with s.get(f"{host}/v1/groups/search?keyword={kw}&limit=25", timeout=8) as r:
                    if r.status == 200:
                        for g in (await r.json()).get("data", []):
                            SCAN["checked"] += 1
                            if not g.get("hasVerifiedBadge") and g.get("publicEntryAllowed") and int(g.get("memberCount") or 0) > 0 and not QUEUE.full():
                                await QUEUE.put(g["id"])
            except Exception: pass
            await asyncio.sleep(0.4)

async def verifier_worker(bot: commands.Bot):
    await bot.wait_until_ready()
    async with aiohttp.ClientSession() as s:
        while not bot.is_closed():
            if not SCAN["run"]: await asyncio.sleep(1); continue
            try: gid = await asyncio.wait_for(QUEUE.get(), timeout=2.0)
            except asyncio.TimeoutError: continue
            info = await inspect_group(s, gid)
            QUEUE.task_done()
            if info: await save_and_alert(bot, info)
            await asyncio.sleep(0.35)

def dash_embed() -> discord.Embed:
    return discord.Embed(
        title="⚡ Active Claimable Group Vault (Anti-Deactivated Filter ON)",
        description=(
            f"🎁 **Verified Active Vault:** **`{len(VAULT)} Group(s)`**\n"
            f"📊 **Checked:** `{SCAN['checked']:,}` | **Unowned:** `{SCAN['unowned']:,}` | **Deactivated Filtered:** `{SCAN['dead_skipped']:,}`\n"
            f"🎭 **Min Roles Filter:** `{SCAN['min_roles']}+ roles` | 📍 **Channel:** <#{SCAN['ch']}>\n\n"
            "✅ *5-Layer Filter blocks 0-member, Content-Deleted, and Deactivated groups automatically.*"
        ),
        color=discord.Color.green(),
    )

class ScanView(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)

    @discord.ui.button(label="Get Active Group Now", style=discord.ButtonStyle.success, emoji="⚡", custom_id="ig:get", row=0)
    async def get_b(self, i: discord.Interaction, b: discord.ui.Button):
        await i.response.defer(ephemeral=False)
        async with aiohttp.ClientSession() as s:
            while VAULT:
                cand = VAULT.pop(0); save_vault()
                live = await inspect_group(s, cand["id"])
                if live:
                    v = discord.ui.View()
                    v.add_item(discord.ui.Button(label=f"Claim Group ({live['role_count']} Roles)", style=discord.ButtonStyle.link, url=live["url"], emoji="👑"))
                    return await i.followup.send(content=f"⚡ {i.user.mention} **Verified Active Group!**", embed=make_group_embed(live, "⚡ INSTANT VAULT HIT"), view=v)

            for _ in range(3):
                lo, hi = random.choice(RANGES)
                base = random.randint(lo // 100, hi // 100) * 100
                ids = ",".join(str(x) for x in range(base, base + 100))
                try:
                    async with s.get(f"{random.choice(HOSTS)}/v2/groups?groupIds={ids}", timeout=8) as r:
                        if r.status == 200:
                            SCAN["checked"] += 100
                            for g in (await r.json()).get("data", []):
                                if g.get("owner") is None:
                                    SCAN["unowned"] += 1
                                    live = await inspect_group(s, g["id"])
                                    if live:
                                        v = discord.ui.View()
                                        v.add_item(discord.ui.Button(label=f"Claim Group ({live['role_count']} Roles)", style=discord.ButtonStyle.link, url=live["url"], emoji="👑"))
                                        return await i.followup.send(content=f"🎉 {i.user.mention} **Live Turbo Burst Hit!**", embed=make_group_embed(live, "⚡ TURBO BURST HIT"), view=v)
                except Exception: pass
        await i.followup.send(f"⏳ Scanned **300 groups** live & filtered out `{SCAN['dead_skipped']}` deactivated/dead groups. Watch <#{SCAN['ch']}> for the next verified active hit!")

    @discord.ui.button(label="Purge Dead Vault", style=discord.ButtonStyle.danger, emoji="🧹", custom_id="ig:purge", row=0)
    async def purge_b(self, i: discord.Interaction, b: discord.ui.Button):
        VAULT.clear(); save_vault()
        await i.response.edit_message(embed=dash_embed(), view=self)

    @discord.ui.button(label="Refresh Dashboard", style=discord.ButtonStyle.primary, emoji="🔄", custom_id="ig:ref", row=0)
    async def ref_b(self, i: discord.Interaction, b: discord.ui.Button):
        SCAN["ch"] = i.channel.id
        await i.response.edit_message(embed=dash_embed(), view=self)

class ScannerBot(commands.Bot):
    def __init__(self, msg_intent: bool = True):
        intents = discord.Intents.default(); intents.message_content = msg_intent
        super().__init__(command_prefix="!", intents=intents, help_command=None)
    async def setup_hook(self):
        self.add_view(ScanView()); await self.tree.sync()
    async def on_ready(self):
        global BOT_STATUS; BOT_STATUS = f"ONLINE as {self.user}"
        print(f"✅ Anti-Deactivated Group Scanner ONLINE as {self.user}")

def reg_cmds(b: ScannerBot):
    @b.tree.command(name="scanner", description="Open the Active Claimable Group Scanner")
    async def s_scan(i: discord.Interaction):
        SCAN["ch"] = i.channel.id
        await i.response.send_message(embed=dash_embed(), view=ScanView())

async def main():
    global BOT_STATUS
    load_vault(); await run_web(); asyncio.create_task(pinger())
    if not BOT_TOKEN:
        BOT_STATUS = "MISSING BOT_TOKEN"
        while True: await asyncio.sleep(3600)
    bot = ScannerBot(True); reg_cmds(bot)
    asyncio.create_task(harvester_loop())
    for _ in range(3): asyncio.create_task(verifier_worker(bot))
    try: await bot.start(BOT_TOKEN)
    except discord.PrivilegedIntentsRequired:
        await bot.close(); bot = ScannerBot(False); reg_cmds(bot)
        for _ in range(3): asyncio.create_task(verifier_worker(bot))
        await bot.start(BOT_TOKEN)

if __name__ == "__main__":
    asyncio.run(main())
