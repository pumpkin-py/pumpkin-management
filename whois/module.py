from typing import Optional

import discord
from discord.ext import commands

from core import acl, text, utils
from database.acl import ACL_group


tr = text.Translator(__file__).translate


class Whois(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.guild_only()
    @commands.check(acl.check)
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
    @commands.check(acl.check)
    @commands.command()
    async def channelinfo(self, ctx, channel: discord.TextChannel):
        if ctx.author not in channel.members:
            ctx.reply("channelinfo", "not permitted", ctx)
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


def setup(bot) -> None:
    bot.add_cog(Whois(bot))
