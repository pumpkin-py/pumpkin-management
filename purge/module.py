from __future__ import annotations

from typing import Optional

import discord
from discord.ext import commands
from pie import check, i18n, logger, utils

_ = i18n.Translator("modules/mgmt").translate
guild_log = logger.Guild.logger()


class Purge(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @staticmethod
    def _not_pinned(message: discord.Message) -> bool:
        return not message.pinned

    @commands.guild_only()
    @check.acl2(check.ACLevel.SUBMOD)
    @commands.command(name="purge")
    async def purge(self, ctx: commands.Context, count: Optional[int] = None):
        """Purge spam messages.

        Either reply to the oldest message you want to keep or provide a number of messages to delete.

        This command keeps pinned messages intact.
        """
        channel = ctx.channel
        msg = ctx.message
        if msg.type == discord.MessageType.reply:
            if count is not None:
                await ctx.reply(
                    _(
                        ctx,
                        "Please use either a reply or provide a number of messages to delete. Not both.",
                    )
                )
                return
            replied_to = msg.reference.cached_message
            if replied_to is None:
                replied_to = await ctx.fetch_message(msg.reference.message_id)

            embed = utils.discord.create_embed(
                author=ctx.author,
                title=_(ctx, "Confirm delete."),
            )
            embed.add_field(
                name=_(ctx, "Delete messages after"),
                value=replied_to.jump_url,
                inline=False,
            )
            view = utils.ConfirmView(ctx, embed)

            value = await view.send()

            if value is None:
                await ctx.send(_(ctx, "Confirmation timed out."))
                return
            elif value:
                deleted = await channel.purge(
                    after=replied_to.created_at,
                    before=ctx.message.created_at,
                    check=self._not_pinned,
                )
            else:
                await ctx.send(_(ctx, "Aborted."))
                return

        elif count is not None:
            if count > 10:
                embed = utils.discord.create_embed(
                    author=ctx.author,
                    title=_(ctx, "Confirm delete."),
                )
                embed.add_field(
                    name=_(ctx, "Number of messages to delete"),
                    value=str(count),
                    inline=False,
                )
                view = utils.ConfirmView(ctx, embed)

                value = await view.send()

                if value is None:
                    await ctx.send(_(ctx, "Confirmation timed out."))
                    return
                elif value:
                    deleted = await channel.purge(
                        limit=count,
                        before=ctx.message.created_at,
                        check=self._not_pinned,
                    )
                else:
                    await ctx.send(_(ctx, "Aborted."))
                    return
            else:
                deleted = await channel.purge(
                    limit=count,
                    before=ctx.message.created_at,
                    check=self._not_pinned,
                )

        else:
            await ctx.reply(
                _(
                    ctx,
                    "Please use either a reply or provide a number of messages to delete.",
                )
            )
            return

        await guild_log.info(
            ctx.author,
            ctx.channel,
            f"Deleted {len(deleted)} message(s)",
        )
        await channel.send(
            _(ctx, "Deleted {deleted} message(s)").format(deleted=len(deleted))
        )


async def setup(bot) -> None:
    await bot.add_cog(Purge(bot))
