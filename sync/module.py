import json
import re
from typing import Dict, Optional, List

import discord
from discord.ext import commands

from core import check, i18n, logger, utils

from .database import Link, Satellite

_ = i18n.Translator("modules/mgmt").translate
guild_log = logger.Guild.logger()


class Sync(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.guild_only()
    @commands.check(check.acl)
    @commands.group(name="sync")
    async def sync(self, ctx):
        await utils.Discord.send_help(ctx)

    @commands.check(check.acl)
    @sync.command(name="me")
    async def sync_me(self, ctx):
        await utils.Discord.delete_message(ctx.message)

        link: Optional[Link] = Link.get_by_satellite(ctx.guild.id)
        if not link:
            await ctx.send(
                _(ctx, "{mention} This server is not a satellite.").format(
                    mention=ctx.author.mention
                ),
                delete_after=120,
            )
            return
        satellite: Optional[Satellite] = Satellite.get(ctx.guild.id)
        if not satellite:
            await ctx.send(
                _(ctx, "{mention} This server is not a satellite.").format(
                    mention=ctx.author.mention
                ),
                delete_after=120,
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
                _(ctx, "{mention} I could not contact the main server.").format(
                    mention=ctx.author.mention
                ),
                delete_after=120,
            )
            return
        main_member: Optional[discord.Member] = main_guild.get_member(ctx.author.id)
        if not main_member:
            await ctx.send(
                _(ctx, "{mention} You are not on the main server.").format(
                    mention=ctx.author.mention
                ),
                delete_after=120,
            )
            return

        roles = await self._get_satellite_roles(ctx, main_member, satellite.data)
        if not roles:
            await ctx.send(
                _(
                    ctx,
                    "{mention} You don't have any synchronizable roles on the main server.",
                ).format(mention=ctx.author.mention),
                delete_after=120,
            )
            return

        await guild_log.info(
            ctx.author,
            ctx.channel,
            f"Sync: Adding roles {', '.join(r.name for r in roles)}.",
        )
        await ctx.author.add_roles(*roles)
        await ctx.send(
            _(ctx, "{mention} I've added **{count}** new roles to you.").format(
                mention=ctx.author.mention, count=len(roles)
            ),
            delete_after=120,
        )

    async def _get_satellite_roles(
        self,
        ctx: commands.Context,
        main_member: discord.Member,
        mapping: Dict[str, int],
    ) -> List[discord.Role]:
        roles: List[discord.Role] = []
        for role in main_member.roles:
            for role_from, role_to in mapping.items():
                if str(role.id) != role_from:
                    continue
                satellite_role = ctx.guild.get_role(role_to)
                if not satellite_role:
                    await guild_log.error(
                        ctx.author,
                        ctx.channel,
                        f"Could not find sync role '{role_to}' "
                        f"on server '{main_member.guild.name}'.",
                    )
                    continue
                roles.append(satellite_role)
        return roles

    @commands.check(check.acl)
    @sync.command(name="list")
    async def sync_list(self, ctx):
        satellite: Optional[Link] = Link.get_by_satellite(satellite_id=ctx.guild.id)
        satellites: List[Link] = Link.get_all(guild_id=ctx.guild.id)

        embed = utils.Discord.create_embed(
            author=ctx.author,
            title=_(ctx, "Synchronizations"),
        )
        if satellite:
            guild = self.bot.get_guild(satellite.guild_id)
            embed.add_field(
                name=_(ctx, "This server is a satellite of"),
                value=getattr(guild, "name", _(ctx, "not found"))
                + f"\n{satellite.guild_id}",
                inline=False,
            )

        if satellites:
            guilds = [self.bot.get_guild(s.guild_id) for s in satellites]
            for link, guild in zip(satellites, guilds):
                embed.add_field(
                    name=_(ctx, "Satellite of this server"),
                    value=getattr(guild, "name", _(ctx, "not found"))
                    + f"\n{link.satellite_id}",
                )

        if not (satellite or satellites):
            embed.add_field(
                name=_(ctx, "Disabled"),
                value=_(ctx, "This server is not synchronized."),
            )

        await ctx.reply(embed=embed)

    @commands.check(check.acl)
    @sync.command(name="add")
    async def sync_add(self, ctx, guild_id: int):
        satellite: Optional[discord.Guild] = None
        for guild in self.bot.guilds:
            if guild.id == guild_id:
                satellite = guild
                break
        else:
            await ctx.reply(_(ctx, "I'm not on that server."))
            return

        try:
            Link.add(guild_id=ctx.guild.id, satellite_id=guild_id)
        except ValueError:
            await ctx.reply(_(ctx, "That server is already a satellite."))
            return

        await ctx.reply(_(ctx, "I've sucessfully registered the new satellite."))
        await guild_log.info(
            ctx.author,
            ctx.channel,
            f"Added sync satellite '{satellite.name}' ({guild_id}).",
        )

    @commands.check(check.acl)
    @sync.command(name="remove")
    async def sync_remove(self, ctx, guild_id: int):
        link = Link.get_by_satellite(satellite_id=guild_id)
        if link is None or link.guild_id != ctx.guild.id:
            await ctx.reply(_(ctx, "That server is not synchronized."))
            return

        Link.remove(guild_id=ctx.guild.id, satellite_id=guild_id)
        await ctx.reply(_(ctx, "Satellite has been sucessfully removed."))

        guild_name: str = getattr(self.bot.get_guild(guild_id), "name", "???")
        await guild_log.info(
            ctx.author,
            ctx.channel,
            f"Removed sync satellite '{guild_name}' ({guild_id}).",
        )

    @commands.check(check.acl)
    @commands.group(name="satellite")
    async def satellite_(self, ctx):
        await utils.Discord.send_help(ctx)

    @commands.check(check.acl)
    @satellite_.command(name="template")
    async def satellite_template(self, ctx):
        template = {
            "mapping": {
                "0123456789": 9876543210,
                "1234567890": 8765432109,
            }
        }
        help_text = _(
            ctx,
            (
                "Values on the left are IDs of roles on the main server, "
                "values on the right are role IDs on the satellite."
            ),
        )
        text = (
            f"{help_text} ```json\n"
            + f"{json.dumps(template, ensure_ascii=False, indent=4)}\n```"
        )

        await ctx.reply(text)

    @commands.check(check.acl)
    @satellite_.command(name="get")
    async def satellite_get(self, ctx):
        embed = utils.Discord.create_embed(
            author=ctx.author, title=_(ctx, "Satellite information")
        )

        link = Link.get_by_satellite(satellite_id=ctx.guild.id)
        if link:
            main_guild: Optional[discord.Guild] = self.bot.get_guild(link.guild_id)
            embed.add_field(
                name=_(ctx, "Main server"),
                value=getattr(main_guild, "name", f"{link.guild_id}"),
                inline=False,
            )
        else:
            embed.add_field(
                name=_(ctx, "Disabled"),
                value=_(ctx, "This server is not synchronized."),
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
                name=_(ctx, "Role mapping"),
                value=result[:512],
                inline=False,
            )
        else:
            embed.add_field(
                name=_(ctx, "Role mapping"),
                value=_(ctx, "There are no mapped roles"),
                inline=False,
            )

        await ctx.reply(embed=embed)

    @commands.check(check.acl)
    @satellite_.command(name="set")
    async def satellite_set(self, ctx, *, data: str):
        try:
            satellite_data = json.loads(
                re.search(r"```([^\s]+)?([^`]*)```", ctx.message.content, re.M).group(2)
            )
        except (AttributeError, json.decoder.JSONDecodeError):
            await ctx.reply(_(ctx, r"I'm expecting JSON data enclosed in \`\`\`."))
            return

        if "mapping" not in satellite_data:
            await ctx.reply(_(ctx, "JSON must include dictionary `mapping`."))
            return

        try:
            for key, value in satellite_data["mapping"].items():
                int(key)
                int(value)
        except ValueError as exc:
            await ctx.reply(
                _(ctx, "Error while decoding: `{error}`.").format(error=str(exc))
            )
            return

        Satellite.add(ctx.guild.id, satellite_data["mapping"])

        await guild_log.info(ctx.author, ctx.channel, "Satellite enabled.")
        await ctx.reply(_(ctx, "Satellite has been sucessfully constructed."))

    @commands.check(check.acl)
    @satellite_.command(name="unset")
    async def satellite_unset(self, ctx):
        deleted: int = Satellite.remove(ctx.guild.id)
        if not deleted:
            await ctx.reply(_(ctx, "This server does not have any satellite mapping."))
            return

        await guild_log.info(ctx.author, ctx.channel, "Satellite disabled.")
        await ctx.reply(_(ctx, "Satellite has been deconstructed."))


def setup(bot) -> None:
    bot.add_cog(Sync(bot))
