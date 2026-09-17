"""Task-aware, backend-independent mutation families."""

from enum import StrEnum


class MutationFamily(StrEnum):
    NAVIGATION = "navigation"
    TEXT_WRITE = "text_write"
    CREATE_ENTITY = "create_entity"
    DELETE_ENTITY = "delete_entity"
    TOGGLE = "toggle"
    SUBMIT = "submit"
    SAVE = "save"
    SCROLL = "scroll"
    OTHER = "other"
