from __future__ import annotations

from typing import List, Optional

from sqlalchemy import BigInteger, Column, Integer, JSON

from database import database, session


class Link(database.base):
    """Sync permission.

    Multiple satellites may be connected to one main guild.

    The guild may also be a satellite on its own.

    :param guild_id: ID of a guild the satellite is connected to.
    :param satellite_id: ID of satellite guild.
    """

    __tablename__ = "mgmt_sync_links"

    idx = Column(Integer, primary_key=True, autoincrement=True)
    guild_id = Column(BigInteger)
    satellite_id = Column(BigInteger, unique=True)

    @staticmethod
    def add(guild_id: int, satellite_id: int) -> Link:
        sync = Link.get(guild_id=guild_id, satellite_id=satellite_id)
        if sync:
            return sync
        if Link.get_by_satellite(satellite_id=satellite_id) is not None:
            raise ValueError("That server is already a satellite.")

        sync = Link(guild_id=guild_id, satellite_id=satellite_id)
        session.add(sync)
        session.commit()

        return sync

    @staticmethod
    def get(guild_id: int, satellite_id: int) -> Optional[Link]:
        query = (
            session.query(Link)
            .filter_by(guild_id=guild_id, satellite_id=satellite_id)
            .one_or_none()
        )
        return query

    @staticmethod
    def get_by_satellite(satellite_id: int) -> Optional[Link]:
        query = session.query(Link).filter_by(satellite_id=satellite_id).one_or_none()
        return query

    @staticmethod
    def get_all(guild_id: int) -> List[Link]:
        query = session.query(Link).filter_by(guild_id=guild_id).all()
        return query

    @staticmethod
    def remove(guild_id: int, satellite_id: int) -> int:
        query = (
            session.query(Link)
            .filter_by(guild_id=guild_id, satellite_id=satellite_id)
            .delete()
        )
        return query

    def __repr__(self) -> str:
        return f'<Link guild_id="{self.guild_id}" satellite_id="{self.satellite_id}">'

    def dump(self) -> dict:
        return {
            "guild_id": self.guild_id,
            "satellite_id": self.satellite_id,
        }


class Satellite(database.base):
    """Satellite data.

    A guild may be satellite of at most one another guild.
    """

    __tablename__ = "mgmt_sync_satellites"

    guild_id = Column(BigInteger, primary_key=True)
    data = Column(JSON)

    @staticmethod
    def add(guild_id: int, data: dict) -> Satellite:
        """Add new satellite.

        If new satellite is added with the same Guild ID it overwrites the old
        one.
        """
        satellite = Satellite(guild_id=guild_id, data=data)
        session.merge(satellite)
        session.commit()

        return satellite

    @staticmethod
    def get(guild_id: int) -> Optional[Satellite]:
        """Get satellite."""
        query = session.query(Satellite).filter_by(guild_id=guild_id).one_or_none()
        return query

    @staticmethod
    def remove(guild_id: int) -> int:
        """Remove the satellite."""
        query = session.query(Satellite).filter_by(guild_id=guild_id).delete()
        return query

    def __repr__(self) -> str:
        return f'<Satellite guild_id="{self.guild_id}" data="{self.data}">'

    def dump(self) -> dict:
        return {
            "guild_id": self.guild_id,
            "data": self.data,
        }
