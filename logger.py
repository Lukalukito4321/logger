import os
import json
from pathlib import Path
from dotenv import load_dotenv



# ---------- ENV ----------
load_dotenv(Path(__file__).with_name(".env"))

TOKEN = os.getenv("DISCORD_TOKEN")
LOG_CHANNEL_ID = int(os.getenv("LOG_CHANNEL_ID", "0"))
DASHBOARD_CODE = os.getenv("DASHBOARD_CODE", "")
DEFAULT_DASHBOARD_URL = os.getenv("DEFAULT_DASHBOARD_URL", "https://example.com")

if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN missing")
if LOG_CHANNEL_ID == 0:
    raise RuntimeError("LOG_CHANNEL_ID missing")

# ---------- DATA ----------
DATA_PATH = Path(__file__).with_name("dashboard_links.json")
dashboard_links = json.loads(DATA_PATH.read_text(encoding="utf-8")) if DATA_PATH.exists() else {}

def save_dashboard_links():
    DATA_PATH.write_text(json.dumps(dashboard_links, ensure_ascii=False, indent=2), encoding="utf-8")

# ---------- BOT ----------
intents = discord.Intents.default()
intents.members = True
intents.guilds = True
intents.messages = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ---- Invite cache ----
invite_cache: dict[int, dict[str, int]] = {}

# ---------- HELPERS ----------
def get_log_channel(guild: discord.Guild):
    return guild.get_channel(LOG_CHANNEL_ID) if guild else None

async def send_log(guild: discord.Guild, title: str, description: str):
    ch = get_log_channel(guild)
    if not ch:
        return
    embed = discord.Embed(
        title=title,
        description=description,
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow()
    )
    try:
        await ch.send(embed=embed)
    except Exception as e:
        print(f"Failed to send log: {e}")

async def refresh_invites(guild: discord.Guild):
    try:
        invites = await guild.invites()
        invite_cache[guild.id] = {i.code: (i.uses or 0) for i in invites}
    except Exception:
        invite_cache[guild.id] = {}

async def detect_invite_or_vanity(guild: discord.Guild) -> str:
    before = invite_cache.get(guild.id, {})
    try:
        invites = await guild.invites()
        after = {i.code: (i.uses or 0) for i in invites}
        invite_cache[guild.id] = after

        for code, uses_after in after.items():
            if uses_after > before.get(code, 0):
                used = next((i for i in invites if i.code == code), None)
                if used:
                    inviter = f"{used.inviter} (`{used.inviter.id}`)" if used.inviter else "Unknown"
                    return f"**Invite:** `{used.code}`\n**Inviter:** {inviter}\n**Uses:** {used.uses}"
    except Exception:
        pass

    try:
        vanity = await guild.vanity_invite()
        if vanity and vanity.code:
            return f"**Vanity:** `{vanity.code}`"
    except Exception:
        pass

    return "**Invite:** Unknown"

async def audit_entry(guild: discord.Guild, action: discord.AuditLogAction, target_id: int, sec: int = 20):
    # Needs permission: View Audit Log
    try:
        async for entry in guild.audit_logs(limit=10, action=action):
            if entry.target and getattr(entry.target, "id", None) == target_id:
                if (discord.utils.utcnow() - entry.created_at).total_seconds() <= sec:
                    return entry
    except Exception:
        return None
    return None

# ---------- SLASH COMMANDS ----------
class Dashboard(app_commands.Group):
    def __init__(self):
        super().__init__(name="dashboard", description="Dashboard commands")

    @app_commands.command(name="show", description="Show dashboard link")
    async def show(self, interaction: discord.Interaction):
        url = dashboard_links.get(str(interaction.guild_id), DEFAULT_DASHBOARD_URL)
        await interaction.response.send_message(f"📊 Dashboard: {url}", ephemeral=True)

    @app_commands.command(name="set", description="Set dashboard link (requires code)")
    async def set(self, interaction: discord.Interaction, url: str, code: str):
        if not interaction.guild_id:
            return await interaction.response.send_message("Server only.", ephemeral=True)

        if not DASHBOARD_CODE:
            return await interaction.response.send_message("DASHBOARD_CODE missing in .env", ephemeral=True)

        if code != DASHBOARD_CODE:
            return await interaction.response.send_message("❌ Wrong code", ephemeral=True)

        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("❌ Admin only", ephemeral=True)

        dashboard_links[str(interaction.guild_id)] = url
        save_dashboard_links()
        await interaction.response.send_message("✅ Dashboard updated", ephemeral=True)

# ---------- EVENTS ----------
@bot.event
async def on_ready():
    for g in bot.guilds:
        await refresh_invites(g)

    try:
        bot.tree.add_command(Dashboard())
        await bot.tree.sync()
    except Exception as e:
        print(f"Slash sync error: {e}")

    print(f"Logged in as {bot.user} (id: {bot.user.id})")

@bot.event
async def on_guild_join(guild: discord.Guild):
    await refresh_invites(guild)

@bot.event
async def on_invite_create(invite: discord.Invite):
    if invite.guild:
        await refresh_invites(invite.guild)

@bot.event
async def on_invite_delete(invite: discord.Invite):
    if invite.guild:
        await refresh_invites(invite.guild)

@bot.event
async def on_member_join(member: discord.Member):
    info = await detect_invite_or_vanity(member.guild)
    await send_log(
        member.guild,
        "✅ Member Joined",
        f"**User:** {member} (`{member.id}`)\n"
        f"**Account created:** {discord.utils.format_dt(member.created_at, style='F')}\n\n"
        f"{info}"
    )

@bot.event
async def on_member_remove(member: discord.Member):
    entry = await audit_entry(member.guild, discord.AuditLogAction.kick, member.id)
    if entry:
        mod = f"{entry.user} (`{entry.user.id}`)" if entry.user else "Unknown"
        await send_log(
            member.guild,
            "👢 Member Kicked",
            f"**User:** {member} (`{member.id}`)\n"
            f"**By:** {mod}\n"
            f"**Reason:** {entry.reason or 'No reason'}"
        )
    else:
        await send_log(member.guild, "❌ Member Left", f"**User:** {member} (`{member.id}`)")

@bot.event
async def on_member_ban(guild: discord.Guild, user: discord.User | discord.Member):
    entry = await audit_entry(guild, discord.AuditLogAction.ban, user.id)
    mod = f"{entry.user} (`{entry.user.id}`)" if entry and entry.user else "Unknown"
    await send_log(
        guild,
        "⛔ Member Banned",
        f"**User:** {user} (`{user.id}`)\n"
        f"**By:** {mod}\n"
        f"**Reason:** {(entry.reason if entry else None) or 'No reason'}"
    )

@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    # ---------- ROLE ADD / REMOVE ----------
    before_roles = set(before.roles)
    after_roles = set(after.roles)

    added = [r for r in (after_roles - before_roles) if r.name != "@everyone"]
    removed = [r for r in (before_roles - after_roles) if r.name != "@everyone"]

    if added or removed:
        entry = await audit_entry(after.guild, discord.AuditLogAction.member_role_update, after.id, sec=25)
        mod = f"{entry.user} (`{entry.user.id}`)" if entry and entry.user else "Unknown"

        added_txt = ", ".join(r.mention for r in added) if added else "None"
        removed_txt = ", ".join(r.mention for r in removed) if removed else "None"

        await send_log(
            after.guild,
            "🎭 Roles Updated",
            f"**User:** {after} (`{after.id}`)\n"
            f"**By:** {mod}\n"
            f"**Added:** {added_txt}\n"
            f"**Removed:** {removed_txt}"
        )

    # ---------- NICKNAME CHANGE ----------
    if before.nick != after.nick:
        entry = await audit_entry(after.guild, discord.AuditLogAction.member_update, after.id, sec=25)
        mod = f"{entry.user} (`{entry.user.id}`)" if entry and entry.user else "Unknown"

        old_nick = before.nick if before.nick is not None else before.name
        new_nick = after.nick if after.nick is not None else after.name

        await send_log(
            after.guild,
            "📝 Nickname Changed",
            f"**User:** {after} (`{after.id}`)\n"
            f"**By:** {mod}\n"
            f"**Before:** {old_nick}\n"
            f"**After:** {new_nick}"
        )

    # ---------- TIMEOUT ----------
    if before.communication_disabled_until != after.communication_disabled_until:
        entry = await audit_entry(after.guild, discord.AuditLogAction.member_update, after.id, sec=25)
        mod = f"{entry.user} (`{entry.user.id}`)" if entry and entry.user else "Unknown"

        if after.communication_disabled_until:
            await send_log(
                after.guild,
                "⏳ Timeout Applied/Updated",
                f"**User:** {after} (`{after.id}`)\n"
                f"**By:** {mod}\n"
                f"**Until:** {discord.utils.format_dt(after.communication_disabled_until, style='F')}"
            )
        else:
            await send_log(
                after.guild,
                "✅ Timeout Removed",
                f"**User:** {after} (`{after.id}`)\n"
                f"**By:** {mod}"
            )

@bot.event
async def on_message_delete(message: discord.Message):
    if not message.guild or (message.author and message.author.bot):
        return

    author = f"{message.author} (`{message.author.id}`)" if message.author else "Unknown"
    content = (message.content or "*no text*")[:1500]

    await send_log(
        message.guild,
        "🗑️ Message Deleted",
        f"**Author:** {author}\n"
        f"**Channel:** {message.channel.mention}\n"
        f"**Content:**\n{content}"
    )

@bot.event
async def on_message_edit(before: discord.Message, after: discord.Message):
    if not after.guild or (after.author and after.author.bot):
        return
    if before.content == after.content:
        return

    before_txt = (before.content or "*no text*")[:900]
    after_txt = (after.content or "*no text*")[:900]

    await send_log(
        after.guild,
        "✏️ Message Edited",
        f"**Author:** {after.author} (`{after.author.id}`)\n"
        f"**Channel:** {after.channel.mention}\n\n"
        f"**Before:**\n{before_txt}\n\n"
        f"**After:**\n{after_txt}"
    )

bot.run(TOKEN)
