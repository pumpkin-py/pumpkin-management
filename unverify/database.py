from __future__ import annotations

from datetime import datetime

import enum
import nextcord
from typing import Optional, List, Dict

from sqlalchemy import ARRAY, Column, Integer, String, DateTime, BigInteger, Enum
from sqlalchemy import or_

from pie.database import database, session


class UnverifyStatus(enum.Enum):
    waiting: str = "waiting"
    finished: str = "finished"
    member_left: str = "member left server"
    guild_not_found: str = "guild could not be found"


class UnverifyType(enum.Enum):
    selfunverify: str = "Selfunverify"
    unverify: str = "Unverify"


class GuildConfig(database.base):
    """Represents a cofiguration of a guild.

    Attributes:
        guild_id: ID of the guild.
        unverify_role: ID of the Role that unverified users get.
    """

    __tablename__ = "unverify_guild_config"

    guild_id = Column(BigInteger, primary_key=True, autoincrement=False)
    unverify_role_id = Column(BigInteger)

    @staticmethod
    def set(guild: nextcord.Guild, unverify_role: nextcord.Role) -> GuildConfig:
        """Updates the Guild Config item. Creates if not already present

        Args:
            guild: Guild to configure.
            unverify_role: Role that unverified users get.

        Returns:
            Added/Updated config object
        """
        query = session.query(GuildConfig).filter_by(guild_id=guild.id).one_or_none()
        if query is not None:
            query.unverify_role_id = unverify_role
        else:
            query = GuildConfig(guild_id=guild.id, unverify_role_id=unverify_role.id)
            session.add(query)
        session.commit()
        return query

    @staticmethod
    def get(guild_id: nextcord.Guild) -> Optional[GuildConfig]:
        """Retreives the guild configuration

        Args:
            guild_id: Guild to get configuration for.

        Returns:
            Config object (if found)
        """
        return session.query(GuildConfig).filter_by(guild_id=guild_id).one_or_none()

    def __repr__(self) -> str:
        return f'<GuildConfig guild_id="{self.guild_id}" unverify_role_id="{self.unverify_role_id}">'

    def dump(self) -> Dict:
        """Dumps GuildConfig into a dictionary.

        Returns:
            The GuildConfig as a dictionary.
        """
        return {"guild_id": self.guild_id, "unverify_role_id": self.unverify_role_id}


class UnverifyItem(database.base):
    """Represents a database Unverify item for :class:`Unverify` module.

    Attributes:
        idx: The database ID.
        guild_id: ID of the guild.
        user_id: ID of the unverified user.
        start_time: When the unverify started.
        end_time: When the unverify ends.
        last_check: When the item was last checked. Used when the user left or guild cannot be found.
        roles_to_return: List of Role IDs to return after the unverify ends.
        channels_to_return: List of GuildChannel IDs to return after the unverify ends.
        channels_to_remove: List of GuildChannel IDs to remove after the unverify ends.
        reason: Reason of the unverify.
        status: Status of the unverify.
        type: Type of the unverify.
    """

    __tablename__ = "unverify_table"

    idx = Column(Integer, primary_key=True, autoincrement=True)
    guild_id = Column(BigInteger)
    user_id = Column(BigInteger)
    start_time = Column(DateTime(timezone=True))
    end_time = Column(DateTime(timezone=True))
    last_check = Column(DateTime(timezone=True))
    roles_to_return = Column(ARRAY(BigInteger))
    channels_to_return = Column(ARRAY(BigInteger))
    channels_to_remove = Column(ARRAY(BigInteger))
    reason = Column(String)
    status = Column(Enum(UnverifyStatus), default=UnverifyStatus.waiting)
    type = Column(Enum(UnverifyType))

    @staticmethod
    def add(
        member: nextcord.Member,
        end_time: datetime,
        roles_to_return: List[nextcord.Role],
        channels_to_return: List[nextcord.abc.GuildChannel],
        channels_to_remove: List[nextcord.abc.GuildChannel],
        reason: str,
        type: UnverifyType,
    ) -> UnverifyItem:
        """Creates a new UnverifyItem in the database.

        Args:
            member: The member to be unverified.
            end_time: When the unverify ends.
            roles_to_return: List of Roles to return after the unverify ends.
            channels_to_return: List of GuildChannels to return after the unverify ends.
            channels_to_remove: List of GuildChannels to remove after the unverify ends.
            reason: Reason of the unverify.
            type: Type of the unverify.

        Raises:
            ValueError: End time already passed or Member is already unverified.
            sqlalchemy.orm.exc.MultipleResultsFound: Multiple items were found (which is not expected).

        Returns:
            The created database item.
        """
        start_time = datetime.now()
        if end_time < start_time:
            raise ValueError

        query = (
            session.query(UnverifyItem)
            .filter_by(
                user_id=member.id,
                guild_id=member.guild.id,
                status=UnverifyStatus.waiting,
            )
            .one_or_none()
        )

        if query is not None:
            raise ValueError

        roles_to_return_ids = (
            [role.id for role in roles_to_return] if roles_to_return is not None else []
        )
        channels_to_return_ids = (
            [channel.id for channel in channels_to_return]
            if channels_to_return is not None
            else []
        )
        channels_to_remove_ids = (
            [channel.id for channel in channels_to_remove]
            if channels_to_remove is not None
            else []
        )

        added = UnverifyItem(
            guild_id=member.guild.id,
            user_id=member.id,
            start_time=start_time,
            end_time=end_time,
            roles_to_return=roles_to_return_ids,
            channels_to_return=channels_to_return_ids,
            channels_to_remove=channels_to_remove_ids,
            reason=reason,
            type=type,
        )
        session.add(added)
        session.commit()
        return added

    def remove(self):
        """DANGER
        ------
        Deletes the item from the database. Does not reverify the member.
        """
        session.delete(self)
        session.commit()

    @staticmethod
    def get_member(
        member: nextcord.Member,
        status: UnverifyStatus = None,
        type: UnverifyType = None,
    ) -> Optional[List[UnverifyItem]]:
        """Retreives UnverifyItems filtered by member and optionally by status and type.

        Args:
            member: The unverified member.
            status: Status of the unverify. Defaults to None.
            type: Type of the unverify. Defaults to None.

        Returns:
            :class:`List[UnverifyItem]`
        """
        query = session.query(UnverifyItem).filter_by(
            user_id=member.id, guild_id=member.guild.id
        )
        if status is not None:
            query = query.filter_by(status=status)
        if type is not None:
            query = query.filter_by(type=type)

        return query.all()

    @classmethod
    def get_by_idx(cls, idx: int) -> Optional[UnverifyItem]:
        """Retreives UnverifyItem filtered by idx.

        Args:
            idx: The database idx of the item to retrieve.

        Raises:
            :class:`sqlalchemy.orm.exc.MultipleResultsFound`: Multiple results were found (which is not expected).

        Returns:
            :class:`Optional[UnverifyItem]`
        """
        return session.query(UnverifyItem).filter_by(idx=idx).one_or_none()

    @staticmethod
    def get_items(
        guild: nextcord.Guild = None,
        type: UnverifyType = None,
        status: UnverifyStatus = None,
        max_end_time: datetime = None,
        min_last_check: datetime = None,
    ) -> Optional[List[UnverifyItem]]:
        """Retreives List of UnverifyItems filtered optionally by:
            Unverify type, Unverify status, up to end time, with minimum last check time.

        Args:
            guild: Guild whose items are to be returned.
            type: Type of the unverify. Defaults to None.
            status: Status of the unverify. Defaults to None.
            max_end_time: Status of the unverify. Defaults to None.

        Returns:
            :class:`List[UnverifyItem]`: The retrieved unverify items.
        """
        query = session.query(UnverifyItem)

        if guild is not None:
            query = query.filter_by(guild_id=guild.id)
        if type is not None:
            query = query.filter_by(type=type)
        if status is not None:
            query = query.filter_by(status=status)
        if max_end_time is not None:
            query = query.filter(UnverifyItem.end_time < max_end_time)
        if min_last_check is not None:
            query = query.filter(
                or_(
                    UnverifyItem.last_check < min_last_check,
                    UnverifyItem.last_check == None,  # noqa: E711
                )
            )

        return query.order_by(UnverifyItem.end_time.asc()).all()

    @staticmethod
    def remove_all(guild: nextcord.Guild) -> int:
        """DANGER
        ------
        Removes all existing UnverifyItems in the guild! Does not reverify anyone.

        Args:
            guild_id (:class:`int`)
                ID of the :class:`nextcord.Guild` whose items are to be deleted.

        Returns:
            :class:`int`: Number of deleted items
        """

        return session.query(UnverifyItem).filter_by(guild_id=guild.id).delete()

    def __repr__(self) -> str:
        return (
            f'<UnverifyItem idx="{self.idx}" guild_id="{self.guild_id}" '
            f'user_id="{self.user_id}" start_time="{self.start_time}" end_time="{self.end_time}" '
            f'roles_to_return="{self.roles_to_return}" channels_to_return="{self.channels_to_return}" '
            f'channels_to_remove="{self.channels_to_remove}" reason="{self.reason}" status="{self.status}" '
            f'last_check="{self.last_check}" type="{self.type}">'
        )

    def dump(self) -> Dict:
        """Dumps UnverifyItem into a dictionary.

        Returns:
            :class:`Dict`: The UnverifyItem as a dictionary.
        """
        return {
            "idx": self.idx,
            "guild_id": self.guild_id,
            "user_id": self.user_id,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "roles_to_return": self.roles_to_return,
            "channels_to_return": self.channels_to_return,
            "channels_to_remove": self.channels_to_remove,
            "reason": self.reason,
            "status": self.status,
            "type": self.type,
        }

    def save(self):
        """Commits the UnverifyItem to the database."""
        session.commit()
