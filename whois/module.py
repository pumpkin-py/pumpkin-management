from typing import Optional, Union

import discord
import re

from discord.ext import commands

from core import check, logger, text, utils
from database.acl import ACL_group
from ..verify.database import VerifyMember
from ..verify.enums import VerifyStatus


tr = text.Translator(__file__).translate
guild_log = logger.Guild.logger()


class Whois(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.guild_only()
    @commands.check(check.acl)
    @commands.command()
    async def roleinfo(self, ctx, role: discord.Role):
        acl_group: Optional[ACL_group] = ACL_group.get_by_role(
            guild_id=ctx.guild.id, role_id=role.id
        )

        embed = utils.Discord.create_embed(
            author=ctx.author,
            title=role.name,
            description=role.id,
        )
        embed.add_field(
            name=tr("roleinfo", "member count", ctx),
            value=f"{len(role.members)}",
        )
        embed.add_field(
            name=tr("roleinfo", "mentionable", ctx),
            value=tr("roleinfo", f"{role.mentionable}", ctx),
        )
        if acl_group is not None:
            embed.add_field(
                name=tr("roleinfo", "ACL group", ctx),
                value=acl_group.name,
            )
        await ctx.reply(embed=embed)

    @commands.guild_only()
    @commands.check(check.acl)
    @commands.command()
    async def channelinfo(self, ctx, channel: discord.TextChannel):
        if ctx.author not in channel.members:
            ctx.reply(tr("channelinfo", "not permitted", ctx))
            return

        webhook_count = len(await channel.webhooks())
        role_count: int = 0
        user_count: int = 0
        for overwrite in channel.overwrites:
            if isinstance(overwrite, discord.Role):
                role_count += 1
            else:
                user_count += 1

        topic: str = f"{channel.topic}\n" if channel.topic else ""
        embed = utils.Discord.create_embed(
            author=ctx.author,
            title=f"#{channel.name}",
            description=f"{topic}{channel.id}",
        )

        if role_count:
            embed.add_field(
                name=tr("channelinfo", "role count", ctx),
                value=f"{role_count}",
            )
        if user_count:
            embed.add_field(
                name=tr("channelinfo", "user count", ctx),
                value=f"{user_count}",
            )
        if webhook_count:
            embed.add_field(
                name=tr("channelinfo", "webhook count", ctx),
                value=f"{webhook_count}",
            )
        await ctx.reply(embed=embed)
        
    @commands.guild_only()
    @commands.check(check.acl)
    @commands.command()
    async def rwhois(self, ctx, member):

        db_member: Optional[VerifyMember]
        dc_member: Optional[discord.Member] = None
        
        db_member = VerifyMember.get_by_address(ctx.guild.id, member)
        
        if db_member is not None:
            dc_member = ctx.guild.get_member(db_member.user_id)

        if db_member is None and dc_member is None:
            await ctx.reply(tr("whois", "none", ctx))
            return

        whois_reply(ctx, db_member, dc_member)
    

    @commands.guild_only()
    @commands.check(check.acl)
    @commands.command()
    async def whois(self, ctx, member: Union[discord.Member, int]):
        dc_member: Optional[discord.Member] = None
        user_id: Optional[int] = None

        if type(member) == discord.Member:
            user_id = member.id
            dc_member = member
        elif type(member) == int:
            user_id = member

        db_member: Optional[VerifyMember]
        db_member = VerifyMember.get_by_member(ctx.guild.id, user_id)

        if db_member is not None and dc_member is None:
            dc_member = ctx.guild.get_member(db_member.user_id)

        if db_member is None and dc_member is None:
            await ctx.reply(tr("whois", "none", ctx))
            return
            
        whois_reply(ctx, db_member, dc_member)

    def whois_reply(ctx, db_member, dc_member):
        description: str
        if dc_member is not None:
            description = f"{dc_member.name} ({dc_member.id})"
        else:
            description = f"{db_member.user_id}"

        embed = utils.Discord.create_embed(
            author=ctx.author,
            title=tr("whois", "title", ctx),
            description=description,
        )

        if db_member is not None:
            embed.add_field(
                name=tr("whois", "address", ctx),
                value=db_member.address,
                inline=False,
            )
            embed.add_field(
                name=tr("whois", "code", ctx),
                value=f"`{db_member.code}`",
            )
            embed.add_field(
                name=tr("whois", "status", ctx),
                value=f"{VerifyStatus(db_member.status).name}",
            )
            embed.add_field(
                name=tr("whois", "timestamp", ctx),
                value=utils.Time.datetime(db_member.timestamp),
                inline=False,
            )
        if dc_member is not None:
            avatar_url: str = dc_member.display_avatar.replace(size=256).url
            embed.set_thumbnail(url=avatar_url)

            if dc_member.roles:
                roles: str = ", ".join(list(r.name for r in dc_member.roles[::-1][:-1]))
                embed.add_field(
                    name=tr("whois", "roles", ctx),
                    value=roles if roles else tr("whois", "no roles"),
                )

        await ctx.reply(embed=embed)

        await guild_log.info(ctx.author, ctx.channel, f"Whois lookup for {member}.")
        


def setup(bot) -> None:
    bot.add_cog(Whois(bot))
