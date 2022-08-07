from typing import List
import shlex
from emoji import UNICODE_EMOJI as _UNICODE_EMOJI
from io import BytesIO

import discord
from discord.ext import commands

from pie import check, i18n, logger, utils

from . import utils as helper_utils
from .database import ReactionChannel, ReactionChannelType

UNICODE_EMOJI = _UNICODE_EMOJI["en"]
del _UNICODE_EMOJI

_ = i18n.Translator("modules/mgmt").translate
guild_log = logger.Guild.logger()


class React2Role(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.guild_only()
    @check.acl2(check.ACLevel.MOD)
    @commands.group(name="reaction-channel")
    async def reaction_channel(self, ctx):
        """Manage react2role channels."""
        await utils.discord.send_help(ctx)

    @commands.guild_only()
    @check.acl2(check.ACLevel.MOD)
    @reaction_channel.command(name="list")
    async def reaction_channel_list(self, ctx):
        """List react2role channels."""
        db_channels: List[ReactionChannel] = ReactionChannel.get_all(ctx.guild.id)
        if not db_channels:
            await ctx.reply(
                _(ctx, "React2role functionality is not enabled on this server.")
            )
            return

        class Item:
            def __init__(self, db_channel: ReactionChannel):
                dc_channel = ctx.guild.get_channel(db_channel.channel_id)
                top_role = ctx.guild.get_role(db_channel.top_role)
                bottom_role = ctx.guild.get_role(db_channel.bottom_role)

                self.name = (
                    f"#{dc_channel.name}" if dc_channel else str(db_channel.channel_id)
                )
                self.type = db_channel.channel_type.name

                if db_channel.channel_type == ReactionChannelType.ROLE:
                    self.max_roles = (
                        db_channel.max_roles if db_channel.max_roles > 0 else "-"
                    )
                    if db_channel.top_role:
                        self.top = getattr(
                            top_role,
                            "name",
                            str(db_channel.top_role),
                        )
                    else:
                        self.top = "-"

                    if db_channel.bottom_role:
                        self.bottom = getattr(
                            bottom_role,
                            "name",
                            str(db_channel.bottom_role),
                        )
                    else:
                        self.bottom = "-"
                else:
                    self.max_roles = ""
                    self.top = ""
                    self.bottom = ""

        channels = [Item(db_channel) for db_channel in db_channels]
        table: List[str] = utils.text.create_table(
            channels,
            header={
                "name": _(ctx, "Channel"),
                "type": _(ctx, "Type"),
                "max_roles": _(ctx, "Role limit"),
                "top": _(ctx, "Top role"),
                "bottom": _(ctx, "Bottom role"),
            },
        )

        for page in table:
            await ctx.send("```" + page + "```")

    @commands.guild_only()
    @check.acl2(check.ACLevel.MOD)
    @reaction_channel.command(name="add")
    async def reaction_channel_add(
        self, ctx, channel: discord.TextChannel, channel_type: str
    ):
        """Add new react2role channel.

        Args:
            channel: A text channel.
            channel_type: 'role' or 'channel' string.
        """
        if ReactionChannel.get(ctx.guild.id, channel.id) is not None:
            await ctx.reply(
                _(ctx, "Channel **#{channel}** is already react2role channel.").format(
                    channel=channel.name
                )
            )
            return
        types: List[str] = [m.value for m in ReactionChannelType.__members__.values()]
        if channel_type not in types:
            await ctx.reply(
                _(ctx, "Channel type can only be one of {types}.").format(
                    types=", ".join(types)
                )
            )
            return
        channel_type = ReactionChannelType(channel_type)

        reaction_channel = ReactionChannel.add(
            guild_id=ctx.guild.id,
            channel_id=channel.id,
            channel_type=channel_type,
        )
        await ctx.reply(
            _(ctx, "**#{channel}** has been set as {type} channel.").format(
                channel=channel.name,
                type=reaction_channel.React2name,
            )
        )
        await guild_log.info(
            ctx.author,
            ctx.channel,
            f"{reaction_channel.React2name} #{channel.name} set up.",
        )

    @commands.guild_only()
    @check.acl2(check.ACLevel.MOD)
    @reaction_channel.command(name="unlimit")
    async def reaction_channel_unlimit(self, ctx, channel: discord.TextChannel):
        """Remove limits on 'role' channel."""
        reaction_channel = ReactionChannel.get(ctx.guild.id, channel.id)
        if reaction_channel is None:
            await ctx.reply(
                _(ctx, "Channel **#{channel}** is not react2role channel.").format(
                    channel=channel.name
                )
            )
            return
        if reaction_channel.channel_type != ReactionChannelType.ROLE:
            await ctx.reply(_(ctx, "Limiting is only available for 'role' channels."))
            return

        reaction_channel.max_roles = 0
        reaction_channel.top_role = None
        reaction_channel.bottom_role = None
        reaction_channel.save()

        await ctx.reply(
            _(ctx, "Role limits for #{channel} were unset.").format(
                channel=channel.name
            )
        )
        await guild_log.info(
            ctx.author,
            ctx.channel,
            f"{reaction_channel.React2name} #{channel.name}'s role limits unset.",
        )

    @commands.guild_only()
    @check.acl2(check.ACLevel.MOD)
    @reaction_channel.command(name="limits", aliases=["set-limits"])
    async def reaction_channel_limits(
        self,
        ctx,
        channel: discord.TextChannel,
        top: discord.Role,
        bottom: discord.Role,
    ):
        """Set top and bottom limits for 'role' channel."""
        reaction_channel = ReactionChannel.get(ctx.guild.id, channel.id)
        if reaction_channel is None:
            await ctx.reply(
                _(ctx, "Channel **#{channel}** is not react2role channel.").format(
                    channel=channel.name
                )
            )
            return
        if reaction_channel.channel_type != ReactionChannelType.ROLE:
            await ctx.reply(_(ctx, "Limiting is only available for 'role' channels."))
            return
        if bottom > top:
            top, bottom = bottom, top

        reaction_channel.top_role = top.id
        reaction_channel.bottom_role = bottom.id
        reaction_channel.save()

        await ctx.reply(
            _(
                ctx, "Role limits for #{channel} were set to **<{top}, {bottom}>**."
            ).format(channel=channel.name, top=top.name, bottom=bottom.name)
        )
        await guild_log.info(
            ctx.author,
            ctx.channel,
            f"{reaction_channel.React2name} #{channel.name}'s "
            f"role limits set to <{top.name}, {bottom.name}>.",
        )

    @commands.guild_only()
    @check.acl2(check.ACLevel.MOD)
    @reaction_channel.command(name="limit", aliases=["set-limit"])
    async def reaction_channel_limit(
        self, ctx, channel: discord.TextChannel, maximum: int
    ):
        """Set role count limit for 'role' channel."""
        reaction_channel = ReactionChannel.get(ctx.guild.id, channel.id)
        if reaction_channel is None:
            await ctx.reply(
                _(ctx, "Channel **#{channel}** is not react2role channel.").format(
                    channel=channel.name
                )
            )
            return
        if reaction_channel.channel_type != ReactionChannelType.ROLE:
            await ctx.reply(_(ctx, "Limiting is only available for 'role' channels."))
            return
        if not reaction_channel.top_role or not reaction_channel.bottom_role:
            await ctx.reply(_(ctx, "You have to set top and bottom roles first."))
            return

        if maximum < 0:
            maximum = 0
        reaction_channel.max_roles = maximum
        reaction_channel.save()

        await ctx.reply(
            _(ctx, "Role limit for #{channel} was set to **{limit}**.").format(
                channel=channel.name, limit=maximum
            )
        )
        await guild_log.info(
            ctx.author,
            ctx.channel,
            f"{reaction_channel.React2name} #{channel.name}'s "
            f"role limit set to {maximum}.",
        )

    @commands.guild_only()
    @check.acl2(check.ACLevel.MOD)
    @reaction_channel.command(name="remove")
    async def reaction_channel_remove(self, ctx, channel: discord.TextChannel):
        """Remove react2role functionality from a channel."""
        reaction_channel = ReactionChannel.get(ctx.guild.id, channel.id)
        if reaction_channel is None:
            await ctx.reply(
                _(ctx, "Channel **#{channel}** is not react2role channel.").format(
                    channel=channel.name
                )
            )
            return

        channel_type: str = reaction_channel.React2name
        ReactionChannel.remove(guild_id=ctx.guild.id, channel_id=channel.id)
        await ctx.reply(
            _(
                ctx,
                "{type} functionality has been removed from **#{channel}**.",
            ).format(type=channel_type, channel=channel.name)
        )
        await guild_log.info(
            ctx.author,
            ctx.channel,
            f"{channel_type} #{channel.name} unset.",
        )

    @commands.guild_only()
    @check.acl2(check.ACLevel.MOD)
    @reaction_channel.command(name="init-channels")
    async def reaction_channel_init_channels(
        self,
        ctx,
        target: discord.TextChannel,
        *,
        channel_groups: str,
    ):
        """Initialise links for react2role functionality.

        Args:
            target: Target text channel that will act as react2role hub.
            channel_groups: Space separated list of channel groups.
        """
        categories: List[discord.CategoryChannel] = []
        channel_count: int = 0
        for name in shlex.split(channel_groups):
            category = discord.utils.get(ctx.guild.categories, name=name)
            if category is None:
                await ctx.reply(
                    _(ctx, "Category **{category}** could not be found.").format(
                        category=utils.text.sanitise(name)
                    )
                )
                return
            categories.append(category)

        for category in categories:
            channel_count += len(category.text_channels)
            # send header
            header_file = BytesIO()
            header_image = helper_utils.generate_header(category.name)
            header_image.save(header_file, "png")
            header_file.seek(0)
            file = discord.File(fp=header_file, filename="category.png")
            await target.send(file=file)

            # send list of channels
            message: List[str] = []
            for i, channel in enumerate(
                sorted(category.text_channels, key=lambda ch: ch.name)
            ):
                if i > 9 and i % 10 == 0:
                    await target.send("\n".join(message))
                    message = []

                num: str = helper_utils.get_digit_emoji(i % 10)
                line = f"{num} **{channel.name}**" + (
                    f" {channel.topic}" if channel.topic else ""
                )
                message.append(line)
            await target.send("\n".join(message))

        await guild_log.info(
            ctx.author,
            ctx.channel,
            f"Initiated react2channel links in #{target.name}",
        )
        await ctx.reply(
            _(ctx, "Processed {channels} channels in {groups} channel groups.").format(
                channels=channel_count, groups=len(categories)
            )
        )

    #

    @commands.Cog.listener()
    async def on_message(self, message):
        """Listen for react2role message."""
        if not isinstance(message.channel, discord.TextChannel):
            return
        reaction_channel = ReactionChannel.get(message.guild.id, message.channel.id)
        if reaction_channel is None:
            return

        await self._handle_react2role_message_update(message, reaction_channel)

    @commands.Cog.listener()
    async def on_raw_message_edit(self, payload: discord.RawMessageUpdateEvent):
        """Listen for react2role message."""
        reaction_channel = ReactionChannel.get(payload.guild_id, payload.channel_id)
        if reaction_channel is None:
            return

        message = await utils.discord.get_message(
            self.bot,
            payload.guild_id or payload.user_id,
            payload.channel_id,
            payload.message_id,
        )
        await self._handle_react2role_message_update(message, reaction_channel)

    async def _handle_react2role_message_update(
        self, message: discord.Message, reaction_channel: ReactionChannel
    ):
        """Check react2role message for emoji changes."""
        # get react2xxx mapping
        mapping = await self._get_react2role_message_mapping(
            message, reaction_channel, announce_warnings=True
        )
        if mapping is None:
            return

        message_emojis = [r.emoji for r in message.reactions]

        mapping_diff: dict = {}
        for emoji in mapping.keys():
            if emoji not in message_emojis:
                await message.add_reaction(emoji)
                mapping_diff[emoji] = mapping[emoji]

        removed_emojis: list = []
        for emoji in message_emojis:
            if emoji not in mapping.keys():
                removed_emojis.append(emoji)

        if mapping_diff:
            diff_str = ", ".join(f"{k} => {v.name}" for k, v in mapping_diff.items())
            await guild_log.info(
                message.author,
                message.channel,
                (
                    f"react2{reaction_channel.channel_type.name.lower()} "
                    f"message updated: added {diff_str}."
                ),
            )

        if removed_emojis:
            diff_str = ", ".join(removed_emojis)
            await guild_log.info(
                message.author,
                message.channel,
                (
                    f"react2{reaction_channel.channel_type.name.lower()} "
                    f"message updated: removed {diff_str}."
                ),
            )

    async def _get_react2role_message_mapping(
        self,
        message: discord.Message,
        reaction_channel: ReactionChannel,
        *,
        announce_warnings: bool,
    ):
        """Get emoji-role or emoji-channel mapping from message."""
        content: List[str] = (
            message.content.replace("*", "")
            .replace("_", "")
            .replace("#", "")
            .split("\n")
        )
        content = [line.strip() for line in content]

        log_messages: List[str] = []

        mapping: dict = {}

        # Because we're converting stuff _here_, we rely on internal functions.
        # The first argument of .convert() is supposed to be 'commands.Context',
        # but as long as we supply all attributes, we should be fine.
        ctx = lambda: None  # noqa: E731
        ctx.bot = self.bot
        ctx.guild = message.guild

        for i, line in enumerate(content, 1):
            line_tokens = line.split(" ")
            if len(line_tokens) < 2:
                log_messages.append(
                    f"Line {i} of message {message.id} does not contain any mapping."
                )
                continue

            emoji_name: str = line_tokens[0]
            name: str = line_tokens[1]

            emoji = None
            try:
                emoji = await commands.EmojiConverter().convert(ctx, emoji_name)
            except commands.EmojiNotFound:
                # try to check if the string is emoji
                if emoji_name in UNICODE_EMOJI:
                    emoji = emoji_name

            if emoji is None:
                log_messages.append(
                    f"Line {i} of message {message.id} does not start with emoji."
                )
                continue

            if reaction_channel.channel_type == ReactionChannelType.ROLE:
                try:
                    target = await commands.RoleConverter().convert(ctx, name)
                except commands.BadArgument:
                    target = None
            else:
                try:
                    target = await commands.GuildChannelConverter().convert(ctx, name)
                except commands.BadArgument:
                    target = None

            if target is None:
                target_name: str = reaction_channel.channel_type.value
                await guild_log.error(
                    None,
                    message.channel,
                    (
                        f"React2Role error, line {i} of message {message.id} "
                        f"does does not have valid {target_name} "
                        f"after the emoji '{emoji}': '{name}' not found."
                    ),
                )
                return

            if emoji in mapping:
                await guild_log.error(
                    None,
                    message.channel,
                    f"React2Role error, line {i} of message {message.id}"
                    f" contains duplicate emoji {emoji}.",
                )
                return

            mapping[emoji] = target

        if log_messages and announce_warnings:
            await guild_log.warning(
                None,
                message.channel,
                "React2Role encountred unexpected lines: " + " ".join(log_messages),
            )

        return mapping

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        reaction_channel = ReactionChannel.get(payload.guild_id, payload.channel_id)
        if reaction_channel is None:
            return

        message = await utils.discord.get_message(
            self.bot,
            payload.guild_id or payload.user_id,
            payload.channel_id,
            payload.message_id,
        )

        mapping = await self._get_react2role_message_mapping(
            message, reaction_channel, announce_warnings=False
        )
        if mapping is None:
            return

        member = message.guild.get_member(payload.user_id)
        if member.bot:
            return

        if payload.emoji.is_custom_emoji():
            emoji = self.bot.get_emoji(payload.emoji.id) or payload.emoji
        else:
            emoji = payload.emoji.name

        if reaction_channel.channel_type == ReactionChannelType.CHANNEL:
            channel = mapping[emoji]
            await channel.set_permissions(member, view_channel=True)
            await guild_log.debug(
                member,
                message.channel,
                f"Adding permission to view {channel.name} ({channel.id}).",
            )
            return

        utx = i18n.TranslationContext(member.guild.id, member.id)

        role = mapping[emoji]
        # TODO Allow escalation under some conditions?
        if role >= member.top_role:
            await member.send(
                _(
                    utx,
                    (
                        "You cannot ask for role that's higher "
                        "than your current highest role."
                    ),
                )
            )
            await utils.discord.remove_reaction(message, emoji, member)
            return

        if reaction_channel.top_role is None or reaction_channel.bottom_role is None:
            # The channel does not have any limits
            await member.add_roles(role)
            return

        top_role = message.guild.get_role(reaction_channel.top_role)
        if top_role is None:
            await guild_log.error(
                member,
                message.channel,
                f"react2role top role {reaction_channel.top_role} is unavailable.",
            )
            await utils.discord.remove_reaction(message, emoji, member)
            return
        if role >= top_role:
            await member.send(_(utx, "This role can't be currently assigned."))
            await guild_log.debug(
                member,
                message.channel,
                (
                    f"react2role '{role}' cannot be assigned becase "
                    "it's higher than configured top role for the channel."
                ),
            )
            await utils.discord.remove_reaction(message, emoji, member)
            return

        bottom_role = message.guild.get_role(reaction_channel.bottom_role)
        if bottom_role is None:
            await guild_log.error(
                member,
                message.channel,
                f"react2role bottom role {reaction_channel.bottom_role} is unavailable.",
            )
            await utils.discord.remove_reaction(message, emoji, member)
            return
        if role <= bottom_role:
            await member.send(_(utx, "This role can't be currently assigned."))
            await utils.discord.remove_reaction(message, emoji, member)
            await guild_log.debug(
                member,
                message.channel,
                (
                    f"react2role '{role}' cannot be assigned becase "
                    "it's lower than configured bottom role for the channel."
                ),
            )
            await utils.discord.remove_reaction(message, emoji, member)
            return

        inbetween_roles: list = [r for r in member.roles if bottom_role < r < top_role]
        if len(inbetween_roles) >= reaction_channel.max_roles:
            await member.send(
                _(
                    utx,
                    (
                        "This role category has a limit of **{limit} roles**. "
                        "Remove some of your roles before adding new ones."
                    ),
                ).format(limit=reaction_channel.max_roles)
            )
            await utils.discord.remove_reaction(message, emoji, member)
            return

        await member.add_roles(role)
        await guild_log.debug(
            member,
            message.channel,
            f"Adding role {role.name} ({role.id}).",
        )

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        reaction_channel = ReactionChannel.get(payload.guild_id, payload.channel_id)
        if reaction_channel is None:
            return

        message = await utils.discord.get_message(
            self.bot,
            payload.guild_id or payload.user_id,
            payload.channel_id,
            payload.message_id,
        )

        mapping = await self._get_react2role_message_mapping(
            message, reaction_channel, announce_warnings=False
        )
        if mapping is None:
            return

        member = message.guild.get_member(payload.user_id)
        if member.bot:
            return

        if payload.emoji.is_custom_emoji():
            emoji = self.bot.get_emoji(payload.emoji.id) or payload.emoji
        else:
            emoji = payload.emoji.name

        if reaction_channel.channel_type == ReactionChannelType.CHANNEL:
            channel = mapping[emoji]
            await channel.set_permissions(member, overwrite=None)
            await guild_log.debug(
                member,
                message.channel,
                f"Removing permission to view {channel.name} ({channel.id}).",
            )
            return

        utx = i18n.TranslationContext(member.guild.id, member.id)

        role = mapping[emoji]
        if member.top_role == role:
            await member.send(_(utx, "You cannot remove your top role."))
            return

        if reaction_channel.top_role is None or reaction_channel.bottom_role is None:
            # The channel does not have any limits
            await member.remove_roles(role)
            return

        top_role = message.guild.get_role(reaction_channel.top_role)
        if top_role is None:
            await guild_log.error(
                member,
                message.channel,
                f"react2role top role {reaction_channel.top_role} is unavailable.",
            )
            return

        bottom_role = message.guild.get_role(reaction_channel.bottom_role)
        if bottom_role is None:
            await guild_log.error(
                member,
                message.channel,
                f"react2role bottom role {reaction_channel.bottom_role} is unavailable.",
            )
            return

        await member.remove_roles(role)
        await guild_log.debug(
            member,
            message.channel,
            f"Removing role {role.name} ({role.id}).",
        )


async def setup(bot) -> None:
    await bot.add_cog(React2Role(bot))
