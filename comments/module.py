from __future__ import annotations

from typing import List

import nextcord
from nextcord.ext import commands

from pie import check, i18n, logger, utils

from .database import Comment


_ = i18n.Translator("modules/mgmt").translate
guild_log = logger.Guild.logger()


class Comments(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.guild_only()
    @check.acl2(check.ACLevel.SUBMOD)
    @commands.group(name="comment")
    async def comment_(self, ctx):
        """Manage comments on guild users."""
        await utils.discord.send_help(ctx)

    @commands.guild_only()
    @check.acl2(check.ACLevel.SUBMOD)
    @comment_.command(name="list")
    async def comment_list(self, ctx, member: nextcord.Member):
        """List all comments of a user."""
        guild_id = ctx.guild.id
        if not member:
            member_name = _(ctx, "Unknown member")
        else:
            member_name = member.display_name
        result: str = (
            _(ctx, "Comments on member **{member_name}**:").format(
                member_name=member_name
            )
            + "\n"
        )
        comments: List[Comment] = Comment.get_user_comments(guild_id, member.id)
        if len(comments) < 1:
            return await ctx.reply(_(ctx, "User does not have any comments yet."))
        result += "\n".join(self._format_comment(ctx, comment) for comment in comments)
        await ctx.reply(result)

    @commands.guild_only()
    @check.acl2(check.ACLevel.SUBMOD)
    @comment_.command(name="add")
    async def comment_add(self, ctx, member: nextcord.Member, *, text: str):
        """Add comment to a user."""
        comment = Comment.add(
            ctx.guild.id, ctx.author.id, member.id, utils.text.sanitise(text)
        )
        await ctx.reply(_(ctx, "Comment successfully added."))
        await guild_log.info(
            ctx.author,
            ctx.channel,
            f"Comment {comment.idx} on user {member.id} added.",
        )

    @commands.guild_only()
    @check.acl2(check.ACLevel.SUBMOD)
    @comment_.command(name="remove")
    async def comment_remove(self, ctx, idx: int):
        """Remove a comment."""
        comment = Comment.get(ctx.guild.id, idx)
        if not comment:
            await ctx.reply(_(ctx, "Comment {idx} not found.").format(idx=idx))
            return
        Comment.remove(ctx.guild.id, idx)
        await ctx.reply(_(ctx, "Comment removed."))
        await guild_log.info(
            ctx.author,
            ctx.channel,
            f"Comment id {idx} about user {comment.user_id} removed.",
        )

    def _format_comment(self, ctx, comment: Comment) -> str:
        author = ctx.guild.get_member(comment.author_id)
        if not author:
            author_name = _(ctx, "Unknown author")
        else:
            author_name = author.display_name
        timestamp: str = utils.time.format_datetime(comment.timestamp)
        text = "\n".join(["> " + line for line in comment.text.split("\n")])
        return f"**{author_name}**, {timestamp} (ID {comment.idx}):\n{text}"


def setup(bot) -> None:
    bot.add_cog(Comments(bot))
