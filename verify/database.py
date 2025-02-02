from __future__ import annotations

import datetime
import re
from typing import List, Optional

import discord
from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Integer,
    String,
    Boolean,
    ForeignKey,
)
from sqlalchemy.orm import relationship

from pie.database import database, session

from .enums import VerifyStatus


class VerifyGroup(database.base):
    """Verify group.

    Groups map e-mail domains to roles.
    To add some role everytime set the :attr:`role_id` parameter to ``0``.
    To block some domain from being used set the :attr:`role_id` parameter to ``-1``.

    When imported, old groups are deleted and the new ones are added one-by-one:
    ordering matters.
    """

    __tablename__ = "mgmt_verify_groups"

    idx = Column(Integer, primary_key=True, autoincrement=True)
    guild_id = Column(BigInteger)
    name = Column(String)
    role_id = Column(BigInteger)
    regex = Column(String)

    @staticmethod
    def add(guild_id: int, name: str, role_id: int, regex: str) -> VerifyGroup:
        """Add new verify group.

        :return: New group.
        """
        group = VerifyGroup(
            guild_id=guild_id,
            name=name,
            role_id=role_id,
            regex=regex,
        )

        session.add(group)
        session.commit()

        return group

    @staticmethod
    def get_by_name(guild_id: int, name: str) -> Optional[VerifyGroup]:
        """Get verify group by its name."""
        query = (
            session.query(VerifyGroup)
            .filter_by(
                guild_id=guild_id,
                name=name,
            )
            .one_or_none()
        )
        return query

    @staticmethod
    def get_by_role(guild_id: int, role_id: int) -> Optional[VerifyGroup]:
        """Get verify group by its role."""
        query = (
            session.query(VerifyGroup)
            .filter_by(
                guild_id=guild_id,
                role_id=role_id,
            )
            .one_or_none()
        )
        return query

    @staticmethod
    def get_all(guild_id: int) -> List[VerifyGroup]:
        """Get all verify groups in the guild.

        :param guild_id: Guild ID.
        :return: List of guild groups.
        """
        query = session.query(VerifyGroup).filter_by(guild_id=guild_id).all()
        return query

    @staticmethod
    def remove(guild_id: int, name: str) -> int:
        """Remove existing verify group.

        :param guild_id: Guild ID.
        :param name: Group name.
        :return: Number of deleted groups, always ``0`` or ``1``.
        """
        query = (
            session.query(VerifyGroup)
            .filter_by(
                guild_id=guild_id,
                name=name,
            )
            .delete()
        )
        session.commit()
        return query

    @staticmethod
    def remove_all(guild_id: int) -> int:
        """Remove all existing verify groups.

        :param guild_id: Guild ID.
        :return: Number of deleted groups.
        """
        query = session.query(VerifyGroup).filter_by(guild_id=guild_id).delete()
        session.commit()
        return query

    def __repr__(self) -> str:
        return (
            f'<VerifyGroup idx="{self.idx}" guild_id="{self.guild_id}" '
            f'name="{self.name}" role_id="{self.role_id}" regex="{self.regex}">'
        )

    def dump(self) -> dict:
        return {
            "guild_id": self.guild_id,
            "name": self.name,
            "role_id": self.role_id,
            "regex": self.regex,
        }


class VerifyMember(database.base):
    """Verify member.

    :param guild_id: Member's guild ID.
    :param user_id: Member ID.
    :param address: E-mail address or identifier.
    :param code: Verification code.
    :param status: Numeric representation of :class:`VerifyStatus`.
    :param timestamp: Creation timestamp.
    """

    __tablename__ = "mgmt_verify_members"

    idx = Column(Integer, primary_key=True, autoincrement=True)
    guild_id = Column(BigInteger)
    user_id = Column(BigInteger)
    address = Column(String)
    code = Column(String)
    status = Column(Integer)
    timestamp = Column(DateTime)
    via_api = Column(Boolean)

    @staticmethod
    def add(
        guild_id: int,
        user_id: int,
        address: Optional[str],
        code: Optional[str],
        status: VerifyStatus,
        via_api: bool = False,
    ) -> Optional[VerifyMember]:
        """Add new member."""
        if VerifyMember.get_by_member(guild_id, user_id) is not None:
            return None
        if VerifyMember.get_by_address(guild_id, address) is not None:
            return None

        member = VerifyMember(
            guild_id=guild_id,
            user_id=user_id,
            address=address,
            code=code,
            status=status.value,
            timestamp=datetime.datetime.now(),
            via_api=via_api,
        )

        session.add(member)
        session.commit()

        return member

    @staticmethod
    def get_by_member(guild_id: int, user_id: int) -> Optional[VerifyMember]:
        """Get member."""
        query = (
            session.query(VerifyMember)
            .filter_by(
                guild_id=guild_id,
                user_id=user_id,
            )
            .one_or_none()
        )
        return query

    @staticmethod
    def get_by_address(guild_id: int, address: str) -> Optional[VerifyMember]:
        """Get member by their e-mail."""
        query = (
            session.query(VerifyMember)
            .filter_by(
                guild_id=guild_id,
                address=address,
            )
            .one_or_none()
        )
        return query

    @classmethod
    def get_all(cls, guild_id: int) -> List[VerifyMember]:
        """Get members with e-mail containing given regex filter."""
        return session.query(cls).filter_by(guild_id=guild_id).all()

    @staticmethod
    def remove(guild_id: int, user_id: int) -> int:
        """Remove member from database."""
        query = (
            session.query(VerifyMember)
            .filter_by(
                guild_id=guild_id,
                user_id=user_id,
            )
            .delete()
        )
        session.commit()
        return query

    @staticmethod
    def update(guild_id: int, user_id: int, status: int) -> Optional[VerifyMember]:
        """Update member from database."""
        query = VerifyMember.get_by_member(guild_id, user_id)
        if not query:
            return None

        query.status = status
        session.add(query)
        session.commit()
        return query

    def save(self):
        session.commit()

    def __repr__(self) -> str:
        return (
            f'<VerifyMember idx="{self.idx}" '
            f'guild_id="{self.guild_id}" user_id="{self.user_id}" '
            f'code="{self.code}" status="{VerifyStatus(self.status)}">'
        )

    def dump(self) -> dict:
        return {
            "guild_id": self.guild_id,
            "user_id": self.user_id,
            "code": self.code,
            "status": VerifyStatus(self.status),
        }


class VerifyMessage(database.base):

    __tablename__ = "mgmt_verify_message"

    idx = Column(Integer, primary_key=True, autoincrement=True)
    role_id = Column(BigInteger)  # Discord role id or 0 for guild default
    guild_id = Column(BigInteger)
    message = Column(String)

    @staticmethod
    def get(guild_id: int, role_id: int = 0) -> Optional[VerifyMessage]:
        return (
            session.query(VerifyMessage)
            .filter_by(role_id=role_id, guild_id=guild_id)
            .one_or_none()
        )

    @staticmethod
    def set(guild_id: int, role_id: int, message: str) -> VerifyMessage:
        config = (
            session.query(VerifyMessage)
            .filter_by(guild_id=guild_id, role_id=role_id)
            .one_or_none()
        )
        if not config:
            config = VerifyMessage(role_id=role_id, guild_id=guild_id, message=message)
            session.add(config)
        else:
            config.message = message
        session.commit()
        return config

    @staticmethod
    def unset(guild_id: int, role_id: int) -> int:
        query = (
            session.query(VerifyMessage)
            .filter_by(guild_id=guild_id, role_id=role_id)
            .delete()
        )
        session.commit()
        return query

    def __repr__(self) -> str:
        return (
            f'<VerifyMessage idx="{self.idx}" '
            f'role_id="{self.role_id}" guild_id="{self.guild_id}" '
            f'message="{self.message}">'
        )

    def dump(self) -> dict:
        return {
            "role_id": self.role_id,
            "guild_id": self.guild_id,
            "message": self.message,
        }


class DBAPI(database.base):
    __tablename__ = "mgmt_verify_apis"

    guild_id = Column(BigInteger, primary_key=True)
    server = Column(String)
    token = Column(String)
    mail_endpoint = Column(String)
    mail_jmespath = Column(String)
    id_regex = Column(String)
    id_guide = Column(String)

    role_endpoints: list[APIRoleEndpoint] = relationship("APIRoleEndpoint")
    role_mappings: list[APIRoleMapping] = relationship("APIRoleMapping")

    @staticmethod
    def get(guild: discord.Guild) -> DBAPI:
        settings = session.query(DBAPI).filter_by(guild_id=guild.id).one_or_none()
        if not settings:
            settings = DBAPI(guild_id=guild.id)
        return settings

    @staticmethod
    def set_url(guild: discord.Guild, url: str):
        settings = DBAPI.get(guild)

        settings.server = url
        session.add(settings)
        session.commit()

    @staticmethod
    def set_token(guild: discord.Guild, token: str):
        settings = DBAPI.get(guild)

        settings.token = token
        session.add(settings)
        session.commit()

    @staticmethod
    def set_mail_endpoint(guild: discord.Guild, mail_endpoint: str, mail_jmespath: str):
        settings = DBAPI.get(guild)

        settings.mail_endpoint = mail_endpoint
        settings.mail_jmespath = mail_jmespath
        session.add(settings)
        session.commit()

    @staticmethod
    def set_id_regex(guild: discord.Guild, regex: re.Pattern):
        settings = DBAPI.get(guild)
        settings.id_regex = regex.pattern
        session.add(settings)
        session.commit()

    @staticmethod
    def set_validation_guide(guild: discord.Guild, text: str):
        settings = DBAPI.get(guild)
        settings.id_guide = text
        session.add(settings)
        session.commit()

    @staticmethod
    def add_role_endpoint(guild: discord.Guild, role_endpoint: str, jmespath: str):
        settings = DBAPI.get(guild)

        endpoint = APIRoleEndpoint(
            guild_id=guild.id, role_endpoint=role_endpoint, role_jmespath=jmespath
        )
        settings.role_endpoints.append(endpoint)
        session.add(endpoint)
        session.add(settings)
        session.commit()

    @staticmethod
    def delete_role_endpoint(guild: discord.Guild, idx: int) -> bool:
        settings = DBAPI.get(guild)
        item_list = [e for e in settings.role_endpoints if e.idx == idx]
        if item_list:
            session.delete(item_list[0])
            session.commit()
            return True
        return False

    @staticmethod
    def add_role_mapping(guild: discord.Guild, role: discord.Role, api_data: str):
        settings = DBAPI.get(guild)
        mapping = APIRoleMapping(
            guild_id=settings.guild_id, role_id=role.id, api_data=api_data
        )
        settings.role_mappings.append(mapping)
        session.add(mapping)
        session.add(settings)
        session.commit()

    @staticmethod
    def delete_role_mapping(guild: discord.Guild, idx: int) -> bool:
        settings = DBAPI.get(guild)
        item_list = [m for m in settings.role_mappings if m.idx == idx]
        if item_list:
            session.delete(item_list[0])
            session.commit()
            return True
        return False

    @property
    def is_valid(self) -> bool:
        return bool(
            self.server
            and self.mail_endpoint
            and self.mail_jmespath
            and self.role_endpoints
            and self.role_mappings
        )


class APIRoleEndpoint(database.base):
    __tablename__ = "mgmt_verify_api_role"

    idx = Column(Integer, primary_key=True, autoincrement=True)
    guild_id = Column(
        BigInteger, ForeignKey("mgmt_verify_apis.guild_id", ondelete="CASCADE")
    )
    role_endpoint = Column(String)
    role_jmespath = Column(String)


class APIRoleMapping(database.base):
    __tablename__ = "mgmt_verify_api_role_mappings"
    idx = Column(Integer, primary_key=True, autoincrement=True)
    guild_id = Column(
        BigInteger, ForeignKey("mgmt_verify_apis.guild_id", ondelete="CASCADE")
    )
    role_id = Column(BigInteger)
    api_data = Column(String)
