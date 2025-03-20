import logging
from typing import cast

import discord
from discord.ext.commands import Cog

from yandex_music import ClientAsync as YMClient
from yandex_music.exceptions import UnauthorizedError

from MusicBot.cogs.utils import VoiceExtension
from MusicBot.database import BaseUsersDatabase
from MusicBot.ui import QueueView, generate_queue_embed

def setup(bot: discord.Bot):
    bot.add_cog(Voice(bot))

users_db = BaseUsersDatabase()

async def get_vibe_stations_suggestions(ctx: discord.AutocompleteContext) -> list[str]:
    if not ctx.interaction.user or not ctx.value or len(ctx.value) < 2:
        return []

    if not (token := await users_db.get_ym_token(ctx.interaction.user.id)):
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

    def __init__(self, bot: discord.Bot):
        VoiceExtension.__init__(self, bot)
        self.typed_bot: discord.Bot = bot

    @Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState) -> None:
        guild = await self.db.get_guild(member.guild.id, projection={'current_menu': 1})

        if not after.channel or not before.channel:
            logging.debug(f"[VOICE] No channel found for member {member.id}")
            return

        vc = cast(
            discord.VoiceClient | None,
            discord.utils.get(
                self.typed_bot.voice_clients,
                guild=await self.typed_bot.fetch_guild(member.guild.id)
            )
        )

        if not vc:
            logging.info(f"[VOICE] No voice client found for guild {member.guild.id}")
            return

        for member in set(before.channel.members + after.channel.members):
            if member.id == self.typed_bot.user.id:  # type: ignore  # should be logged in
                logging.info(f"[VOICE] Voice state update for member {member.id} in guild {member.guild.id}")
                break
        else:
            logging.debug(f"[VOICE] Bot is not in the channel {after.channel.id}")
            return

        if len(after.channel.members) == 1:
            logging.info(f"[VOICE] Clearing history and stopping playback for guild {member.guild.id}")

            if member.guild.id in self.menu_views:
                self.menu_views[member.guild.id].stop()
                del self.menu_views[member.guild.id]

            if guild['current_menu']:
                if (message := self.typed_bot.get_message(guild['current_menu'])):
                    await message.delete()

            await self.db.update(member.guild.id, {
                'previous_tracks': [], 'next_tracks': [], 'votes': {},
                'current_track': None, 'current_menu': None, 'vibing': False,
                'repeat': False, 'shuffle': False, 'is_stopped': True
            })
            vc.stop()

            if member.guild.id in self.menu_views:
                self.menu_views[member.guild.id].stop()
                del self.menu_views[member.guild.id]

    @Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        logging.debug(f"[VOICE] Reaction added by user {payload.user_id} in channel {payload.channel_id}")

        if not self.typed_bot.user or not payload.member:
            return

        if not payload.guild_id:
            logging.info(f"[VOICE] No guild id in reaction payload")
            return

        if payload.user_id == self.typed_bot.user.id:
            return

        channel = self.typed_bot.get_channel(payload.channel_id)
        if not isinstance(channel, discord.VoiceChannel):
            logging.info(f"[VOICE] Channel {payload.channel_id} is not a voice channel")
            return

        try:
            message = await channel.fetch_message(payload.message_id)
        except discord.Forbidden:
            logging.info(f"[VOICE] Bot does not have permissions to read messages in channel {payload.channel_id}")
            return
        except discord.NotFound:
            logging.info(f"[VOICE] Message {payload.message_id} not found in channel {payload.channel_id}")
            return

        if not message or message.author.id != self.typed_bot.user.id:
            logging.info(f"[VOICE] Message {payload.message_id} is not a bot message")
            return

        guild = await self.db.get_guild(payload.guild_id)

        if not guild['use_single_token'] and not (guild['single_token_uid'] or await self.users_db.get_ym_token(payload.user_id)):
            await message.remove_reaction(payload.emoji, payload.member)
            await channel.send("❌ Для участия в голосовании необходимо авторизоваться через /account login.", delete_after=15)
            return

        votes = guild['votes']

        if str(payload.message_id) not in votes:
            logging.info(f"[VOICE] Message {payload.message_id} not found in votes")
            return

        vote_data = votes[str(payload.message_id)]

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
            await message.delete()
            await self.proccess_vote(payload, guild, vote_data)
            del votes[str(payload.message_id)]

        elif len(vote_data['negative_votes']) >= required_votes:
            logging.info(f"[VOICE] Enough negative votes for message {payload.message_id}")
            await message.clear_reactions()
            await message.edit(content='Запрос был отклонён.', delete_after=15)
            del votes[str(payload.message_id)]

        await self.db.update(payload.guild_id, {'votes': votes})

    @Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent) -> None:
        logging.debug(f"[VOICE] Reaction removed by user {payload.user_id} in channel {payload.channel_id}")

        if not self.typed_bot.user or not payload.member:
            return

        if not payload.guild_id:
            return

        channel = self.typed_bot.get_channel(payload.channel_id)
        if not isinstance(channel, discord.VoiceChannel):
            logging.info(f"[VOICE] Channel {payload.channel_id} is not a voice channel")
            return

        guild = await self.db.get_guild(payload.guild_id, projection={'votes': 1})
        votes = guild['votes']

        if str(payload.message_id) not in votes:
            logging.info(f"[VOICE] Message {payload.message_id} not found in votes")
            return

        try:
            message = await channel.fetch_message(payload.message_id)
        except discord.Forbidden:
            logging.info(f"[VOICE] Bot does not have permissions to read messages in channel {payload.channel_id}")
            return
        except discord.NotFound:
            logging.info(f"[VOICE] Message {payload.message_id} not found in channel {payload.channel_id}")
            return

        if not message or message.author.id != self.typed_bot.user.id:
            return

        vote_data = votes[str(payload.message_id)]
        if payload.emoji.name == '✔️':
            logging.info(f"[VOICE] User {payload.user_id} removed positive vote for message {payload.message_id}")
            del vote_data['positive_votes'][payload.user_id]
        elif payload.emoji.name == '❌':
            logging.info(f"[VOICE] User {payload.user_id} removed negative vote for message {payload.message_id}")
            del vote_data['negative_votes'][payload.user_id]

        await self.db.update(payload.guild_id, {'votes': votes})

    @voice.command(name="menu", description="Создать или обновить меню проигрывателя.")
    async def menu(self, ctx: discord.ApplicationContext) -> None:
        logging.info(f"[VOICE] Menu command invoked by user {ctx.author.id} in guild {ctx.guild_id}")
        if await self.voice_check(ctx) and not await self.send_menu_message(ctx):
            await self.respond(ctx, "error", "Не удалось создать меню.", ephemeral=True)

    @voice.command(name="join", description="Подключиться к голосовому каналу, в котором вы сейчас находитесь.")
    async def join(self, ctx: discord.ApplicationContext) -> None:
        logging.info(f"[VOICE] Join command invoked by user {ctx.author.id} in guild {ctx.guild_id}")
        
        if not ctx.guild_id:
            logging.warning("[VOICE] Join command invoked without guild_id")
            await self.respond(ctx, "error", "Эта команда может быть использована только на сервере.", ephemeral=True)
            return
        
        if ctx.author.id not in ctx.channel.voice_states:
            logging.debug("[VC_EXT] User is not connected to the voice channel")
            await self.respond(ctx, "error", "Вы должны находиться в голосовом канале.", delete_after=15, ephemeral=True)
            return

        member = cast(discord.Member, ctx.author)
        guild = await self.db.get_guild(ctx.guild_id, projection={'allow_change_connect': 1, 'use_single_token': 1})

        if guild['use_single_token'] and not await self.users_db.get_ym_token(ctx.author.id):
            await self.respond(
                ctx, "error",
                "У вас нет токена Яндекс Музыки. Используйте команду /account login для установки токена, " \
                "попросите участника с токеном запустить бота или отключите использование общего токена в настройках сервера.",
                delete_after=15, ephemeral=True 
            )
            return
        
        await ctx.defer(ephemeral=True)

        if not member.guild_permissions.manage_channels and not guild['allow_change_connect']:
            response_message = ("error", "У вас нет прав для выполнения этой команды.")
        elif isinstance(ctx.channel, discord.VoiceChannel):
            try:
                await ctx.channel.connect()
            except TimeoutError:
                response_message = ("error", "Не удалось подключиться к голосовому каналу.")
            except discord.ClientException:
                response_message = ("error", "Бот уже находится в голосовом канале.\nВыключите его с помощью команды /voice leave.")
            except discord.DiscordException as e:
                logging.error(f"[VOICE] DiscordException: {e}")
                response_message = ("error", "Произошла неизвестная ошибка при подключении к голосовому каналу.")
            else:
                response_message = ("success", "Подключение успешно!")

                if guild['use_single_token']:
                    response_message = ("success", "Подключение успешно! Ваш токен будет использован для всех операций с музыкой на этом сервере.")
                    await self.db.update(ctx.guild_id, {'single_token_uid': ctx.author.id})
        else:
            response_message = ("error", "Вы должны отправить команду в чате голосового канала.")

        logging.info(f"[VOICE] Join command response: {response_message}")
        await self.respond(ctx, *response_message, delete_after=15, ephemeral=True)

    @voice.command(description="Заставить бота покинуть голосовой канал.")
    async def leave(self, ctx: discord.ApplicationContext) -> None:
        logging.info(f"[VOICE] Leave command invoked by user {ctx.author.id} in guild {ctx.guild_id}")

        if not ctx.guild_id:
            logging.info("[VOICE] Leave command invoked without guild_id")
            await self.respond(ctx, "error", "Эта команда может быть использована только на сервере.", ephemeral=True)
            return

        member = cast(discord.Member, ctx.author)
        guild = await self.db.get_guild(ctx.guild_id, projection={'allow_change_connect': 1})

        if not member.guild_permissions.manage_channels and not guild['allow_change_connect']:
            logging.info(f"[VOICE] User {ctx.author.id} does not have permissions to execute leave command in guild {ctx.guild_id}")
            await self.respond(ctx, "error", "У вас нет прав для выполнения этой команды.", delete_after=15, ephemeral=True)
            return
        
        if not await self.voice_check(ctx):
            return

        if not (vc := await self.get_voice_client(ctx)) or not vc.is_connected:
            logging.info(f"[VOICE] Voice client is not connected in guild {ctx.guild_id}")
            await self.respond(ctx, "error", "Бот не подключен к голосовому каналу.", delete_after=15, ephemeral=True)
            return

        if not await self.stop_playing(ctx, vc=vc, full=True):
            await self.respond(ctx, "error", "Не удалось отключиться.", delete_after=15, ephemeral=True)
            return

        await vc.disconnect(force=True)
        logging.info(f"[VOICE] Successfully disconnected from voice channel in guild {ctx.guild_id}")

        await self.db.update(ctx.guild_id, {'single_token_uid': None})
        await self.respond(ctx, "success", "Отключение успешно!", delete_after=15, ephemeral=True)

    @queue.command(description="Очистить очередь треков и историю прослушивания.")
    async def clear(self, ctx: discord.ApplicationContext) -> None:
        logging.info(f"[VOICE] Clear queue command invoked by user {ctx.author.id} in guild {ctx.guild_id}")

        if not await self.voice_check(ctx):
            return

        member = cast(discord.Member, ctx.author)
        channel = cast(discord.VoiceChannel, ctx.channel)

        if len(channel.members) > 2 and not member.guild_permissions.manage_channels:
            logging.info(f"Starting vote for clearing queue in guild {ctx.guild_id}")

            response_message = f"{member.mention} хочет очистить историю прослушивания и очередь треков.\n\n Выполнить действие?."
            message = cast(discord.Interaction, await self.respond(ctx, "info", response_message, delete_after=60))
            response = await message.original_response()

            await response.add_reaction('✅')
            await response.add_reaction('❌')

            await self.db.update_vote(
                ctx.guild_id,
                response.id,
                {
                    'positive_votes': list(),
                    'negative_votes': list(),
                    'total_members': len(channel.members),
                    'action': 'clear_queue',
                    'vote_content': None
                }
            )
            return

        await self.db.update(ctx.guild_id, {'previous_tracks': [], 'next_tracks': []})
        await self.respond(ctx, "success", "Очередь и история сброшены.", delete_after=15, ephemeral=True)
        logging.info(f"[VOICE] Queue and history cleared in guild {ctx.guild_id}")

    @queue.command(description="Получить очередь треков.")
    async def get(self, ctx: discord.ApplicationContext) -> None:
        logging.info(f"[VOICE] Get queue command invoked by user {ctx.author.id} in guild {ctx.guild_id}")

        if not await self.voice_check(ctx):
            return

        tracks = await self.db.get_tracks_list(ctx.guild_id, 'next')
        if len(tracks) == 0:
            await self.respond(ctx, "error", "Очередь прослушивания пуста.", delete_after=15, ephemeral=True)
            return

        await ctx.respond(embed=generate_queue_embed(0, tracks), view=QueueView(ctx, tracks), ephemeral=True)

        logging.info(f"[VOICE] Queue embed sent to user {ctx.author.id} in guild {ctx.guild_id}")

    @voice.command(description="Прервать проигрывание, удалить историю, очередь и текущий плеер.")
    async def stop(self, ctx: discord.ApplicationContext) -> None:
        logging.info(f"[VOICE] Stop command invoked by user {ctx.author.id} in guild {ctx.guild_id}")

        if not await self.voice_check(ctx):
            return

        member = cast(discord.Member, ctx.author)
        channel = cast(discord.VoiceChannel, ctx.channel)

        if len(channel.members) > 2 and not member.guild_permissions.manage_channels:
            logging.info(f"Starting vote for stopping playback in guild {ctx.guild_id}")

            response_message = f"{member.mention} хочет полностью остановить проигрывание.\n\n Выполнить действие?."
            message = cast(discord.Interaction, await self.respond(ctx, "info", response_message, delete_after=60))
            response = await message.original_response()

            await response.add_reaction('✅')
            await response.add_reaction('❌')

            await self.db.update_vote(
                ctx.guild_id,
                response.id,
                {
                    'positive_votes': list(),
                    'negative_votes': list(),
                    'total_members': len(channel.members),
                    'action': 'stop',
                    'vote_content': None
                }
            )
            return

        await ctx.defer(ephemeral=True)
        res = await self.stop_playing(ctx, full=True)
        if res:
            await self.respond(ctx, "success", "Воспроизведение остановлено.", delete_after=15, ephemeral=True)
        else:
            await self.respond(ctx, "error", "Произошла ошибка при остановке воспроизведения.", delete_after=15, ephemeral=True)

    @voice.command(description="Запустить Мою Волну.")
    @discord.option(
        "запрос",
        parameter_name='name',
        description="Название станции.",
        type=discord.SlashCommandOptionType.string,
        autocomplete=discord.utils.basic_autocomplete(get_vibe_stations_suggestions),
        required=False
    )
    async def vibe(self, ctx: discord.ApplicationContext, name: str | None = None) -> None:
        logging.info(f"[VOICE] Vibe (user) command invoked by user {ctx.user.id} in guild {ctx.guild_id}")

        if not await self.voice_check(ctx):
            return

        guild = await self.db.get_guild(ctx.guild_id, projection={'current_menu': 1, 'vibing': 1})

        if guild['vibing']:
            logging.info(f"[VOICE] Action declined: vibing is already enabled in guild {ctx.guild_id}")
            await self.respond(ctx, "error", "Моя Волна уже включена. Используйте /voice stop, чтобы остановить воспроизведение.", delete_after=15, ephemeral=True)
            return

        await ctx.defer(invisible=False)
        if name:

            if not (client := await self.init_ym_client(ctx)):
                return

            for content in (await client.rotor_stations_list()):
                if content.station and content.station.name == name and content.ad_params:
                    break
            else:
                content = None

            if not content:
                logging.debug(f"[VOICE] Station {name} not found")
                await self.respond(ctx, "error", "Станция не найдена.", delete_after=15, ephemeral=True)
                return

            vibe_type, vibe_id = content.ad_params.other_params.split(':') if content.ad_params else (None, None)

            if not vibe_type or not vibe_id:
                logging.debug(f"[VOICE] Station {name} has no ad params")
                await self.respond(ctx, "error", "Станция не найдена.", delete_after=15, ephemeral=True)
                return
        else:
            vibe_type, vibe_id = 'user', 'onyourwave'
            content = None

        member = cast(discord.Member, ctx.author)
        channel = cast(discord.VoiceChannel, ctx.channel)
        
        await self.users_db.reset_vibe_settings(ctx.user.id)

        if len(channel.members) > 2 and not member.guild_permissions.manage_channels:
            logging.info(f"Starting vote for starting vibe in guild {ctx.guild_id}")

            if vibe_type == 'user' and vibe_id == 'onyourwave':
                station = "Моя Волна"
            elif content and content.station:
                station = content.station.name
            else:
                logging.warning(f"[VOICE] Station {name} not found")
                await self.respond(ctx, "error", "Станция не найдена.", delete_after=15, ephemeral=True)
                return

            response_message = f"{member.mention} хочет запустить станцию **{station}**.\n\n Выполнить действие?"
            message = cast(discord.WebhookMessage, await self.respond(ctx, "info", response_message, delete_after=60))

            await message.add_reaction('✅')
            await message.add_reaction('❌')

            await self.db.update_vote(
                ctx.guild_id,
                message.id,
                {
                    'positive_votes': list(),
                    'negative_votes': list(),
                    'total_members': len(channel.members),
                    'action': 'vibe_station',
                    'vote_content': [vibe_type, vibe_id, ctx.user.id]
                }
            )
            return

        if not await self.update_vibe(ctx, vibe_type, vibe_id):
            await self.respond(ctx, "error", "Операция не удалась. Возможно, у вес нет подписки на Яндекс Музыку.", delete_after=15, ephemeral=True)
            return

        if guild['current_menu']:
            await self.respond(ctx, "success", "Моя Волна включена.", delete_after=15, ephemeral=True)
        elif not await self.send_menu_message(ctx, disable=True):
            await self.respond(ctx, "error", "Не удалось отправить меню. Попробуйте позже.", delete_after=15, ephemeral=True)

        if (next_track := await self.db.get_track(ctx.guild_id, 'next')):
            await self.play_track(ctx, next_track)
