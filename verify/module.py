import asyncio
import contextlib
import csv
import os
import random
import smtplib
import string
import tempfile
import unidecode
from typing import List, Union, Optional

from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import imap_tools

import discord
from discord.ext import commands

import pie.database.config
from pie import check, exceptions, i18n, logger, utils

from .enums import VerifyStatus
from .database import (
    VerifyRole,
    VerifyMapping,
    VerifyMember,
    VerifyMessage,
    VerifyRule,
)


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


class Verify(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

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
        # TODO Use embeds
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

        db_member = VerifyMember.get(guild_id=ctx.guild.id, user_id=ctx.author.id)
        if not db_member or db_member[0].code is None:
            await ctx.send(
                _(ctx, "{mention} You have to request the code first.").format(
                    mention=ctx.author.mention
                ),
                delete_after=120,
            )
            return

        db_member = db_member[0]

        if db_member.status != VerifyStatus.PENDING:
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

        mapping = VerifyMapping.map(guild_id=ctx.guild.id, address=db_member.address)

        if not mapping or not mapping.rule or not mapping.rule.groups:
            await ctx.send(
                _(ctx, "Could not assign roles. Please contact moderator team.")
            )
            await guild_log.error(
                ctx.author,
                ctx.channel,
                "Member could not be verified due to missing mapping, rule or roles. Rule name: {name}".format(
                    name=mapping.rule.name if mapping.rule else "(None)"
                ),
            )
            return

        await self._add_roles(ctx.author, mapping.rule.groups)

        config_message = mapping.rule.message

        db_member.status = VerifyStatus.VERIFIED
        db_member.save()

        await guild_log.info(ctx.author, ctx.channel, "Verification successfull.")

        if not config_message:
            config_message = VerifyMessage.get_default(ctx.guild.id)
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
        db_member = VerifyMember.get(guild_id=ctx.guild.id, user_id=ctx.author.id)
        if db_member:
            db_member = db_member[0]

        if db_member and db_member.status.value < VerifyStatus.NONE.value:
            await guild_log.info(
                ctx.author,
                ctx.channel,
                f"Strip attempt blocked, has status {VerifyStatus(db_member.status)}.",
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

        message: str = "Stripped"
        if db_member:
            db_member.remove()
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

    @commands.guild_only()
    @check.acl2(check.ACLevel.SUBMOD)
    @verification.command(name="groupstrip")
    async def verification_groupstrip(self, ctx, *members: Union[discord.Member, int]):
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

                db_member = VerifyMember.get(guild_id=ctx.guild.id, user_id=member_id)
                if db_member:
                    db_member[0].remove()
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
    @verification.command(name="grouprolestrip")
    async def verification_grouprolestrip(self, ctx, role: discord.Role):
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
                db_member = VerifyMember.get(guild_id=ctx.guild.id, user_id=member.id)
                if db_member:
                    db_member[0].remove()
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
    @verification.group(name="message")
    async def verification_message(self, ctx):
        await utils.discord.send_help(ctx)

    @check.acl2(check.ACLevel.MOD)
    @verification_message.command(name="set")
    async def verification_message_set(self, ctx, rule_name: str, *, text):
        """Set post verification message for your guild or a rule.

        Args:
            rule_name: Name of the rule. Leave empty (`""`) for guild.
        """
        if text == "":
            ctx.reply(_(ctx, "Argument `text` must not be empty."))
            return
        if len(rule_name):
            rule = VerifyRule.get(guild_id=ctx.guild.id, name=rule_name)
            if not rule:
                await ctx.reply(
                    _(ctx, "Rule named {name} was not found!").format(name=rule_name)
                )
                return
        else:
            rule = None
        VerifyMessage.set(ctx.guild.id, text, rule)
        await ctx.reply(
            _(
                ctx,
                "Message has been set for rule {rule}.",
            ).format(role=_(ctx, "(Guild)") if not len(rule_name) else rule_name)
        )
        await guild_log.info(
            ctx.author,
            ctx.channel,
            "Welcome message changed for rule {}.".format(
                "(Guild)" if not len(rule_name) else rule_name
            ),
        )

    @check.acl2(check.ACLevel.MOD)
    @verification_message.command(name="unset")
    async def verification_message_unset(self, ctx, rule_name: str):
        """Unset verification message for your guild or a rule.

        Args:
            rule_name: Name of the rule. Leave empty (`""`) for guild.
        """
        if rule_name:
            rule = VerifyRule.get(guild_id=ctx.guild.id, name=rule_name)
            if rule:
                message = rule.message
            else:
                await ctx.reply(
                    _(ctx, "Rule named {name} was not found!").format(name=rule_name)
                )
                return
        else:
            message = VerifyMessage.get_default(ctx.guild.id)

        if message:
            message.delete()

        await ctx.reply(
            _(ctx, "Welcome message has been set to default for rule {rule}.").format(
                rule=_(ctx, "(Guild)") if not rule_name else rule_name
            )
        )
        await guild_log.info(
            ctx.author,
            ctx.channel,
            f"Welcome message set to default for rule {rule_name}.",
        )

    @check.acl2(check.ACLevel.SUBMOD)
    @verification_message.command(name="list")
    async def verification_message_list(self, ctx):
        """Show verification messages."""

        class Item:
            def __init__(self, message: VerifyMessage = None):
                if not message or not message.rule:
                    return
                self.rule = message.rule.name
                self.message = message.text

        default_message = Item()
        default_message.rule = _(ctx, "Server default")
        default_message.message = getattr(
            VerifyMessage.get_default(ctx.guild.id),
            "message",
            _(ctx, "You have been verified, congratulations!"),
        )
        messages = [default_message]
        configured_messages = [
            Item(message) for message in VerifyMessage.get_all(ctx.guild.id)
        ]
        configured_messages = filter(
            lambda x: True if x.rule and x.message is not None else False,
            configured_messages,
        )
        messages.extend(configured_messages)

        table: List[str] = utils.text.create_table(
            messages,
            header={
                "rule": _(ctx, "Rule name"),
                "message": _(ctx, "Message to send"),
            },
        )
        for page in table:
            await ctx.send("```" + page + "```")

    @check.acl2(check.ACLevel.MOD)
    @verification.command(name="update")
    async def verification_update(
        self, ctx, member: Union[discord.Member, int], status: str
    ):
        """Update the user's verification status."""
        status = status.upper()
        try:
            status_value = VerifyStatus[status]
        except Exception:
            options = ", ".join([vs for vs in VerifyStatus.__members__])
            await ctx.reply(
                _(
                    ctx,
                    "Invalid verification status. " "Available options are: {options}.",
                ).format(status=status, options=options),
            )
            return

        verify_member = VerifyMember.get(ctx.guild.id, user_id=member.id)

        if not verify_member:
            await ctx.reply(_(ctx, "That member is not in the database."))
            return

        verify_member[0].status = status_value
        verify_member[0].save()

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

    @commands.guild_only()
    @check.acl2(check.ACLevel.MOD)
    @verification.group(name="mapping")
    async def verification_mapping(self, ctx):
        await utils.discord.send_help(ctx)

    @check.acl2(check.ACLevel.MOD)
    @verification_mapping.command(name="info")
    async def verification_mapping_get(self, ctx, username: str, domain: str):
        """Get mapping information by username and domain.

        Args:
            username: Username. Leave empty (`""`) for domain default mapping.
            domain: Domain. Leave empty (`""`) for guild default mapping.

        """
        await utils.discord.delete_message(ctx.message)

        mapping = VerifyMapping.map(
            guild_id=ctx.guild.id, username=username, domain=domain
        )

        embed = utils.discord.create_embed(
            author=ctx.author,
            title=_(ctx, "Mapping for {username}@{domain}").format(
                username=username, domain=domain
            ),
        )

        embed.add_field(
            name=_(ctx, "Applied mapping:"),
            value=mapping.username + "@" + mapping.domain if mapping else "-",
        )

        embed.add_field(
            name=_(ctx, "Verification allowed:"),
            value=_(ctx, "True") if mapping and mapping.rule else _(ctx, "False"),
        )

        embed.add_field(
            name=_(ctx, "Rule name:"),
            value=mapping.rule.name if mapping and mapping.rule else "-",
        )

        await ctx.send(embed=embed)

    @check.acl2(check.ACLevel.MOD)
    @commands.max_concurrency(1, per=commands.BucketType.default, wait=False)
    @verification_mapping.command(name="import")
    async def verification_mapping_import(self, ctx, wipe: bool = False):
        """Import mapping data.

        The file must be CSV and must have this format:
        `˙˙username;domain;rule_name```

        Where username is the part before @ sign in email and domain is the part after @ sign.

        For domain global rule leave username empty.
        For global rule leave username and domain empty.

        Args:
            wipe: Remove all mapping data and do clean import.
        """
        if len(ctx.message.attachments) != 1:
            await ctx.reply(_(ctx, "I'm expecting one CSV file."))
            return
        if not ctx.message.attachments[0].filename.lower().endswith("json"):
            await ctx.reply(_(ctx, "Supported format is only CSV."))
            return
        await ctx.reply(_(ctx, "Processing. Make a coffee, it may take a while."))

        if wipe:
            async with ctx.typing():
                wiped = VerifyMapping.wipe(ctx.guild_id)
                await ctx.reply(_(ctx, "Wiped {wiped} mappings.").format(wiped=wiped))

        async with ctx.typing():
            data_file = tempfile.TemporaryFile()
            await ctx.message.attachments[0].save(data_file)
            data_file.seek(0)
            csv_reader = csv.reader(data_file)

            count = 0

            for row in csv_reader:
                count += 1
                if len(row) != 3:
                    await ctx.reply(
                        _(ctx, "Row {row} has invalid number of columns!").format(
                            row=count
                        )
                    )
                    continue

                username, domain, rule_name = row
                rule = None

                if len(rule_name):
                    rule = VerifyRule.get(guild_id=ctx.guild.id, name=rule_name)
                    if not rule:
                        await ctx.reply(
                            _(ctx, "Row {row} has invalid rule name: {name}!").format(
                                row=count, name=rule_name
                            )
                        )
                        continue

                VerifyMapping.add(
                    guild_id=ctx.guild.id, username=username, domain=domain, rule=rule
                )

        data_file.close()

        await ctx.reply(_(ctx, "Imported {count} mappings.").format(count=count))

    @check.acl2(check.ACLevel.MOD)
    @verification_mapping.command(name="remove")
    async def verification_mapping_remove(self, ctx, username: str, domain: str):
        mapping = VerifyMapping.get(
            guild_id=ctx.guild.id, username=username, domain=domain
        )

        if not mapping:
            await ctx.reply(
                _(ctx, "Mapping for {name}@{domain} not found!").format(
                    name=username, domain=domain
                )
            )
            return

        dialog = utils.discord.create_embed(
            author=ctx.author,
            title=_(ctx, "Mapping remove"),
            description=_(
                ctx, "Do you really want to remove mapping for {name}@{domain}?"
            ).format(
                name=username,
                domain=domain,
            ),
        )
        view = utils.objects.ConfirmView(ctx, dialog)
        view.timeout = 90
        answer = await view.send()
        if answer is not True:
            await ctx.reply(_(ctx, "Removing aborted."))
            return

        mapping.delete()
        await ctx.reply(_(ctx, "Mapping successfuly removed."))

    @commands.guild_only()
    @check.acl2(check.ACLevel.MOD)
    @verification.group(name="rule")
    async def verification_rule(self, ctx):
        await utils.discord.send_help(ctx)

    @check.acl2(check.ACLevel.MOD)
    @verification_rule.command(name="add")
    async def verification_rule_add(self, ctx, name: str, roles: List[discord.Role]):
        """Add new verification rule. Name must be unique.

        Assign Discord roles to rule (if provided).

        Rule without roles will not work in verification process and must be fixed later on!

        Args:
            name: Name of the rule.
            roles: List of Discord roles (optional)

        """
        rule = VerifyRule.add(guild_id=ctx.guild.id, name=name)

        if not rule:
            await ctx.reply(
                _(ctx, "Rule with name {name} already exists!").format(name=name)
            )
            return

        role_ids = [role.id for role in roles]
        rule.add_roles(role_ids)

        await ctx.reply(_(ctx, "Rule with name {name} added!").format(name=name))

    @check.acl2(check.ACLevel.MOD)
    @verification_rule.command(name="remove")
    async def verification_rule_remove(self, ctx, name: str):
        """Remove verification rule.

        Args:
            name: Name of the rule.
        """
        rule = VerifyRule.get(guild_id=ctx.guild.id, name=name)

        if not rule:
            await ctx.reply(
                _(ctx, "Rule with name {name} not found!").format(name=name)
            )
            return

        dialog = utils.discord.create_embed(
            author=ctx.author,
            title=_(ctx, "Rule remove"),
            description=_(ctx, "Do you really want to remove rule {name}?").format(
                name=name
            ),
        )
        view = utils.objects.ConfirmView(ctx, dialog)
        view.timeout = 90
        answer = await view.send()
        if answer is not True:
            await ctx.reply(_(ctx, "Removing aborted."))
            return

        rule.delete()

        await ctx.reply(_(ctx, "Rule {name} successfuly removed.").format(name=name))

    @check.acl2(check.ACLevel.MOD)
    @verification_rule.command(name="list")
    async def verification_rule_list(self, ctx):
        """List all rules."""
        rules = VerifyRule.get(guild_id=ctx.guild.id)

        class Item:
            def __init__(self, rule):
                self.name = rule.name
                self.role_count = len(rule.roles)

        items = []

        for rule in rules:
            items.append(Item(rule))

        table: List[str] = utils.text.create_table(
            items,
            header={
                "rule": _(ctx, "Rule name"),
                "role_count": _(ctx, "Role count"),
            },
        )

        for page in table:
            await ctx.send("```" + page + "```")

    @check.acl2(check.ACLevel.MOD)
    @verification_rule.command(name="list")
    async def verification_rule_info(self, ctx, name):
        """Show information about rule.

        Args:
            name: Rule name"""
        rule = VerifyRule.get(guild_id=ctx.guild.id, name=name)

        if not rule:
            await ctx.reply(
                _(ctx, "Rule with name {name} not found!").format(name=name)
            )
            return

        embed = utils.discord.create_embed(
            author=ctx.author,
            title=_(ctx, "Rule information"),
            description=rule.name,
        )

        embed.add_field(
            name=_(ctx, "Has custom message:"),
            value=_(ctx, "True") if rule.message else _(ctx, "False"),
        )

        roles = []

        for db_role in rule.roles:
            role = ctx.guild.get_role(db_role.role_id)
            if role:
                roles.append(role.mention)
            else:
                roles.append(f"{db_role.role_id} (DELETED)")

        embed.add_field(
            name=_(ctx, "Assigned roles:"),
            value="\n".join(roles),
        )

        await ctx.reply(embed=embed)

    @check.acl2(check.ACLevel.MOD)
    @verification_rule.command(name="addroles", aliases=["add-roles"])
    async def verification_rule_addroles(
        self, ctx, name: str, roles: List[discord.Role]
    ):
        rule = VerifyRule.get(guild_id=ctx.guild.id, name=name)

        if not rule:
            await ctx.reply(
                _(ctx, "Rule with name {name} not found!").format(name=name)
            )
            return

        role_ids = [role.id for role in roles]
        rule.add_roles(role_ids)

        await ctx.reply(_(ctx, "Roles added to rule {name}!").format(name=name))

    @check.acl2(check.ACLevel.MOD)
    @verification_rule.command(name="removeroles", aliases=["remove-roles"])
    async def verification_rule_removeroles(
        self, ctx, name: str, roles: List[discord.Role]
    ):
        rule = VerifyRule.get(guild_id=ctx.guild.id, name=name)

        if not rule:
            await ctx.reply(
                _(ctx, "Rule with name {name} not found!").format(name=name)
            )
            return

        role_ids = [role.id for role in roles]
        rule.delete_roles(role_ids)

        await ctx.reply(_(ctx, "Roles removed from rule {name}!").format(name=name))

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        """Add the roles back if they have been verified before."""
        db_member = VerifyMember.get(guild_id=member.guild.id, user_id=member.id)
        if not db_member:
            return
        if db_member[0].status != VerifyStatus.VERIFIED.value:
            return

        mapping = VerifyMapping.map(guild_id=member.guild.id, email=db_member.address)

        if not mapping or not mapping.rule or not mapping.rule.roles:
            await guild_log.error(
                member,
                None,
                "Can't skip verification - mapping, rule or roles missing. Rule name: {name}".format(
                    name=mapping.rule.name if mapping.rule else "(None)"
                ),
            )
            return

        await self._add_roles(member, mapping.rule.roles)

        # We need a channel to log the event in the guild log channel.
        # We are just picking the first one.
        await guild_log.info(
            member,
            None,
            "New member already in database, skipping verification.",
        )

    @commands.Cog.listener()
    async def on_member_ban(self, guild, member: Union[discord.Member, discord.User]):
        """When the member is banned, update the database status."""
        db_member = VerifyMember.get(guild_id=guild.id, user_id=member.id)

        if db_member:
            db_member[0].status = VerifyStatus.BANNED.value
            db_member[0].save()
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
        if VerifyMember.get(guild_id=ctx.guild.id, user_id=ctx.author.id):
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
        db_member = VerifyMember.get(guild_id=ctx.guild.id, address=address)
        if db_member:
            db_member = db_member[0]
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
        """Check if the address is allowed to verify.

        If the address is not supported, the event is logged and a response is
        sent to the user.

        :param ctx: Command context
        :param address: Supplied e-mail address
        """
        mapping = VerifyMapping.map(guild_id=ctx.guild.id, email=address)

        if not mapping or not mapping.rule:
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
                "Your verification e-mail for Discord server {guild_name} is {code}.",
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

    async def _add_roles(self, member: discord.Member, db_roles: List[VerifyRole]):
        """Add roles to the member."""

        roles: List[discord.Role] = list()
        for db_role in db_roles:
            role = member.guild.get_role(db_role.role_id)
            if role:
                roles.append(role)
            else:
                await guild_log.error(
                    member,
                    None,
                    "Role with ID {id} could not be found! Rule: {name}.".format(
                        id=db_role.role_id, name=db_role.rule.name
                    ),
                )
        await member.add_roles(*roles)

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
