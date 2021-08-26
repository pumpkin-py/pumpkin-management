from typing import List
import shlex
import tempfile

import discord
from discord.ext import commands

from core import check, logging, text, utils

from . import utils as helper_utils


tr = text.Translator(__file__).translate
guild_log = logging.Guild.logger()


class Channels(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.check(check.acl)
    @commands.group(name="reaction-channel")
    async def reaction_channel(self, ctx):
        await utils.Discord.send_help(ctx)

    @commands.check(check.acl)
    @reaction_channel.command(name="init-channels")
    async def reaction_channel_init_channels(
        self, ctx, target: discord.TextChannel, *, groups: str
    ):
        """Initialise links for react-to-role functionality.

        :param target: Target text channel that will act as react-to-role hub.
        :param groups: List of group channels that will be linked from in this
            target channel.
        """
        categories: List[discord.CategoryChannel] = []
        for name in shlex.split(groups):
            category = discord.utils.get(ctx.guild.categories, name=name)
            if category is None:
                await ctx.reply(
                    tr(
                        "reaction-channel_init-channels",
                        "not found",
                        ctx,
                        name=utils.Text.sanitise(name),
                    )
                )
                return
            categories.append(category)

        for group in categories:
            # send header
            header_file = tempfile.TemporaryFile()
            header_image = helper_utils.generate_header(group.name)
            header_image.save(header_file, "png")
            header_file.seek(0)
            await target.send(file=discord.File(fp=header_file, filename="group.png"))

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
        await ctx.reply("Done.")


def setup(bot) -> None:
    bot.add_cog(Channels(bot))
