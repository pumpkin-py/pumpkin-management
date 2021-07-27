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
    @sync.command(name="me")
    async def sync_me(self, ctx):
        link: Optional[Link] = Link.get_by_satellite(ctx.guild.id)
        if not link:
            await ctx.send(
                tr("sync me", "not satellite", ctx, mention=ctx.author.mention)
            )
            return
        satellite: Optional[Satellite] = Satellite.get(ctx.guild.id)
        if not satellite:
            await ctx.send(
                tr("sync me", "not satellite", ctx, mention=ctx.author.mention)
            )
            return
        main_guild: Optional[discord.Guild] = self.bot.get_guild(link.guild_id)
        if not main_guild:
            await guild_log.error(
                ctx.author,
                ctx.channel,
                f"Cannot sync, main guild '{link.guild_id}' not found.",
            )
            await ctx.send(
                tr("sync me", "no main guild", ctx, mention=ctx.author.mention)
            )
            return
        main_member: Optional[discord.Member] = main_guild.get_member(ctx.author.id)
        if not main_member:
            await ctx.send(
                tr("sync me", "not in main guild", ctx, mention=ctx.author.mention)
            )
            return

        roles: List[discord.Role] = []
        for role in main_member.roles:
            for role_from, role_to in satellite.data.items():
                if str(role.id) != role_from:
                    continue
                role = ctx.guild.get_role(role_to)
                if not role:
                    await guild_log.error(
                        ctx.author,
                        ctx.channel,
                        f"Could not find sync role '{role_to}' "
                        f"on server '{main_guild.name}'.",
                    )
                    continue
                roles.append(role)
        if not roles:
            await ctx.send(
                tr("sync me", "no sync roles", ctx, mention=ctx.author.mention)
            )
            return

        await guild_log.info(
            ctx.author,
            ctx.channel,
            f"Sync: Adding roles {', '.join(r.name for r in roles)}.",
        )
        await ctx.author.add_roles(*roles)
        await ctx.send(
            tr("sync me", "reply", ctx, mention=ctx.author.mention, count=len(roles))
        )

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
    @satellite_.command(name="get")
    async def satellite_get(self, ctx):
        embed = utils.Discord.create_embed(
            author=ctx.author, title=tr("satellite get", "title", ctx)
        )

        link = Link.get_by_satellite(satellite_id=ctx.guild.id)
        if link:
            main_guild: Optional[discord.Guild] = self.bot.get_guild(link.guild_id)
            embed.add_field(
                name=tr("satellite get", "main guild", ctx),
                value=getattr(main_guild, "name", f"{link.guild_id}"),
                inline=False,
            )
        else:
            embed.add_field(
                name=tr("satellite get", "nothing", ctx),
                value=tr("satellite get", "not satellite", ctx),
                inline=False,
            )
        satellite: Optional[Satellite] = Satellite.get(ctx.guild.id)
        if satellite and satellite.data.keys():
            result: str = ""
            for role_from_id, role_to_id in satellite.data.items():
                role_from = main_guild.get_role(int(role_from_id))
                role_to = ctx.guild.get_role(role_to_id)
                role_from_str = getattr(role_from, "name", f"`{role_from_id}`")
                role_to_str = getattr(role_to, "name", f"`{role_to_id}`")
                result += f"{role_from_str} → {role_to_str}\n"
            embed.add_field(
                name=tr("satellite get", "mapping", ctx),
                value=result[:512],
                inline=False,
            )
        else:
            embed.add_field(
                name=tr("satellite get", "mapping", ctx),
                value=tr("satellite get", "no mapping", ctx),
                inline=False,
            )

        await ctx.reply(embed=embed)

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

        if "mapping" not in satellite_data:
            await ctx.reply(tr("satellite set", "bad json", ctx))
            return

        try:
            for key, value in satellite_data["mapping"].items():
                _, _ = int(key), int(value)
        except ValueError as exc:
            await ctx.reply(tr("satellite set", "broken json", ctx, error=str(exc)))
            return

        Satellite.add(ctx.guild.id, satellite_data["mapping"])

        await guild_log.info(ctx.author, ctx.channel, "Satellite enabled.")
        await ctx.reply(tr("satellite set", "reply", ctx))

    @commands.check(acl.check)
    @satellite_.command(name="unset")
    async def satellite_unset(self, ctx):
        deleted: int = Satellite.remove(ctx.guild.id)
        if not deleted:
            await ctx.reply(tr("satellite unset", "nothing", ctx))
            return

        await guild_log.info(ctx.author, ctx.channel, "Satellite disabled.")
        await ctx.reply(tr("satellite unset", "reply", ctx))


def setup(bot) -> None:
    bot.add_cog(Sync(bot))
