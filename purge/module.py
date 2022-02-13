from __future__ import annotations


import nextcord
from nextcord.ext import commands

from pie import check, i18n, logger


_ = i18n.Translator("modules/mgmt").translate
guild_log = logger.Guild.logger()


class Purge(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @staticmethod
    def _not_pinned(message: nextcord.Message) -> bool:
        return not message.pinned

    @commands.guild_only()
    @check.acl2(check.ACLevel.SUBMOD)
    @commands.command(name="purge")
    async def purge(self, ctx: commands.Context, num_to_delete: int = None):
        """Purge spam messages.

        Either reply to the oldest message you want to keep or provide a number of messages to delete.

        This command keeps pinned messages intact.
        """
        channel = ctx.channel
        msg = ctx.message
        if msg.type == nextcord.MessageType.reply:
            if num_to_delete is not None:
                await channel.reply(
                    _(
                        ctx,
                        "Please use either a reply or provide a number of messages to delete. Not both",
                    )
                )
                return
            replied_to = msg.reference.cached_message
            if replied_to is None:
                replied_to = await ctx.fetch_message(msg.reference.message_id)
            time = replied_to.created_at
            deleted = await channel.purge(after=time, check=self._not_pinned)
        else:
            deleted = await channel.purge(limit=num_to_delete, check=self._not_pinned)

        await guild_log.info(
            ctx.author,
            ctx.channel,
            f"Deleted {len(deleted)} message(s)",
        )
        await channel.send(
            _(ctx, "Deleted {deleted} message(s)").format(deleted=len(deleted))
        )


def setup(bot) -> None:
    bot.add_cog(Purge(bot))
