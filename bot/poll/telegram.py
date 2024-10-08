import math
import asyncio
from typing import Dict, List, Optional, Union

import cachetools
from tortoise.exceptions import DoesNotExist

from bot.poll.models import Poll, PollLimitReached, VoteChoice
from bot.util import Timer
from config import POLL__LIMIT_DURATION, TG_BOT_ID, TG_BOT_USERNAME, POLL__CHANNELS, POLL__THRESHOLD
from ..telegram import client
from ..models import TelegramUser, TelegramChat

from telethon import events, Button, utils
from telethon.tl.types import Channel, Message, PeerChannel, PeerChat, PeerUser, User, InputPeerUser, InputPeerChannel, ChannelParticipantCreator, ChannelParticipantAdmin, TypeInputPeer, TypeMessageEntity, MessageEntityMention, MessageEntityMentionName
from telethon.tl.functions.channels import GetParticipantRequest
from telethon.errors.rpcerrorlist import MessageDeleteForbiddenError, ChatAdminRequiredError, UserNotParticipantError
from telethon.events.newmessage import NewMessage
from telethon.hints import Entity

import re
from datetime import timedelta

import logging
logger = logging.getLogger(__name__)

participant_cache = cachetools.TTLCache(maxsize=64, ttl=3*60)

"""
returns ChannelParticipant if user is in chat, else None
"""
async def get_participant(channel, user):
    tup = (getattr(channel, 'chat_id', None) or getattr(channel, 'channel_id', None), user.user_id)
    res = participant_cache.get(tup)
    if res:
        return res
    else:
        try:
            participant = await client(GetParticipantRequest(channel=channel, participant=user))
            res = participant
        except UserNotParticipantError:
            res = None

        participant_cache[tup] = res
        return res

"""
returns True if user is in chat, else False
"""
async def is_participant(channel, user):
    return (await get_participant(channel, user)) is not None

"""
channel and user can be any InputChannel or InputUser (e.g. Channel, PeerChannel, etc)
"""
async def is_admin(channel, user):
    participant = await get_participant(channel, user)
    if not participant:
        # then this dude's definitely not an admin or the creator of the chat
        return False

    isadmin = (type(participant.participant) == ChannelParticipantAdmin)
    iscreator = (type(participant.participant) == ChannelParticipantCreator)
    return isadmin or iscreator


"""
ent_id can be a telegram id or an @usernamechat public link name without `@`
works with channels and users, hopefully
does it have to include the id prefix as well? I don't know lol
"""
async def get_entity(ent_id, get_peer=False, force_refresh=False) -> Union[PeerChannel, Channel, PeerUser, User]: # throws ValueError if not found, or not channel
    if isinstance(ent_id, str):
        # remove @ if required, I guess
        ent_id = ent_id.lstrip('@')

    if get_peer and force_refresh:
        raise ValueError("Cannot use get_peer and force_refresh together!")

    if get_peer:
        input_entity: TypeInputPeer = await client.get_input_entity(ent_id)

        if isinstance(input_entity, InputPeerChannel):
            return PeerChannel(input_entity.channel_id)
        elif isinstance(input_entity, InputPeerUser):
            return PeerUser(input_entity.user_id)
        else:
            raise ValueError(f"Got a {type(input_entity)} instead of an InputPeerChannel or InputPeerUser!")
    else:
        # one of these will throw ValueError if not found
        input_entity: Union[TypeInputPeer, int, str] = await client.get_input_entity(ent_id) if not force_refresh else ent_id
        entity: Entity = await client.get_entity(input_entity)

        if isinstance(entity, (Channel, User)):
            return entity
        else:
            raise ValueError(f"Got a {type(entity)} instead of a Channel or User!")

"""
chat_id can be a chat id or a chat public link name without `@`
does it have to include the id prefix as well? I don't know lol
"""
async def get_channel(chat_id, get_peer=False, force_refresh=False) -> Union[PeerChannel, Channel]: # throws ValueError if not found, or not channel
    ent: Union[PeerChannel, Channel, PeerUser, User] = await get_entity(chat_id, get_peer, force_refresh)
    if get_peer and not isinstance(ent, PeerChannel):
        raise ValueError(f"Got a {type(ent)} instead of a PeerChannel!")
    elif not get_peer and not isinstance(ent, Channel):
        raise ValueError(f"Got a {type(ent)} instead of a Channel!")
    return ent

"""
user_id can be a user id or username without `@`
does it have to include the id prefix as well? I don't know lol
"""
async def get_user(user_id, get_peer=False, force_refresh=False) -> Union[PeerUser, User]: # throws ValueError if not found, or not user
    ent: Union[PeerChannel, Channel, PeerUser, User] = await get_entity(user_id, get_peer, force_refresh)
    if get_peer and not isinstance(ent, PeerUser):
        raise ValueError(f"Got a {type(ent)} instead of a PeerUser!")
    elif not get_peer and not isinstance(ent, User):
        raise ValueError(f"Got a {type(ent)} instead of a User!")
    return ent

async def get_message(channel, msg_id) -> Message:
    assert msg_id is not None, "msg_id cannot be none"
    async for m in client.iter_messages(entity=channel, ids=msg_id):
        return m


async def build_bob_message(poll: Poll, ended: bool, counts: Dict[VoteChoice, int], winner: VoteChoice = None) -> Dict[str, Union[str, List[Button]]]:
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
    elif winner is None: # poll ended, but no winner
        logger.warning(f"Poll {poll.poll_id} ended, but there was no winner!")
        message_lines = [
            f"Something went wrong, so we've done nothing to {poll.target.get_link()}.",
            "Please try again."
        ]

        return {
            "message": '\n'.join(message_lines),
            "poll": poll,
        }
    else: # poll ended, and we have a winner
        kicked: bool = winner == VoteChoice.YES
        users_list: List[int] = await poll.get_voters(winner)
        users: str = ', '.join([user.get_link() for user in users_list])
        message_lines = [
            f"The community has decided that {poll.target.get_link()} should {'' if kicked else 'not '}be banned.",
            f"The following users voted {'yes' if kicked else 'no'}: {users}"
        ]

        return {
            "message": '\n'.join(message_lines),
            "poll": poll,
        }


async def bob_vote(poll: Poll, user: TelegramUser, choice: VoteChoice) -> Dict[str, Union[str, List[Button]]]:
    changed: bool = await poll.vote(user, choice)

    counts: Dict[VoteChoice, int] = await poll.get_vote_stats()

    ended = poll.ended
    choice = await poll.vote_winner()

    if ended:
        need_delete_perms: bool = False
        need_ban_perms: bool = False
        if choice == VoteChoice.YES:
            channel_ent: Channel = await get_channel(poll.chat.chat_id)
            msg: Optional[str] = None
            if poll.msg_id:
                msg = await get_message(channel_ent, poll.msg_id)

            if msg is not None:
                try:
                    await msg.delete()
                except MessageDeleteForbiddenError:
                    logger.warning(f"No message delete permissions in {poll.chat_id}!")
                    logger.warning(f"Message: {msg}")
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


regex_bob = re.compile(fr'^/(?P<cmd>bob|ngmi)(?:@{TG_BOT_USERNAME})?( *| +(?P<target>.+))$', re.I)
@events.register(events.NewMessage(incoming=True, pattern=regex_bob, chats=POLL__CHANNELS))
async def handler_bob(event: NewMessage):
    chat_ent: PeerChannel = event.to_id
    if not isinstance(chat_ent, PeerChannel):
        logger.error(f"Got handler_bob event from a {type(chat_ent)} instead of a PeerChannel!")
        return

    cmd: str = event.pattern_match.group('cmd')

    bob_arg: Union[str, int] = event.pattern_match.group('target')
    entities: Optional[List[TypeMessageEntity]] = None
    target_msg: Optional[Message] = None
    target_ent: Optional[PeerUser] = None

    if bob_arg:
        entities = list(filter(lambda tup: isinstance(tup[0], (MessageEntityMention, MessageEntityMentionName)), event.get_entities_text()))

        if len(entities) == 0: # no entities?
            if event.is_reply:
                msg: Message = await event.reply(f"It doesn't seem like you've mentioned a valid user. Try again, or reply to a message with just /{cmd} instead.")
            else:
                msg: Message = await event.reply("It doesn't seem like you've mentioned a valid user. Try again.")

            Timer(30, msg.delete)
            return
        else: # take only the first entity, i guess
            ent: TypeMessageEntity
            txt: str
            ent, txt = entities[0]
            if isinstance(ent, MessageEntityMention): # @username
                try:
                    target_ent = await get_user(txt, get_peer=True)
                except ValueError:
                    msg: Message = await event.reply("Hmm, I couldn't find any user with that username!")
                    Timer(30, msg.delete)
                    return
            elif isinstance(ent, MessageEntityMentionName): # no username
                try:
                    target_ent = await get_user(ent.user_id, get_peer=True)
                except ValueError:
                    msg: Message = await event.reply("Sorry, I couldn't find that user.")
                    Timer(30, msg.delete)
                    return
    elif event.is_reply:
        target_msg = await event.get_reply_message()
        target_ent = target_msg.from_id
    else: # no bob_arg, and not a reply
        msg = await event.reply(f"Try replying to a message with /{cmd} instead!")
        Timer(30, msg.delete)
        return

    from_user_ent: PeerUser = event.from_id
    from_user = None
    is_user: bool = isinstance(event.from_id, (PeerUser, User, InputPeerUser))

    if is_user:
        from_user: TelegramUser = await TelegramUser.get_user(client, from_user_ent.user_id)

        chat_id: int = utils.get_peer_id(chat_ent)
        chat: TelegramChat = await TelegramChat.get_chat(client, chat_id)
    else:
        logger.warning(f"User {from_user_ent} doesn't appear to be a user!")
        await event.reply("I'm sorry Dave, I'm afraid I can't do that.")
        return

    if not isinstance(target_ent, PeerUser):
        logger.warning(f"User {from_user or from_user_ent} tried to bob an unsupported target {target_ent} in {chat_ent}!")
        # TODO change message
        await event.reply("I'm sorry Dave, I'm afraid I can't do that.")
        return

    if target_ent.user_id == TG_BOT_ID or await is_admin(chat_ent, target_ent):
        logger.warning(f"User {from_user or from_user_ent} tried to bob an admin or the bot {target_ent} in {chat_ent}!")
        await event.reply("I'm sorry Dave, I'm afraid I can't do that.")
        return

    # at this point, we've got a valid user to bob
    # and it's not an admin either
    # now we do other checks: is the poll limit exceeded, is the sender an admin?
    # actually that happens in models.py lol

    target: TelegramUser = await TelegramUser.get_user(client, target_ent.user_id)

    force: bool = isinstance(event.from_id, PeerUser) and await is_admin(chat_ent, event.from_id)

    target_msg_id: Optional[int] = target_msg.id if target_msg else None

    reply_msg: Message = target_msg or event

    already_exists: bool
    poll: Poll
    try:
        already_exists, poll = await Poll.get_poll(chat=chat, target=target, source=from_user, msg_id=target_msg_id, force=force)
        if already_exists:
            msg: Optional[Message] = None

            if poll.poll_msg_id is None:
                logger.error("poll_msg_id is None, sleeping 1s. hopefully it'll be ready by then")
                await asyncio.sleep(1)
                await poll.refresh_from_db(fields=['poll_msg_id'])

            if poll.poll_msg_id is None:
                logger.error("still not ready, oh well...")
            else:
                msg = await get_message(chat_ent, poll.poll_msg_id)

            if msg is not None:
                if await is_participant(chat_ent, from_user_ent):
                    msg_dict: Dict[str, Union[str, List[Button]]] = await bob_vote(poll, from_user, VoteChoice.YES)

                    if not msg_dict.get('unchanged', False):
                        await msg.edit(
                            msg_dict['message'],
                            buttons = msg_dict.get('buttons')
                        )
                        await event.reply(f'There\'s already an active poll <a href="https://t.me/c/{str(chat_id)[4:]}/{poll.poll_msg_id}">here</a>. Your vote for "Yes" has been added.')
                    else:
                        await event.reply(f'There\'s already an active poll <a href="https://t.me/c/{str(chat_id)[4:]}/{poll.poll_msg_id}">here</a>.')
                else:
                    logger.error(f"User {from_user} is trying to vote in poll {poll} by sending /bob, but is not in the channel!")
                    await event.reply(f'There\'s already an active poll <a href="https://t.me/c/{str(chat_id)[4:]}/{poll.poll_msg_id}">here</a>.')

                return
            else:
                logger.warning("Our old poll message got deleted for some reason!")
                await poll.force_end()
                _, poll = await Poll.get_poll(chat=chat, target=target, source=from_user, msg_id=target_msg_id, force=True) # ahh heck, whatever


        msg_dict: Dict[str, Union[str, List[Button]]] = await bob_vote(poll, from_user, VoteChoice.YES)

        try:
            msg: Message = await reply_msg.reply(
                msg_dict['message'],
                buttons = msg_dict.get('buttons')
            )

            await poll.set_poll_msg_id(msg.id)
        except Exception:
            logger.warning("Got error while trying to send message!")
            await poll.delete()
            return
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

    sender = await event.get_input_sender()
    user: TelegramUser = await TelegramUser.get_user(client, user_id=event.sender_id)

    if await is_participant(PeerChannel(poll.chat.chat_id), sender):
        msg_dict: Dict[str, Union[str, List[Button]]] = await bob_vote(poll, user, choice)

        if not msg_dict.get('unchanged', False):
            await bot_msg.edit(
                msg_dict['message'],
                buttons = msg_dict.get('buttons')
            )
            await event.answer(f"You've voted for {choice_str.capitalize()}!")
        else:
            await event.answer("You can't vote for the same choice multiple times.")
    else:
        logger.warning(f"User {user} is trying to vote in poll {poll} despite not being in the channel!")
        await event.answer()
