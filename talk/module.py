import discord
from discord.ext import commands

from core import acl, text, logging, utils

tr = text.Translator(__file__).translate
bot_log = logging.Bot.logger()
guild_log = logging.Guild.logger()


class Talk(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    async def repeat(self, ctx, *, message: str):
        """Repeat, a demonstration of sanitisation.

        Untrusted user input shouldn't be blindly repeated back, to prevent
        excessive tagging (role tags are disabled, but user tags are not).

        The 'sanitise()' also has an 'escape' boolean parameter, which can
        be used to print user nicknames without them being rendered in
        markdown. We can disable this feature here.
        """
        safe_message: str = utils.Text.sanitise(message, escape=False)
        await ctx.send(safe_message)

    @commands.command()
    async def hi(self, ctx):
        """'Hi!', a demonstration of translation.

        Every command MUST have a key with its fully-qualified name
        in 'lang/{en,cs}.ini' file relative to this module file.

        Each file has '[module]' section with 'help' key. Each command has
        'help' key. Generic command reply SHOULD be the 'reply' key.

        Some language have gender-dependent grammar. This is solved by using
        '.f' (female) and '.m' (male) suffix to the key.
        """
        await ctx.send(tr("hi", "reply"))

    @commands.check(acl.check)
    @commands.command()
    async def power(self, ctx):
        """'Power', an ACL demonstration.

        To allow dynamic permission management, each command may have the
        'acl.check' call. See the ACL module documentation for more
        information.
        """
        await ctx.send(tr("power", "reply"))

    @commands.command()
    async def guildlog(self, ctx, *, message: str):
        """Guild log should be used for guild-wide manipulations.

        Most of the code uses this kind of logs, no module should be
        writing bot-wide data unless neccesary.
        """
        await guild_log.debug(ctx.author, ctx.channel, "Guild log with 'DEBUG' level.")
        await guild_log.info(ctx.author, ctx.channel, "Guild log with 'INFO' level.")
        await guild_log.warning(ctx.author, ctx.channel, "Guild log with 'WARNING' level.")
        await guild_log.error(ctx.author, ctx.channel, "Guild log with 'ERROR' level.")
        await guild_log.critical(ctx.author, ctx.channel, "Guild log with 'CRITICAL' level.")

    @commands.command()
    async def botlog(self, ctx, *, message: str):
        """Bot log should be used for changes to the bot itself.

        This is used in the 'base.admin' module for unloading modules,
        for example.
        """
        await bot_log.debug(ctx.author, ctx.channel, "Bot log with 'DEBUG' level.")
        await bot_log.info(ctx.author, ctx.channel, "Bot log with 'INFO' level.")
        await bot_log.warning(ctx.author, ctx.channel, "Bot log with 'WARNING' level.")
        await bot_log.error(ctx.author, ctx.channel, "Bot log with 'ERROR' level.")
        await bot_log.critical(ctx.author, ctx.channel, "Bot log with 'CRITICAL' level.")


def setup(bot) -> None:
    bot.add_cog(Talk(bot))
