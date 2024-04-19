import asyncio
import contextlib
import datetime
import json
import os
import random
import re
import smtplib
import string
import tempfile
import unidecode
from typing import Dict, List, Union, Optional

from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import imap_tools

import discord
from discord.ext import commands

import pie.database.config
from pie import check, exceptions, i18n, logger, utils

from .enums import VerifyStatus
from .database import VerifyGroup, VerifyMember, VerifyMessage


_ = i18n.Translator("modules/mgmt").translate
bot_log = logger.Bot.logger()
guild_log = logger.Guild.logger()
config = pie.database.config.Config.get()


SMTP_SERVER: str = os.getenv("SMTP_SERVER")
IMAP_SERVER: str = os.getenv("IMAP_SERVER")
SMTP_ADDRESS: str = os.getenv("SMTP_ADDRESS")
SMTP_PASSWORD: str = os.getenv("SMTP_PASSWORD")


def test_dotenv() -> None:
    if type(SMTP_SERVER) != str:
        raise exceptions.DotEnvException("SMTP_SERVER is not set.")
    if type(SMTP_ADDRESS) != str:
        raise exceptions.DotEnvException("SMTP_ADDRESS is not set.")
    if type(SMTP_PASSWORD) != str:
        raise exceptions.DotEnvException("SMTP_PASSWORD is not set.")
    if type(IMAP_SERVER) != str:
        raise exceptions.DotEnvException("IMAP_SERVER is not set.")


test_dotenv()


MAIL_HEADER_PREFIX = "X-pumpkin.py-"


# TODO: confirm dialog for groupstrip and grouprolestrip


class Verify(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    #

    @commands.guild_only()
    @check.acl2(check.ACLevel.EVERYONE)
    @commands.command()
    async def verify(self, ctx, address: Optional[str] = None):
        """Ask for a verification code."""
        await utils.discord.delete_message(ctx.message)
        if not address:
            await ctx.send(
                _(ctx, "{mention} You have to include your e-mail.").format(
                    mention=ctx.author.mention
                ),
                delete_after=120,
            )
            return
        address = address.lower()

        # Check if user is in database
        if await self._member_exists(ctx, address):
            return

        # Check if address is in use
        if await self._address_exists(ctx, address):
            return

        # Check if address is supported
        if not await self._is_supported_address(ctx, address):
            return

        code: str = self._generate_code()
        VerifyMember.add(
            guild_id=ctx.guild.id,
            user_id=ctx.author.id,
            address=address,
            code=code,
            status=VerifyStatus.PENDING,
        )

        message: MIMEMultipart = self._get_message(
            ctx.author, ctx.channel, address, code
        )

        email_sent = await self._send_email(ctx, message)

        if not email_sent:
            return

        await guild_log.info(
            ctx.author,
            ctx.channel,
            "Verification e-mail sent.",
        )

        await ctx.send(
            _(
                ctx,
                (
                    "{mention} I've sent you the verification code "
                    "to the submitted e-mail."
                ),
            ).format(mention=ctx.author.mention),
            delete_after=120,
        )

        await self.post_verify(ctx, address)

    async def post_verify(self, ctx, address: str):
        """Wait some time after the user requested verification code.

        Then connect to IMAP server and check for possilibity that they used
        wrong, invalid e-mail. If such e-mails are found, they will be logged.

        :param address: User's e-mail address.
        """
        # TODO Use embeds when we support them.
        await asyncio.sleep(20)
        unread_messages = self._check_inbox_for_errors()
        for message in unread_messages:
            guild: discord.Guild = self.bot.get_guild(int(message["guild"]))
            user: discord.Member = self.bot.get_user(int(message["user"]))
            channel: discord.TextChannel = guild.get_channel(int(message["channel"]))
            await guild_log.warning(
                user,
                channel,
                "Could not deliver verification code: "
                f"{message['subject']} (User ID {message['user']})",
            )

            error_private: str = _(
                ctx,
                (
                    "I could not send the verification code, you've probably made "
                    "a typo: `{address}`. Invoke the command `{prefix}strip` "
                    "before requesting a new code."
                ),
            ).format(address=address, prefix=config.prefix)
            error_public: str = _(
                ctx,
                (
                    "I could not send the verification code, you've probably made "
                    "a typo. Invoke the command `{prefix}strip` "
                    "before requesting a new code."
                ),
            ).format(address=address, prefix=config.prefix)
            error_epilog: str = _(
                ctx,
                (
                    "If I'm wrong and the e-mail is correct, "
                    "contact the moderator team."
                ),
            )

            if not await utils.discord.send_dm(
                ctx.author,
                error_private + "\n" + error_epilog,
            ):
                await ctx.send(
                    error_public + "\n" + error_epilog,
                    delete_after=120,
                )

    @commands.guild_only()
    @check.acl2(check.ACLevel.EVERYONE)
    @commands.command()
    async def submit(self, ctx, code: Optional[str] = None):
        """Submit verification code."""
        await utils.discord.delete_message(ctx.message)
        if not code:
            await ctx.send(
                _(ctx, "{mention} You have to include your verification code.").format(
                    mention=ctx.author.mention
                ),
                delete_after=120,
            )
            return

        db_member = VerifyMember.get_by_member(ctx.guild.id, ctx.author.id)
        if db_member is None or db_member.code is None:
            await ctx.send(
                _(ctx, "{mention} You have to request the code first.").format(
                    mention=ctx.author.mention
                ),
                delete_after=120,
            )
            return

        if db_member.status != VerifyStatus.PENDING.value:
            await guild_log.info(
                ctx.author,
                ctx.channel,
                (
                    "Attempted to submit the code with bad status: "
                    f"`{VerifyStatus(db_member.status).name}`."
                ),
            )
            await ctx.send(
                _(
                    ctx,
                    (
                        "{mention} You are not in code verification phase. "
                        "Contact the moderator team."
                    ),
                ).format(mention=ctx.author.mention),
                delete_after=120,
            )
            return

        fixed_code: str = self._repair_code(code)
        if db_member.code != fixed_code:
            await guild_log.info(
                ctx.author,
                ctx.channel,
                f"Attempted to submit bad code: `{utils.text.sanitise(code)}`.",
            )
            await ctx.send(
                _(ctx, "{mention} That is not your verification code.").format(
                    mention=ctx.author.mention
                ),
                delete_after=120,
            )
            return

        db_member.status = VerifyStatus.VERIFIED.value
        db_member.save()

        await guild_log.info(ctx.author, ctx.channel, "Verification successfull.")

        await self._add_roles(ctx.author, db_member)

        config_message = None
        roles = self._map_address_to_groups(
            ctx.guild.id, ctx.author.id, db_member.address
        )
        for role in roles:
            # searching for role override
            config_message = VerifyMessage.get(ctx.guild.id, role.role_id)
            if config_message is not None:
                break
        if not config_message:
            config_message = VerifyMessage.get(ctx.guild.id, 0)
        if not config_message:
            await utils.discord.send_dm(
                ctx.author,
                _(ctx, "You have been verified, congratulations!"),
            )
        else:
            await utils.discord.send_dm(ctx.author, config_message.message)

        await ctx.send(
            _(ctx, "Member **{name}** has been verified.").format(
                name=utils.text.sanitise(ctx.author.name),
            ),
            delete_after=120,
        )

    @commands.guild_only()
    @check.acl2(check.ACLevel.MEMBER)
    @commands.command(name="strip")
    async def strip(self, ctx):
        """Remove all roles and reset verification status to None."""
        db_member = VerifyMember.get_by_member(ctx.guild.id, ctx.author.id)
        if db_member is not None and db_member.status < 0:
            await guild_log.info(
                ctx.author,
                ctx.channel,
                f"Strip attempt blocked, has status {VerifyStatus(db_member.status).value}.",
            )
            await ctx.reply(_(ctx, "Something went wrong, contact the moderator team."))
            return

        dialog = utils.discord.create_embed(
            author=ctx.author,
            title=_(ctx, "Strip"),
            description=_(
                ctx,
                (
                    "By clicking the confirm button you will have all your roles removed "
                    "and your verification will be revoked. "
                    "You will be able to perform new verification afterwards."
                ),
            ),
        )
        view = utils.objects.ConfirmView(ctx, dialog)
        view.timeout = 90
        answer = await view.send()
        if answer is not True:
            await ctx.reply(_(ctx, "Stripping aborted."))
            return

        roles = [role for role in ctx.author.roles if role.is_assignable()]

        with contextlib.suppress(discord.Forbidden):
            await ctx.author.remove_roles(*roles, reason="strip")
        removed: int = VerifyMember.remove(ctx.guild.id, ctx.author.id)

        message: str = "Stripped"
        if removed:
            message += " and removed from database"
        message += "."
        await guild_log.info(ctx.author, ctx.channel, message)

        await utils.discord.send_dm(
            ctx.author,
            _(
                ctx,
                (
                    "You've been deleted from the database "
                    "and your roles have been removed. "
                    "You have to go through verfication in order to get back."
                ),
            ),
        )
        await utils.discord.delete_message(ctx.message)

    @commands.guild_only()
    @check.acl2(check.ACLevel.SUBMOD)
    @commands.command()
    async def groupstrip(self, ctx, *members: Union[discord.Member, int]):
        """Remove all roles and reset verification status to None
        from multiple users. Users are not notified about this."""
        removed_db: int = 0
        removed_dc: int = 0

        dialog = utils.discord.create_embed(
            author=ctx.author,
            title=_(ctx, "Group strip"),
            description=_(
                ctx,
                (
                    "**{count}** mentioned users will lose all their roles and their "
                    "verification will be revoked. They will not be notified about this. "
                    "Do you want to continue?"
                ),
            ).format(count=len(members)),
        )
        view = utils.objects.ConfirmView(ctx, dialog)
        view.timeout = 90
        answer = await view.send()
        if answer is not True:
            await ctx.reply(_(ctx, "Stripping aborted."))
            return

        async with ctx.typing():
            for member in members:
                if isinstance(member, int):
                    member_id = member
                    member = ctx.guild.get_member(member_id)
                else:
                    member_id = member.id

                db_member = VerifyMember.get_by_member(ctx.guild.id, member_id)
                if db_member:
                    VerifyMember.remove(ctx.guild.id, member_id)
                    removed_db += 1
                if len(getattr(member, "roles", [])) > 1:
                    roles = [role for role in member.roles if role.is_assignable()]
                    with contextlib.suppress(discord.Forbidden):
                        await member.remove_roles(*roles, reason="groupstrip")
                    removed_dc += 1
                elif member is not None:
                    await ctx.send(
                        _(
                            ctx,
                            "Member **{member_id}** (<@{member_id}>) has no roles.",
                        ).format(member_id=member_id)
                    )
                else:
                    await ctx.send(
                        _(
                            ctx,
                            "Member **{member_id}** (<@{member_id}>) not found.",
                        ).format(member_id=member_id)
                    )

        await ctx.reply(
            _(
                ctx,
                (
                    "**{db}** database entries have been removed, "
                    "**{dc}** users have been stripped."
                ),
            ).format(db=removed_db, dc=removed_dc)
        )
        await guild_log.warning(
            ctx.author,
            ctx.channel,
            f"Removed {removed_db} users from database and "
            f"stripped {removed_dc} members with groupstrip.",
        )

    @commands.guild_only()
    @check.acl2(check.ACLevel.MOD)
    @commands.command()
    async def grouprolestrip(self, ctx, role: discord.Role):
        """Remove all roles and reset verification status to None
        from all the users that have given role. Users are not notified
        about this.
        """

        dialog = utils.discord.create_embed(
            author=ctx.author,
            title=_(ctx, "Role based strip"),
            description=_(
                ctx,
                (
                    "**{count}** members with role **{role}** will lose all their roles "
                    "and their verification will be revoked. They will not be notified "
                    "about this. Do you want to continue?"
                ),
            ).format(role=role.name, count=len(role.members)),
        )
        view = utils.objects.ConfirmView(ctx, dialog)
        view.timeout = 90
        answer = await view.send()
        if answer is not True:
            await ctx.reply(_(ctx, "Stripping aborted."))
            return

        removed_db: int = 0
        removed_dc: int = 0

        async with ctx.typing():
            for member in role.members:
                db_member = VerifyMember.get_by_member(ctx.guild.id, member.id)
                if db_member:
                    VerifyMember.remove(ctx.guild.id, member.id)
                    removed_db += 1
                if len(getattr(member, "roles", [])) > 1:
                    roles = [r for r in member.roles if r.is_assignable()]
                    with contextlib.suppress(discord.Forbidden):
                        await member.remove_roles(*roles, reason="grouprolestrip")
                    removed_dc += 1

        await ctx.reply(
            _(
                ctx,
                (
                    "**{db}** database entries have been removed, "
                    "**{dc}** users have been stripped."
                ),
            ).format(db=removed_db, dc=removed_dc)
        )
        await guild_log.warning(
            ctx.author,
            ctx.channel,
            f"Removed {removed_db} database entries and "
            f"stripped {removed_dc} members with group role strip on {role.name}.",
        )

    @commands.guild_only()
    @check.acl2(check.ACLevel.SUBMOD)
    @commands.group(name="welcome-message")
    async def welcome_message(self, ctx):
        await utils.discord.send_help(ctx)

    @check.acl2(check.ACLevel.MOD)
    @welcome_message.command(name="set")
    async def welcome_message_set(self, ctx, role: Union[discord.Role, int], *, text):
        """Set post verification message for your guild or a role.
        Insert role of a verify group, 0 for server default"""
        if text == "":
            ctx.reply(_(ctx, "Argument `text` must not be empty."))
            return
        if isinstance(role, discord.Role):
            verify_role = VerifyGroup.get_by_role(ctx.guild.id, role.id)
            role_id = verify_role.role_id
            if not role:
                await ctx.reply(
                    _(ctx, "Role {role} not found in verify configuration!").format(
                        role=role
                    )
                )
                return
        else:
            role_id = role
        VerifyMessage.set(ctx.guild.id, role_id, text)
        await ctx.reply(
            _(
                ctx,
                "Message has been set for group {role}.",
            ).format(role=_(ctx, "(Guild)") if role_id == 0 else role)
        )
        await guild_log.info(
            ctx.author, ctx.channel, f"Welcome message changed for group {role}."
        )

    @check.acl2(check.ACLevel.MOD)
    @welcome_message.command(name="unset")
    async def welcome_message_unset(self, ctx, role: Union[discord.Role, int] = 0):
        """Set verification message to default for your guild or a role."""
        if isinstance(role, discord.Role):
            role_id = role.id
        else:
            role_id = role
        VerifyMessage.unset(ctx.guild.id, role_id)
        await ctx.reply(
            _(ctx, "Welcome message has been set do default for group {role}.").format(
                role=_(ctx, "(Guild)") if role_id == 0 else role
            )
        )
        await guild_log.info(
            ctx.author,
            ctx.channel,
            f"Welcome message set to default for group {role}.",
        )

    @check.acl2(check.ACLevel.SUBMOD)
    @welcome_message.command(name="list")
    async def welcome_message_list(self, ctx):
        """Show verification messages."""

        class Item:
            def __init__(self, group: VerifyGroup):
                verify_message_obj = VerifyMessage.get(ctx.guild.id, group.role_id)
                self.role_id = group.role_id
                self.group_name = group.name
                self.message = getattr(verify_message_obj, "message", None)

        server_group = Item(VerifyGroup())
        server_group.group_name = _(ctx, "Server default")
        server_group.role_id = "-"
        server_group.message = getattr(
            VerifyMessage.get(ctx.guild.id, 0), "message", None
        )
        if not server_group.message:
            server_group.message = _(ctx, "You have been verified, congratulations!")
        groups = [server_group]
        configured_groups = [Item(group) for group in VerifyGroup.get_all(ctx.guild.id)]
        configured_groups = filter(
            lambda x: True if x.role_id > 0 and x.message is not None else False,
            configured_groups,
        )
        groups.extend(configured_groups)
        table: List[str] = utils.text.create_table(
            groups,
            header={
                "role_id": _(ctx, "Role ID"),
                "group_name": _(ctx, "Group name"),
                "message": _(ctx, "Message to send"),
            },
        )
        for page in table:
            await ctx.send("```" + page + "```")

    @commands.guild_only()
    @check.acl2(check.ACLevel.MOD)
    @commands.group(name="verification")
    async def verification(self, ctx):
        await utils.discord.send_help(ctx)

    @check.acl2(check.ACLevel.MOD)
    @verification.command(name="statistics", aliases=["stats"])
    async def verification_statistics(self, ctx):
        """Filter the data by verify status."""
        # TODO
        pass

    @check.acl2(check.ACLevel.MOD)
    @verification.command(name="update")
    async def verification_update(
        self, ctx, member: Union[discord.Member, int], status: str
    ):
        """Update the user's verification status."""
        status = status.upper()
        try:
            status_value = VerifyStatus[status].value
        except Exception:
            options = ", ".join([vs for vs in VerifyStatus.__members__])
            await ctx.reply(
                _(
                    ctx,
                    "Invalid verification status. " "Available options are: {options}.",
                ).format(status=status, options=options),
            )
            return

        if not VerifyMember.update(ctx.guild.id, member.id, status_value):
            await ctx.reply(_(ctx, "That member is not in the database."))
            return

        await guild_log.info(
            member,
            member.guild.text_channels[0],
            f"Verification status of {member} changed to {status}.",
        )
        await ctx.reply(
            _(
                ctx,
                "Member verification status of **{member}** has been updated to **{status}**.",
            ).format(member=utils.text.sanitise(member.display_name), status=status),
        )

    @check.acl2(check.ACLevel.MOD)
    @verification.group(name="groups")
    async def verification_groups(self, ctx):
        await utils.discord.send_help(ctx)

    @check.acl2(check.ACLevel.MOD)
    @verification_groups.command(name="list")
    async def verification_groups_list(self, ctx):
        """Display list of all verification groups."""
        embed = utils.discord.create_embed(
            author=ctx.author,
            title=_(ctx, "Verification groups"),
        )
        for group in VerifyGroup.get_all(ctx.guild.id):
            role_label: str = _(ctx, "Role")
            regex_label: str = _(ctx, "Regex")
            embed.add_field(
                name=group.name,
                value=f"{role_label} {group.role_id}\n{regex_label} `{group.regex}`",
                inline=False,
            )
        await ctx.reply(embed=embed)

    @check.acl2(check.ACLevel.MOD)
    @verification_groups.command(name="template")
    async def verification_groups_template(self, ctx):
        """Export template for verification groups."""
        filename: str = f"verification_{ctx.guild.id}_template.json"

        export = {
            "allow example.org": {
                "role_id": 1,
                "regex": "[a-z]{5}@example\\.org",
            },
            "allow example.com": {
                "role_id": 2,
                "regex": "[a-z]{7}[0-9]{3}@example\\.com",
            },
            "disallow evilcorp.com": {
                "role_id": -1,
                "regex": ".*@evilcorp\\.com",
            },
            "add to everyone else": {
                "role_id": 3,
                "regex": ".*",
            },
            "add to every allowed": {
                "role_id": 4,
                "regex": "",
            },
        }

        file = tempfile.TemporaryFile(mode="w+")
        json.dump(export, file, indent="\t")

        file.seek(0)
        await ctx.reply(
            _(ctx, "The template file has been exported."),
            file=discord.File(fp=file, filename=filename),
        )
        file.close()

    @check.acl2(check.ACLevel.MOD)
    @verification_groups.command(name="export")
    async def verification_groups_export(self, ctx):
        """Export current verification groups."""
        timestamp: str = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
        filename: str = f"verification_{ctx.guild.id}_{timestamp}.json"

        groups: List[VerifyGroup] = VerifyGroup.get_all(ctx.guild.id)
        export: Dict[str, Union[str, int]] = dict()

        for group in groups:
            group_dict: dict = group.dump()
            del group_dict["guild_id"]
            del group_dict["name"]
            export[group.name] = group_dict

        file = tempfile.TemporaryFile(mode="w+")
        json.dump(export, file, indent="\t", ensure_ascii=False)

        file.seek(0)
        await ctx.reply(
            _(ctx, "**{count}** verification groups have been exported.").format(
                count=len(groups)
            ),
            file=discord.File(fp=file, filename=filename),
        )
        file.close()
        await guild_log.info(ctx.author, ctx.channel, "Verification groups exported.")

    @check.acl2(check.ACLevel.MOD)
    @verification_groups.command(name="import")
    async def verification_groups_import(self, ctx):
        """Import new verification groups. This fully replaces old data."""
        if len(ctx.message.attachments) != 1:
            await ctx.reply(_(ctx, "I'm expecting one JSON file."))
            return
        if not ctx.message.attachments[0].filename.lower().endswith(".json"):
            await ctx.reply(_(ctx, "You have to upload a JSON file."))
            return

        # download the file
        data_file = tempfile.TemporaryFile()
        await ctx.message.attachments[0].save(data_file)

        data_file.seek(0)
        try:
            json_data = json.load(data_file)
        except json.decoder.JSONDecodeError as exc:
            await ctx.reply(_(ctx, "Your JSON file contains errors.") + f"\n> `{exc}`")
            data_file.close()
            return

        # export the groups, just to make sure
        await self.verification_groups_export(ctx)

        groups = self._replace_verification_groups(ctx.guild.id, json_data)
        data_file.close()

        await ctx.reply(
            _(
                ctx,
                (
                    "I've imported **{count}** verification groups. "
                    "Old groups have been backed up above."
                ),
            ).format(count=len(groups))
        )

    #

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        """Add the roles back if they have been verified before."""
        db_member = VerifyMember.get_by_member(member.guild.id, member.id)
        if db_member is None:
            return
        if db_member.status != VerifyStatus.VERIFIED.value:
            return

        await self._add_roles(member, db_member)
        # We need a channel to log the event in the guild log channel.
        # We are just picking the first one.
        await guild_log.info(
            member,
            member.guild.text_channels[0],
            "New member already in database, skipping verification.",
        )

    @commands.Cog.listener()
    async def on_member_ban(self, guild, member: Union[discord.Member, discord.User]):
        """When the member is banned, update the database status."""
        db_member = VerifyMember.get_by_member(guild.id, member.id)

        if db_member is not None:
            db_member.status = VerifyStatus.BANNED.value
            db_member.save()
            await guild_log.info(
                member,
                member.guild.text_channels[0],
                "Member has been banned, database status updated.",
            )
            return

        VerifyMember.add(
            guild_id=guild.id,
            user_id=member.id,
            address=None,
            code=None,
            status=VerifyStatus.BANNED,
        )
        await guild_log.info(
            member,
            member.guild.text_channels[0],
            "Member has been banned, adding to database.",
        )

    #

    # TODO Loop to check the inbox for error e-mails

    #

    async def _member_exists(self, ctx: commands.Context, address: str):
        """Check if VerifyMember exists in database.

        If the member exists, the event is logged and a response is
        sent to the user.

        :param ctx: Command context
        :param address: Supplied e-mail address
        """
        if VerifyMember.get_by_member(ctx.guild.id, ctx.author.id) is not None:
            await guild_log.debug(
                ctx.author,
                ctx.channel,
                (
                    "Attempted to verify with ID already in database: "
                    f"'{utils.text.sanitise(address, tag_escape=False)}'."
                ),
            )
            await ctx.send(
                _(
                    ctx,
                    (
                        "{mention} Your user account is already in the database. "
                        "Check the e-mail inbox or contact the moderator team."
                    ),
                ).format(mention=ctx.author.mention),
                delete_after=120,
            )
            return True

        return False

    async def _address_exists(self, ctx: commands.Context, address: str):
        """Check if member's e-mail exists in database.

        If the e-mail exists, the event is logged and a response is
        sent to the user.

        :param ctx: Command context
        :param address: Supplied e-mail address
        """
        if (
            db_member := VerifyMember.get_by_address(ctx.guild.id, address)
        ) is not None:
            dc_member: Optional[discord.User] = self.bot.get_user(db_member.user_id)
            dc_member_str: str = (
                f"'{utils.text.sanitise(dc_member.name)}' ({db_member.user_id})"
                if dc_member is not None
                else f"ID '{db_member.user_id}'"
            )
            await guild_log.info(
                ctx.author,
                ctx.channel,
                (
                    "Attempted to verify with address associated with different user: "
                    f"'{address}' is registered to account {dc_member_str} "
                    f"with status '{VerifyStatus(db_member.status).name}'."
                ),
            )

            await ctx.send(
                _(
                    ctx,
                    (
                        "{mention} This e-mail is already in the database "
                        "registered under different user account. "
                        "Login with that account and/or contact the moderator team."
                    ),
                ).format(mention=ctx.author.mention),
                delete_after=120,
            )
            return True

        return False

    async def _is_supported_address(self, ctx: commands.Context, address: str):
        """Check if the address has any verify groups.

        If the address is not supported, the event is logged and a response is
        sent to the user.

        :param ctx: Command context
        :param address: Supplied e-mail address
        """
        # Make the address domain case insensitive
        domain_regex: str = r"([^@]+$)"
        domain = re.search(domain_regex, address)
        if domain is not None:
            address = re.sub(domain_regex, domain.group(0).lower(), address)

        groups: List[VerifyGroup] = self._map_address_to_groups(
            ctx.guild.id, ctx.author.id, address, include_wildcard=False
        )
        if not len(groups):
            await guild_log.info(
                ctx.author,
                ctx.channel,
                f"Attempted to verify with unsupported address '{address}'.",
            )
            await ctx.send(
                _(ctx, "{mention} This e-mail cannot be used.").format(
                    mention=ctx.author.mention
                ),
                delete_after=120,
            )
            return False

        return True

    def _map_address_to_groups(
        self,
        guild_id: int,
        user_id: int,
        address: str,
        *,
        include_wildcard: bool = True,
    ) -> List[VerifyGroup]:
        """Try to get mapping from e-mail to verify groups.

        One or more groups may be returned.

        If a group with ``role_id`` of ``-1`` is found it means that the address
        shouldn't be accepted. In this case an empty list is returned.

        If the :attr:`VerifyGroup.regex` is empty string (``""``), it will not be
        included if ``include_wildcard`` is :class:`False`. This is useful when you want
        to check if there are also other, more strict matches.

        .. warning::

            Only the first group with non-empty regex is returned, and unlimited number
            of groups with empty regex. This allows you to implement fallback groups:
            if the e-mail explicitly doesn't match groups A nor B, it will be assigned
            to the group C.

        .. warning::

            Groups with empty regex MUST be included at the end. Otherwise other groups
            may never be matched.

        :param guild_id: Guild ID.
        :param user_id: User ID.
        :param address: User-submitted e-mail address.
        :param include_wildcard: If :class:`True`, even groups with empty regex will be
            matched.
        :return: List of matching verify groups.
        """
        query: List[VerifyGroup] = list()

        for group in VerifyGroup.get_all(guild_id):
            if group.regex == "" and include_wildcard:
                query.append(group)
                continue

            if re.fullmatch(group.regex, address) is None:
                continue

            if group.role_id == -1:
                return list()

            if len(query) > 0:
                # do not add another matching group
                continue

            query.append(group)

        return query

    def _generate_code(self):
        """Generate verification code."""
        letters: str = string.ascii_uppercase.replace("O", "").replace("I", "")
        code: str = "".join(random.choices(letters + string.digits, k=8))
        return code

    def _repair_code(self, code: str):
        """Repair user-submitted code.

        Return the uppercase version. Disallow capital ``i`` and ``o`` as they
        may be similar to ``1`` and ``0``.
        """
        return code.upper().replace("I", "1").replace("O", "0")

    def _get_message(
        self,
        member: discord.Member,
        channel: discord.TextChannel,
        address: str,
        code: str,
    ) -> MIMEMultipart:
        """Generate the verification e-mail."""
        BOT_URL = "https://github.com/pumpkin-py"

        utx = i18n.TranslationContext(member.guild.id, member.id)

        clear_list: List[str] = [
            _(
                utx,
                "Your verification code for Discord server {guild_name} is {code}.",
            ).format(guild_name=member.guild.name, code=code),
            _(utx, "You can use it by sending the following message:"),
            "  "
            + _(utx, "{prefix}submit {code}").format(prefix=config.prefix, code=code),
            _(utx, "to the channel named #{channel}.").format(channel=channel.name),
        ]
        clear: str = "\n".join(clear_list)

        message = MIMEMultipart("alternative")

        # TODO Instead of normalization to ASCII we should do encoding
        # so the accents are kept.
        # '=?utf-8?b?<base64 with plus instead of equals>?=' should work,
        # but it needs more testing.
        ascii_bot_name: str = unidecode.unidecode(self.bot.user.name)
        ascii_member_name: str = unidecode.unidecode(member.name)
        ascii_guild_name: str = unidecode.unidecode(member.guild.name)

        message["Subject"] = f"{ascii_guild_name} → {ascii_member_name}"
        message["From"] = f"{ascii_bot_name} <{SMTP_ADDRESS}>"
        message["To"] = f"{ascii_member_name} <{address}>"
        message["Bcc"] = f"{ascii_bot_name} <{SMTP_ADDRESS}>"

        message[MAIL_HEADER_PREFIX + "user"] = f"{member.id}"
        message[MAIL_HEADER_PREFIX + "bot"] = f"{self.bot.user.id}"
        message[MAIL_HEADER_PREFIX + "channel"] = f"{channel.id}"
        message[MAIL_HEADER_PREFIX + "guild"] = f"{member.guild.id}"
        message[MAIL_HEADER_PREFIX + "url"] = BOT_URL

        message.attach(MIMEText(clear, "plain"))

        return message

    async def _send_email(
        self, ctx, message: MIMEMultipart, retry: bool = True
    ) -> None:
        """Send the verification e-mail."""
        try:
            with smtplib.SMTP_SSL(SMTP_SERVER) as server:
                server.ehlo()
                server.login(SMTP_ADDRESS, SMTP_PASSWORD)
                server.send_message(message)
                return True
        except smtplib.SMTPException as exc:
            if retry:
                await bot_log.warning(
                    ctx.author,
                    ctx.channel,
                    "Could not send verification e-mail, trying again.",
                    exception=exc,
                )
                return await self._send_email(ctx, message, False)
            else:
                await bot_log.error(
                    ctx.author,
                    ctx.channel,
                    "Could not send verification e-mail.",
                    exception=exc,
                )
                await ctx.send(
                    _(
                        ctx,
                        (
                            "{mention} An error has occured while sending the code. "
                            "Contact the moderator team."
                        ),
                    ).format(mention=ctx.author.mention),
                    delete_after=120,
                )
                return False

    async def _add_roles(self, member: discord.Member, db_member: VerifyMember):
        """Add roles to the member."""
        groups: List[VerifyGroup] = self._map_address_to_groups(
            member.guild.id, member.id, db_member.address
        )
        roles: List[discord.Role] = list()
        for group in groups:
            roles.append(member.guild.get_role(group.role_id))
        await member.add_roles(*roles)

    def _replace_verification_groups(
        self, guild_id: int, json_data: dict
    ) -> List[VerifyGroup]:
        """Import JSON verification groups."""
        # TODO Should we be checking if some rules were added or removed?
        VerifyGroup.remove_all(guild_id)

        # TODO Should we be checking the data?

        groups: List[VerifyGroup] = list()
        for group_name, group_data in json_data.items():
            group = VerifyGroup.add(
                guild_id=guild_id,
                name=group_name,
                role_id=group_data["role_id"],
                regex=group_data["regex"],
            )
            groups.append(group)

        return groups

    def _check_inbox_for_errors(self):
        """Connect to the IMAP server and fetch unread e-mails.

        If the message contains verification headers, it will be returned as
        dictionary containing those headers.
        """
        unread_messages = []

        with imap_tools.MailBox(IMAP_SERVER).login(
            SMTP_ADDRESS, SMTP_PASSWORD
        ) as mailbox:
            messages = [
                m
                for m in mailbox.fetch(
                    imap_tools.AND(seen=False),
                    mark_seen=False,
                )
            ]
            mark_as_read: List = []

            for m in messages:
                has_delivery_status: bool = False

                for part in m.obj.walk():
                    if part.get_content_type() == "message/delivery-status":
                        has_delivery_status = True
                        break

                if not has_delivery_status:
                    continue

                rfc_message = m.obj.as_string()
                info: dict = {}

                for line in rfc_message.split("\n"):
                    if line.startswith(MAIL_HEADER_PREFIX):
                        key, value = line.split(":", 1)
                        info[key.replace(MAIL_HEADER_PREFIX, "")] = value.strip()
                if not info:
                    continue

                mark_as_read.append(m)
                info["subject"] = m.subject
                unread_messages.append(info)

            mailbox.flag(
                [m.uid for m in mark_as_read],
                (imap_tools.MailMessageFlags.SEEN,),
                True,
            )

        return unread_messages


async def setup(bot) -> None:
    await bot.add_cog(Verify(bot))
