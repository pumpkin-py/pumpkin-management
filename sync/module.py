import json
import re
from typing import Optional, List

import discord
from discord.ext import commands

from core import acl, logging, text, utils

from .database import Link, Satellite

tr = text.Translator(__file__).translate
guild_log = logging.Guild.logger()


class Sync(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.guild_only()
    @commands.check(acl.check)
    @commands.group(name="sync")
    async def sync(self, ctx):
        await utils.Discord.send_help(ctx)

    @commands.check(acl.check)
    @sync.command(name="list")
    async def sync_list(self, ctx):
        satellite: Optional[Link] = Link.get_by_satellite(satellite_id=ctx.guild.id)
        satellites: List[Link] = Link.get_all(guild_id=ctx.guild.id)

        embed = utils.Discord.create_embed(
            author=ctx.author,
            title=tr("sync list", "title", ctx),
        )
        if satellite:
            guild = self.bot.get_guild(satellite.guild_id)
            embed.add_field(
                name=tr("sync list", "is satellite", ctx),
                value=getattr(guild, "name", tr("sync list", "not found", ctx))
                + f"\n{satellite.guild_id}",
                inline=False,
            )

        if satellites:
            guilds = [self.bot.get_guild(s.guild_id) for s in satellites]
            for link, guild in zip(satellites, guilds):
                embed.add_field(
                    name=tr("sync list", "satellite", ctx),
                    value=getattr(guild, "name", tr("sync list", "not found", ctx))
                    + f"\n{link.satellite_id}",
                )

        if not (satellite or satellites):
            embed.add_field(
                name=tr("sync list", "nothing", ctx),
                value=tr("sync list", "no satellites", ctx),
            )

        await ctx.reply(embed=embed)

    @commands.check(acl.check)
    @sync.command(name="add")
    async def sync_add(self, ctx, guild_id: int):
        satellite: Optional[discord.Guild] = None
        for guild in self.bot.guilds:
            if guild.id == guild_id:
                satellite = guild
                break
        else:
            await ctx.reply(tr("sync add", "not in guild", ctx))
            return

        try:
            Link.add(guild_id=ctx.guild.id, satellite_id=guild_id)
        except ValueError:
            await ctx.reply(tr("sync add", "already satellite", ctx))
            return

        await ctx.reply(tr("sync add", "reply", ctx))
        await guild_log.info(
            ctx.author,
            ctx.channel,
            f"Added sync satellite '{satellite.name}' ({guild_id}).",
        )

    @commands.check(acl.check)
    @sync.command(name="remove")
    async def sync_remove(self, ctx, guild_id: int):
        link = Link.get_by_satellite(satellite_id=guild_id)
        if link is None or link.guild_id != ctx.guild.id:
            await ctx.reply(tr("sync remove", "not linked", ctx))
            return

        Link.remove(guild_id=ctx.guild.id, satellite_id=guild_id)
        await ctx.reply(tr("sync remove", "reply", ctx))

        guild_name: str = getattr(self.bot.get_guild(guild_id), "name", "???")
        await guild_log.info(
            ctx.author,
            ctx.channel,
            f"Removed sync satellite '{guild_name}' ({guild_id}).",
        )

    @commands.check(acl.check)
    @commands.group(name="satellite")
    async def satellite_(self, ctx):
        await utils.Discord.send_help(ctx)

    @commands.check(acl.check)
    @satellite_.command(name="template")
    async def satellite_template(self, ctx):
        template = {
            "mapping": {
                "0123456789": 9876543210,
                "1234567890": 8765432109,
            }
        }
        text = (
            f'{tr("satellite template", "text", ctx)} ```json\n'
            + f"{json.dumps(template, ensure_ascii=False, indent=4)}\n```"
        )

        await ctx.reply(text)

    @commands.check(acl.check)
    @satellite_.command(name="set")
    async def satellite_set(self, ctx, *, data: str):
        try:
            satellite_data = json.loads(
                re.search(r"```([^\s]+)?([^`]*)```", ctx.message.content, re.M).group(2)
            )
        except (AttributeError, json.decoder.JSONDecodeError):
            await ctx.reply(tr("satellite set", "no json", ctx))
            return

        if "mapping" not in satellite_data.keys():
            await ctx.reply(tr("satellite set", "bad json", ctx))
            return

        # TODO Add to private variable
        # TODO Add on_member_update & on_member_join listeners

    @commands.check(acl.check)
    @satellite_.command(name="unset")
    async def satellite_unset(self, ctx):
        deleted: int = Satellite.remove(ctx.guild.id)
        if not deleted:
            await ctx.reply(tr("satellite unset", "nothing", ctx))
            return
        await ctx.reply(tr("satellite unset", "reply", ctx))


def setup(bot) -> None:
    bot.add_cog(Sync(bot))
