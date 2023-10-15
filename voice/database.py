from __future__ import annotations

from typing import Iterable, Optional, Union

import discord
from pie.database import database, session
from sqlalchemy import BigInteger, Boolean, Integer, Column


class VoiceSettings(database.base):
    __tablename__ = "mgmt_voice_settings"

    guild_id = Column(BigInteger, primary_key=True, autoincrement=False)
    category_id = Column(BigInteger, unique=True)
    high_res_bitrate = Column(Integer)

    @staticmethod
    def set_category(
        guild: discord.Guild, category: discord.CategoryChannel
    ) -> VoiceSettings:
        query = VoiceSettings.get(guild)
        if not query:
            query = VoiceSettings(guild_id=guild.id, category_id=category.id)
        else:
            query.category_id = category.id
        session.merge(query)
        session.commit()
        return query

    @staticmethod
    def set_high_bitrate(guild: discord.Guild, bitrate: int):
        if bitrate and not (64000 < bitrate < 384000):
            raise ValueError(f"Higher bit rate cannot be set to {bitrate}!")
        query = VoiceSettings.get(guild)
        if not query:
            query = VoiceSettings(guild_id=guild.id, high_res_bitrate=bitrate)
        else:
            query.high_res_bitrate = bitrate
        session.merge(query)
        session.commit()
        return query

    @staticmethod
    def remove(guild: discord.Guild) -> bool:
        query = VoiceSettings.get(guild)
        if query:
            session.delete(query)
            session.commit()
            return True
        return False

    @staticmethod
    def get(guild: discord.Guild) -> Optional[VoiceSettings]:
        return session.query(VoiceSettings).filter_by(guild_id=guild.id).one_or_none()

    @staticmethod
    def get_all() -> Iterable[VoiceSettings]:
        return session.query(VoiceSettings).all()

    @staticmethod
    def validate_settings(guild: discord.Guild) -> bool:
        query = VoiceSettings.get(guild)
        if not query:
            return False
        if not query.category_id:
            return False
        return True


class LockedChannels(database.base):
    __tablename__ = "mgmt_voice_locked"

    guild_id = Column(BigInteger)
    channel_id = Column(BigInteger, primary_key=True, autoincrement=False)
    locked = Column(Boolean)

    @staticmethod
    def lock(guild: discord.Guild, channel: discord.VoiceChannel):
        query = LockedChannels(guild_id=guild.id, channel_id=channel.id, locked=True)
        session.merge(query)
        session.commit()
        return query

    @staticmethod
    def unlock(guild: discord.Guild, channel: discord.VoiceChannel):
        query = LockedChannels(guild_id=guild.id, channel_id=channel.id, locked=False)
        session.merge(query)
        session.commit()
        return query

    @staticmethod
    def remove(channel: Union[discord.VoiceChannel, int]) -> bool:
        if isinstance(channel, discord.VoiceChannel):
            query = (
                session.query(LockedChannels)
                .filter_by(channel_id=channel.id)
                .one_or_none()
            )
        elif isinstance(channel, int):
            query = (
                session.query(LockedChannels)
                .filter_by(channel_id=channel)
                .one_or_none()
            )
        else:
            raise TypeError()
        if query:
            session.delete(query)
            session.commit()
            return True
        return False

    @staticmethod
    def is_locked(channel: discord.VoiceChannel) -> bool:
        query = (
            session.query(LockedChannels).filter_by(channel_id=channel.id).one_or_none()
        )
        return getattr(query, "locked", False)

    @staticmethod
    def get_all() -> Iterable[LockedChannels]:
        return session.query(LockedChannels).all()
