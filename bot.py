import asyncio
import os
import re
import time
from collections import defaultdict, deque
from datetime import timedelta
from typing import Optional

import aiosqlite
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN", "")
LOG_CHANNEL_ID = int(os.getenv("LOG_CHANNEL_ID", "0") or 0)
QUARANTINE_ROLE_ID = int(os.getenv("QUARANTINE_ROLE_ID", "0") or 0)
DB_PATH = os.getenv("DB_PATH", "security_bot.db")
MOD_ROLE_IDS = {int(x.strip()) for x in os.getenv("MOD_ROLE_IDS", "").split(",") if x.strip().isdigit()}

INVITE_RE = re.compile(r"(?:discord(?:\.gg|(?:app)?\.com/invite)/|invite\.gg/)[\w-]+", re.I)
URL_RE = re.compile(r"https?://\S+", re.I)

intents = discord.Intents.default()
intents.members = True
intents.message_content = True

class SecurityBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)
        self.db: Optional[aiosqlite.Connection] = None
        self.message_windows = defaultdict(deque)
        self.join_windows = defaultdict(deque)
        self.warn_locks = defaultdict(asyncio.Lock)

    async def setup_hook(self):
        self.db = await aiosqlite.connect(DB_PATH)
        await self.db.execute("""CREATE TABLE IF NOT EXISTS warnings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
            moderator_id INTEGER NOT NULL, reason TEXT NOT NULL,
            created_at INTEGER NOT NULL
        )""")
        await self.db.commit()
        await self.tree.sync()

    async def close(self):
        if self.db:
            await self.db.close()
        await super().close()

bot = SecurityBot()

async def send_log(guild: discord.Guild, title: str, description: str, color=discord.Color.blurple()):
    channel = guild.get_channel(LOG_CHANNEL_ID) if LOG_CHANNEL_ID else None
    if not isinstance(channel, discord.TextChannel):
        return
    embed = discord.Embed(title=title, description=description, color=color, timestamp=discord.utils.utcnow())
    try:
        await channel.send(embed=embed)
    except discord.HTTPException:
        pass

def is_mod(member: discord.Member) -> bool:
    if member.guild_permissions.manage_guild or member.guild_permissions.moderate_members:
        return True
    return any(role.id in MOD_ROLE_IDS for role in member.roles)

def can_target(actor: discord.Member, target: discord.Member) -> bool:
    return target != actor and target != actor.guild.owner and actor.top_role > target.top_role

async def add_warning(guild_id: int, user_id: int, moderator_id: int, reason: str) -> int:
    async with bot.warn_locks[(guild_id, user_id)]:
        await bot.db.execute("INSERT INTO warnings(guild_id,user_id,moderator_id,reason,created_at) VALUES(?,?,?,?,?)",
                             (guild_id, user_id, moderator_id, reason, int(time.time())))
        await bot.db.commit()
        cursor = await bot.db.execute("SELECT COUNT(*) FROM warnings WHERE guild_id=? AND user_id=?", (guild_id, user_id))
        row = await cursor.fetchone()
        await cursor.close()
        return int(row[0])

async def warning_count(guild_id: int, user_id: int) -> int:
    cursor = await bot.db.execute("SELECT COUNT(*) FROM warnings WHERE guild_id=? AND user_id=?", (guild_id, user_id))
    row = await cursor.fetchone()
    await cursor.close()
    return int(row[0])

async def punish_spammer(message: discord.Message, reason: str):
    member = message.author
    try:
        await message.delete()
    except discord.HTTPException:
        pass
    if isinstance(member, discord.Member) and member.guild.me and member.guild.me.guild_permissions.moderate_members and can_target(member.guild.me, member):
        try:
            await member.timeout(timedelta(minutes=10), reason=reason)
        except discord.HTTPException:
            pass
    await send_log(message.guild, "자동 보안 조치", f"대상: {member.mention}\n사유: {reason}", discord.Color.orange())

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (id={bot.user.id})")

@bot.event
async def on_member_join(member: discord.Member):
    now = time.monotonic()
    bucket = bot.join_windows[member.guild.id]
    bucket.append(now)
    while bucket and now - bucket[0] > 20:
        bucket.popleft()
    account_age = discord.utils.utcnow() - member.created_at
    if account_age < timedelta(days=3):
        role = member.guild.get_role(QUARANTINE_ROLE_ID) if QUARANTINE_ROLE_ID else None
        if role and member.guild.me and member.guild.me.top_role > role:
            try:
                await member.add_roles(role, reason="신규 계정 자동 격리")
            except discord.HTTPException:
                pass
        await send_log(member.guild, "신규 계정 감지", f"대상: {member.mention}\n계정 생성: <t:{int(member.created_at.timestamp())}:R>", discord.Color.orange())
    if len(bucket) >= 8:
        await send_log(member.guild, "입장 급증 감지", f"최근 20초 동안 {len(bucket)}명이 입장했습니다.", discord.Color.red())

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild or not isinstance(message.author, discord.Member):
        return
    if not is_mod(message.author):
        now = time.monotonic()
        bucket = bot.message_windows[(message.guild.id, message.author.id)]
        bucket.append(now)
        while bucket and now - bucket[0] > 8:
            bucket.popleft()
        if len(bucket) >= 6:
            await punish_spammer(message, "8초 내 과도한 메시지 전송")
            bucket.clear()
            return
        if len(message.mentions) >= 6:
            await punish_spammer(message, "과도한 멘션 도배")
            return
        if INVITE_RE.search(message.content):
            await punish_spammer(message, "허용되지 않은 Discord 초대 링크")
            return
        # 흔한 피싱 패턴은 삭제하고 로그만 남깁니다. 오탐 방지를 위해 일반 URL은 차단하지 않습니다.
        lowered = message.content.lower()
        if URL_RE.search(lowered) and any(x in lowered for x in ("nitro-free", "free-nitro", "discord-gift", "claim-reward")):
            await punish_spammer(message, "의심스러운 보상/니트로 피싱 링크")
            return
    await bot.process_commands(message)

async def require_mod(interaction: discord.Interaction) -> bool:
    if not isinstance(interaction.user, discord.Member) or not is_mod(interaction.user):
        await interaction.response.send_message("운영진 권한이 필요합니다.", ephemeral=True)
        return False
    return True

@bot.tree.command(name="warn", description="멤버에게 경고를 추가합니다.")
@app_commands.describe(member="경고 대상", reason="경고 사유")
async def warn(interaction: discord.Interaction, member: discord.Member, reason: str):
    if not await require_mod(interaction): return
    if not can_target(interaction.user, member):
        await interaction.response.send_message("자신과 같거나 높은 역할의 멤버는 제재할 수 없습니다.", ephemeral=True); return
    count = await add_warning(interaction.guild.id, member.id, interaction.user.id, reason)
    await interaction.response.send_message(f"{member.mention}에게 경고를 추가했습니다. 누적 경고: **{count}회**")
    await send_log(interaction.guild, "경고 추가", f"대상: {member.mention}\n운영진: {interaction.user.mention}\n사유: {reason}\n누적: {count}회", discord.Color.yellow())

@bot.tree.command(name="warnings", description="멤버의 누적 경고 수를 확인합니다.")
async def warnings(interaction: discord.Interaction, member: discord.Member):
    if not await require_mod(interaction): return
    count = await warning_count(interaction.guild.id, member.id)
    await interaction.response.send_message(f"{member.mention}의 누적 경고는 **{count}회**입니다.", ephemeral=True)

@bot.tree.command(name="timeout", description="멤버를 타임아웃합니다.")
@app_commands.describe(member="대상", minutes="분 단위 시간", reason="사유")
async def timeout(interaction: discord.Interaction, member: discord.Member, minutes: app_commands.Range[int, 1, 40320], reason: str = "운영진 조치"):
    if not await require_mod(interaction): return
    if not can_target(interaction.user, member):
        await interaction.response.send_message("해당 멤버를 제재할 수 없습니다.", ephemeral=True); return
    await member.timeout(timedelta(minutes=minutes), reason=reason)
    await interaction.response.send_message(f"{member.mention}을(를) {minutes}분 타임아웃했습니다.")
    await send_log(interaction.guild, "타임아웃", f"대상: {member.mention}\n운영진: {interaction.user.mention}\n사유: {reason}", discord.Color.orange())

@bot.tree.command(name="kick", description="멤버를 추방합니다.")
async def kick(interaction: discord.Interaction, member: discord.Member, reason: str = "운영진 조치"):
    if not await require_mod(interaction): return
    if not can_target(interaction.user, member):
        await interaction.response.send_message("해당 멤버를 추방할 수 없습니다.", ephemeral=True); return
    await member.kick(reason=reason)
    await interaction.response.send_message(f"{member}을(를) 추방했습니다.")
    await send_log(interaction.guild, "멤버 추방", f"대상: {member}\n운영진: {interaction.user.mention}\n사유: {reason}", discord.Color.red())

@bot.tree.command(name="ban", description="멤버를 차단합니다.")
async def ban(interaction: discord.Interaction, member: discord.Member, reason: str = "운영진 조치"):
    if not await require_mod(interaction): return
    if not can_target(interaction.user, member):
        await interaction.response.send_message("해당 멤버를 차단할 수 없습니다.", ephemeral=True); return
    await member.ban(reason=reason, delete_message_seconds=86400)
    await interaction.response.send_message(f"{member}을(를) 차단했습니다.")
    await send_log(interaction.guild, "멤버 차단", f"대상: {member}\n운영진: {interaction.user.mention}\n사유: {reason}", discord.Color.dark_red())

@bot.tree.command(name="purge", description="현재 채널의 최근 메시지를 삭제합니다.")
@app_commands.describe(amount="삭제할 메시지 수")
async def purge(interaction: discord.Interaction, amount: app_commands.Range[int, 1, 100]):
    if not await require_mod(interaction): return
    if not isinstance(interaction.channel, discord.TextChannel):
        await interaction.response.send_message("텍스트 채널에서만 사용할 수 있습니다.", ephemeral=True); return
    await interaction.response.defer(ephemeral=True)
    deleted = await interaction.channel.purge(limit=amount)
    await interaction.followup.send(f"메시지 {len(deleted)}개를 삭제했습니다.", ephemeral=True)
    await send_log(interaction.guild, "메시지 일괄 삭제", f"채널: {interaction.channel.mention}\n운영진: {interaction.user.mention}\n삭제: {len(deleted)}개", discord.Color.orange())

@bot.tree.command(name="security", description="보안봇 상태와 설정을 확인합니다.")
async def security(interaction: discord.Interaction):
    if not await require_mod(interaction): return
    embed = discord.Embed(title="보안봇 상태", color=discord.Color.green())
    embed.add_field(name="자동 감지", value="스팸 · 멘션 도배 · 초대 링크 · 피싱 패턴", inline=False)
    embed.add_field(name="신규 계정 격리", value="활성" if QUARANTINE_ROLE_ID else "비활성", inline=True)
    embed.add_field(name="로그 채널", value=f"<#{LOG_CHANNEL_ID}>" if LOG_CHANNEL_ID else "미설정", inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)

if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("DISCORD_TOKEN이 설정되지 않았습니다. .env 파일을 확인하세요.")
    bot.run(TOKEN)
