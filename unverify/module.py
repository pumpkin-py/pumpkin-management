from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime, timedelta

from typing import Optional, List, Tuple
import dateutil.parser

import nextcord
from nextcord import Guild, Member
from nextcord.errors import NotFound
from nextcord.ext.commands.bot import Bot
from nextcord.ext import tasks, commands

import database.config
from core import check, i18n, logger, utils
from core import TranslationContext

from .database import UnverifyStatus, UnverifyType, UnverifyItem, GuildConfig


_ = i18n.Translator("modules/mgmt").translate
bot_log = logger.Bot.logger()
guild_log = logger.Guild.logger()
config = database.config.Config.get()


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
                    f"Reverify failed: Guild ({item.guild_id}) was not found.\nSetting status to `guild could not be found`",
                )
                item.status = UnverifyStatus.guild_not_found
            item.last_check = datetime.now()
            item.save()
            await bot_log.warning(
                None,
                None,
                f"Reverify failed: Guild ({item.guild_id}) still was not found.",
            )
            raise NotFound
        return guild

    @staticmethod
    async def _get_member(guild: Guild, item: UnverifyItem) -> Optional[Member]:
        member = guild.get_member(item.user_id)

        if member is None:
            try:
                member = await guild.fetch_member(item.user_id)
            except NotFound:
                if item.status != UnverifyStatus.member_left:
                    gtx = TranslationContext(member.guild.id, None)
                    await guild_log.warning(
                        None,
                        guild,
                        _(
                            gtx,
                            "Reverify failed: Member ({user_id}) was not found. Setting status to `member left server`.",
                        ).format(
                            user_id=item.user_id,
                        ),
                    )
                    item.status = UnverifyStatus.member_left
                    item.save()
                item.last_check = datetime.now()
                item.save()
                raise NotFound
        return member

    @staticmethod
    async def _return_roles(member: Member, item: UnverifyItem):
        for role_id in item.roles_to_return:
            role = nextcord.utils.get(member.guild.roles, id=role_id)
            if role is not None:
                try:
                    await member.add_roles(role, reason="Reverify", atomic=True)
                except nextcord.errors.Forbidden:
                    gtx = TranslationContext(member.guild.id, None)
                    await guild_log.warning(
                        None,
                        member.guild,
                        _(
                            gtx,
                            "Returning role {role_name} to {member_name} ({member_id}) failed. Insufficient permissions.",
                        ).format(
                            role_name=role.name,
                            member_name=member.name,
                            member_id=member.id,
                        ),
                    )
            else:
                gtx = TranslationContext(member.guild.id, None)
                await guild_log.warning(
                    None,
                    member.guild,
                    _(gtx, "Role with ID {role_id} could not be found.",).format(
                        role_id=role_id,
                    ),
                )

    @staticmethod
    async def _return_channels(member: Member, item: UnverifyItem):
        for channel_id in item.channels_to_return:
            channel = nextcord.utils.get(member.guild.channels, id=channel_id)
            if channel is not None:
                user_overw = nextcord.PermissionOverwrite(read_messages=True)
                try:
                    await channel.set_permissions(
                        member, overwrite=user_overw, reason="Reverify"
                    )
                except nextcord.errors.Forbidden:
                    gtx = TranslationContext(member.guild.id, None)
                    await guild_log.warning(
                        None,
                        member.guild,
                        _(
                            gtx,
                            "Could not add {member_name} ({member_id}) to {channel_name}. Insufficient permissions.",
                        ).format(
                            member_name=member.name,
                            member_id=member.id,
                            channel_name=channel.name,
                        ),
                    )
            else:
                await guild_log.warning(
                    None,
                    member.guild,
                    _(
                        gtx,
                        "Could not add {member_name} ({member_id}) to channel ({channel_id}). Channel doesn't exist.",
                    ).format(
                        member_name=member.name,
                        member_id=member.id,
                        channel_id=channel_id,
                    ),
                    f"Could not add {member.name} ({member.id}) to {channel.name}. Channel does not exist.",
                )

    @staticmethod
    async def _remove_temp_channels(member: Member, item: UnverifyItem):
        for channel_id in item.channels_to_remove:
            channel = nextcord.utils.get(member.guild.channels, id=channel_id)
            if channel is not None:
                user_overw = channel.overwrites_for(member)
                user_overw.update(read_messages=None)
                try:
                    await channel.set_permissions(
                        member, overwrite=user_overw, reason="Reverify"
                    )
                except nextcord.errors.Forbidden:
                    gtx = TranslationContext(member.guild.id, None)
                    await guild_log.warning(
                        None,
                        member.guild,
                        _(
                            gtx,
                            "Could not remove {member_name} ({member_id}) from {channel_name}. Insufficient permissions.",
                        ).format(
                            member_name=member.name,
                            member_id=member.id,
                            channel_name=channel.name,
                        ),
                    )
            else:
                gtx = TranslationContext(member.guild.id, None)
                await guild_log.warning(
                    None,
                    member.guild,
                    _(
                        gtx,
                        "Could not remove {member_name} ({member_id}) from channel ({channel_id}). Channel doesn't exist.",
                    ).format(
                        member_name=member.name,
                        member_id=member.id,
                        channel_id=channel_id,
                    ),
                )

    async def _reverify_user(self, item: UnverifyItem):
        try:
            guild = await self._get_guild(item)
            member = await self._get_member(guild, item)
        except NotFound:
            return

        now = datetime.now()
        if item.end_time > now:
            duration = item.end_time - datetime.now()
            duration_in_s = duration.total_seconds()
            await asyncio.sleep(duration_in_s)

        gtx = TranslationContext(member.guild.id, None)
        await guild_log.info(
            None,
            member.guild,
            _(gtx, "Reverifying {member_name} ({member_id}).",).format(
                member_name=member.name,
                member_id=member.id,
            ),
        )

        await self._return_roles(member, item)
        await self._return_channels(member, item)
        await self._remove_temp_channels(member, item)

        config = GuildConfig.get(guild.id)
        unverify_role = nextcord.utils.get(guild.roles, id=config.unverify_role_id)
        if unverify_role is not None:
            try:
                await member.remove_roles(unverify_role, reason="Reverify", atomic=True)
            except nextcord.errors.Forbidden:
                gtx = TranslationContext(member.guild.id, None)
                await guild_log.warning(
                    None,
                    member.guild,
                    _(
                        gtx,
                        "Removing unverify role from  {member_name} ({member_id}) failed. Insufficient permissions.",
                    ).format(
                        member_name=member.name,
                        member_id=member.id,
                    ),
                )
        else:
            gtx = TranslationContext(member.guild.id, None)
            await guild_log.warning(
                None,
                member.guild,
                _(
                    gtx,
                    "Removing unverify role from  {member_name} ({member_id}) failed. Role not found.",
                ).format(
                    member_name=member.name,
                    member_id=member.id,
                ),
            )

        gtx = TranslationContext(guild.id, None)
        utx = TranslationContext(guild.id, member.id)
        await guild_log.info(
            None,
            member.guild,
            _(gtx, "Reverify success for member {member_name}.").format(
                member_name=member.name
            ),
        )
        try:
            await member.send(
                _(
                    utx, "Your access to the guild **{guild_name}** was returned."
                ).format(guild_name=guild.name)
            )
        except nextcord.Forbidden:
            await guild_log.info(
                None,
                member.guild,
                _(gtx, "Couldn't send reverify info to {member_name}'s DM").format(
                    member_name=member.name
                ),
            )
        item.status = UnverifyStatus.finished
        item.save()

    @staticmethod
    async def _remove_roles(member: Member, type: UnverifyType) -> List[nextcord.Role]:
        guild = member.guild
        removed_roles = []
        for role in member.roles:
            try:
                await member.remove_roles(role, reason=type.value, atomic=True)
                removed_roles.append(role)
            except NotFound:
                # This could be deleted roles just moment after the unverify started of someone tried to unverify a bot.
                pass
            except nextcord.errors.Forbidden:
                gtx = TranslationContext(member.guild.id, None)
                await guild_log.warning(
                    None,
                    member.guild,
                    _(
                        gtx,
                        "Removing role {role_name} from  {member_name} ({member_id}) failed. Insufficient permissions.",
                    ).format(
                        role_name=role.name,
                        member_name=member.name,
                        member_id=member.id,
                    ),
                )

        config = GuildConfig.get(guild.id)
        unverify_role = nextcord.utils.get(guild.roles, id=config.unverify_role_id)
        if unverify_role is not None:
            try:
                await member.add_roles(unverify_role, reason=type.value, atomic=True)
            except nextcord.errors.Forbidden:
                gtx = TranslationContext(member.guild.id, None)
                await guild_log.warning(
                    None,
                    member.guild,
                    _(
                        gtx,
                        "Adding unverify role to {member_name} ({member_id}) failed. Insufficient permissions.",
                    ).format(
                        member_name=member.name,
                        member_id=member.id,
                    ),
                )
        else:
            gtx = TranslationContext(member.guild.id, None)
            await guild_log.warning(
                None,
                member.guild,
                _(
                    gtx,
                    "Adding unverify role to {member_name} ({member_id}) failed. Role not found.",
                ).format(
                    member_name=member.name,
                    member_id=member.id,
                ),
            )
        return removed_roles

    @staticmethod
    async def _remove_or_keep_channels(
        member: Member,
        type: UnverifyType,
        channels_to_keep: List[nextcord.abc.GuildChannel],
    ) -> Tuple[List[nextcord.abc.GuildChannel], List[nextcord.abc.GuildChannel]]:
        removed_channels = []
        added_channels = []

        for channel in member.guild.channels:
            if isinstance(channel, nextcord.CategoryChannel):
                continue

            perms = channel.permissions_for(member)
            user_overw = channel.overwrites_for(member)

            if channels_to_keep is not None and channel in channels_to_keep:
                if not perms.read_messages:
                    user_overw.update(read_messages=True)
                    try:
                        await channel.set_permissions(
                            member, overwrite=user_overw, reason=type.value
                        )
                        added_channels.append(channel)
                    except PermissionError:
                        gtx = TranslationContext(member.guild.id, None)
                        await guild_log.warning(
                            None,
                            member.guild,
                            _(
                                gtx,
                                "Adding temp permissions for {member_name} ({member_id}) to {channel_name} failed. Insufficient permissions.",
                            ).format(
                                member_name=member.name,
                                member_id=member.id,
                                channel_name=channel.name,
                            ),
                        )

            elif perms.read_messages and not user_overw.read_messages:
                pass
            elif not perms.read_messages:
                pass
            else:
                user_overw.update(read_messages=False)
                try:
                    await channel.set_permissions(
                        member, overwrite=user_overw, reason=type.value
                    )
                    removed_channels.append(channel)
                except PermissionError:
                    gtx = TranslationContext(member.guild.id, None)
                    await guild_log.warning(
                        None,
                        member.guild,
                        _(
                            gtx,
                            "Removing {member_name} ({member_id}) from {channel_name} failed. Insufficient permissions.",
                        ).format(
                            member_name=member.name,
                            member_id=member.id,
                            channel_name=channel.name,
                        ),
                    )
        return removed_channels, added_channels

    async def _unverify_member(
        self,
        member: Member,
        end_time: datetime,
        reason: str,
        type: UnverifyType,
        channels_to_keep: List[nextcord.abc.GuildChannel] = None,
    ) -> UnverifyItem:
        result = UnverifyItem.get_member(member=member, status=UnverifyStatus.waiting)
        if result != []:
            raise ValueError

        removed_roles = await self._remove_roles(member, type)
        await asyncio.sleep(2)
        removed_channels, added_channels = await self._remove_or_keep_channels(
            member, type, channels_to_keep
        )

        # Avoiding discord Embed troubles
        if len(reason) > 1024:
            reason = reason[:1024]

        result = UnverifyItem.add(
            member=member,
            end_time=end_time,
            roles_to_return=removed_roles,
            channels_to_return=removed_channels,
            channels_to_remove=added_channels,
            reason=reason,
            type=type,
        )
        return result

    @commands.guild_only()
    @commands.check(check.acl)
    @commands.group(name="unverify")
    async def unverify_(self, ctx):
        """Pest control."""
        await utils.Discord.send_help(ctx)

    @commands.guild_only()
    @commands.check(check.acl)
    @unverify_.command(name="set")
    async def unverify_set(self, ctx, unverify_role: nextcord.Role):
        """Set configuration of guild that the message was sent from.

        Args:
            unverify_role: Role that unverified members get.
        """
        GuildConfig.set(guild_id=ctx.guild.id, unverify_role_id=unverify_role.id)

        gtx = TranslationContext(ctx.guild.id, None)
        await guild_log.info(
            ctx.author,
            ctx.channel,
            _(gtx, "Unverify role was set to {role_name}.",).format(
                role_name=unverify_role.name,
                guild_name=ctx.guild.name,
            ),
        )
        await ctx.reply(
            _(ctx, "Unverify role was set to {role_name}.").format(
                role_name=unverify_role.mention, guild_name=ctx.guild.name
            )
        )

    @commands.check(check.acl)
    @unverify_.command(name="user")
    async def unverify_user(
        self,
        ctx: commands.Context,
        member: nextcord.Member,
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
        try:
            end_time = utils.Time.parse_datetime(datetime_str)
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
                member, end_time, reason, type=UnverifyType.unverify
            )
        except ValueError:
            await ctx.reply(
                _(
                    ctx,
                    "Member is already unverified.",
                )
            )
            return

        utx = TranslationContext(ctx.guild.id, member.id)
        embed = utils.Discord.create_embed(
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
            value=end_time,
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

        with contextlib.suppress(nextcord.Forbidden):
            await member.send(embed=embed)

        end_time_str = utils.Time.datetime(end_time)

        await ctx.reply(
            _(
                ctx,
                "Member {member_name} was temporarily unverified. The access will be returned on: {end_time}",
            ).format(
                member_name=member.name,
                end_time=end_time_str,
            )
        )

        gtx = TranslationContext(member.guild.id, None)
        await guild_log.info(
            ctx.message.author,
            member.guild,
            _(
                gtx,
                "Member {member_name} ({member_id}) unverified: Until - {end_time}, reason - {reason}, type - {type}",
            ).format(
                member_name=member.name,
                member_id=member.id,
                end_time=end_time_str,
                reason=reason,
                type=UnverifyType.unverify.value,
            ),
        )

    @commands.guild_only()
    @commands.check(check.acl)
    @unverify_.command(name="pardon")
    async def unverify_pardon(self, ctx, member: nextcord.Member):
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

        gtx = TranslationContext(ctx.guild.id, None)
        await guild_log.info(
            ctx.author,
            ctx.channel,
            _(gtx, "Unverify of {member_name} ({member_id}) was pardoned.",).format(
                member_name=member.name,
                member_id=member.id,
            ),
        )
        await ctx.reply(
            _(
                ctx,
                "Unverify of {member_name} ({member_id}) was pardoned. Access will be returned next time the reverifier loop runs.",
            ).format(
                member_name=member.name,
                member_id=member.id,
            )
        )

    @commands.guild_only()
    @commands.check(check.acl)
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
                except nextcord.errors.NotFound:
                    user_name = "_(Unknown user)_"
            else:
                user_name = f"{user.mention}\n{user.name} ({user.id})"

            start_time = utils.Time.datetime(item.start_time)
            end_time = utils.Time.datetime(item.end_time)

            roles = []
            for role_id in item.roles_to_return:
                role = nextcord.utils.get(guild.roles, id=role_id)
                roles.append(role)
            channels = []
            for channel_id in item.channels_to_return:
                channel = nextcord.utils.get(guild.channels, id=channel_id)
                channels.append(channel)

            embed = utils.Discord.create_embed(
                author=ctx.message.author, title=_(ctx, "Unverify list")
            )
            embed.add_field(name=_(ctx, "User"), value=user_name, inline=False)
            embed.add_field(
                name=_(ctx, "Start time"), value=str(start_time), inline=True
            )
            embed.add_field(name=_(ctx, "End time"), value=str(end_time), inline=True)
            embed.add_field(name=_(ctx, "Status"), value=item.status.value, inline=True)
            embed.add_field(name=_(ctx, "Type"), value=item.type.value, inline=True)
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
    @commands.command()
    async def selfunverify(self, ctx: commands.Context, datetime_str: str):
        """Unverify self.

        Args:
            datetime_str: Until when. Preferably quoted.
        """
        try:
            end_time = utils.Time.parse_datetime(datetime_str)
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
                ctx.message.author,
                end_time,
                UnverifyType.selfunverify.value,
                type=UnverifyType.selfunverify,
            )
        except ValueError:
            await ctx.reply(
                _(
                    ctx,
                    "Member is already unverified.",
                )
            )
            return

        with contextlib.suppress(nextcord.Forbidden):
            utx = TranslationContext(ctx.guild.id, ctx.message.author.id)
            embed = utils.Discord.create_embed(
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
                value=end_time,
                inline=False,
            )
            await ctx.message.author.send(embed=embed)

        end_time_str = utils.Time.datetime(end_time)

        await ctx.reply(
            _(
                ctx,
                "Member {member_name} was temporarily unverified. The access will be returned on: {end_time}",
            ).format(
                member_name=ctx.message.author.name,
                end_time=end_time_str,
            )
        )

        gtx = TranslationContext(ctx.message.author.guild.id, None)
        await guild_log.info(
            ctx.message.author,
            ctx.message.author.guild,
            _(
                gtx,
                "Member {member_name} ({member_id}) unverified: Until - {end_time}, type - {type}",
            ).format(
                member_name=ctx.message.author.name,
                member_id=ctx.message.author.id,
                end_time=end_time_str,
                type=UnverifyType.selfunverify.value,
            ),
        )

    @commands.guild_only()
    @commands.command()
    async def gn(self, ctx: commands.Context):
        """Goodnight!

        Selfunverifies user until the morning.
        """
        end_time = datetime.now().replace(hour=6, minute=0, second=0, microsecond=0)
        if end_time < datetime.now():
            end_time = end_time + timedelta(days=1)

        try:
            await self._unverify_member(
                ctx.message.author,
                end_time,
                UnverifyType.selfunverify.value,
                type=UnverifyType.selfunverify,
            )
        except ValueError:
            await ctx.reply(
                _(
                    ctx,
                    "Member is already unverified.",
                )
            )
            return

        with contextlib.suppress(nextcord.Forbidden):
            utx = TranslationContext(ctx.guild.id, ctx.message.author.id)
            embed = utils.Discord.create_embed(
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
                value=end_time,
                inline=False,
            )
            await ctx.message.author.send(embed=embed)

        end_time_str = utils.Time.datetime(end_time)

        await ctx.reply(
            _(
                ctx,
                "Member {member_name} was temporarily unverified. The access will be returned on: {end_time}",
            ).format(
                member_name=ctx.message.author.name,
                end_time=end_time_str,
            )
        )

        gtx = TranslationContext(ctx.message.author.guild.id, None)
        await guild_log.info(
            ctx.message.author,
            ctx.message.author.guild,
            _(
                gtx,
                "Member {member_name} ({member_id}) unverified: Until - {end_time}, type - {type}",
            ).format(
                member_name=ctx.message.author.name,
                member_id=ctx.message.author.id,
                end_time=end_time_str,
                type=UnverifyType.selfunverify.value,
            ),
        )


def setup(bot) -> None:
    bot.add_cog(Unverify(bot))
