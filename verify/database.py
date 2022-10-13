from __future__ import annotations

import datetime
import enum
from typing import List, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from pie.database import database, session


class VerifyStatus(enum.Enum):
    NONE = 0
    PENDING = 1
    VERIFIED = 2
    BANNED = -1


class VerifyRule(database.base):
    """Verify rules for assigning rules to groups and sending correct
    message.

    The name must be unique per guild, as it's used to assign the right
    rule to each mapping during import.

    :param idx: Unique ID used as foreign key.
    :param name: Name of the rule.
    :param guild_id: Guild ID.
    :param groups: List of groups assigned to user.
    :param message: Message sent to the user.
    """

    __tablename__ = "mgmt_verify_rules"

    __table_args__ = (
        UniqueConstraint(
            "name",
            "guild_id",
            name="name_guild_id_unique",
        ),
    )

    idx = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String)
    guild_id = Column(BigInteger)
    groups = relationship(lambda: VerifyGroup, back_populates="rule")
    message = relationship(lambda: VerifyMessage)

    def __repr__(self) -> str:
        return (
            f'<VerifyRule idx="{self.idx}" name="{self.name}" '
            f'guild_id="{self.guild_id}" groups="{self.groups}" '
            f'message="{self.message}">'
        )

    def dump(self) -> dict:
        return {
            "idx": self.idx,
            "name": self.name,
            "guild_id": self.guild_id,
            "groups": self.groups,
            "message": self.message,
        }


class VerifyGroup(database.base):
    """Acts as discord role list for VerifyRule.

    :param rule_id: ID of the rule.
    :param role_id: ID of Discord role to assign.
    :param guild_id: Guild ID.
    :param rule: Back reference to VerifyRule.
    """

    __tablename__ = "mgmt_verify_groups"

    rule_id = Column(
        Integer,
        ForeignKey("mgmt_verify_rules.idx", ondelete="CASCADE"),
        primary_key=True,
    )
    role_id = Column(BigInteger, primary_key=True)
    guild_id = Column(BigInteger)
    rule = relationship(lambda: VerifyRule, back_populates="groups")

    def __repr__(self) -> str:
        return (
            f'<VerifyGroup rule_id="{self.rule_id}" '
            f'role_id="{self.role_id}" guild_id="{self.guild_id}" '
            f'rule="{self.rule}">'
        )

    def dump(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "role_id": self.role_id,
            "guild_id": self.guild_id,
            "rule": self.rule,
        }


class VerifyMapping(database.base):
    """Verify mapping rules to users.

    Maps username and domain (representing user or user groups) to Verify rules.
    The algorithm looks first if theres combination of username and domain.
    If the combination is not found, it tries to look only for domain (username == "").
    If the domain is not found, it looks for default mapping (username == "" and domain == "").
    If there are no records found, the user is not allowed to verify.

    To block some user / domain, set blocked to True.

    To add default rule for domain, add record with username = "" and domain = "someValue.xy"
    To add default rule for all domains, add record with username = "" and domain = ""

    When imported, this DB is wiped.

    :param guild_id: ID of guild that owns the mapping.
    :param rule_id: ID of rule to assign groups and send message.
    :param username: Part of email before @ (empty string to act as default value).
    :param domain: Part of email after @ (empty string to act as default value).
    :param blocked: If combination is blocked from verification (default = False).
    :param rule: Relationship with :class:`VerifyRule` based on rule_id.
    """

    __tablename__ = "mgmt_verify_mapping"

    guild_id = Column(BigInteger, primary_key=True)
    rule_id = Column(
        Integer,
        ForeignKey("mgmt_verify_rules.idx", ondelete="CASCADE"),
    )
    username = Column(String, primary_key=True)
    domain = Column(String, primary_key=True)
    blocked = Column(Boolean, default=False)
    rule = relationship(lambda: VerifyRule)

    def __repr__(self) -> str:
        return (
            f'<VerifyMapping guild_id="{self.guild_id}" rule_id="{self.rule_id}" '
            f'username="{self.username}" domain="{self.domain}" '
            f'blocked="{self.blocked}" rule="{self.rule}">'
        )

    def dump(self) -> dict:
        return {
            "guild_id": self.guild_id,
            "rule_id": self.rule_id,
            "username": self.username,
            "domain": self.domain,
            "blocked": self.blocked,
            "rule": self.rule,
        }


class VerifyMember(database.base):
    """Verify member.

    :param guild_id: Member's guild ID.
    :param user_id: Member ID.
    :param address: E-mail address.
    :param code: Verification code.
    :param status: Verify status represented by enum :class:`VerifyStatus`.
    :param timestamp: Creation timestamp.
    """

    __tablename__ = "mgmt_verify_members"

    __table_args__ = (
        UniqueConstraint(
            "guild_id",
            "user_id",
            name="guild_id_user_id_unique",
        ),
        UniqueConstraint(
            "guild_id",
            "address",
            name="guild_id_address_unique",
        ),
    )

    idx = Column(Integer, primary_key=True, autoincrement=True)
    guild_id = Column(BigInteger)
    user_id = Column(BigInteger)
    address = Column(String)
    code = Column(String)
    status = Column(Enum(VerifyStatus))
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
            status=status,
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
            f'code="{self.code}" status="{self.status}">'
        )

    def dump(self) -> dict:
        return {
            "guild_id": self.guild_id,
            "user_id": self.user_id,
            "code": self.code,
            "status": self.status,
        }


class VerifyMessage(database.base):
    """Maps messages to rules, but allows default message
    for guild.

    IDX is necessary as primary key to allow Null values in rule_id.

    :param idx: Artificial PK.
    :param rule_id: ID of rule message bellongs to (None if default).
    :param guild_id: Guild ID.
    :param message: Text of the message.
    """

    __tablename__ = "mgmt_verify_messages"

    __table_args__ = (
        UniqueConstraint(
            "rule_id",
            "guild_id",
            name="rule_id_guild_id_unique",
        ),
    )

    idx = Column(Integer, primary_key=True, autoincrement=True)
    rule_id = Column(
        Integer, ForeignKey("mgmt_verify_rules.idx", ondelete="CASCADE"), nullable=True
    )
    guild_id = Column(BigInteger)
    message = Column(String)

    def __repr__(self) -> str:
        return (
            f'<VerifyMessage idx="{self.idx}" '
            f'rule_id="{self.rule_id}" guild_id="{self.guild_id}" '
            f'message="{self.message}">'
        )

    def dump(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "guild_id": self.guild_id,
            "message": self.message,
        }
