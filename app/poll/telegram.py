import math
import asyncio
from typing import Dict, List, Optional, Union

from tortoise.exceptions import DoesNotExist
from app.poll.models import Poll, PollLimitReached, VoteChoice
from telethon.events.newmessage import NewMessage
from telethon.hints import Entity
from config import POLL__LIMIT_DURATION, TG_BOT_ID, TG_BOT_USERNAME, POLL__CHANNELS, POLL__THRESHOLD
from ..telegram import client
from ..models import TelegramUser, TelegramChat

from telethon import events, Button, utils
from telethon.tl.types import Channel, Message, PeerChannel, PeerChat, PeerUser, User, ChannelParticipantCreator, ChannelParticipantAdmin
from telethon.tl.functions.channels import GetParticipantRequest
from telethon.errors.rpcerrorlist import MessageDeleteForbiddenError, ChatAdminRequiredError, UserNotParticipantError

import re
from datetime import timedelta

import logging
logger = logging.getLogger(__name__)

"""
channel and user can be any InputChannel or InputUser (e.g. Channel, PeerChannel, etc)
"""
async def is_admin(channel, user):
    try:
        participant = await client(GetParticipantRequest(channel=channel, participant=user))
        isadmin = (type(participant.participant) == ChannelParticipantAdmin)
        iscreator = (type(participant.participant) == ChannelParticipantCreator)
        return isadmin or iscreator
    except UserNotParticipantError:
        # then this dude's definitely not an admin or the creator of the chat
        return False


"""
chat_id has to be, well, a chat_id
does it have to include the id prefix as well? I don't know lol
"""
async def get_channel(chat_id) -> Channel: # throws ValueError if not found, or not channel
    entity: Entity = await client.get_entity(chat_id) # throws ValueError if not found

    if isinstance(entity, Channel):
        return entity
    else:
        raise ValueError(f"Got a {type(entity)} instead of a Channel!")


"""
user_id has to be, well, a user_id
does it have to include the id prefix as well? I don't know lol
"""
async def get_user(user_id) -> User: # throws ValueError if not found, or not user
    entity: Entity = await client.get_entity(user_id) # throws ValueError if not found

    if isinstance(entity, User):
        return entity
    else:
        raise ValueError(f"Got a {type(entity)} instead of a User!")


async def build_bob_message(poll: Poll, ended: bool, counts: Dict[VoteChoice, int], winner: VoteChoice = None) -> Dict[str, Union[str, List[Button]]]:
    assert not ended or winner is not None, "Poll ended, but no winner passed to build_bob_message!"
    
    if not ended:
        message_lines = [
            f"{poll.source.get_link()} would like to kick {poll.target.get_link()}.",
            "Do you agree?"
        ]

        # make sure button data is less than... 64 bytes? 14 bytes + 36 for uuid = 50, nice
        buttons = [
            Button.inline(f"Yes: {counts.get(VoteChoice.YES, 0)}/{POLL__THRESHOLD}", f"poll_vote {poll.poll_id} yes"),
            Button.inline(f"No: {counts.get(VoteChoice.NO, 0)}/{POLL__THRESHOLD}", f"poll_vote {poll.poll_id} no"),
        ]

        return {
            "message": '\n'.join(message_lines),
            "poll": poll,
            "buttons": buttons,
        }
    else:
        kicked: bool = winner == VoteChoice.YES
        users_list: List[int] = await poll.get_voters(winner)
        users: str = ', '.join([user.get_link() for user in users_list])
        message_lines = [
            f"The community has decided that {poll.target.get_link()} should {'' if kicked else 'not '}be banned.",
            f"The following users voted {'Yes' if kicked else 'No'}: {users}"
        ]

        return {
            "message": '\n'.join(message_lines),
            "poll": poll,
        }


async def bob_vote(poll: Poll, user: TelegramUser, choice: VoteChoice) -> Dict[str, Union[str, List[Button]]]:
    changed: bool = await poll.vote(user, choice)
    
    counts: Dict[VoteChoice, int] = await poll.get_vote_stats()

    ended = False
    for choice, count in counts.items():
        if count >= POLL__THRESHOLD:
            ended = True
            break
    
    if ended:
        need_delete_perms: bool = False
        need_ban_perms: bool = False
        if choice == VoteChoice.YES:
            channel_ent: Channel = await get_channel(poll.chat.chat_id)
            msg: Optional[str] = None
            async for m in client.iter_messages(entity=channel_ent, ids=poll.msg_id):
                msg = m
                break # lol

            if msg is not None:
                try:
                    await msg.delete()
                except MessageDeleteForbiddenError:
                    logger.warning(f"No message delete permissions in {poll.chat_id}!")
                    need_delete_perms = True
                except Exception:
                    logger.exception(f"Uh oh, got an exception while trying to delete a message ({poll})")

            to_ban = await get_user(poll.target.user_id)

            try:
                await client.edit_permissions(channel_ent, to_ban, view_messages=False)
            except ChatAdminRequiredError:
                logger.warning(f"No ban permissions in {poll.chat_id}!")
                need_ban_perms = True
            except Exception:
                logger.exception(f"Uh oh, got exception while trying to ban a user ({poll})")
        
        bob_message: Dict[str, Union[str, List[Button]]] = await build_bob_message(poll, ended, counts, winner = choice)
        if not need_delete_perms and not need_ban_perms:
            return bob_message
        else:            
            perms_msg = f"\n\n(I require {'delete ' if need_delete_perms else ''}{'and ' if need_delete_perms and need_ban_perms else ''}{'ban ' if need_ban_perms else ''}permissions to work properly!)"
            bob_message['message'] += perms_msg
            return bob_message
    else:
        bob_message: Dict[str, Union[str, List[Button]]] = await build_bob_message(poll, ended, counts)
        bob_message['unchanged'] = not changed
        return bob_message


def pretty_timedelta(td: timedelta) -> str:
    parts = list()

    if td.days > 0:
        parts.append(f"{td.days} {'day' if td.days == 1 else 'days'}")
    
    if td.seconds > 0:
        hours = math.floor(td.seconds/60/60)
        minutes = math.floor(td.seconds/60 % 60)
        seconds = math.floor(td.seconds % 60)

        if hours > 0:
            parts.append(f"{hours} {'hour' if hours == 1 else 'hours'}")
        if minutes > 0:
            parts.append(f"{minutes} {'minute' if minutes == 1 else 'minutes'}")
        if seconds > 0:
            parts.append(f"{seconds} {'second' if seconds == 1 else 'seconds'}")

    return ', '.join(parts)


regex_bob = re.compile(fr'^/bob(?:@{TG_BOT_USERNAME})?$', re.I)
@events.register(events.NewMessage(incoming=True, pattern=regex_bob, chats=POLL__CHANNELS))
async def handler_bob(event: NewMessage):
    chat_ent: PeerChannel = event.to_id
    if not isinstance(chat_ent, PeerChannel):
        logger.error(f"Got handler_bob event from a {type(chat_ent)} instead of a PeerChannel!")
        return

    chat_id: int = utils.get_peer_id(chat_ent)
    
    if not event.is_reply:
        await event.reply("Try replying to a message with /bob instead!")
        return
    
    chat: TelegramChat = await TelegramChat.get_chat(client, chat_id)
    target_msg: Message = await event.get_reply_message()

    if not isinstance(target_msg.from_id, PeerUser) or target_msg.from_id.user_id == TG_BOT_ID or await is_admin(chat_ent, target_msg.from_id):
        await event.reply("I'm sorry Dave, I can't let you do that.")
        return
    
    target_ent: PeerUser = target_msg.from_id
    target: TelegramUser = await TelegramUser.get_user(client, target_ent.user_id)

    # at this point, we've got a valid user to bob
    # and it's not an admin either
    # now we do other checks: is the poll limit exceeded, is the sender an admin?
    # actually that happens in models.py lol

    from_user_ent: PeerUser = event.from_id
    from_user: TelegramUser = await TelegramUser.get_user(client, from_user_ent.user_id)

    force: bool = isinstance(event.from_id, PeerUser) and await is_admin(chat_ent, event.from_id)

    already_exists: bool
    poll: Poll
    try:
        already_exists, poll = await Poll.get_poll(chat=chat, target=target, source=from_user, msg_id=target_msg.id, force=force)
        if already_exists:
            if poll.poll_msg_id is None:
                logger.error("poll_msg_id is None, sleeping 1s. hopefully it'll be ready by then")
                asyncio.sleep(1)
            if poll.poll_msg_id is None:
                logger.error("still not ready, oh well...")
                await event.reply(f"There's already a vote in progress!")
                return
            else:
                msg: Optional[Message] = None
                async for m in client.iter_messages(entity=chat_ent, ids=poll.poll_msg_id):
                    msg = m
                    break # lol

                if msg is not None:
                    await event.reply(f'Please vote <a href="https://t.me/c/{str(chat_id)[4:]}/{poll.poll_msg_id}">here</a> instead.')
                    return
                else:
                    logger.warning("Our old poll message got deleted for some reason!")
                    poll.ended = True
                    await poll.save()
                    _, poll = await Poll.get_poll(chat=chat, target=target, source=from_user, msg_id=target_msg.id, force=True) # ahh heck, whatever


        msg_dict: Dict[str, Union[str, List[Button]]] = await bob_vote(poll, from_user, VoteChoice.YES)

        msg: Message = await target_msg.reply(
            msg_dict['message'],
            buttons = msg_dict.get('buttons')
        )

        await poll.set_poll_msg_id(msg.id)
    except PollLimitReached:
        await event.reply(f"Too many ban attempts in the past {pretty_timedelta(POLL__LIMIT_DURATION)}. Please contact an admin instead.")


regex_bob_callback = re.compile("^poll_vote (?P<poll_id>[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}) (?P<choice>[a-z_]+)$", re.I)
@events.register(events.CallbackQuery(data=re.compile(b'poll_vote ')))
async def handler_bob_callback(event):
    bot_msg: Message = await event.get_message()
    data: str = event.data.decode('ascii')
    match: re.Match = regex_bob_callback.match(data)

    if not match:
        logger.error("bob_callback data doesn't match regex!")
        await bot_msg.edit(bot_msg.text + '\n\n' + f"Oops, something went wrong!", buttons=None)
        return
    
    poll_id: str = match.group("poll_id")
    choice_str: str = match.group("choice").lower()
    
    if choice_str == "yes":
        choice: VoteChoice = VoteChoice.YES
    elif choice_str == 'no':
        choice: VoteChoice = VoteChoice.NO
    else:
        logger.error(f"bob_callback data got an invalid choice ({choice_str})!")
        await bot_msg.edit(bot_msg.text + '\n\n' + f"Oops, something went wrong!", buttons=None)
        return

    try:
        poll: Poll = await Poll.get_poll_by_id(poll_id=poll_id)
    except DoesNotExist:
        logger.error(f"bob_callback data got a valid poll_id, but there's no Poll corresponding to this id!")
        await bot_msg.edit(bot_msg.text + '\n\n' + f"Oops, something went wrong!", buttons=None)
        return
    
    user: TelegramUser = await TelegramUser.get_user(client, user_id=event.sender_id)
    msg_dict: Dict[str, Union[str, List[Button]]] = await bob_vote(poll, user, choice)

    if not msg_dict.get('unchanged', False):
        await bot_msg.edit(
            msg_dict['message'],
            buttons = msg_dict.get('buttons')
        )
        await event.answer(f"You've voted for {choice_str.capitalize()}!")
    else:
        await event.answer("You can't vote for the same choice multiple times.")
