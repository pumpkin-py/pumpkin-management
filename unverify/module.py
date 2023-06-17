from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

import dateutil.parser
import discord
import pie.database.config
from discord import Guild, Member
from discord.errors import NotFound
from discord.ext import commands, tasks
from discord.ext.commands.bot import Bot
from pie import check, i18n, logger, utils, acl

from .database import UnverifyGuildConfig, UnverifyItem, UnverifyStatus, UnverifyType

_ = i18n.Translator("modules/mgmt").translate
bot_log = logger.Bot.logger()
guild_log = logger.Guild.logger()
config = pie.database.config.Config.get()


class Unverify(commands.Cog):
    def __init__(self, bot: Bot):
        self.bot = bot
        self.reverifier.start()

    def cog_unload(self):
        self.reverifier.cancel()

    @tasks.loop(seconds=30.0)
    async def reverifier(self):
        max_end_time = datetime.now() + timedelta(seconds=30)
        min_last_check = datetime.now() - timedelta(hours=1)
        items = UnverifyItem.get_items(
            status=UnverifyStatus.waiting,
            max_end_time=max_end_time,
            min_last_check=min_last_check,
        )
        if items is not None:
            for item in items:
                await self._reverify_user(item)

    @reverifier.before_loop
    async def before_reverifier(self):
        print("Reverify loop waiting until ready().")
        await self.bot.wait_until_ready()

    async def _get_guild(self, item: UnverifyItem) -> Optional[Guild]:
        guild = self.bot.get_guild(item.guild_id)

        if guild is None:
            if item.status != UnverifyStatus.guild_not_found:
                await bot_log.warning(
                    None,
                    None,
                    f"Reverify failed: Guild ({item.guild_id}) was not found. "
                    + "Setting status to `guild could not be found`",
                )
                item.status = UnverifyStatus.guild_not_found
            item.last_check = datetime.now()
            item.save()
            await bot_log.warning(
                None,
                None,
                f"Reverify failed: Guild ({item.guild_id}) still was not found.",
            )
            return None
        return guild

    @staticmethod
    async def _get_member(guild: Guild, item: UnverifyItem) -> Optional[Member]:
        member = guild.get_member(item.user_id)

        if member is None:
            try:
                member = await guild.fetch_member(item.user_id)
            except NotFound:
                if item.status != UnverifyStatus.member_left:
                    await guild_log.warning(
                        None,
                        guild,
                        f"Reverify failed: Member ({item.user_id}) was not found. "
                        + "Setting status to `member left server`.",
                    )
                    item.status = UnverifyStatus.member_left
                    item.save()
                item.last_check = datetime.now()
                item.save()
                return None
        return member

    @staticmethod
    async def _return_roles(member: Member, item: UnverifyItem):
        for role_id in item.roles_to_return:
            role = discord.utils.get(member.guild.roles, id=role_id)
            if role is not None:
                try:
                    await member.add_roles(role, reason="Reverify", atomic=True)
                except discord.errors.Forbidden:
                    await guild_log.warning(
                        None,
                        member.guild,
                        f"Returning role {role.name} to {member.name} ({member.id}) failed. "
                        + "Insufficient permissions.",
                    )
            else:
                await guild_log.warning(
                    None,
                    member.guild,
                    f"Role with ID {role_id} could not be found.",
                )

    @staticmethod
    async def _return_channels(member: Member, item: UnverifyItem):
        for channel_id in item.channels_to_return:
            channel = discord.utils.get(member.guild.channels, id=channel_id)
            if channel is not None:
                user_overw = discord.PermissionOverwrite(read_messages=True)
                try:
                    await channel.set_permissions(
                        member, overwrite=user_overw, reason="Reverify"
                    )
                except discord.errors.Forbidden:
                    await guild_log.warning(
                        None,
                        member.guild,
                        f"Could not add {member.name} ({member.id}) to {channel.name}. "
                        + "Insufficient permissions.",
                    )
            else:
                await guild_log.warning(
                    None,
                    member.guild,
                    f"Could not add {member.name} ({member.id}) to {channel.name}. "
                    + "Channel doesn't exist.",
                )

    @staticmethod
    async def _remove_temp_channels(member: Member, item: UnverifyItem):
        for channel_id in item.channels_to_remove:
            channel = discord.utils.get(member.guild.channels, id=channel_id)
            if channel is not None:
                user_overw = discord.PermissionOverwrite(read_messages=None)
                try:
                    await channel.set_permissions(
                        member, overwrite=user_overw, reason="Reverify"
                    )
                except discord.errors.Forbidden:
                    await guild_log.warning(
                        None,
                        member.guild,
                        f"Could not remove {member.name} ({member.id}) "
                        + f"from {channel.name}. Insufficient permissions.",
                    )
            else:
                await guild_log.warning(
                    None,
                    member.guild,
                    f"Could not remove {member.name} ({member.id}) "
                    + f"from channel ({channel.id}). Channel doesn't exist.",
                )

    async def _reverify_user(self, item: UnverifyItem):
        guild = await self._get_guild(item)
        member = await self._get_member(guild, item)

        if member is None or guild is None:
            return

        now = datetime.now()

        if item.end_time > now:
            duration = item.end_time - datetime.now()
            duration_in_s = duration.total_seconds()
            await asyncio.sleep(duration_in_s)

        await guild_log.info(
            None, member.guild, f"Reverifying {member.name} ({member.id})."
        )

        await self._return_roles(member, item)
        await self._return_channels(member, item)
        await self._remove_temp_channels(member, item)

        config = UnverifyGuildConfig.get(guild=guild)
        unverify_role = discord.utils.get(guild.roles, id=config.unverify_role_id)
        if unverify_role is not None:
            try:
                await member.remove_roles(unverify_role, reason="Reverify", atomic=True)
            except discord.errors.Forbidden:
                await guild_log.warning(
                    None,
                    member.guild,
                    f"Removing unverify role from  {member.name} ({member.id}) failed. "
                    + "Insufficient permissions.",
                )
        else:
            await guild_log.warning(
                None,
                member.guild,
                f"Removing unverify role from  {member.name} ({member.id}) failed. "
                + "Role not found.",
            )

        utx = i18n.TranslationContext(guild.id, member.id)
        await guild_log.info(
            None, member.guild, f"Reverify success for member {member.name}."
        )

        try:
            await member.send(
                _(
                    utx, "Your access to the guild **{guild_name}** was returned."
                ).format(guild_name=guild.name)
            )
        except discord.Forbidden:
            await guild_log.info(
                None, member.guild, f"Couldn't send reverify info to {member.name}'s DM"
            )
        item.status = UnverifyStatus.finished
        item.save()

    @staticmethod
    async def _remove_roles(
        member: Member, unverify_type: UnverifyType
    ) -> List[discord.Role]:
        guild = member.guild
        removed_roles = []
        for role in member.roles:
            try:
                await member.remove_roles(role, reason=unverify_type.value, atomic=True)
                removed_roles.append(role)
            except NotFound:
                # The role got deleted or someone tried to unverify a bot.
                pass
            except discord.errors.Forbidden:
                await guild_log.warning(
                    None,
                    member.guild,
                    f"Removing role {role.name} from  {member.name} ({member.id}) failed. "
                    + "Insufficient permissions.",
                )

        config = UnverifyGuildConfig.get(guild=guild)
        unverify_role = discord.utils.get(guild.roles, id=config.unverify_role_id)
        if unverify_role is not None:
            try:
                await member.add_roles(
                    unverify_role, reason=unverify_type.value, atomic=True
                )
            except discord.errors.Forbidden:
                await guild_log.warning(
                    None,
                    member.guild,
                    f"Adding unverify role to {member.name} ({member.id}) failed. "
                    + "Insufficient permissions.",
                )
        else:
            await guild_log.warning(
                None,
                member.guild,
                f"Adding unverify role to {member.name} ({member.id}) failed. Role not found.",
            )
        return removed_roles

    @staticmethod
    async def _remove_or_keep_channels(
        member: Member,
        unverify_type: UnverifyType,
        channels_to_keep: List[discord.abc.GuildChannel],
    ) -> Tuple[List[discord.abc.GuildChannel], List[discord.abc.GuildChannel]]:
        removed_channels = []
        added_channels = []

        for channel in member.guild.channels:
            if isinstance(channel, discord.CategoryChannel):
                continue

            perms = channel.permissions_for(member)
            try:
                user_overw = channel.overwrites_for(member)
            except TypeError:
                user_overw = discord.PermissionOverwrite(read_messages=None)

            if channels_to_keep is not None and channel in channels_to_keep:
                if not perms.read_messages:
                    user_overw = discord.PermissionOverwrite(read_messages=True)
                    try:
                        await channel.set_permissions(
                            member, overwrite=user_overw, reason=unverify_type.value
                        )
                        added_channels.append(channel)
                    except PermissionError:
                        await guild_log.warning(
                            None,
                            member.guild,
                            f"Adding temp permissions for {member.name} ({member.id}) "
                            + f"to {channel.name} failed. Insufficient permissions.",
                        )

            elif perms.read_messages and not user_overw.read_messages:
                pass
            elif not perms.read_messages:
                pass
            else:
                user_overw = discord.PermissionOverwrite(read_messages=False)
                try:
                    await channel.set_permissions(
                        member, overwrite=user_overw, reason=unverify_type.value
                    )
                    removed_channels.append(channel)
                except PermissionError:
                    await guild_log.warning(
                        None,
                        member.guild,
                        f"Removing {member.name} ({member.id}) from {channel.name} failed. "
                        + "Insufficient permissions.",
                    )
        return removed_channels, added_channels

    async def _unverify_member(
        self,
        member: Member,
        end_time: datetime,
        reason: str,
        unverify_type: UnverifyType,
        channels_to_keep: List[discord.abc.GuildChannel] = None,
    ) -> UnverifyItem:
        result = UnverifyItem.get_member(member=member, status=UnverifyStatus.waiting)
        if result != []:
            raise ValueError

        removed_roles = await self._remove_roles(member, unverify_type)
        await asyncio.sleep(2)
        removed_channels, added_channels = await self._remove_or_keep_channels(
            member, unverify_type, channels_to_keep
        )

        # Avoiding discord Embed troubles
        if reason is not None and len(reason) > 1024:
            reason = reason[:1024]

        result = UnverifyItem.add(
            member=member,
            end_time=end_time,
            roles_to_return=removed_roles,
            channels_to_return=removed_channels,
            channels_to_remove=added_channels,
            reason=reason,
            unverify_type=unverify_type,
        )
        return result

    @commands.guild_only()
    @check.acl2(check.ACLevel.SUBMOD)
    @commands.group(name="unverify")
    async def unverify_(self, ctx):
        """Pest control."""
        await utils.discord.send_help(ctx)

    @commands.guild_only()
    @check.acl2(check.ACLevel.SUBMOD)
    @unverify_.group(name="config")
    async def unverify_config_(self, ctx):
        """Pest control config."""
        await utils.discord.send_help(ctx)

    @commands.guild_only()
    @check.acl2(check.ACLevel.MOD)
    @unverify_config_.command(name="set")
    async def unverify_config_set(self, ctx, unverify_role: discord.Role):
        """Set configuration of guild that the message was sent from.

        Args:
            unverify_role: Role that unverified members get.
        """
        UnverifyGuildConfig.set(guild=ctx.guild, unverify_role=unverify_role)

        await guild_log.info(
            ctx.author, ctx.channel, f"Unverify role was set to {unverify_role.name}."
        )
        await ctx.reply(
            _(ctx, "Unverify role was set to {role_name}.").format(
                role_name=unverify_role.mention
            )
        )

    @commands.guild_only()
    @check.acl2(check.ACLevel.SUBMOD)
    @unverify_config_.command(name="get")
    async def unverify_config_get(self, ctx: commands.Context):
        """Set configuration of guild that the message was sent from.

        Args:
            unverify_role: Role that unverified members get.
        """
        unverify_guild_config = UnverifyGuildConfig.get(guild=ctx.guild)

        if unverify_guild_config is not None:
            role = discord.utils.get(
                ctx.guild.roles, id=unverify_guild_config.unverify_role_id
            )
            await ctx.reply(
                _(ctx, "Unverify role is set to {role_name}.").format(
                    role_name=role.name
                )
            )
        else:
            await ctx.reply(_(ctx, "Unverify role is not set."))

    @check.acl2(check.ACLevel.SUBMOD)
    @unverify_.command(name="user")
    async def unverify_user(
        self,
        ctx: commands.Context,
        member: discord.Member,
        datetime_str: str,
        *,
        reason: str = None,
    ):
        """Unverify a guild member.

        Args:
            member: Member to be unverified
            datetime_str: Datetime string Preferably quoted.
            reason: Reason of Unverify. Defaults to None.
        """
        unverify_guild_config = UnverifyGuildConfig.get(guild=ctx.guild)
        if unverify_guild_config is None:
            await ctx.reply(
                _(
                    ctx,
                    "Unverify config is not set for this guild. The administrators need to fix this.",
                )
            )
            return
        banner_acl_level = acl.map_member_to_ACLevel(bot=self.bot, member=ctx.author)
        banned_acl_level = acl.map_member_to_ACLevel(bot=self.bot, member=member)
        if banned_acl_level >= banner_acl_level:
            await ctx.reply(_(ctx, "Cannot unverify member with higher or equal role!"))
            return
        try:
            end_time = utils.time.parse_datetime(datetime_str)
        except dateutil.parser.ParserError:
            await ctx.reply(
                _(
                    ctx,
                    "I don't know how to parse `{datetime_str}`, please try again.",
                ).format(datetime_str=datetime_str)
            )
            return

        if end_time < datetime.now():
            await ctx.reply(
                _(
                    ctx,
                    "End time already passed.",
                ).format(datetime_str=datetime_str)
            )
            return

        try:
            await self._unverify_member(
                member, end_time, reason, unverify_type=UnverifyType.unverify
            )
        except ValueError:
            await ctx.reply(
                _(
                    ctx,
                    "Member is already unverified.",
                )
            )
            return

        end_time_str = utils.time.format_datetime(end_time)

        utx = i18n.TranslationContext(ctx.guild.id, member.id)
        embed = utils.discord.create_embed(
            author=ctx.message.author,
            title=_(
                utx,
                "Your access to {guild_name} was temporarily revoked.",
            ).format(
                guild_name=ctx.guild.name,
            ),
        )
        embed.add_field(
            name=_(
                utx,
                "Your access will be automatically returned on",
            ),
            value=end_time_str,
            inline=False,
        )
        if reason is not None:
            embed.add_field(
                name=_(
                    utx,
                    "Reason",
                ),
                value=reason,
                inline=False,
            )

        with contextlib.suppress(discord.Forbidden):
            await member.send(embed=embed)

        end_time_str = utils.time.format_datetime(end_time)

        await ctx.reply(
            _(
                ctx,
                (
                    "Member {member_name} was temporarily unverified. "
                    "The access will be returned on: {end_time}"
                ),
            ).format(
                member_name=member.name,
                end_time=end_time_str,
            )
        )

        await guild_log.info(
            member,
            ctx.channel,
            f"Member {member.name} ({member.id}) unverified "
            + f"until {end_time_str}, unverify_type {UnverifyType.unverify.value}",
        )

    @commands.guild_only()
    @check.acl2(check.ACLevel.SUBMOD)
    @unverify_.command(name="pardon")
    async def unverify_pardon(self, ctx, member: discord.Member):
        """Pardon unverified member.

        Args:
            member: Member to be pardoned
        """
        result = UnverifyItem.get_member(member=member, status=UnverifyStatus.waiting)
        if result == []:
            await ctx.reply(_(ctx, "Is this member really unverified?"))
            return
        item = result[0]
        item.end_time = datetime.now()
        item.save()

        await guild_log.info(
            ctx.author,
            ctx.channel,
            f"Unverify of {member.name} ({member.id}) was pardoned.",
        )
        await ctx.reply(
            _(
                ctx,
                (
                    "Unverify of {member_name} ({member_id}) was pardoned. "
                    "Access will be returned next time the reverifier loop runs."
                ),
            ).format(
                member_name=member.name,
                member_id=member.id,
            )
        )

    @commands.guild_only()
    @check.acl2(check.ACLevel.SUBMOD)
    @unverify_.command(name="list")
    async def unverify_list(self, ctx, status: str = "waiting"):
        """List unverified members.

        Args:
            status: One of ["waiting", "finished", "member_left", "guild_not_found", "all"]. Defaults to "waiting".
        """

        status: str = status.lower()
        if status not in (
            "waiting",
            "finished",
            "member_left",
            "guild_not_found",
            "all",
        ):
            await ctx.reply(_(ctx, "Invalid status. Check the command help."))
            return

        if status == "all":
            result = UnverifyItem.get_items(guild=ctx.guild, status=None)
        else:
            result = UnverifyItem.get_items(
                guild=ctx.guild, status=UnverifyStatus[status]
            )
        embeds = []
        for item in result:
            guild = self.bot.get_guild(item.guild_id)
            user = guild.get_member(item.user_id)
            if user is None:
                try:
                    user = await self.bot.fetch_user(item.user_id)
                    user_name = f"{user.mention}\n{user.name} ({user.id})"
                except discord.errors.NotFound:
                    user_name = "_(Unknown user)_"
            else:
                user_name = f"{user.mention}\n{user.name} ({user.id})"

            start_time = utils.time.format_datetime(item.start_time)
            end_time = utils.time.format_datetime(item.end_time)

            roles = []
            for role_id in item.roles_to_return:
                role = discord.utils.get(guild.roles, id=role_id)
                if role is None:
                    continue
                roles.append(role)

            channels = []
            for channel_id in item.channels_to_return:
                channel = discord.utils.get(guild.channels, id=channel_id)
                channels.append(channel)

            embed = utils.discord.create_embed(
                author=ctx.message.author, title=_(ctx, "Unverify list")
            )
            embed.add_field(name=_(ctx, "User"), value=user_name, inline=False)
            embed.add_field(
                name=_(ctx, "Start time"), value=str(start_time), inline=True
            )
            embed.add_field(name=_(ctx, "End time"), value=str(end_time), inline=True)
            embed.add_field(name=_(ctx, "Status"), value=item.status.value, inline=True)
            embed.add_field(
                name=_(ctx, "Type"), value=item.unverify_type.value, inline=True
            )
            if roles != []:
                embed.add_field(
                    name=_(ctx, "Roles to return"),
                    value=", ".join(role.name for role in roles),
                    inline=True,
                )

            if channels != []:
                embed.add_field(
                    name=_(ctx, "Channels to return"),
                    value=", ".join(channel.name for channel in channels),
                    inline=True,
                )
            if item.reason != "{}":
                embed.add_field(name=_(ctx, "Reason"), value=item.reason, inline=False)
            embeds.append(embed)

        scrollable_embed = utils.ScrollableEmbed(ctx, embeds)
        await scrollable_embed.scroll()

    @commands.guild_only()
    @check.acl2(check.ACLevel.MEMBER)
    @commands.command()
    async def selfunverify(
        self,
        ctx: commands.Context,
        datetime_str: str,
        channels: commands.Greedy[discord.TextChannel],
    ):
        """Unverify self.

        Args:
            datetime_str: Until when. Preferably quoted.
            channels: Channels you want to keep.
        """
        unverify_guild_config = UnverifyGuildConfig.get(guild=ctx.guild)
        if unverify_guild_config is None:
            await ctx.reply(
                _(
                    ctx,
                    "Unverify config is not set for this guild. The administrators need to fix this.",
                )
            )
            return
        try:
            end_time = utils.time.parse_datetime(datetime_str)
        except dateutil.parser.ParserError:
            await ctx.reply(
                _(
                    ctx,
                    "I don't know how to parse `{datetime_str}`, please try again.",
                ).format(datetime_str=datetime_str)
            )
            return

        if end_time < datetime.now():
            await ctx.reply(
                _(
                    ctx,
                    "End time already passed.",
                ).format(datetime_str=datetime_str)
            )
            return

        cleaned_channels = []
        for channel in channels:
            perms = channel.permissions_for(ctx.author)
            if perms.read_messages:
                cleaned_channels.append(channel)

        try:
            await self._unverify_member(
                ctx.message.author,
                end_time,
                UnverifyType.selfunverify.value,
                unverify_type=UnverifyType.selfunverify,
                channels_to_keep=cleaned_channels,
            )
        except ValueError:
            await ctx.reply(
                _(
                    ctx,
                    "Member is already unverified.",
                )
            )
            return

        end_time_str = utils.time.format_datetime(end_time)

        utx = i18n.TranslationContext(ctx.guild.id, ctx.message.author.id)
        embed = utils.discord.create_embed(
            author=ctx.message.author,
            title=_(
                utx,
                "Your access to {guild_name} was temporarily revoked.",
            ).format(
                guild_name=ctx.guild.name,
            ),
        )
        embed.add_field(
            name=_(
                utx,
                "Your access will be automatically returned on",
            ),
            value=end_time_str,
            inline=False,
        )

        with contextlib.suppress(discord.Forbidden):
            await ctx.message.author.send(embed=embed)

        await ctx.reply(
            _(
                ctx,
                (
                    "Member {member_name} was temporarily unverified. "
                    "The access will be returned on: {end_time}"
                ),
            ).format(
                member_name=ctx.message.author.name,
                end_time=end_time_str,
            )
        )

        member = ctx.message.author
        await guild_log.info(
            member,
            ctx.channel,
            f"Member {member.name} ({member.id}) unverified "
            + f"until {end_time_str}, unverify_type {UnverifyType.selfunverify.value}",
        )

    @commands.guild_only()
    @check.acl2(check.ACLevel.MEMBER)
    @commands.command()
    async def gn(self, ctx: commands.Context):
        """Goodnight!

        Selfunverifies user until the morning.
        """
        unverify_guild_config = UnverifyGuildConfig.get(guild=ctx.guild)
        if unverify_guild_config is None:
            await ctx.reply(
                _(
                    ctx,
                    "Unverify config is not set for this guild. The administrators need to fix this.",
                )
            )
            return
        end_time = datetime.now().replace(hour=6, minute=0, second=0, microsecond=0)
        if end_time < datetime.now():
            end_time = end_time + timedelta(days=1)

        try:
            await self._unverify_member(
                ctx.message.author,
                end_time,
                UnverifyType.selfunverify.value,
                unverify_type=UnverifyType.selfunverify,
            )
        except ValueError:
            await ctx.reply(
                _(
                    ctx,
                    "Member is already unverified.",
                )
            )
            return

        end_time_str = utils.time.format_datetime(end_time)

        utx = i18n.TranslationContext(ctx.guild.id, ctx.message.author.id)
        embed = utils.discord.create_embed(
            author=ctx.message.author,
            title=_(
                utx,
                "Your access to {guild_name} was temporarily revoked.",
            ).format(
                guild_name=ctx.guild.name,
            ),
        )
        embed.add_field(
            name=_(
                utx,
                "Your access will be automatically returned on",
            ),
            value=end_time_str,
            inline=False,
        )

        with contextlib.suppress(discord.Forbidden):
            await ctx.message.author.send(embed=embed)

        await ctx.reply(
            _(
                ctx,
                (
                    "Member {member_name} was temporarily unverified. "
                    "The access will be returned on: {end_time}"
                ),
            ).format(
                member_name=ctx.message.author.name,
                end_time=end_time_str,
            )
        )

        member = ctx.message.author
        await guild_log.info(
            member,
            ctx.channel,
            f"Member {member.name} ({member.id}) unverified "
            + f"until {end_time_str}, unverify_type {UnverifyType.selfunverify.value}",
        )


async def setup(bot) -> None:
    await bot.add_cog(Unverify(bot))
