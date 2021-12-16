from __future__ import annotations

import enum
from typing import List, Optional

from sqlalchemy import BigInteger, Column, Enum, Integer

from pie.database import database, session


class ReactionChannelType(enum.Enum):
    ROLE = "role"
    CHANNEL = "channel"


class ReactionChannel(database.base):
    """Channel for react-to-role functionality.

    Despite the name, channel can also assign channels, not only roles.

    max_roles, top_role and bottom_role are only applicable to 'role' type.
    """

    __tablename__ = "mgmt_react2role_channels"

    idx = Column(Integer, primary_key=True, autoincrement=True)
    guild_id = Column(BigInteger)
    channel_id = Column(BigInteger, unique=True)
    channel_type = Column(Enum(ReactionChannelType))
    max_roles = Column(Integer, default=0)
    top_role = Column(BigInteger, default=None)
    bottom_role = Column(BigInteger, default=None)

    @property
    def react2name(self) -> str:
        return "react2" + self.channel_type.value

    @property
    def React2name(self) -> str:
        return "React2" + self.channel_type.value

    @staticmethod
    def add(
        guild_id: int,
        channel_id: int,
        channel_type: ReactionChannelType,
    ) -> ReactionChannel:
        channel = ReactionChannel.get(guild_id=guild_id, channel_id=channel_id)
        if channel:
            raise ValueError("This channel is already a react to role channel.")

        channel = ReactionChannel(
            guild_id=guild_id,
            channel_id=channel_id,
            channel_type=channel_type,
        )
        session.add(channel)
        session.commit()
        return channel

    @staticmethod
    def get(guild_id: int, channel_id: int) -> Optional[ReactionChannel]:
        query = (
            session.query(ReactionChannel)
            .filter_by(guild_id=guild_id, channel_id=channel_id)
            .one_or_none()
        )
        return query

    @staticmethod
    def get_all(guild_id: int) -> List[ReactionChannel]:
        query = session.query(ReactionChannel).filter_by(guild_id=guild_id).all()
        return query

    @staticmethod
    def remove(guild_id: int, channel_id: int) -> int:
        query = (
            session.query(ReactionChannel)
            .filter_by(guild_id=guild_id, channel_id=channel_id)
            .delete()
        )
        return query

    def save(self):
        session.commit()

    def __repr__(self) -> str:
        return (
            f'<{self.__class__.__name__} guild_id="{self.guild_id}" '
            f'channel_id="{self.channel_id}" channel_type="{self.channel_type.name}" '
            f'max_roles="{self.max_roles}" '
            f'top_role="{self.top_role}" bottom_role="{self.bottom_role}">'
        )

    def dump(self) -> dict:
        return {
            "guild_id": self.guild_id,
            "channel_id": self.channel_id,
            "channel_type": self.channel_type,
            "max_roles": self.max_roles,
            "top_role": self.top_role,
            "bottom_role": self.bottom_role,
        }
