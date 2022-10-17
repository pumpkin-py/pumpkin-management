from __future__ import annotations

import datetime
import enum
from typing import List, Optional

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import relationship

from pie.database import database, session


class VerifyStatus(enum.Enum):
    NONE = 0
    PENDING = 1
    VERIFIED = 2
    BANNED = -1


class VerifyRule(database.base):
    """Verify rules for assigning roles to rules and sending correct
    message.

    The name must be unique per guild, as it's used to assign the right
    rule to each mapping during import.

    :param idx: Unique ID used as foreign key.
    :param name: Name of the rule.
    :param guild_id: Guild ID.
    :param roles: List of roles to assing to user.
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
    roles = relationship(
        lambda: VerifyRole, back_populates="rule", cascade="all, delete-orphan"
    )
    message = relationship(lambda: VerifyMessage, back_populates="rule")

    @staticmethod
    def get(guild_id: int, name: str = None) -> List[VerifyRule]:
        """Get guild rules.

        Args:
            guild_id: ID of the guild
            name: Search name (optional)
        Returns:
            List of VerifyRule
        """
        query = session.query(VerifyRule).filter_by(guild_id=guild_id)

        if name:
            query = query.filter(func.lower(VerifyRule.name) == func.lower(name))

        return query.all()

    @staticmethod
    def add(guild_id: int, name: str) -> Optional[VerifyRule]:
        """Add VerifyRule to DB. Name must be unique for each guild.

        Args:
            guild_id: ID of the guild
            name: Name of the rule
        Returns:
            None if same record exists, VerifyRule otherwise.

        """
        query = (
            session.query(VerifyRule)
            .filter_by(guild_id=guild_id)
            .filter(func.lower(VerifyRule.name) == func.lower(name))
            .one_or_none()
        )

        if query:
            return None

        rule = VerifyRule(guild_id=guild_id, name=name)

        session.add(rule)
        session.commit()

    def add_roles(self, roles: List[int]):
        """Add Discord roles to the rule. Skips existing roles.

        Args:
            roles: List of Discord role IDs.
        """
        for role in self.roles:
            if role.role_id in roles:
                roles.remove(role.role_id)

        for role in roles:
            self.roles.append(VerifyRole(role_id=role, guild_id=self.guild_id))

        session.commit()

    def delete_roles(self, roles: List[int]):
        """Add Discord roles to the rule. Skips existing roles.

        Args:
            roles: List of Discord role IDs.
        """
        for role in self.roles:
            if role.role_id in roles:
                self.roles.remove(role.role_id)

        session.commit()

    def delete(self):
        session.delete(self)
        session.commit()

    def __repr__(self) -> str:
        return (
            f'<VerifyRule idx="{self.idx}" name="{self.name}" '
            f'guild_id="{self.guild_id}" roles="{self.roles}" '
            f'message="{self.message}">'
        )

    def dump(self) -> dict:
        return {
            "idx": self.idx,
            "name": self.name,
            "guild_id": self.guild_id,
            "roles": self.roles,
            "message": self.message,
        }


class VerifyRole(database.base):
    """Acts as discord role list for VerifyRule.

    :param rule_id: ID of the rule.
    :param role_id: ID of Discord role to assign.
    :param guild_id: Guild ID.
    :param rule: Back reference to VerifyRule.
    """

    __tablename__ = "mgmt_verify_roles"

    rule_id = Column(
        Integer,
        ForeignKey("mgmt_verify_rules.idx", ondelete="CASCADE"),
        primary_key=True,
    )
    role_id = Column(BigInteger, primary_key=True)
    guild_id = Column(BigInteger)
    rule = relationship(lambda: VerifyRule, back_populates="roles")

    def delete(self):
        session.delete(self)
        session.commit()

    def __repr__(self) -> str:
        return (
            f'<VerifyRole rule_id="{self.rule_id}" '
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

    To block some user / domain, set Rule to None.

    To add default rule for domain, add record with username = "" and domain = "someValue.xy"
    To add default rule for all domains, add record with username = "" and domain = ""

    When imported, this DB is wiped.

    :param guild_id: ID of guild that owns the mapping.
    :param rule_id: ID of rule to assign roles and send message.
    :param username: Part of email before @ (empty string to act as default value).
    :param domain: Part of email after @ (empty string to act as default value).
    :param rule: Relationship with :class:`VerifyRule` based on rule_id.
    """

    __tablename__ = "mgmt_verify_mapping"

    guild_id = Column(BigInteger, primary_key=True)
    rule_id = Column(
        Integer, ForeignKey("mgmt_verify_rules.idx", ondelete="CASCADE"), nullable=True
    )
    username = Column(String, primary_key=True)
    domain = Column(String, primary_key=True)
    rule = relationship(lambda: VerifyRule)

    @staticmethod
    def add(guild_id: int, username: str, domain: str, rule: VerifyRule):
        """Add or update mapping.

        If user is empty string, the mapping is considered as domain default.
        If user and domain is empty, the mapping is considered as guild default.

        If Rule is None, verification is blocked.

        Args:
            guild_id: Discord ID of the guild.
            username: Username (part of the email before @)
            domain: Domain (part of the email after @)
            rule: VerifyRule applied to the user
        """
        mapping = (
            session.query(VerifyMapping)
            .filter_by(guild_id=guild_id, username=username, domain=domain)
            .one_or_none()
        )

        if not mapping:
            mapping = VerifyMapping(guild_id=guild_id, username=username, domain=domain)

        mapping.rule = rule

        session.merge(mapping)
        session.commit()

    @staticmethod
    def get(
        guild_id: int, rule: VerifyRule = None, username: str = "", domain: str = ""
    ) -> List[VerifyMapping]:
        """Get VerifyMapping.

        Args:
            guild_id: Discord ID of the guild.
            rule: Applied rule.
            username: Username (part of the email before @)
            domain: Domain (part of the email after @)

        Returns:
            List of VerifyMapping
        """
        query = session.query(VerifyMapping).filter_by(guild_id=guild_id)

        if rule:
            query = query.filter_by(rule=rule)
        if username:
            query = query.filter(
                func.lower(VerifyMapping.username) == func.lower(username)
            )
        if domain:
            query = query.filter(func.lower(VerifyMapping.domain) == func.lower(domain))

        return query.all()

    @staticmethod
    def map(
        guild_id: int, username: str = None, domain: str = None, email: str = None
    ) -> Optional[VerifyMapping]:
        """Maps the given username and domain to the guild rule.

        First it searches for exact username and domain.
        If the result is not found, it searches for domain only rule.
        If the result is not found, it searches for global rule.
        If the result is not found, returns None.

        Args:
            guild_id: Discord ID of the guild.
            username: Username to search for (empty string for guild / global rule).
            domain: Domain to search for (empty string for global rule).
            email: Can be used instead of username and domain.

        Returns:
            VerifyMapping if user is mapped, None otherwise

        """
        if email:
            username, domain = email.rsplit("@", 1)

        if username is None and domain is None:
            raise ValueError("Username and domain can't be empty!")

        query = (
            session.query(VerifyMapping)
            .filter_by(guild_id=guild_id, username=username, domain=domain)
            .filter(func.lower(VerifyMapping.username) == username.lower())
            .filter(func.lower(VerifyMapping.domain) == domain.lower())
            .one_or_none()
        )

        if not query:
            if username:
                return VerifyMapping.map(guild_id, "", domain)
            elif domain:
                return VerifyMapping.map(guild_id, "", "")
            else:
                return None

        return query

    @staticmethod
    def wipe(guild_id: int):
        query = session.query(VerifyMapping).filter_by(guild_id=guild_id)

        return query.delete()

    def delete(self):
        session.delete(self)
        session.commit()

    def __repr__(self) -> str:
        return (
            f'<VerifyMapping guild_id="{self.guild_id}" rule_id="{self.rule_id}" '
            f'username="{self.username}" domain="{self.domain}" '
            f'rule="{self.rule}">'
        )

    def dump(self) -> dict:
        return {
            "guild_id": self.guild_id,
            "rule_id": self.rule_id,
            "username": self.username,
            "domain": self.domain,
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
        """Add new member.

        Args:
            guild_id: Discord ID of the guild.
            user_id: Discord ID of the user.
            address: Email address of the user.
            code: Verify code.
            Status: VerifyStatus

        Returns:
            Null if already exists, otherwise VerifyMember
        """
        if VerifyMember.get(guild_id, user_id=user_id):
            return None
        if VerifyMember.get(guild_id, address=address):
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
    def get(
        guild_id: int, user_id: int = None, address: str = None
    ) -> List[VerifyMember]:
        """Get member.

        Args:
            guild_id: Discord ID of the guild.
            user_id: Discord user ID (optional)
            address: Email address of the user (optional)

        Returns:
            List of VerifyMembers
        """
        query = session.query(VerifyMember).filter_by(guild_id=guild_id)

        if user_id:
            query = query.filter_by(user_id=user_id)
        if address:
            query = query.filter(
                func.lower(VerifyMember.address) == func.lower(address)
            )

        return query.all()

    def delete(self):
        """Remove member from database."""
        session.delete(self)
        session.commit()

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

    If rule_id is set to None, it means that it's default message for guild.

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
    rule = relationship(lambda: VerifyRule, back_populates="message")

    @staticmethod
    def set(guild_id: int, message: str, rule: VerifyRule = None):
        """Set or update message for rule.

        If rule is None, the message is considered as default.

        Args:
            guild_id: Discord ID of the guild.
            message: Text of the message.
            rule: VerifyRule the message is assigned to.
        """
        rule_id = rule.idx if rule else None
        db_message = session.query(VerifyMessage).filter_by(
            quild_id=guild_id, rule_id=rule_id
        )

        if not db_message:
            message = VerifyMessage(guild_id=guild_id, rule_id=rule_id)

        db_message.message = message

        session.merge(message)
        session.commit()

    @staticmethod
    def get_default(guild_id: int) -> Optional[VerifyMessage]:
        """Get guild global message.

        Args:
            guild_id: Discord ID of the guild.

        Returns:
            VerifyMessage if found, None otherwise.
        """
        query = (
            session.query(VerifyMessage)
            .filter_by(guild_id=guild_id)
            .filter_by(rule=None)
        )
        message = query.one_or_none()

        return message

    def get_all(guild_id: int) -> List[VerifyMessage]:
        """Get all messages.

        Args:
            guild_id: Discord ID of the guild.

        Returns:
            List of VerifyMessage
        """
        query = session.query(VerifyMessage).filter_by(guild_id=guild_id)
        return query.all()

    def delete(self):
        session.delete(self)
        session.commit()

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
