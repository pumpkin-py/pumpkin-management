from typing import List
import shlex
import tempfile

import nextcord
from nextcord.ext import commands

from pie import check, i18n, logger, utils

from . import utils as helper_utils


_ = i18n.Translator("modules/mgmt").translate
guild_log = logger.Guild.logger()


class React2Role(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.check(check.acl)
    @commands.group(name="reaction-channel")
    async def reaction_channel(self, ctx):
        """Manage react-to-role channels."""
        await utils.discord.send_help(ctx)

    @commands.check(check.acl)
    @reaction_channel.command(name="init-channels")
    async def reaction_channel_init_channels(
        self, ctx, target: nextcord.TextChannel, *, groups: str
    ):
        """Initialise links for react-to-role functionality.

        :param target: Target text channel that will act as react-to-role hub.
        :param groups: List of group channels that will be linked from in this
            target channel.
        """
        categories: List[nextcord.CategoryChannel] = []
        for name in shlex.split(groups):
            category = nextcord.utils.get(ctx.guild.categories, name=name)
            if category is None:
                await ctx.reply(_(ctx, "That category does not exist here."))
                return
            categories.append(category)

        for group in categories:
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
            "Initiated react-to-role channel links.",
        )
        await ctx.reply(_(ctx, "Done."))


def setup(bot) -> None:
    bot.add_cog(React2Role(bot))
