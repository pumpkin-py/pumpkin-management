from __future__ import annotations

from random import choice
from typing import Optional

import discord
from discord.ext import commands, tasks
from discord.ext.commands.bot import Bot

import pie.database.config
from pie import i18n, logger, check, utils

from .database import VoiceSettings, LockedChannels

_ = i18n.Translator("modules/mgmt").translate
bot_log = logger.Bot.logger()
guild_log = logger.Guild.logger()
config = pie.database.config.Config.get()

HIGH_RES_CHANNEL_PREFIX = "HI-RES-"
ADJECTIVES = ["Red", "Green", "Blue", "Black", "White", "Pink", "Orange"]
NOUNS = [
    "cat",
    "dog",
    "elephant",
    "horse",
    "mouse",
    "fish",
    "octopus",
    "cockroach",
    "butterfly",
    "owl",
    "fox",
    "tiger",
    "bear",
    "sheep",
    "duck",
    "panda",
    "rabbit",
    "wolf",
]


class Voice(commands.Cog):
    """Module for dynamic VoiceChannel management.
    Requires a VoiceChannel category, in which the category permissions are set correctly!"""

    def __init__(self, bot: Bot):
        self.bot = bot
        self.sync_channels.start()
        self.admin_role_cache = set()

    @tasks.loop(seconds=60)
    async def sync_channels(self):
        """Main sync loop."""
        settings = VoiceSettings.get_all()
        for setting in settings:
            guild = self.bot.get_guild(setting.guild_id)
            await self._check_and_sync(guild)

    @sync_channels.before_loop
    async def before_sync(self):
        await self.bot.wait_until_ready()

    def cog_unload(self):
        self.sync_channels.cancel()

    @staticmethod
    def _check_is_in_voice(ctx):
        """Check if command is invoked from a VoiceChannel."""
        return isinstance(ctx.channel, discord.VoiceChannel)

    @staticmethod
    def _check_active_settings(ctx):
        """Check if Voice settings is deployed on the server."""
        return VoiceSettings.validate_settings(ctx.guild)

    @staticmethod
    def _check_category(ctx):
        """Check if command is invoked in the configured category."""
        category = ctx.channel.category
        if not category:
            return False
        return category.id == VoiceSettings.get(ctx.guild).category_id

    @staticmethod
    def _get_category(guild: discord.Guild, category_id: int):
        """Get category by ID."""
        return next((x for x in guild.categories if x.id == category_id), None)

    @staticmethod
    async def _create_channel(
        category: discord.CategoryChannel, bitrate: Optional[int] = None
    ):
        """Create a VoiceChannel in a specified category."""
        options = (
            {"bitrate": min(bitrate, int(category.guild.bitrate_limit))}
            if bitrate
            else dict()
        )
        name = f"{choice(ADJECTIVES)} {choice(NOUNS)}"
        if bitrate:
            name = HIGH_RES_CHANNEL_PREFIX + name
        await category.create_voice_channel(name, **options)

    @staticmethod
    async def _remove_channel(channel: discord.VoiceChannel):
        """Remove a VoiceChannel."""
        try:
            await channel.delete(reason="Cleanup.")
        except Exception:
            gtx = i18n.TranslationContext(channel.guild.id, None)
            await guild_log.warning(
                None,
                None,
                _(gtx, "Could not delete empty voicechannel id {id}.").format(
                    id=channel.id
                ),
            )

    @staticmethod
    async def _send_welcome_message(channel: discord.VoiceChannel):
        """Initiate a VoiceChannel."""
        gtx = i18n.TranslationContext(channel.guild.id, None)
        await channel.send(
            _(
                gtx,
                "Welcome. Voice channel can become invisible by calling the command `lock` (with this bot's prefix).",
            )
        )

    async def _check_and_sync(self, guild: discord.Guild):
        """Check if the settings is valid and proceed with VoiceChannel sync."""
        if not VoiceSettings.validate_settings(guild):
            return
        settings = VoiceSettings.get(guild)
        category = Voice._get_category(guild, settings.category_id)
        if not category:
            gtx = i18n.TranslationContext(guild.id, None)
            await guild_log.warning(
                None, guild, _(gtx, "Non-existent category for Voice Settings!")
            )
            return
        await self._sync(settings)

    @staticmethod
    async def __maintain_one_empty_channel(
        category: discord.CategoryChannel, bitrate: Optional[int] = None
    ):
        """Maintain an empty channel of a kind (normal/high bitrate) in a category."""
        high_resolution = False if bitrate is None else True
        # Includes channels with no visitors yet
        empty_channels = list(
            filter(
                lambda x: True
                if isinstance(x, discord.VoiceChannel)
                and (not x.members)
                and (high_resolution == x.name.startswith(HIGH_RES_CHANNEL_PREFIX))
                else False,
                category.channels,
            )
        )

        # Only used channels
        abandoned_channels = list(
            filter(lambda x: True if x.last_message else False, empty_channels)
        )
        if len(empty_channels) - len(abandoned_channels) < 1:
            await Voice._create_channel(category, bitrate)
        for abandoned in abandoned_channels:
            await Voice._remove_channel(abandoned)

    async def _sync(self, setting: VoiceSettings):
        """Sync VoiceChannels."""
        # First reduce DB rows containing invalid lock data.
        for locked_channel in LockedChannels.get_all():
            guild = self.bot.get_guild(locked_channel.guild_id)
            if not VoiceSettings.validate_settings(guild):
                LockedChannels.remove(locked_channel.channel_id)
                continue
            channel = guild.get_channel(locked_channel.channel_id)
            if not channel:
                LockedChannels.remove(locked_channel.channel_id)

        guild = self.bot.get_guild(setting.guild_id)
        category = self._get_category(guild, setting.category_id)
        if not category:
            return
        if setting.high_res_bitrate:
            await Voice.__maintain_one_empty_channel(category, setting.high_res_bitrate)
        await Voice.__maintain_one_empty_channel(category)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, beforeState, afterState):
        """Listener listening to VoiceChannel changes."""
        # Do not act if the feature is not turned on
        if not VoiceSettings.validate_settings(member.guild):
            return
        setting = VoiceSettings.get(member.guild)
        before = beforeState.channel
        after = afterState.channel

        # Do not act if no one has left or joined
        if before == after or (before is None and after is None):
            return
        # Do not act if the action is not in the configured category
        category = Voice._get_category(member.guild, setting.category_id)
        if before not in category.channels and after not in category.channels:
            return

        if after:
            # User has joined the `after` channel
            if after.category.id == setting.category_id:
                if after.last_message is None:
                    LockedChannels.set_lock(member.guild, after, False)
                    await Voice._send_welcome_message(after)
                await self._check_and_sync(member.guild)

        if before:
            if before.category.id == setting.category_id:
                # User has left the `before` channel
                if len(list(before.members)) < 1:
                    LockedChannels.remove(before)
                    await Voice._remove_channel(before)

    @commands.guild_only()
    @check.acl2(check.ACLevel.MEMBER)
    @commands.cooldown(1, 10, commands.BucketType.channel)
    @commands.check(_check_is_in_voice)
    @commands.check(_check_active_settings)
    @commands.check(_check_category)
    @commands.command(name="lock")
    async def lock(self, ctx):
        """Lock the channel. Members outside the channel will not see it afterward."""
        if LockedChannels.is_locked(ctx.channel):
            await ctx.reply(_(ctx, "Channel is already locked."))
            return
        LockedChannels.set_lock(ctx.guild, ctx.channel, True)
        settings = VoiceSettings.get(ctx.guild)
        category = Voice._get_category(ctx.guild, settings.category_id)
        for role in category.overwrites:
            if not isinstance(role, discord.Role):
                continue
            if role in self.admin_role_cache:
                continue
            try:
                await ctx.channel.set_permissions(role, view_channel=False)
            except Exception:
                self.admin_role_cache.add(role)
        await guild_log.debug(ctx.author, ctx.channel, _(ctx, "Voice channel locked."))
        await ctx.reply(_(ctx, "Channel locked."))

    @commands.guild_only()
    @check.acl2(check.ACLevel.MEMBER)
    @commands.check(_check_is_in_voice)
    @commands.check(_check_active_settings)
    @commands.check(_check_category)
    @commands.command(name="unlock")
    async def unlock(self, ctx):
        """Make the VoiceChannel visible again."""
        if not LockedChannels.is_locked(ctx.channel):
            await ctx.reply(_(ctx, "Channel is already unlocked."))
            return
        LockedChannels.set_lock(ctx.guild, ctx.channel, False)
        settings = VoiceSettings.get(ctx.guild)
        category = Voice._get_category(ctx.guild, settings.category_id)
        for role in category.overwrites:
            if not isinstance(role, discord.Role):
                continue
            await ctx.channel.set_permissions(role, overwrite=category.overwrites[role])
        await guild_log.debug(
            ctx.author, ctx.channel, _(ctx, "Voice channel unlocked.")
        )
        await ctx.reply(_(ctx, "Channel unlocked."))

    @commands.guild_only()
    @check.acl2(check.ACLevel.MOD)
    @commands.group(name="voice")
    async def voice(self, ctx):
        """VoiceChannel settings."""
        await utils.discord.send_help(ctx)

    @voice.command(
        name="category",
        aliases=["set_category", "set-category"],
    )
    async def voice_category(self, ctx, category: discord.CategoryChannel):
        """Set VoiceChannel category. Only channels in that category will be managed dynamically."""
        VoiceSettings.set_category(ctx.guild, category)
        await guild_log.info(
            ctx.author, ctx.channel, _(ctx, "Category for dynamic voice channels set.")
        )
        await ctx.reply(_(ctx, "Category for dynamic voice channels set."))
        await self._check_and_sync(ctx.guild)

    @voice.command(
        name="bitrate",
        aliases=["set_bitrate", "set-bitrate"],
    )
    async def voice_bitrate(self, ctx, bitrate: int):
        """Set bitrate for high resolution channels.
        This only applies to a specific kind of dynamic channels."""
        if not (64000 < bitrate <= ctx.guild.bitrate_limit):
            await ctx.reply(_(ctx, "Invalid bitrate for this server."))
            return
        VoiceSettings.set_high_bitrate(ctx.guild, bitrate)
        await guild_log.info(
            ctx.author,
            ctx.channel,
            _(ctx, "Bitrate for dynamic voice channels set to {bitrate}.").format(
                bitrate=bitrate
            ),
        )
        await ctx.reply(_(ctx, "Bitrate for dynamic voice channels set."))
        await self._check_and_sync(ctx.guild)

    @voice.command(name="disable", aliases=["stop", "unset", "delete", "remove"])
    async def voice_disable(self, ctx):
        """Stop dynamic VoiceChannels on this server."""
        VoiceSettings.remove(ctx.guild)
        await guild_log.info(
            ctx.author, ctx.channel, _(ctx, "Voice Channel Settings disabled.")
        )
        await ctx.reply(_(ctx, "Voice Channel Settings disabled."))

    @voice.command(name="list", aliases=["get", "show"])
    async def voice_list(self, ctx):
        """List the current settings."""
        settings = VoiceSettings.get(ctx.guild)
        if settings is None:
            await ctx.reply(_(ctx, "Dynamic VoiceChannels are disabled."))
            return
        category = Voice._get_category(ctx.guild, settings.category_id)

        embed = utils.discord.create_embed(
            author=ctx.author,
            title=_(ctx, "VoiceChannel configuration"),
        )
        embed.add_field(
            name=_(ctx, "Used category"),
            value=category or _(ctx, "Functionality not enabled."),
        )
        embed.add_field(
            name=_(ctx, "High bitrate"),
            value=settings.high_res_bitrate
            or _(ctx, "No high resolution settings in place."),
        )
        await ctx.send(embed=embed)


async def setup(bot) -> None:
    await bot.add_cog(Voice(bot))
