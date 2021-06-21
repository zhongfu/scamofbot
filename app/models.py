import logging
from datetime import datetime
from typing import Optional, Dict, Union

from telethon import TelegramClient, utils
from telethon.hints import Entity
from telethon.tl.types import User, Chat, Channel

from tortoise import fields
from tortoise.models import Model
from tortoise.exceptions import DoesNotExist

logger = logging.getLogger(__name__)

# represents telethon.tl.types.User
class TelegramUser(Model):
    user_id: int = fields.IntField(pk=True, description="Telegram internal user id")
    username: str = fields.CharField(max_length=64, null=True, description="User's @username")
    first_name: str = fields.CharField(max_length=128, null=False, description="User's first name")
    last_name: str = fields.CharField(max_length=128, null=True, description="User's last name")
    last_update: datetime = fields.DatetimeField(null=False, description="last update in UTC")

    @classmethod
    async def get_user(cls, client: TelegramClient, user_id: int, max_staleness: Optional[int] = 60): # returns TelegramUser
        if not user_id >= 0:
            raise ValueError("user_id is negative -- that's a Chat or a Channel!")

        try:
            user = await cls.get(user_id=user_id)
        except DoesNotExist:
            user = TelegramUser(user_id=user_id)

        refresh: bool = not user.last_update or (max_staleness and (datetime.utcnow().timestamp() - user.last_update.timestamp()) > max_staleness)

        if refresh:
            await user.refresh_from_tg(client)

        return user

    async def refresh_from_tg(self, client: TelegramClient):
        entity: Entity = await client.get_entity(self.user_id) # throws ValueError if not found

        # this should never happen because we check for user_id >= 0
        assert isinstance(entity, User), f"Got a {type(entity)} instead of a User!"

        self.username = entity.username
        self.first_name = entity.first_name
        self.last_name = entity.last_name
        self.last_update = datetime.utcnow()

        await self.save()
    
    def get_link(self):
        if self.username:
            return f"@{self.username}"
        else:
            return f"<a href=tg://user?id={self.user_id}>{self.first_name}</a>"

    def __str__(self):
        return self.__repr__()

    def __repr__(self):
        attrs: Dict[str, Union[str, int]] = {"user_id": self.user_id}
        if self.username:
            attrs['username'] = self.username

        attrs['first_name'] = self.first_name
        if self.last_name:
            attrs['last_name'] = self.last_name

        attrs['last_update'] = round(self.last_update.timestamp())

        return f"<TelegramUser({', '.join(f'{k}={v}' for k,v in attrs.items())})>"

# represents telethon.tl.types.Chat -- can be a "group" or a megagroup... or maybe even a "channel"
# does not account for migrations -- migrations will completely change the chat id, not just the prefix
# assume we just got dumped into a new chat if that's the case
class TelegramChat(Model):
    chat_id: int = fields.IntField(pk=True, description="Telegram internal chat id")
    chat_link: str = fields.CharField(max_length=64, null=True, description="Chat @link")
    chat_title: str = fields.CharField(max_length=512, null=False, description="Chat title")
    last_update: datetime = fields.DatetimeField(null=False, description="last update in UTC")

    """
    Arguments:
    - client (TelegramClient): active telethon.TelegramClient
    - chat_id (int): chat id to get TelegramChat for
    - max_staleness (Optional[int]): max number of seconds from last update before we refresh chat info

    Returns:
    TelegramChat representing the chat with the given chat_id
    """
    @classmethod
    async def get_chat(cls, client: TelegramClient, chat_id: int, max_staleness: Optional[int] = 120): # returns TelegramChat
        if not chat_id < 0:
            raise ValueError("chat_id is a raw id -- should be negative to indicate a Chat or a Channel!")

        try:
            chat: TelegramChat = await cls.get(chat_id=chat_id)
        except DoesNotExist:
            chat: TelegramChat = TelegramChat(chat_id=chat_id)

        refresh: bool = not chat.last_update or (max_staleness and (datetime.utcnow().timestamp() - chat.last_update.timestamp()) > max_staleness)

        if refresh:
            await chat.refresh_from_tg(client)

        return chat

    async def refresh_from_tg(self, client: TelegramClient):
        entity: Entity = await client.get_entity(self.chat_id) # throws ValueError if not found

        # this should never happen because we check for chat_id < 0
        assert isinstance(entity, Channel), f"Got a {type(entity)} instead of a Channel!"

        self.chat_title = entity.title
        self.chat_link = entity.username if isinstance(entity, Channel) else None # don't bother with has_link since username will be None anyway
        self.last_update = datetime.utcnow()

        await self.save()

    def __str__(self):
        return self.__repr__()

    def __repr__(self):
        attrs: Dict[str, Union[str, int]] = {"chat_id": self.chat_id}

        if self.chat_link:
            attrs['chat_link'] = self.chat_link

        attrs['chat_title'] = self.chat_title
        attrs['last_update'] = round(self.last_update.timestamp())

        return f"<TelegramChat({', '.join(f'{k}={v}' for k,v in attrs.items())})>"
