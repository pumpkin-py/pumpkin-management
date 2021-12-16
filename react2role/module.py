from typing import List
import shlex
import tempfile
from emoji import UNICODE_EMOJI as _UNICODE_EMOJI

import nextcord
from nextcord.ext import commands

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
    @commands.check(check.acl)
    @commands.group(name="reaction-channel")
    async def reaction_channel(self, ctx):
        """Manage react2role channels."""
        await utils.discord.send_help(ctx)

    @commands.check(check.acl)
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

    @commands.check(check.acl)
    @reaction_channel.command(name="add")
    async def reaction_channel_add(
        self, ctx, channel: nextcord.TextChannel, channel_type: str
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

    @commands.check(check.acl)
    @reaction_channel.command(name="unlimit")
    async def reaction_channel_unlimit(self, ctx, channel: nextcord.TextChannel):
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

    @commands.check(check.acl)
    @reaction_channel.command(name="limits", aliases=["set-limits"])
    async def reaction_channel_limits(
        self,
        ctx,
        channel: nextcord.TextChannel,
        top: nextcord.Role,
        bottom: nextcord.Role,
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

    @commands.check(check.acl)
    @reaction_channel.command(name="limit", aliases=["set-limit"])
    async def reaction_channel_limit(
        self, ctx, channel: nextcord.TextChannel, maximum: int
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

    @commands.check(check.acl)
    @reaction_channel.command(name="remove")
    async def reaction_channel_remove(self, ctx, channel: nextcord.TextChannel):
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

    @commands.check(check.acl)
    @reaction_channel.command(name="init-channels")
    async def reaction_channel_init_channels(
        self, ctx, target: nextcord.TextChannel, *, groups: str
    ):
        """Initialise links for react2role functionality.

        Args:
            target: Target text channel that will act as react2role hub.
            groups: List of group channels that will be linked from in this target channel.
        """
        categories: List[nextcord.CategoryChannel] = []
        channel_count: int = 0
        for name in shlex.split(groups):
            category = nextcord.utils.get(ctx.guild.categories, name=name)
            if category is None:
                await ctx.reply(_(ctx, "That category does not exist here."))
                return
            categories.append(category)

        for group in categories:
            channel_count += len(group.text_channels)
            # send header
            header_file = tempfile.TemporaryFile()
            header_image = helper_utils.generate_header(group.name)
            header_image.save(header_file, "png")
            header_file.seek(0)
            await target.send(file=nextcord.File(fp=header_file, filename="group.png"))

            # send list of channels
            message: List[str] = []
            for i, channel in enumerate(
                sorted(group.text_channels, key=lambda ch: ch.name)
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


def setup(bot) -> None:
    bot.add_cog(React2Role(bot))
