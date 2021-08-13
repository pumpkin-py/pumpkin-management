import contextlib
import datetime
import json
import os
import random
import re
import smtplib
import string
import tempfile
from typing import Dict, List, Union, Optional

from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import discord
from discord.ext import commands

import database.config
from core import acl, exceptions, text, logging, utils

from .enum import VerifyStatus
from .database import VerifyGroup, VerifyMember


tr = text.Translator(__file__).translate
bot_log = logging.Bot.logger()
guild_log = logging.Guild.logger()
config = database.config.Config.get()


SMTP_SERVER: str = os.getenv("SMTP_SERVER")
SMTP_PORT: int = os.getenv("SMTP_PORT")
SMTP_ADDRESS: str = os.getenv("SMTP_ADDRESS")
SMTP_PASSWORD: str = os.getenv("SMTP_PASSWORD")


def test_dotenv() -> None:
    global SMTP_PORT

    if type(SMTP_SERVER) != str:
        raise exceptions.DotEnvException("SMTP_SERVER is not set.")
    if type(SMTP_PORT) != str:
        raise exceptions.DotEnvException("SMTP_PORT is not set.")
    try:
        SMTP_PORT = int(SMTP_PORT)
    except ValueError:
        raise exceptions.DotEnvException("SMTP_PORT is not set correctly.")
    if type(SMTP_ADDRESS) != str:
        raise exceptions.DotEnvException("SMTP_ADDRESS is not set.")
    if type(SMTP_PASSWORD) != str:
        raise exceptions.DotEnvException("SMTP_PASSWORD is not set.")


test_dotenv()


class Verify(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.guild_only()
    @commands.check(acl.check)
    @commands.command()
    async def verify(self, ctx, address: Optional[str] = None):
        """Ask for a verification code."""
        await utils.Discord.delete_message(ctx.message)
        if not address:
            await ctx.reply(
                tr("verify", "no address", ctx, mention=ctx.author.mention),
                delete_after=120,
            )
            return

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
                tr("verify", "unsupported address", mention=ctx.author.mention),
                delete_after=120,
            )
            return

        if VerifyMember.get_by_member(ctx.guild.id, ctx.author.id) is not None:
            await guild_log.info(
                ctx.author,
                ctx.channel,
                (
                    "Attempted to verify with ID already in database: "
                    f"'{utils.Text.sanitise(address)}'."
                ),
            )
            await ctx.send(
                tr("verify", "in database", mention=ctx.author.mention),
                delete_after=120,
            )
            return

        if VerifyMember.get_by_address(ctx.guild.id, address) is not None:
            await guild_log.info(
                ctx.author,
                ctx.channel,
                (
                    "Attempted to verify with address already in database: "
                    f"'{utils.Text.sanitise(address)}'."
                ),
            )
            await ctx.send(
                tr("verify", "in database", mention=ctx.author.mention),
                delete_after=120,
            )
            return

        code: str = self._generate_code()
        VerifyMember.add(
            guild_id=ctx.guild.id,
            user_id=ctx.author.id,
            address=address,
            code=code,
            status=VerifyStatus.PENDING,
        )

        message: MIMEMultipart = self._get_message(ctx.author, address, code)

        try:
            self._send_email(message)
        except smtplib.SMTPException as exc:
            await bot_log.warning(
                ctx.author,
                ctx.channel,
                "Could not send verification e-mail, trying again.",
                exception=exc,
            )

            try:
                self._send_email(message)
            except smtplib.SMTPException as exc:
                await bot_log.error(
                    ctx.author,
                    ctx.channel,
                    "Could not send verification e-mail.",
                    exception=exc,
                )
                await ctx.send(
                    tr("verify", "smtp error", mention=ctx.author.mention),
                    delete_after=120,
                )
                return

        await guild_log.info(
            ctx.author,
            ctx.channel,
            f"Verification e-mail sent with code '{code}'.",
        )

        await ctx.send(
            tr("verify", "reply", mention=ctx.author.mention),
            delete_after=120,
        )

    @commands.guild_only()
    @commands.check(acl.check)
    @commands.command()
    async def submit(self, ctx, code: Optional[str] = None):
        """Submit verification code."""
        await utils.Discord.delete_message(ctx.message)
        if not code:
            await ctx.reply(
                tr("submit", "no code", ctx, mention=ctx.author.mention),
                delete_after=120,
            )
            return

        db_member = VerifyMember.get_by_member(ctx.guild.id, ctx.author.id)
        if db_member is None or db_member.code is None:
            await ctx.send(
                tr("submit", "no code", mention=ctx.author.mention),
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
                tr("submit", "not pending", mention=ctx.author.mention),
                delete_after=120,
            )
            return

        fixed_code: str = self._repair_code(code)
        if db_member.code != fixed_code:
            await guild_log.info(
                ctx.author,
                ctx.channel,
                (
                    "Attempted to submit bad code: "
                    f"`{utils.Text.sanitise(code)}` instead of `{db_member.code}`."
                ),
            )
            await ctx.send(
                tr("submit", "bad code", mention=ctx.author.mention),
                delete_after=120,
            )
            return

        db_member.status = VerifyStatus.VERIFIED.value
        db_member.save()

        await guild_log.info(ctx.author, ctx.channel, "Verification succesfull.")

        await self._add_roles(ctx.author, db_member)

        with contextlib.suppress(discord.Forbidden):
            await ctx.author.send(tr("submit", "reply dm"))

        await ctx.send(
            tr(
                "submit",
                "reply public",
                name=utils.Text.sanitise(ctx.author.name),
            ),
            delete_after=120,
        )

    @commands.guild_only()
    @commands.check(acl.check)
    @commands.command(name="strip")
    async def strip(self, ctx):
        """Remove all roles and reset verification status to None."""
        db_member = VerifyMember.get_by_member(ctx.guild.id, ctx.author.id)
        if db_member is None:
            await ctx.reply(tr("strip", "not in database"))
            return

        if db_member.status < 0:
            await guild_log.info(
                ctx.author,
                ctx.channel,
                f"Strip attempt blocked, has status {VerifyStatus(db_member.status).value}.",
            )
            await ctx.reply("strip", "disallowed", ctx)
            return

        roles: List[discord.Role] = [
            role for role in ctx.author.roles if role.name != "@everyone"
        ]

        with contextlib.suppress(discord.Forbidden):
            await ctx.author.remove_roles(*roles, reason="strip")
        VerifyMember.remove(ctx.guild.id, ctx.author.id)
        await guild_log.info(
            ctx.author, ctx.channel, "Stripped and removed from database."
        )

        await ctx.author.send(tr("strip", "reply"))
        await utils.Discord.delete_message(ctx.message)

    @commands.check(acl.check)
    @commands.command()
    async def groupstrip(self, ctx, member_ids: commands.Greedy[int]):
        """Remove all roles and reset verification status to None
        from multiple users. Users are not notified about this."""
        removed_db: int = 0
        removed_dc: int = 0

        async with ctx.typing():
            for member_id in member_ids:
                member = ctx.guild.get_member(member_id)
                db_member = VerifyMember.get_by_member(ctx.guild.id, member_id)
                if db_member:
                    VerifyMember.remove(ctx.guild.id, member.id)
                    removed_db += 1
                if len(getattr(member, "roles", [])) > 1:
                    roles = [role for role in member.roles if role.name != "@everyone"]
                    with contextlib.suppress(discord.Forbidden):
                        await member.remove_roles(*roles, reason="groupstrip")
                    removed_dc += 1
                else:
                    await ctx.send(
                        tr("groupstrip", "no member", ctx, member_id=member_id)
                    )

        await ctx.reply(tr("groupstrip", "reply", ctx, db=removed_db, dc=removed_dc))

    @commands.guild_only()
    @commands.check(acl.check)
    @commands.group(name="verification")
    async def verification(self, ctx):
        await utils.Discord.send_help(ctx)

    @commands.check(acl.check)
    @verification.command(name="statistics", aliases=["stats"])
    async def verification_statistics(self, ctx):
        """Filter the data by verify status."""
        pass

    @commands.check(acl.check)
    @verification.group(name="groups")
    async def verification_groups(self, ctx):
        await utils.Discord.send_help(ctx)

    @commands.check(acl.check)
    @verification_groups.command(name="list")
    async def verification_groups_list(self, ctx):
        """Display list of all verification groups."""
        embed = utils.Discord.create_embed(
            author=ctx.author,
            title=tr("verification groups list", "title"),
        )
        for group in VerifyGroup.get_all(ctx.guild.id):
            embed.add_field(
                name=group.name,
                value=f"Role {group.role_id}\nRegex `{group.regex}`",
                inline=False,
            )
        await ctx.reply(embed=embed)

    @commands.check(acl.check)
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
            tr("verification groups template", "reply", ctx),
            file=discord.File(fp=file, filename=filename),
        )
        file.close()

    @commands.check(acl.check)
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
        json.dump(export, file, indent="\t")

        file.seek(0)
        await ctx.reply(
            tr("verification groups export", "reply", ctx, count=len(groups)),
            file=discord.File(fp=file, filename=filename),
        )
        file.close()
        await guild_log.info(ctx.author, ctx.channel, "Verification groups exported.")

    @commands.check(acl.check)
    @verification_groups.command(name="import")
    async def verification_groups_import(self, ctx):
        """Import new verification groups. This fully replaces old data."""
        if len(ctx.message.attachments) != 1:
            await ctx.reply(tr("verification groups import", "wrong file", ctx))
            return
        if not ctx.message.attachments[0].filename.lower().endswith(".json"):
            await ctx.reply(tr("verification groups import", "wrong json", ctx))
            return

        # download the file
        data_file = tempfile.TemporaryFile()
        await ctx.message.attachments[0].save(data_file)

        data_file.seek(0)
        try:
            json_data = json.load(data_file)
        except json.decoder.JSONDecodeError as exc:
            await ctx.reply(
                tr("verification groups import", "bad json", ctx) + f"\n> `{exc}`"
            )
            data_file.close()
            return

        # export the groups, just to make sure
        await self.verification_groups_export(ctx)

        groups = self._replace_verification_groups(ctx.guild.id, json_data)
        data_file.close()

        await ctx.reply(
            tr("verification groups import", "reply", ctx, count=len(groups))
        )

    #

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        """Add the roles back if they have been verified before."""
        db_member = VerifyMember.get_by_member(member.guild.id, member.id)
        if db_member is None:
            return

        await self._add_roles(member, db_member)
        await guild_log.info(
            member, None, "New member already in database, skipping verification."
        )

    @commands.Cog.listener()
    async def on_member_ban(self, guild, member: Union[discord.Member, discord.User]):
        """When the member is banned, update the database status."""
        db_member = VerifyMember.get_by_member(guild.id, member.id)

        if db_member is not None:
            db_member.status = VerifyStatus.BANNED.value
            db_member.save()
            await guild_log.info(
                member, None, "Member has been banned, updating database status."
            )
            return

        VerifyMember.add(
            guild_id=guild.id,
            user_id=member.id,
            address=None,
            group=None,
            code=None,
            status=VerifyStatus.BANNED,
        )
        await guild_log.info(
            member, None, "Member has been banned, adding to database."
        )

    #

    # TODO Loop to check the inbox for error e-mails

    #

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
        self, member: discord.Member, address: str, code: str
    ) -> MIMEMultipart:
        """Generate the verification e-mail."""
        BOT_URL = "https://github.com/pumpkin-py/pumpkin.py"

        clear = tr(
            "_get_email",
            "plain",
            guild_name=member.guild.name,
            code=code,
            # TODO Update when we have guild-dependent prefix
            prefix=config.prefix,
        )
        rich = tr(
            "_get_email",
            "html",
            color_bg="#AA761B",
            color_fg="#EFEFEF",
            font_family="Arial,Verdana,sans-serif",
            guild_name=member.guild.name,
            user_name=member.name,
            bot_name=self.bot.user.name,
            code=code,
            prefix=config.prefix,
            bot_url=BOT_URL,
        )

        message = MIMEMultipart("alternative")

        message["Subject"] = tr(
            "_get_email",
            "subject",
            guild_name=member.guild.name,
            user_name=member.name,
        )
        message["From"] = f"{self.bot.user.name} <{SMTP_ADDRESS}>"
        message["To"] = f"{member.name} <{address}>"
        message["Bcc"] = f"{self.bot.user.name} <{SMTP_ADDRESS}>"

        message["X-pumpkin.py-url"] = BOT_URL
        message["X-pumpkin.py-bot"] = f"{self.bot.user.id}"
        message["X-pumpkin.py-guild"] = f"{member.guild.id}"
        message["X-pumpkin.py-user"] = f"{member.id}"

        message.attach(MIMEText(clear, "plain"))
        message.attach(MIMEText(rich, "html"))

        return message

    def _send_email(self, message: MIMEMultipart) -> None:
        """Send the verification e-mail."""
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.ehlo()
            server.login(SMTP_ADDRESS, SMTP_PASSWORD)
            server.send_message(message)

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


def setup(bot) -> None:
    bot.add_cog(Verify(bot))
