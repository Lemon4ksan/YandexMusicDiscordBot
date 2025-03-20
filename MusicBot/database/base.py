import os
from typing import Iterable, Any, cast
from pymongo import AsyncMongoClient, ReturnDocument, UpdateOne
from pymongo.asynchronous.collection import AsyncCollection
from pymongo.results import UpdateResult

from .user import User, ExplicitUser
from .guild import Guild, ExplicitGuild, MessageVotes

mongo_server = os.getenv('MONGO_URI')
if not mongo_server:
    raise ValueError('MONGO_URI environment variable is not set')

client: AsyncMongoClient = AsyncMongoClient(mongo_server)

db = client.YandexMusicBot
users: AsyncCollection[ExplicitUser] = db.users
guilds: AsyncCollection[ExplicitGuild] = db.guilds

class BaseUsersDatabase:
    DEFAULT_USER = User(
        ym_token=None,
        vibe_batch_id=None,
        vibe_type=None,
        vibe_id=None,
        vibe_settings={
            'mood': 'all',
            'diversity': 'default',
            'lang': 'any'
        }
    )

    async def update(self, uid: int, data: User | dict[str, Any]) -> UpdateResult:
        return await users.update_one(
            {'_id': uid},
            {'$set': data},
            upsert=True
        )

    async def get_user(self, uid: int, projection: User | Iterable[str] | None = None) -> ExplicitUser:
        user = await users.find_one_and_update(
            {'_id': uid},
            {'$setOnInsert': self.DEFAULT_USER},
            return_document=ReturnDocument.AFTER,
            upsert=True,
            projection=projection
        )
        ops = []

        for key, value in self.DEFAULT_USER.items():
            if key not in user and (projection is None or key in projection):
                user[key] = value
                ops.append(UpdateOne({'_id': uid}, {'$set': {key: value}}))
        for key, value in user.copy().items():
            if key not in self.DEFAULT_USER and key != '_id':
                del user[key]
                ops.append(UpdateOne({'_id': uid}, {'$unset': {key: ''}}))

        if ops:
            await users.bulk_write(ops)
        return cast(ExplicitUser, user)

    async def get_ym_token(self, uid: int) -> str | None:
        user = await users.find_one(
            {'_id': uid},
            projection={'ym_token': 1}
        )
        return cast(str | None, user.get('ym_token') if user else None)

    async def reset_vibe_settings(self, uid: int) -> None:
        await users.update_one(
            {'_id': uid},
            {'$set': {'vibe_settings': {
                'mood': 'all',
                'diversity': 'default',
                'lang': 'any'
            }}}
        )


class BaseGuildsDatabase:
    DEFAULT_GUILD = Guild(
        next_tracks=[],
        previous_tracks=[],
        current_track=None,
        current_menu=None,
        is_stopped=True,
        allow_change_connect=True,
        vote_switch_track=True,
        vote_add=True,
        shuffle=False,
        repeat=False,
        votes={},
        vibing=False,
        current_viber_id=None,
        use_single_token=False,
        single_token_uid=None
    )

    async def update(self, gid: int, data: Guild | dict[str, Any]) -> UpdateResult:
        return await guilds.update_one(
            {'_id': gid},
            {'$set': data},
            upsert=True
        )

    async def get_guild(self, gid: int, projection: Guild | Iterable[str] | None = None) -> ExplicitGuild:
        guild = await guilds.find_one_and_update(
            {'_id': gid},
            {'$setOnInsert': self.DEFAULT_GUILD},
            return_document=ReturnDocument.AFTER,
            upsert=True,
            projection=projection
        )
        ops = []

        for key, value in self.DEFAULT_GUILD.items():
            if key not in guild and (projection is None or key in projection):
                guild[key] = value
                ops.append(UpdateOne({'_id': gid}, {'$set': {key: value}}))
        for key, value in guild.copy().items():
            if key not in self.DEFAULT_GUILD and key != '_id':
                del guild[key]
                ops.append(UpdateOne({'_id': gid}, {'$unset': {key: ''}}))
    
        if ops:
            await guilds.bulk_write(ops)
        return cast(ExplicitGuild, guild)

    async def update_vote(self, gid: int, mid: int, data: MessageVotes) -> UpdateResult:
        return await guilds.update_one(
            {'_id': gid},
            {'$set': {f'votes.{mid}': data}}
        )
