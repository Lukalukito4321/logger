import os
from pathlib import Path
from dotenv import load_dotenv


from discord.ext import commands

# Load .env from same folder as this file
load_dotenv(Path(__file__).with_name(".env"))

TOKEN = os.getenv("DISCORD_TOKEN")
LOG_CHANNEL_ID = os.getenv("LOG_CHANNEL_ID", "0")

# Optional: set this for INSTANT slash updates on one server (recommended)
# Put your server id in .env as: GUILD_ID=123...
GUILD_ID = os.getenv("GUILD_ID", "").strip()

if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN missing in .env")
if not LOG_CHANNEL_ID or LOG_CHANNEL_ID == "0":
    raise RuntimeError("LOG_CHANNEL_ID missing in .env")

LOG_CHANNEL_ID = int(LOG_CHANNEL_ID)

# Intents
intents = discord.Intents.default()
intents.members = True
intents.guilds = True
intents.messages = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Invite cache: { guild_id: {invite_code: uses} }
invite_cache: dict[int, dict[str, int]] = {}


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
        timestamp=discord.utils.utcnow(),
    )

    try:
        await ch.send(embed=embed)
    except Exception as e:
        print(f"Failed to send log: {e}")


async def refresh_invites_for_guild(guild: discord.Guild):
    try:
        invites = await guild.invites()
        invite_cache[guild.id] = {i.code: (i.uses or 0) for i in invites}
    except discord.Forbidden:
        invite_cache[guild.id] = {}
    except Exception:
        invite_cache[guild.id] = {}


async def detect_used_invite_or_vanity(guild: discord.Guild) -> str:
    before = invite_cache.get(guild.id, {})
    try:
        invites = await guild.invites()
        after = {i.code: (i.uses or 0) for i in invites}

        used = None
        for code, uses_after in after.items():
            if uses_after > before.get(code, 0):
                used = next((i for i in invites if i.code == code), None)
                break

        invite_cache[guild.id] = after

        if used:
            inviter = f"{used.inviter} (`{used.inviter.id}`)" if used.inviter else "Unknown inviter"
            return f"**Invite:** `{used.code}`\n**Inviter:** {inviter}\n**Uses:** {used.uses}"

        try:
            vanity = await guild.vanity_invite()
            if vanity and vanity.code:
                return f"**Vanity:** `{vanity.code}`"
        except Exception:
            pass

        return "**Invite:** Unknown"
    except discord.Forbidden:
        return "**Invite:** Unknown (missing permission to read invites)"
    except Exception:
        return "**Invite:** Unknown (error reading invites)"


async def find_audit_actor(
    guild: discord.Guild,
    action: discord.AuditLogAction,
    target_id: int,
    seconds_window: int = 20
):
    try:
        async for entry in guild.audit_logs(limit=10, action=action):
            if entry.target and getattr(entry.target, "id", None) == target_id:
                now = discord.utils.utcnow()
                if entry.created_at and (now - entry.created_at).total_seconds() <= seconds_window:
                    return entry
    except Exception:
        return None
    return None


# -----------------------
# ONLY SLASH COMMAND
# -----------------------
@bot.tree.command(name="dashboard", description="Open dashboard")
async def dashboard(interaction: discord.Interaction):
    # ONLY the link, nothing else:
    await interaction.response.send_message(
        "http://127.0.0.1:5000/guilds",
        ephemeral=True
    )


@bot.event
async def on_ready():
    # Cache invites for all guilds
    for g in bot.guilds:
        await refresh_invites_for_guild(g)

    # Remove any old slash commands and sync
    try:
        # Clear global commands from this bot's tree, then re-sync
        bot.tree.clear_commands(guild=None)

        # If you set GUILD_ID in .env, sync instantly to that server too (recommended)
        if GUILD_ID.isdigit():
            guild_obj = discord.Object(id=int(GUILD_ID))
            bot.tree.clear_commands(guild=guild_obj)  # remove old guild commands
            bot.tree.copy_global_to(guild=guild_obj)
            synced = await bot.tree.sync(guild=guild_obj)
            print(f"✅ Synced to guild {GUILD_ID}: {[c.name for c in synced]}")
        else:
            # Global sync (may take longer to appear)
            synced = await bot.tree.sync()
            print(f"✅ Global synced: {[c.name for c in synced]}")
    except Exception as e:
        print(f"Slash sync error: {e}")

    print(f"Logged in as {bot.user} (id: {bot.user.id})")


@bot.event
async def on_guild_join(guild: discord.Guild):
    await refresh_invites_for_guild(guild)


@bot.event
async def on_invite_create(invite: discord.Invite):
    if invite.guild:
        await refresh_invites_for_guild(invite.guild)


@bot.event
async def on_invite_delete(invite: discord.Invite):
    if invite.guild:
        await refresh_invites_for_guild(invite.guild)


@bot.event
async def on_member_join(member: discord.Member):
    invite_info = await detect_used_invite_or_vanity(member.guild)
    await send_log(
        member.guild,
        "✅ Member Joined",
        f"**User:** {member} (`{member.id}`)\n"
        f"**Account created:** {discord.utils.format_dt(member.created_at, style='F')}\n\n"
        f"{invite_info}"
    )


@bot.event
async def on_member_remove(member: discord.Member):
    entry = await find_audit_actor(member.guild, discord.AuditLogAction.kick, member.id)
    if entry:
        moderator = f"{entry.user} (`{entry.user.id}`)" if entry.user else "Unknown"
        reason = entry.reason or "No reason"
        await send_log(
            member.guild,
            "👢 Member Kicked",
            f"**User:** {member} (`{member.id}`)\n"
            f"**By:** {moderator}\n"
            f"**Reason:** {reason}"
        )
    else:
        await send_log(
            member.guild,
            "❌ Member Left",
            f"**User:** {member} (`{member.id}`)"
        )


@bot.event
async def on_member_ban(guild: discord.Guild, user: discord.User | discord.Member):
    entry = await find_audit_actor(guild, discord.AuditLogAction.ban, user.id)
    moderator = f"{entry.user} (`{entry.user.id}`)" if entry and entry.user else "Unknown"
    reason = entry.reason if entry else None
    await send_log(
        guild,
        "⛔ Member Banned",
        f"**User:** {user} (`{user.id}`)\n"
        f"**By:** {moderator}\n"
        f"**Reason:** {reason or 'No reason'}"
    )


@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    # --- ROLE ADD / REMOVE ---
    before_roles = set(before.roles)
    after_roles = set(after.roles)

    added = [r for r in (after_roles - before_roles) if r.name != "@everyone"]
    removed = [r for r in (before_roles - after_roles) if r.name != "@everyone"]

    if added or removed:
        entry = await find_audit_actor(after.guild, discord.AuditLogAction.member_role_update, after.id, seconds_window=25)
        moderator = f"{entry.user} (`{entry.user.id}`)" if entry and entry.user else "Unknown"
        added_txt = ", ".join(r.mention for r in added) if added else "None"
        removed_txt = ", ".join(r.mention for r in removed) if removed else "None"

        await send_log(
            after.guild,
            "🎭 Roles Updated",
            f"**User:** {after} (`{after.id}`)\n"
            f"**By:** {moderator}\n"
            f"**Added:** {added_txt}\n"
            f"**Removed:** {removed_txt}"
        )

    # --- NICKNAME CHANGE ---
    if before.nick != after.nick:
        entry = await find_audit_actor(after.guild, discord.AuditLogAction.member_update, after.id, seconds_window=25)
        moderator = f"{entry.user} (`{entry.user.id}`)" if entry and entry.user else "Unknown"

        old_nick = before.nick if before.nick is not None else before.name
        new_nick = after.nick if after.nick is not None else after.name

        await send_log(
            after.guild,
            "📝 Nickname Changed",
            f"**User:** {after} (`{after.id}`)\n"
            f"**By:** {moderator}\n"
            f"**Before:** {old_nick}\n"
            f"**After:** {new_nick}"
        )

    # --- TIMEOUT ---
    if before.communication_disabled_until != after.communication_disabled_until:
        entry = await find_audit_actor(after.guild, discord.AuditLogAction.member_update, after.id, seconds_window=25)
        moderator = f"{entry.user} (`{entry.user.id}`)" if entry and entry.user else "Unknown"

        if after.communication_disabled_until:
            await send_log(
                after.guild,
                "⏳ Timeout Applied/Updated",
                f"**User:** {after} (`{after.id}`)\n"
                f"**By:** {moderator}\n"
                f"**Until:** {discord.utils.format_dt(after.communication_disabled_until, style='F')}"
            )
        else:
            await send_log(
                after.guild,
                "✅ Timeout Removed",
                f"**User:** {after} (`{after.id}`)\n"
                f"**By:** {moderator}"
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
