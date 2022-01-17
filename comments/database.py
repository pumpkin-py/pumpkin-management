from __future__ import annotations
from datetime import datetime

from typing import List, Optional, Dict

from sqlalchemy import BigInteger, Column, Integer, String, DateTime

from pie.database import database, session


class Comment(database.base):
    """Manage user information"""

    __tablename__ = "mgmt_comments_comments"

    idx = Column(Integer, primary_key=True, autoincrement=True)
    guild_id = Column(BigInteger)
    author_id = Column(BigInteger)
    user_id = Column(BigInteger)
    text = Column(String)
    timestamp = Column(DateTime)

    @staticmethod
    def add(guild_id: int, author_id: int, user_id: int, text: str) -> Comment:
        """Add a new comment."""
        comment = Comment(
            guild_id=guild_id,
            author_id=author_id,
            user_id=user_id,
            text=text,
            timestamp=datetime.now(),
        )

        session.merge(comment)
        session.commit()

        return comment

    @staticmethod
    def get(guild_id: int, idx: int) -> Optional[Comment]:
        """Get a comment if it exists."""
        return (
            session.query(Comment).filter_by(guild_id=guild_id, idx=idx).one_or_none()
        )

    @staticmethod
    def get_user_comments(guild_id: int, user_id: int) -> List[Comment]:
        """Get list of comments of a user."""
        return (
            session.query(Comment)
            .filter_by(
                guild_id=guild_id,
                user_id=user_id,
            )
            .all()
        )

    @staticmethod
    def remove(guild_id: int, idx: int) -> bool:
        """Remove user comment."""
        result = session.query(Comment).filter_by(guild_id=guild_id, idx=idx).delete()
        session.commit()
        return result > 0

    def __repr__(self) -> str:
        return (
            f'<Comment idx="{self.idx}" guild_id="{self.guild_id}" '
            f'author_id="{self.author_id}" user_id="{self.user_id}" '
            f'text="{self.text}" timestamp="{self.timestamp}">'
        )

    def dump(self) -> Dict:
        """Dumps Comment into a dictionary.

        Returns:
            :class:`Dict`: The Comment as a dictionary.
        """
        return {
            "idx": self.idx,
            "guild_id": self.guild_id,
            "author_id": self.author_id,
            "user_id": self.user_id,
            "text": self.text,
            "timestamp": self.timestamp,
        }

    def save(self):
        """Commits the Comment to the database."""
        session.commit()
