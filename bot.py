import asyncio, json, os, random, time, aiohttp
from aiohttp import web
import discord
from discord.ext import commands

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip().strip('"').strip("'")
ALERT_CH_ID = int(os.getenv("ALERT_CHANNEL_ID", "1568991830245253263"))
VAULT_FILE = "verified_vault_v4.json"
START_TIME, BOT_STATUS = time.time(), "Starting..."

# Valid Roblox Group ID ranges (skips 17.3M-32M dead gap)
RANGES = [(1000000, 9000000), (9000000, 17300000), (32000000, 35950000)]
HOSTS = ["https://groups.roblox.com", "https://groups.roproxy.com"]
KEYWORDS = [
    "police", "swat", "sheriff", "patrol", "academy", "rp", "roleplay", "studio",
    "studios", "games", "club", "fan", "official", "army", "clan", "squad", "team",
    "dept", "department", "division", "bureau", "agency", "security", "tactical",
    "force", "empire", "legion", "elite", "gaming", "production", "holdings", "corp"
]

SCAN = {"run": True, "min_roles": 1, "ch": ALERT_CH_ID, "checked": 0, "unowned": 0, "claimable": 0, "dead": 0}
QUEUE = asyncio.Queue(maxsize=600)
SEEN = set()
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
    app.router.add_get("/", lambda r: web.Response(text=f"V4 Anti-Deactivated Scanner | Checked: {SCAN['checked']:,} | Vault: {len(VAULT)} | Status: {BOT_STATUS}"))
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

async def fetch_json(s: aiohttp.ClientSession, path: str) -> tuple[int, dict | None]:
    for host in HOSTS:
        try:
            async with s.get(f"{host}{path}", timeout=7) as r:
                if r.status in (200, 400, 403, 404):
                    return r.status, await r.json()
        except Exception:
            pass
    return 0, None

async def inspect_group(s: aiohttp.ClientSession, gid: int) -> dict | None:
    """
    6-Gate Anti-Deactivated Verification:
    Eliminates groups where V1 & V2 disagree on owner, deactivated communities,
    locked groups, 0-member ghost groups, and groups with hidden rank-255 owners.
    """
    # Gate 1: V1 Group Info (Must have owner=None, publicEntryAllowed=True, isLocked=False)
    st1, d1 = await fetch_json(s, f"/v1/groups/{gid}")
    if st1 != 200 or not d1:
        return None
    if d1.get("owner") is not None or d1.get("publicEntryAllowed") is not True or d1.get("isLocked"):
        return None
    if d1.get("isBuildersClubOnly"):
        return None

    name = (d1.get("name") or "").strip()
    desc = (d1.get("description") or "").strip()
    members = int(d1.get("memberCount") or 0)
    if not name or "content deleted" in name.lower() or "content deleted" in desc.lower() or members < 2:
        SCAN["dead"] += 1; return None

    # Gate 2: Cross-verify V2 Group Endpoint!
    # (Fixes Roblox bug where banned/deactivated owners show owner=null in V1 but still have an owner in V2!)
    st2, d2 = await fetch_json(s, f"/v2/groups?groupIds={gid}")
    if st2 != 200 or not d2 or not d2.get("data"):
        SCAN["dead"] += 1; return None
    g2 = d2["data"][0]
    if g2.get("owner") is not None:
        SCAN["dead"] += 1; return None

    # Gate 3: Roles & Rank 255 Verification
    st_r, dr = await fetch_json(s, f"/v1/groups/{gid}/roles")
    if st_r != 200 or not dr:
        SCAN["dead"] += 1; return None

    roles = []
    for r in dr.get("roles", []):
        rk = r.get("rank", 0)
        mc = int(r.get("memberCount") or 0)
        # If Rank 255 (Owner) still has a member or non-guest roles have 0 members total, it's broken/deactivated
        if rk == 255 and mc > 0:
            SCAN["dead"] += 1; return None
        if rk > 0:
            roles.append(r.get("name", "Role"))
    if not roles:
        SCAN["dead"] += 1; return None

    # Gate 4: Active Member Roster & Live User Verification
    # Deactivated communities fail or return 0 active users when querying /users
    st_u, du = await fetch_json(s, f"/v1/groups/{gid}/users?limit=10&sortOrder=Desc")
    if st_u != 200 or not du:
        SCAN["dead"] += 1; return None
    user_list = du.get("data", [])
    if len(user_list) < 1:
        SCAN["dead"] += 1; return None

    # Verify the newest member in the group is an active, non-banned Roblox account
    sample_uid = user_list[0].get("user", {}).get("userId")
    if not sample_uid:
        SCAN["dead"] += 1; return None
    try:
        async with s.get(f"https://users.roblox.com/v1/users/{sample_uid}", timeout=6) as ur:
            if ur.status == 200:
                ud = await ur.json()
                if ud.get("isBanned"):
                    SCAN["dead"] += 1; return None
    except Exception:
        pass

    # Gate 5: Community Settings / Wall / Name-History Liveness Probe
    # Deactivated communities return error code 1 / 400 / 404 on internal community sub-endpoints
    st_nh, d_nh = await fetch_json(s, f"/v1/groups/{gid}/name-history?limit=10")
    if st_nh in (400, 404) or (isinstance(d_nh, dict) and d_nh.get("errors")):
        SCAN["dead"] += 1; return None

    # Gate 6: Rolimons / External Metadata Check (if tracked as Private/Locked/Deactivated)
    try:
        async with s.get(f"https://www.roblox.com/communities/{gid}", allow_redirects=True, timeout=6) as pr:
            if pr.status >= 400:
                SCAN["dead"] += 1; return None
            txt = (await pr.text()).lower()
            if "deactivated" in txt or "locked" in txt:
                SCAN["dead"] += 1; return None
    except Exception:
        pass

    rc = len(roles)
    return {
        "id": gid, "name": name, "members": len(user_list) if members < len(user_list) else members,
        "role_count": rc, "roles": roles, "robux": max(0, (rc - 3) * 25),
        "url": f"https://www.roblox.com/groups/{gid}",
    }

def make_embed(g: dict, tag: str = "🔓 CLAIMABLE GROUP FOUND") -> discord.Embed:
    return discord.Embed(
        title=f"{tag}: {g['name']} ({g['role_count']} Roles)",
        description=(
            f"🆔 **ID:** `{g['id']}` | 👥 **Members:** `{g['members']:,}`\n"
            f"👑 **V1 & V2 Owner:** `None (Verified)` | 🟢 **Status:** `Open & Claimable`\n"
            f"🎭 **Roles ({g['role_count']}):** `{', '.join(g['roles'][:12])}`\n"
            f"💰 **Role Value:** `~{g['robux']} Robux saved`\n\n"
            f"👉 **[CLICK HERE TO OPEN & CLAIM GROUP]({g['url']})**"
        ),
        color=discord.Color.gold() if g["role_count"] >= 5 else discord.Color.green(),
    )

async def alert_hit(bot: commands.Bot, info: dict):
    if not any(x["id"] == info["id"] for x in VAULT):
        VAULT.append(info); save_vault()
    SCAN["claimable"] += 1
    if info["role_count"] < SCAN["min_roles"]: return
    ch = bot.get_channel(SCAN["ch"])
    if not ch: return
    v = discord.ui.View()
    v.add_item(discord.ui.Button(label=f"Claim Group ({info['role_count']} Roles)", style=discord.ButtonStyle.link, url=info["url"], emoji="👑"))
    try: await ch.send(content="@here 🚨 **6-GATE VERIFIED CLAIMABLE GROUP!**", embed=make_embed(info), view=v)
    except Exception: pass

async def harvester(worker_id: int):
    cursor_id = RANGES[worker_id % len(RANGES)][0] + random.randint(0, 500000)
    async with aiohttp.ClientSession() as s:
        while True:
            if not SCAN["run"] or QUEUE.qsize() >= 500:
                await asyncio.sleep(0.4); continue
            if random.random() < 0.55:
                lo, hi = random.choice(RANGES)
                base = random.randint(lo // 100, hi // 100) * 100
            else:
                base = cursor_id
                cursor_id += 100
                if 17300000 < cursor_id < 32000000: cursor_id = 32000000

            ids = ",".join(str(i) for i in range(base, base + 100))
            host = HOSTS[worker_id % len(HOSTS)]
            try:
                async with s.get(f"{host}/v2/groups?groupIds={ids}", timeout=7) as r:
                    if r.status == 200:
                        SCAN["checked"] += 100
                        for g in (await r.json()).get("data", []):
                            gid = g.get("id")
                            if g.get("owner") is None and gid and gid not in SEEN and not QUEUE.full():
                                SEEN.add(gid)
                                if len(SEEN) > 50000: SEEN.clear()
                                SCAN["unowned"] += 1
                                await QUEUE.put(gid)
            except Exception: pass

            if worker_id == 0 and random.random() < 0.35:
                kw = random.choice(KEYWORDS)
                try:
                    async with s.get(f"{host}/v1/groups/search?keyword={kw}&limit=25", timeout=7) as r:
                        if r.status == 200:
                            for g in (await r.json()).get("data", []):
                                gid = g.get("id")
                                SCAN["checked"] += 1
                                if gid and gid not in SEEN and g.get("publicEntryAllowed") and not g.get("hasVerifiedBadge") and not QUEUE.full():
                                    SEEN.add(gid); await QUEUE.put(gid)
                except Exception: pass
            await asyncio.sleep(0.25)

async def verifier(bot: commands.Bot):
    await bot.wait_until_ready()
    async with aiohttp.ClientSession() as s:
        while not bot.is_closed():
            if not SCAN["run"]: await asyncio.sleep(1); continue
            try: gid = await asyncio.wait_for(QUEUE.get(), timeout=2.0)
            except asyncio.TimeoutError: continue
            info = await inspect_group(s, gid)
            QUEUE.task_done()
            if info: await alert_hit(bot, info)
            await asyncio.sleep(0.08)

def dash_embed() -> discord.Embed:
    up_hr = max(0.01, (time.time() - START_TIME) / 3600)
    rate_24h = int((SCAN["checked"] / up_hr) * 24)
    return discord.Embed(
        title="🛡️ V4 Anti-Deactivated Roblox Group Hunter",
        description=(
            f"🎁 **Verified in Vault:** **`{len(VAULT)} Active Group(s)`** | 🏆 **Found:** `{SCAN['claimable']}`\n"
            f"📊 **Scanned:** `{SCAN['checked']:,}` (`~{rate_24h:,}/24h` pace)\n"
            f"🔍 **Unowned Checked:** `{SCAN['unowned']:,}` | 🚫 **Deactivated/Ghost Filtered:** `{SCAN['dead']:,}`\n"
            f"📥 **Verifier Queue:** `{QUEUE.qsize()}` | 📍 **Alerts:** <#{SCAN['ch']}>"
        ),
        color=discord.Color.green(),
    )

class ScanView(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)

    @discord.ui.button(label="Get Verified Group Now", style=discord.ButtonStyle.success, emoji="⚡", custom_id="v4:get")
    async def get_b(self, i: discord.Interaction, b: discord.ui.Button):
        await i.response.defer(ephemeral=False)
        async with aiohttp.ClientSession() as s:
            while VAULT:
                cand = VAULT.pop(0); save_vault()
                live = await inspect_group(s, cand["id"])
                if live:
                    v = discord.ui.View()
                    v.add_item(discord.ui.Button(label=f"Claim ({live['role_count']} Roles)", style=discord.ButtonStyle.link, url=live["url"], emoji="👑"))
                    return await i.followup.send(content=f"⚡ {i.user.mention} **Verified Active Group!**", embed=make_embed(live, "⚡ VAULT HIT"), view=v)
        await i.followup.send(embed=dash_embed(), content=f"🛡️ {i.user.mention} V4 6-Gate Filter is actively blocking deactivated groups (`{SCAN['dead']:,}` filtered so far) and hunting live claimable groups for <#{SCAN['ch']}>!")

    @discord.ui.button(label="Refresh Stats", style=discord.ButtonStyle.primary, emoji="🔄", custom_id="v4:ref")
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
        print(f"✅ V4 Anti-Deactivated Group Scanner ONLINE as {self.user}")

def reg_cmds(b: ScannerBot):
    @b.tree.command(name="scanner", description="Open the V4 Anti-Deactivated Group Scanner")
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
    for w in range(2): asyncio.create_task(harvester(w))
    for _ in range(8): asyncio.create_task(verifier(bot))
    try: await bot.start(BOT_TOKEN)
    except discord.PrivilegedIntentsRequired:
        await bot.close(); bot = ScannerBot(False); reg_cmds(bot)
        for _ in range(8): asyncio.create_task(verifier(bot))
        await bot.start(BOT_TOKEN)

if __name__ == "__main__":
    asyncio.run(main())
    
