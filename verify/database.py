from __future__ import annotations

import datetime
from typing import List, Optional

from sqlalchemy import BigInteger, Column, DateTime, Integer, String

from database import database, session

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
    :param address: E-mail address.
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

    @staticmethod
    def add(
        guild_id: int,
        user_id: int,
        address: Optional[str],
        code: Optional[str],
        status: VerifyStatus,
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
        return query

    @staticmethod
    def update(guild_id: int, user_id: int, status: int) -> Optional[VerifyMember]:
        """Update member from database."""
        query = VerifyMember.get_by_member(guild_id, user_id)
        if not query:
            return None

        query.status = status
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
