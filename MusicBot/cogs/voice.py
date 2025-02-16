import logging
from typing import cast

import discord
from discord.ext.commands import Cog

from yandex_music import ClientAsync as YMClient
from yandex_music.exceptions import UnauthorizedError

from MusicBot.database import BaseUsersDatabase
from MusicBot.cogs.utils import VoiceExtension, menu_views
from MusicBot.ui import QueueView, generate_queue_embed

def setup(bot: discord.Bot):
    bot.add_cog(Voice(bot))

users_db = BaseUsersDatabase()

async def get_vibe_stations_suggestions(ctx: discord.AutocompleteContext) -> list[str]:
    if not ctx.interaction.user or not ctx.value or len(ctx.value) < 2:
        return []

    token = await users_db.get_ym_token(ctx.interaction.user.id)
    if not token:
        logging.info(f"[GENERAL] User {ctx.interaction.user.id} has no token")
        return []

    try:
        client = await YMClient(token).init()
    except UnauthorizedError:
        logging.info(f"[GENERAL] User {ctx.interaction.user.id} provided invalid token")
        return []

    stations = await client.rotor_stations_list()
    return [station.station.name for station in stations if station.station and ctx.value in station.station.name][:100]


class Voice(Cog, VoiceExtension):

    voice = discord.SlashCommandGroup("voice", "Команды, связанные с голосовым каналом.")
    queue = discord.SlashCommandGroup("queue", "Команды, связанные с очередью треков.")
    track = discord.SlashCommandGroup("track", "Команды, связанные с треками в голосовом канале.")

    def __init__(self, bot: discord.Bot):
        VoiceExtension.__init__(self, bot)
        self.typed_bot: discord.Bot = bot

    @Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState) -> None:
        logging.info(f"[VOICE] Voice state update for member {member.id} in guild {member.guild.id}")

        gid = member.guild.id
        guild = await self.db.get_guild(gid, projection={'current_menu': 1, 'always_allow_menu': 1})
        discord_guild = await self.typed_bot.fetch_guild(gid)
        current_menu = guild['current_menu']

        channel = after.channel or before.channel
        if not channel:
            logging.info(f"[VOICE] No channel found for member {member.id}")
            return

        vc = cast(discord.VoiceClient | None, discord.utils.get(self.typed_bot.voice_clients, guild=discord_guild))

        if len(channel.members) == 1 and vc:
            logging.info(f"[VOICE] Clearing history and stopping playback for guild {gid}")

            if member.guild.id in menu_views:
                menu_views[member.guild.id].stop()
                del menu_views[member.guild.id]

            if guild['current_menu']:
                message = self.typed_bot.get_message(guild['current_menu'])
                if message:
                    await message.delete()

            await self.db.update(gid, {
                'previous_tracks': [], 'next_tracks': [], 'votes': [],
                'current_track': None, 'current_menu': None, 'vibing': False,
                'repeat': False, 'shuffle': False, 'is_stopped': True
            })
            vc.stop()
        elif len(channel.members) > 2 and not guild['always_allow_menu']:
            if current_menu:
                logging.info(f"[VOICE] Disabling current menu for guild {gid} due to multiple members")

                await self.db.update(gid, {'current_menu': None, 'repeat': False, 'shuffle': False, 'vibing': False})
                try:
                    message = await channel.fetch_message(current_menu)
                    await message.delete()
                    await channel.send("Меню отключено из-за большого количества участников.", delete_after=15)
                except (discord.NotFound, discord.Forbidden):
                    pass
            
            if member.guild.id in menu_views:
                menu_views[member.guild.id].stop()
                del menu_views[member.guild.id]

    @Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        logging.info(f"[VOICE] Reaction added by user {payload.user_id} in channel {payload.channel_id}")
        if not self.typed_bot.user or not payload.member:
            return

        bot_id = self.typed_bot.user.id
        if payload.user_id == bot_id:
            return

        channel = cast(discord.VoiceChannel, self.typed_bot.get_channel(payload.channel_id))
        if not channel:
            return

        message = await channel.fetch_message(payload.message_id)
        if not message or message.author.id != bot_id:
            return

        if not await self.users_db.get_ym_token(payload.user_id):
            await message.remove_reaction(payload.emoji, payload.member)
            await channel.send("Для участия в голосовании необходимо авторизоваться через /account login.", delete_after=15)
            return

        guild_id = payload.guild_id
        if not guild_id:
            return

        guild = await self.db.get_guild(guild_id, projection={'votes': 1, 'current_track': 1})
        votes = guild['votes']

        if str(payload.message_id) not in votes:
            logging.info(f"[VOICE] Message {payload.message_id} not found in votes")
            return

        vote_data = votes[str(payload.message_id)]
        logging.debug(f"[VOICE] Vote data for message {payload.message_id}: {vote_data}")

        if payload.emoji.name == '✅':
            logging.info(f"[VOICE] User {payload.user_id} voted positively for message {payload.message_id}")
            vote_data['positive_votes'].append(payload.user_id)
        elif payload.emoji.name == '❌':
            logging.info(f"[VOICE] User {payload.user_id} voted negatively for message {payload.message_id}")
            vote_data['negative_votes'].append(payload.user_id)

        total_members = len(channel.members)
        required_votes = 2 if total_members <= 5 else 4 if total_members <= 10 else 6 if total_members <= 15 else 9
        if len(vote_data['positive_votes']) >= required_votes:
            logging.info(f"[VOICE] Enough positive votes for message {payload.message_id}")

            if vote_data['action'] == 'next':
                logging.info(f"[VOICE] Skipping track for message {payload.message_id}")

                title = await self.next_track(payload)
                await message.clear_reactions()
                await message.edit(content=f"Сейчас играет: **{title}**!", delete_after=15)
                del votes[str(payload.message_id)]

            elif vote_data['action'] == 'add_track':
                logging.info(f"[VOICE] Adding track for message {payload.message_id}")
                await message.clear_reactions()

                track = vote_data['vote_content']
                if not track:
                    logging.info(f"[VOICE] Recieved empty vote context for message {payload.message_id}")
                    return

                await self.db.modify_track(guild_id, track, 'next', 'append')

                if guild['current_track']:
                    await message.edit(content=f"Трек был добавлен в очередь!", delete_after=15)
                else:
                    title = await self.next_track(payload)
                    await message.edit(content=f"Сейчас играет: **{title}**!", delete_after=15)

                del votes[str(payload.message_id)]

            elif vote_data['action'] in ('add_album', 'add_artist', 'add_playlist'):
                logging.info(f"[VOICE] Performing '{vote_data['action']}' action for message {payload.message_id}")

                await message.clear_reactions()
                
                tracks = vote_data['vote_content']
                if not tracks:
                    logging.info(f"[VOICE] Recieved empty vote context for message {payload.message_id}")
                    return

                await self.db.update(guild_id, {'is_stopped': False})
                await self.db.modify_track(guild_id, tracks, 'next', 'extend')

                if guild['current_track']:
                    await message.edit(content=f"Контент был добавлен в очередь!", delete_after=15)
                else:
                    title = await self.next_track(payload)
                    await message.edit(content=f"Сейчас играет: **{title}**!", delete_after=15)

                del votes[str(payload.message_id)]

        elif len(vote_data['negative_votes']) >= required_votes:
            logging.info(f"[VOICE] Enough negative votes for message {payload.message_id}")
            await message.clear_reactions()
            await message.edit(content='Запрос был отклонён.', delete_after=15)
            del votes[str(payload.message_id)]

        await self.db.update(guild_id, {'votes': votes})

    @Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent) -> None:
        logging.info(f"[VOICE] Reaction removed by user {payload.user_id} in channel {payload.channel_id}")
        if not self.typed_bot.user:
            return

        guild_id = payload.guild_id
        if not guild_id:
            return
        guild = await self.db.get_guild(guild_id, projection={'votes': 1})
        votes = guild['votes']
        
        if str(payload.message_id) not in votes:
            logging.info(f"[VOICE] Message {payload.message_id} not found in votes")
            return

        channel = cast(discord.VoiceChannel, self.typed_bot.get_channel(payload.channel_id))
        if not channel:
            return

        message = await channel.fetch_message(payload.message_id)
        if not message or message.author.id != self.typed_bot.user.id:
            return

        vote_data = votes[str(payload.message_id)]
        if payload.emoji.name == '✔️':
            logging.info(f"[VOICE] User {payload.user_id} removed positive vote for message {payload.message_id}")
            del vote_data['positive_votes'][payload.user_id]
        elif payload.emoji.name == '❌':
            logging.info(f"[VOICE] User {payload.user_id} removed negative vote for message {payload.message_id}")
            del vote_data['negative_votes'][payload.user_id]

        await self.db.update(guild_id, {'votes': votes})
    
    @voice.command(name="menu", description="Создать меню проигрывателя. Доступно только если вы единственный в голосовом канале.")
    async def menu(self, ctx: discord.ApplicationContext) -> None:
        logging.info(f"[VOICE] Menu command invoked by user {ctx.author.id} in guild {ctx.guild.id}")
        if not await self.voice_check(ctx):
            return

        guild = await self.db.get_guild(ctx.guild.id, projection={'always_allow_menu': 1})
        channel = cast(discord.VoiceChannel, ctx.channel)

        if len(channel.members) > 2 and not guild['always_allow_menu']:
            logging.info(f"[VOICE] Action declined: other members are present in the voice channel")
            await ctx.respond("❌ Вы не единственный в голосовом канале.", ephemeral=True)
            return

        await self.send_menu_message(ctx)

    @voice.command(name="join", description="Подключиться к голосовому каналу, в котором вы сейчас находитесь.")
    async def join(self, ctx: discord.ApplicationContext) -> None:
        logging.info(f"[VOICE] Join command invoked by user {ctx.author.id} in guild {ctx.guild.id}")

        member = cast(discord.Member, ctx.author)
        guild = await self.db.get_guild(ctx.guild.id, projection={'allow_connect': 1})

        if not member.guild_permissions.manage_channels and not guild['allow_connect']:
            response_message = "❌ У вас нет прав для выполнения этой команды."
        elif (vc := await self.get_voice_client(ctx)) and vc.is_connected():
            response_message = "❌ Бот уже находится в голосовом канале. Выключите его с помощью команды /voice leave."
        elif isinstance(ctx.channel, discord.VoiceChannel):
            await ctx.channel.connect(timeout=15)
            response_message = "Подключение успешно!"
        else:
            response_message = "❌ Вы должны отправить команду в голосовом канале."

        logging.info(f"[VOICE] Join command response: {response_message}")
        await ctx.respond(response_message, delete_after=15, ephemeral=True)

    @voice.command(description="Заставить бота покинуть голосовой канал.")
    async def leave(self, ctx: discord.ApplicationContext) -> None:
        logging.info(f"[VOICE] Leave command invoked by user {ctx.author.id} in guild {ctx.guild.id}")

        member = cast(discord.Member, ctx.author)
        guild = await self.db.get_guild(ctx.guild.id, projection={'allow_connect': 1})
        
        if not member.guild_permissions.manage_channels and not guild['allow_connect']:
            logging.info(f"[VOICE] User {ctx.author.id} does not have permissions to execute leave command in guild {ctx.guild.id}")
            await ctx.respond("❌ У вас нет прав для выполнения этой команды.", delete_after=15, ephemeral=True)
            return

        if (vc := await self.get_voice_client(ctx)) and await self.voice_check(ctx) and vc.is_connected:
            res = await self.stop_playing(ctx, full=True)
            if res:
                await vc.disconnect(force=True)
                await ctx.respond("Отключение успешно!", delete_after=15, ephemeral=True)
                logging.info(f"[VOICE] Successfully disconnected from voice channel in guild {ctx.guild.id}")
                return
            else:
                await ctx.respond("❌ Не удалось отключиться.", delete_after=15, ephemeral=True)
        else:
            await ctx.respond("❌ Бот не подключен к голосовому каналу.", delete_after=15, ephemeral=True)

    @queue.command(description="Очистить очередь треков и историю прослушивания.")
    async def clear(self, ctx: discord.ApplicationContext) -> None:
        logging.info(f"[VOICE] Clear queue command invoked by user {ctx.author.id} in guild {ctx.guild.id}")

        member = cast(discord.Member, ctx.author)
        channel = cast(discord.VoiceChannel, ctx.channel)

        if len(channel.members) > 2 and not member.guild_permissions.manage_channels:
            logging.info(f"[VOICE] User {ctx.author.id} does not have permissions to execute leave command in guild {ctx.guild.id}")
            await ctx.respond("❌ У вас нет прав для выполнения этой команды.", delete_after=15, ephemeral=True)
        elif await self.voice_check(ctx):
            await self.db.update(ctx.guild.id, {'previous_tracks': [], 'next_tracks': []})
            await ctx.respond("Очередь и история сброшены.", delete_after=15, ephemeral=True)
            logging.info(f"[VOICE] Queue and history cleared in guild {ctx.guild.id}")

    @queue.command(description="Получить очередь треков.")
    async def get(self, ctx: discord.ApplicationContext) -> None:
        logging.info(f"[VOICE] Get queue command invoked by user {ctx.author.id} in guild {ctx.guild.id}")

        if not await self.voice_check(ctx):
            return
        await self.users_db.update(ctx.user.id, {'queue_page': 0})

        tracks = await self.db.get_tracks_list(ctx.guild.id, 'next')
        embed = generate_queue_embed(0, tracks)
        await ctx.respond(embed=embed, view=await QueueView(ctx).init(), ephemeral=True)

        logging.info(f"[VOICE] Queue embed sent to user {ctx.author.id} in guild {ctx.guild.id}")

    @track.command(description="Приостановить текущий трек.")
    async def pause(self, ctx: discord.ApplicationContext) -> None:
        logging.info(f"[VOICE] Pause command invoked by user {ctx.author.id} in guild {ctx.guild.id}")

        member = cast(discord.Member, ctx.author)
        channel = cast(discord.VoiceChannel, ctx.channel)

        if len(channel.members) > 2 and not member.guild_permissions.manage_channels:
            logging.info(f"[VOICE] User {ctx.author.id} does not have permissions to pause the track in guild {ctx.guild.id}")
            await ctx.respond("❌ Вы не можете остановить воспроизведение, пока в канале находятся другие пользователи.", delete_after=15, ephemeral=True)

        elif await self.voice_check(ctx) and (vc := await self.get_voice_client(ctx)) is not None:
            if not vc.is_paused():
                vc.pause()

                menu = await self.db.get_current_menu(ctx.guild.id)
                if menu:
                    await self.update_menu_full(ctx, menu)

                logging.info(f"[VOICE] Track paused in guild {ctx.guild.id}")
                await ctx.respond("Воспроизведение приостановлено.", delete_after=15, ephemeral=True)
            else:
                logging.info(f"[VOICE] Track already paused in guild {ctx.guild.id}")
                await ctx.respond("Воспроизведение уже приостановлено.", delete_after=15, ephemeral=True)

    @track.command(description="Возобновить текущий трек.")
    async def resume(self, ctx: discord.ApplicationContext) -> None:
        logging.info(f"[VOICE] Resume command invoked by user {ctx.author.id} in guild {ctx.guild.id}")

        member = cast(discord.Member, ctx.author)
        channel = cast(discord.VoiceChannel, ctx.channel)

        if len(channel.members) > 2 and not member.guild_permissions.manage_channels:
            logging.info(f"[VOICE] User {ctx.author.id} does not have permissions to resume the track in guild {ctx.guild.id}")
            await ctx.respond("❌ Вы не можете остановить воспроизведение, пока в канале находятся другие пользователи.", delete_after=15, ephemeral=True)

        elif await self.voice_check(ctx) and (vc := await self.get_voice_client(ctx)):
            if vc.is_paused():
                vc.resume()
                menu = await self.db.get_current_menu(ctx.guild.id)
                if menu:
                    await self.update_menu_full(ctx, menu)
                logging.info(f"[VOICE] Track resumed in guild {ctx.guild.id}")
                await ctx.respond("Воспроизведение восстановлено.", delete_after=15, ephemeral=True)
            else:
                logging.info(f"[VOICE] Track is not paused in guild {ctx.guild.id}")
                await ctx.respond("Воспроизведение не на паузе.", delete_after=15, ephemeral=True)

    @track.command(description="Прервать проигрывание, удалить историю, очередь и текущий плеер.")
    async def stop(self, ctx: discord.ApplicationContext) -> None:
        logging.info(f"[VOICE] Stop command invoked by user {ctx.author.id} in guild {ctx.guild.id}")

        member = cast(discord.Member, ctx.author)
        channel = cast(discord.VoiceChannel, ctx.channel)

        if len(channel.members) > 2 and not member.guild_permissions.manage_channels:
            logging.info(f"[VOICE] User {ctx.author.id} tried to stop playback in guild {ctx.guild.id} but there are other users in the channel")
            await ctx.respond("❌ Вы не можете остановить воспроизведение, пока в канале находятся другие пользователи.", delete_after=15, ephemeral=True)

        elif await self.voice_check(ctx):
            res = await self.stop_playing(ctx, full=True)
            if res:
                await ctx.respond("Воспроизведение остановлено.", delete_after=15, ephemeral=True)
            else:
                await ctx.respond("❌ Произошла ошибка при остановке воспроизведения.", delete_after=15, ephemeral=True)

    @track.command(description="Переключиться на следующую песню в очереди.")
    async def next(self, ctx: discord.ApplicationContext) -> None:
        logging.info(f"[VOICE] Next command invoked by user {ctx.author.id} in guild {ctx.guild.id}")
        if not await self.voice_check(ctx):
            return

        gid = ctx.guild.id
        guild = await self.db.get_guild(gid, projection={'next_tracks': 1, 'vote_next_track': 1})
        if not guild['next_tracks']:
            logging.info(f"[VOICE] No tracks in queue in guild {ctx.guild.id}")
            await ctx.respond("❌ Нет песенен в очереди.", delete_after=15, ephemeral=True)
            return

        member = cast(discord.Member, ctx.author)
        channel = cast(discord.VoiceChannel, ctx.channel)

        if guild['vote_next_track'] and len(channel.members) > 2 and not member.guild_permissions.manage_channels:
            logging.info(f"[VOICE] User {ctx.author.id} started vote to skip track in guild {ctx.guild.id}")

            message = cast(discord.Interaction, await ctx.respond(f"{member.mention} хочет пропустить текущий трек.\n\nВыполнить переход?", delete_after=30))
            response = await message.original_response()

            await response.add_reaction('✅')
            await response.add_reaction('❌')

            await self.db.update_vote(
                gid,
                response.id,
                {
                    'positive_votes': list(),
                    'negative_votes': list(),
                    'total_members': len(channel.members),
                    'action': 'next',
                    'vote_content': None
                }
            )
        else:
            logging.info(f"[VOICE] Skipping vote for user {ctx.author.id} in guild {ctx.guild.id}")

            await self.db.update(gid, {'is_stopped': False})
            title = await self.next_track(ctx)
            await ctx.respond(f"Сейчас играет: **{title}**!", delete_after=15)

    @track.command(description="Добавить трек в избранное или убрать, если он уже там.")
    async def like(self, ctx: discord.ApplicationContext) -> None:
        logging.info(f"[VOICE] Like command invoked by user {ctx.author.id} in guild {ctx.guild.id}")

        if not await self.voice_check(ctx):
            return

        vc = await self.get_voice_client(ctx)
        if not vc or not vc.is_playing:
            logging.info(f"[VOICE] No current track in {ctx.guild.id}")
            await ctx.respond("Нет воспроизводимого трека.", delete_after=15, ephemeral=True)
            return

        result = await self.react_track(ctx, 'like')
        if not result[0]:
            logging.warning(f"Like command failed for user {ctx.author.id} in guild {ctx.guild.id}")
            await ctx.respond("❌ Операция не удалась.", delete_after=15, ephemeral=True)
        elif result[1] == 'removed':
            logging.info(f"[VOICE] Track removed from favorites for user {ctx.author.id} in guild {ctx.guild.id}")
            await ctx.respond("Трек был удалён из избранного.", delete_after=15, ephemeral=True)
        elif result[1] == 'added':
            logging.info(f"[VOICE] Track added to favorites for user {ctx.author.id} in guild {ctx.guild.id}")
            await ctx.respond(f"Трек **{result}** был добавлен в избранное.", delete_after=15, ephemeral=True)
        else:
            raise ValueError(f"Unknown like command result: '{result}'")
    
    @track.command(name='vibe', description="Запустить Мою Волну по текущему треку.")
    async def track_vibe(self, ctx: discord.ApplicationContext) -> None:
        logging.info(f"[VOICE] Vibe (track) command invoked by user {ctx.author.id} in guild {ctx.guild.id}")
        if not await self.voice_check(ctx):
            return

        guild = await self.db.get_guild(ctx.guild.id, projection={'always_allow_menu': 1, 'current_track': 1, 'current_menu': 1, 'vibing': 1})
        channel = cast(discord.VoiceChannel, ctx.channel)

        if len(channel.members) > 2 and not guild['always_allow_menu']:
            logging.info(f"[VOICE] Action declined: other members are present in the voice channel")
            await ctx.respond("❌ Вы не единственный в голосовом канале.", ephemeral=True)
            return

        if guild['vibing']:
            logging.info(f"[VOICE] Action declined: vibing is already enabled in guild {ctx.guild.id}")
            await ctx.respond("❌ Моя Волна уже включена. Используйте /track stop, чтобы остановить воспроизведение.", ephemeral=True)
            return

        if not guild['current_track']:
            logging.info(f"[VOICE] No current track in {ctx.guild.id}")
            await ctx.respond("❌ Нет воспроизводимого трека.", ephemeral=True)
            return

        feedback = await self.update_vibe(ctx, 'track', guild['current_track']['id'])
        if not feedback:
            await ctx.respond("❌ Операция не удалась. Возможно, у вес нет подписки на Яндекс Музыку.", ephemeral=True)
            return

        if not guild['current_menu']:
            await self.send_menu_message(ctx, disable=True)

        next_track = await self.db.get_track(ctx.guild_id, 'next')
        if next_track:
            await self._play_next_track(ctx, next_track)

    @voice.command(name='vibe', description="Запустить Мою Волну.")
    @discord.option(
        "запрос",
        parameter_name='name',
        description="Название станции.",
        type=discord.SlashCommandOptionType.string,
        autocomplete=discord.utils.basic_autocomplete(get_vibe_stations_suggestions),
        required=False
    )
    async def user_vibe(self, ctx: discord.ApplicationContext, name: str | None = None) -> None:
        logging.info(f"[VOICE] Vibe (user) command invoked by user {ctx.user.id} in guild {ctx.guild_id}")
        if not await self.voice_check(ctx):
            return

        guild = await self.db.get_guild(ctx.guild.id, projection={'always_allow_menu': 1, 'current_menu': 1, 'vibing': 1})
        channel = cast(discord.VoiceChannel, ctx.channel)

        if len(channel.members) > 2 and not guild['always_allow_menu']:
            logging.info(f"[VOICE] Action declined: other members are present in the voice channel")
            await ctx.respond("❌ Вы не единственный в голосовом канале.", ephemeral=True)
            return
        if guild['vibing']:
            logging.info(f"[VOICE] Action declined: vibing is already enabled in guild {ctx.guild.id}")
            await ctx.respond("❌ Моя Волна уже включена. Используйте /track stop, чтобы остановить воспроизведение.", ephemeral=True)
            return

        if name:
            token = await users_db.get_ym_token(ctx.user.id)
            if not token:
                logging.info(f"[GENERAL] User {ctx.user.id} has no token")
                return

            try:
                client = await YMClient(token).init()
            except UnauthorizedError:
                logging.info(f"[GENERAL] User {ctx.user.id} provided invalid token")
                return

            stations = await client.rotor_stations_list()
            for content in stations:
                if content.station and content.station.name == name and content.ad_params:
                    break
            else:
                content = None

            if not content:
                logging.debug(f"[VOICE] Station {name} not found")
                await ctx.respond("❌ Станция не найдена.", ephemeral=True)
                return

            _type, _id = content.ad_params.other_params.split(':') if content.ad_params else (None, None)

            if not _type or not _id:
                logging.debug(f"[VOICE] Station {name} has no ad params")
                await ctx.respond("❌ Станция не найдена.", ephemeral=True)
                return

            feedback = await self.update_vibe(ctx, _type, _id)
        else:
            feedback = await self.update_vibe(ctx, 'user', 'onyourwave')

        if not feedback:
            await ctx.respond("❌ Операция не удалась. Возможно, у вес нет подписки на Яндекс Музыку.", ephemeral=True)
            return

        if not guild['current_menu']:
            await self.send_menu_message(ctx, disable=True)

        next_track = await self.db.get_track(ctx.guild_id, 'next')
        if next_track:
            await self._play_next_track(ctx, next_track)
