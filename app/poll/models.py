import logging
from time import time
import uuid
import pytz

from enum import IntEnum
from datetime import datetime
from typing import AsyncIterator, Iterable, List, Optional, Dict, Union

from telethon import TelegramClient
from telethon.hints import Entity
from telethon.tl.types import User, Chat, Channel

from tortoise import fields
from tortoise.models import Model
from tortoise.exceptions import DoesNotExist, MultipleObjectsReturned
from tortoise.queryset import QuerySet
from tortoise.functions import Count

from app.models import TelegramUser, TelegramChat

from config import POLL__LIMIT, POLL__LIMIT_DURATION, POLL__THRESHOLD

logger = logging.getLogger(__name__)

class PollType(IntEnum):
    BAN = 1

class VoteChoice(IntEnum):
    YES = 1
    NO = 2

class Poll(Model):
    poll_id: uuid.UUID = fields.UUIDField(pk=True, default=uuid.uuid4, description="Unique poll id")
    timestamp: datetime = fields.DatetimeField(null=False, auto_now_add=True, description="Time of poll")
    poll_type: PollType = fields.IntEnumField(enum_type=PollType, null=False, default=PollType.BAN, description="type of poll")
    chat: TelegramChat = fields.ForeignKeyField("models.TelegramChat", null=False, on_delete=fields.RESTRICT, related_name=False, description="chat in which the poll was started")
    source: TelegramUser = fields.ForeignKeyField("models.TelegramUser", null=False, on_delete=fields.RESTRICT, related_name=False, description="user who initiated the poll")
    target: TelegramUser = fields.ForeignKeyField("models.TelegramUser", null=False, on_delete=fields.RESTRICT, related_name=False, description="target of the poll")
    ended: bool = fields.BooleanField(null=False, default=False, description="has the poll ended?")
    forced: bool = fields.BooleanField(null=False, default=False, description="was this poll forced, e.g. started by an admin?")
    msg_id: int = fields.IntField(null=False, description="message ID that bob was called on")
    poll_msg_id: int = fields.IntField(null=True, description="msg id of poll message")

    @classmethod
    async def poll_limit_reached(cls, chat: TelegramChat, poll_type: PollType = PollType.BAN, timestamp: datetime = None) -> bool:
        duration_start: datetime = (timestamp or datetime.now(tz=pytz.utc)) - POLL__LIMIT_DURATION
        count: int = await Poll.filter(chat=chat, poll_type=poll_type, timestamp__gt=duration_start, forced=False).count()
        return count >= POLL__LIMIT
    
    
    @classmethod
    async def get_poll_by_id(cls, poll_id: int):
        try:
            poll: Poll = await Poll.get(poll_id=poll_id)
            await poll.fetch_related('source', 'target', 'chat')
        except MultipleObjectsReturned:
            logger.exception("We're literally getting a Poll by the public key, why do we have multiple objects???")
        except DoesNotExist:
            logger.exception("poll id doesn't exist")
            raise
            
        return poll

    """
    throws PollLimitReached
    """
    @classmethod
    async def get_poll(cls, chat: TelegramChat, target: TelegramUser, source: TelegramUser, msg_id: int, poll_type: PollType = PollType.BAN, force: bool = False): # returns (already_exists: bool, Poll)
        poll: Optional[Poll] = None
        try:
            poll = await Poll.get(chat=chat, target=target, poll_type=poll_type, ended=False)
        except MultipleObjectsReturned:
            logger.exception("Uh oh, we got multiple objects (this should never happen)! Trying to reconcile...")
            polls: QuerySet[Poll] = await Poll.filter(chat=chat, target=target, poll_type=poll_type, ended=False).order_by('-timestamp')

            # why is there no aiter()?
            # actually, idk lol, whatever
            got_latest: bool = False

            async for item in polls:
                if not got_latest:
                    poll = item
                    got_latest = True
                else:
                    item.ended = True
                    await item.save()

            assert got_latest == True, f"We got MultipleObjectsReturned for a poll (chat={chat}, target={target}, poll_type={poll_type}), but somehow we got none at all?!?"

            # at this point we HAVE to have a poll, right?
        except DoesNotExist:
            poll = None

        # if we got a suitable Poll instance
        if poll is not None:
            # but it's already finished...
            if await poll.vote_finished():
                poll.ended = True
                await poll.save()
                logger.warning(f"Got a poll {poll} that has already ended, let's try again")
                poll = None
            # or it's still running?
            else:
                await poll.fetch_related('source', 'target', 'chat')
                return (True, poll)

        # no suitable Poll instance, then
        # note that this can't be an else branch
        # because we set poll = None if the poll instance we got is unsuitable
        if poll is None:
            timestamp: datetime = datetime.now(tz=pytz.utc)
            limit_reached: bool = await Poll.poll_limit_reached(chat, poll_type, timestamp)
            if limit_reached:
                raise PollLimitReached(chat, poll_type, timestamp)

            poll: Poll = Poll(poll_type=poll_type, chat=chat, source=source, target=target, msg_id=msg_id)
            await poll.save()
            logger.info(f"Created new poll {poll.poll_id} in {chat}, type {poll_type}, source {source}, target {target}")
            return (False, poll)

    async def set_poll_msg_id(self, poll_msg_id: int):
        self.poll_msg_id = poll_msg_id
        await self.save()


    """
    returns true if changed, false otherwise
    """
    async def vote(self, user: TelegramUser, choice: VoteChoice) -> bool:
        await self.refresh_from_db() # try to avoid races?
        if self.ended:
            return True # fail fast

        try:
            vote: Vote = await Vote.get(poll=self, user=user)
            if vote.choice == choice:
                return False
            new: bool = False
        except MultipleObjectsReturned:
            logger.exception("Uh oh, we somehow got multiple objects for this!")
            raise
        except DoesNotExist:
            vote: Vote = Vote(poll=self, user=user)
            new: bool = False
        
        # at this point, we've either got an existing vote that needs to be changed
        # or a new vote

        # well, if it's an old vote, let this vote go through first

        if not new:
            vote.choice = choice
            await vote.save()
            logger.info(f"Updating vote choice to {choice} for vote id {vote.vote_id}")
        
        if await self.vote_finished():
            self.ended = True
            await self.save()
            logger.info(f"Poll finished for {self.poll_id}")
        elif new: # since we handled the thingamajig for !new
            vote.choice = choice
            await vote.save()
            logger.info(f"Creating new vote by {user} for {choice} on poll {self.poll_id} with vote id {vote.vote_id}")
        
        return True
    
    async def get_vote_stats(self) -> Dict[VoteChoice, int]:
        ret = dict()

        # [{"choice": VoteChoice.BLAH, "count": 5}, ...]
        counts: List[Dict[str, Union[VoteChoice, int]]] = await Vote.filter(poll=self).annotate(count=Count("user_id")).group_by('choice').values('choice', 'count')

        for cnt in counts:
            ret[cnt['choice']] = cnt['count']
        
        return ret
    
    async def vote_finished(self) -> bool:
        stats: Dict[VoteChoice, int] = await self.get_vote_stats()
        for choice, count in stats.items():
            if count >= POLL__THRESHOLD:
                return True

        return False

    
    async def get_voters(self, choice: VoteChoice) -> List[TelegramUser]:
        user_ids: List[int] = await Vote.filter(poll=self, choice=choice).order_by('timestamp').values_list('user__user_id', flat=True)
        users_dict: Dict[int, TelegramUser] = {user.user_id: user async for user in TelegramUser.filter(user_id__in=user_ids)}
        assert len(user_ids) == len(users_dict), "user_ids len doesn't match users_dict len"
        users: List[TelegramUser] = [users_dict[user_id] for user_id in user_ids]
        return users
            
    
    def __repr__(self):
        attrs: Dict[str, Union[str, int, bool]] = {
            "poll_id": self.poll_id,
            "timestamp": str(self.timestamp),
            "poll_type": str(self.poll_type),
            "chat": repr(self.chat),
            "target": repr(self.target),
            "ended": self.ended,
            "forced": self.forced,
            "msg_id": self.msg_id
        }

        return f"<Poll({', '.join(f'{k}={v}' for k,v in attrs.items())})"

class PollLimitReached(Exception):
    def __init__(self, chat: TelegramChat, poll_type: PollType, timestamp: datetime):
        self.chat = chat
        self.poll_type = type
        self.timestamp = timestamp

    def __repr__(self):
        return f"<PollLimitReached(chat={self.chat}, poll_type={self.poll_type}, timestamp={self.timestamp})>"
    
    def __str__(self):
        return f"Poll limit reached for poll type {self.poll_type} in chat={self.chat} at timestamp={self.timestamp}"

class Vote(Model):
    vote_id: uuid.UUID = fields.UUIDField(pk=True, default=uuid.uuid4, description="Unique vote id")
    poll: Poll = fields.ForeignKeyField("models.Poll", on_delete=fields.RESTRICT)
    user: TelegramUser = fields.ForeignKeyField("models.TelegramUser", on_delete=fields.RESTRICT, related_name=False, description="user that cast this vote")
    choice: VoteChoice = fields.IntEnumField(enum_type=VoteChoice, null=False, description="choice selected by user")
    timestamp: datetime = fields.DatetimeField(null=False, auto_now_add=True, description="Time of first vote")

    class Meta:
        # compound index for the components that make up `key`
        unique_together = (("poll", "user"),)
