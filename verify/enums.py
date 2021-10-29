from enum import IntEnum


class VerifyStatus(IntEnum):
    NONE = 0
    PENDING = 1
    VERIFIED = 2
    BANNED = -1

    @classmethod
    def has_member(cls, value):
        return value in cls.__members__
