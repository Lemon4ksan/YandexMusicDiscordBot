import logging
from time import monotonic
from typing import Self, Literal, cast

from discord.ui import View, Button, Item, Select
from discord import (
    Interaction, ApplicationContext, RawReactionActionEvent,
    VoiceChannel, ButtonStyle, Embed, ComponentType, SelectOption, Member, HTTPException
)

import yandex_music.exceptions
from yandex_music import TrackLyrics, Playlist, ClientAsync as YMClient

from MusicBot.cogs.utils import VoiceExtension

class ToggleButton(Button, VoiceExtension):
    def __init__(self, root: 'MenuView', *args, **kwargs):
        super().__init__(*args, **kwargs)
        VoiceExtension.__init__(self, None)
        self.root = root
    
    async def callback(self, interaction: Interaction) -> None:

        if (callback_type := interaction.custom_id) not in ('repeat', 'shuffle'):
            raise ValueError(f"Invalid callback type: '{callback_type}'")
        
        logging.info(f'[MENU] {callback_type.capitalize()} button callback')
        
        if not (gid := interaction.guild_id) or not interaction.user:
            logging.warning('[MENU] Failed to get guild ID.')
            await self.respond(interaction, "error", "Что-то пошло не так. Попробуйте снова.", delete_after=15, ephemeral=True)
            return
        
        if not await self.voice_check(interaction):
            return

        guild = await self.db.get_guild(gid)
        member = cast(Member, interaction.user)
        channel = cast(VoiceChannel, interaction.channel)

        if len(channel.members) > 2 and not member.guild_permissions.manage_channels:
            logging.info(f"[MENU] User {interaction.user.id} started vote to pause/resume track in guild {gid}")
            
            action = "выключить" if guild[callback_type] else "включить"
            task = "перемешивание треков" if callback_type == 'shuffle' else "повтор трека"
            message = cast(Interaction, await self.respond(interaction, "info", f"{member.mention} хочет {action} {task}.\n\nВыполнить действие?", delete_after=60))
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
                    'action': callback_type,
                    'vote_content': None
                }
            )
            return
        
        await self.db.update(gid, {callback_type: not guild[callback_type]})

        button = self.root.repeat_button if callback_type == 'repeat' else self.root.shuffle_button
        button.style = ButtonStyle.secondary if guild[callback_type] else ButtonStyle.success

        await interaction.edit(view=await self.root.update())

class PlayPauseButton(Button, VoiceExtension):
    def __init__(self, **kwargs):
        Button.__init__(self, **kwargs)
        VoiceExtension.__init__(self, None)
    
    async def callback(self, interaction: Interaction) -> None:
        logging.info('[MENU] Play/Pause button callback...')

        if not await self.voice_check(interaction):
            return

        if not (gid := interaction.guild_id) or not interaction.user:
            logging.warning('[MENU] Failed to get guild ID or user.')
            return
        
        if not (vc := await self.get_voice_client(interaction)) or not interaction.message:
            return

        member = cast(Member, interaction.user)
        channel = cast(VoiceChannel, interaction.channel)

        if len(channel.members) > 2 and not member.guild_permissions.manage_channels:
            logging.info(f"[MENU] User {interaction.user.id} started vote to pause/resume track in guild {gid}")
            
            task = "приостановить" if vc.is_playing() else "возобновить"
            message = cast(Interaction, await self.respond(interaction, "info", f"{member.mention} хочет {task} проигрывание.\n\nВыполнить действие?", delete_after=60))
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
                    'action': "play/pause",
                    'vote_content': None
                }
            )
            return
        
        if vc.is_paused():
            vc.resume()
        else:
            vc.pause()

        try:
            embed = interaction.message.embeds[0]
        except IndexError:
            await self.respond(interaction, "error", "Нет воспроизводимого трека.", delete_after=15, ephemeral=True)
            return

        guild = await self.db.get_guild(interaction.guild_id, projection={'single_token_uid': 1})
    
        if not vc.is_paused() and guild['single_token_uid']:
            user = await self.get_discord_user_by_id(interaction, guild['single_token_uid'])

            if guild['single_token_uid'] and user:
                embed.set_footer(text=f"Используется токен {user.display_name}", icon_url=user.display_avatar.url)
            else:
                embed.set_footer(text='Используется токен (неизвестный пользователь)')

        elif vc.is_paused():
            embed.set_footer(text='Приостановлено')
        else:
            embed.remove_footer()

        await interaction.edit(embed=embed)

class SwitchTrackButton(Button, VoiceExtension):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        VoiceExtension.__init__(self, None)
    
    async def callback(self, interaction: Interaction) -> None:

        if (callback_type := interaction.custom_id) not in ('next', 'previous'):
            raise ValueError(f"Invalid callback type: '{callback_type}'")

        if not (gid := interaction.guild_id) or not interaction.user:
            logging.warning(f"[MENU] {callback_type.capitalize()} track button callback without guild id or user")
            return

        logging.info(f'[MENU] {callback_type.capitalize()} track button callback')

        if not await self.voice_check(interaction):
            return

        tracks_type = callback_type + '_tracks'
        guild = await self.db.get_guild(gid, projection={tracks_type: 1, 'vote_switch_track': 1, 'vibing': 1})

        if not guild[tracks_type] and not guild['vibing']:
            logging.info(f"[MENU] No tracks in '{tracks_type}' list in guild {gid}")
            await self.respond(interaction, "error", f"Нет треков в {'очереди' if callback_type == 'next' else 'истории'}.", delete_after=15, ephemeral=True)
            return

        member = cast(Member, interaction.user)
        channel = cast(VoiceChannel, interaction.channel)

        if guild['vote_switch_track'] and len(channel.members) > 2 and not member.guild_permissions.manage_channels:
            logging.info(f"[MENU] User {interaction.user.id} started vote to skip track in guild {gid}")

            task = "пропустить текущий трек" if callback_type == 'next' else "вернуться к предыдущему треку"
            message = cast(Interaction, await self.respond(interaction, "info", f"{member.mention} хочет {task}.\n\nВыполнить переход?", delete_after=60))
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
                    'action': callback_type,
                    'vote_content': None
                }
            )
            return

        if callback_type == 'next':
            title = await self.play_next_track(interaction, button_callback=True)
        else:
            title = await self.play_previous_track(interaction, button_callback=True)

        if not title:
            await self.respond(interaction, "error", "Что-то пошло не так. Попробуйте позже.", delete_after=15, ephemeral=True)

class ReactionButton(Button, VoiceExtension):
    def __init__(self, root: 'MenuView', *args, **kwargs):
        super().__init__(*args, **kwargs)
        VoiceExtension.__init__(self, None)
        self.root = root
    
    async def callback(self, interaction: Interaction):
        callback_type = interaction.custom_id
        if callback_type not in ('like', 'dislike'):
            raise ValueError(f"Invalid callback type: '{callback_type}'")

        logging.info(f'[MENU] {callback_type.capitalize()} button callback')

        if not await self.voice_check(interaction) or not (gid := interaction.guild_id):
            return

        if not (vc := await self.get_voice_client(interaction)) or not vc.is_playing:
            await self.respond(interaction, "error", "Нет воспроизводимого трека.", delete_after=15, ephemeral=True)

        channel = cast(VoiceChannel, interaction.channel)
        res = await self.react_track(interaction, callback_type)

        if callback_type == 'like' and res[0]:
            button = self.root.like_button
            response_message = f"Трек был {'добавлен в понравившиеся.' if res[1] == 'added' else 'удалён из понравившихся.'}"

        elif callback_type == 'dislike' and res[0]:

            if len(channel.members) == 2:
                await self.play_next_track(interaction, vc=vc, button_callback=True)
                return

            button = self.root.dislike_button
            response_message =f"Трек был {'добавлен в дизлайки.' if res[1] == 'added' else 'удалён из дизлайков.'}"

        else:
            logging.debug(f"[VC_EXT] Failed to get {callback_type} tracks")
            await self.respond(interaction, "error", "Операция не удалась. Попробуйте позже.", delete_after=15, ephemeral=True)
            return

        if len(channel.members) == 2:
            button.style = ButtonStyle.success if res[1] == 'added' else ButtonStyle.secondary
            await interaction.edit(view=await self.root.update())
        else:
            await self.respond(interaction, "success", response_message, delete_after=15, ephemeral=True)
    
    async def react_track(
        self,
        ctx: ApplicationContext | Interaction,
        action: Literal['like', 'dislike']
    ) -> tuple[bool, Literal['added', 'removed'] | None]:
        """Like or dislike current track. Return track title on success.

        Args:
            ctx (ApplicationContext | Interaction): Context.
            action (Literal['like', 'dislike']): Action to perform.

        Returns:
            (tuple[bool, Literal['added', 'removed'] | None]): Tuple with success status and action.
        """
        if not (gid := ctx.guild_id) or not ctx.user:
            logging.warning("[VC_EXT] Guild or User not found")
            return (False, None)

        if not (current_track := await self.db.get_track(gid, 'current')):
            logging.debug("[VC_EXT] Current track not found")
            return (False, None)

        if not (client := await self.init_ym_client(ctx)):
            return (False, None)

        if action == 'like':
            tracks = await client.users_likes_tracks()
            add_func = client.users_likes_tracks_add
            remove_func = client.users_likes_tracks_remove
        else:
            tracks = await client.users_dislikes_tracks()
            add_func = client.users_dislikes_tracks_add
            remove_func = client.users_dislikes_tracks_remove

        if tracks is None:
            logging.debug(f"[VC_EXT] No {action}s found")
            return (False, None)

        if str(current_track['id']) not in [str(track.id) for track in tracks]:
            logging.debug(f"[VC_EXT] Track not found in {action}s. Adding...")
            await add_func(current_track['id'])
            return (True, 'added')
        else:
            logging.debug(f"[VC_EXT] Track found in {action}s. Removing...")
            await remove_func(current_track['id'])
            return (True, 'removed')

class LyricsButton(Button, VoiceExtension):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        VoiceExtension.__init__(self, None)
        
    async def callback(self, interaction: Interaction) -> None:
        logging.info('[MENU] Lyrics button callback...')

        if not await self.voice_check(interaction) or not interaction.guild_id or not interaction.user:
            return
        
        if not (client := await self.init_ym_client(interaction)):
            return

        if not (current_track := await self.db.get_track(interaction.guild_id, 'current')):
            logging.debug('[MENU] No current track found')
            return

        try:
            lyrics = cast(TrackLyrics, await client.tracks_lyrics(current_track['id']))
        except yandex_music.exceptions.NotFoundError:
            logging.debug('[MENU] Lyrics not found')
            await self.respond(interaction, "error", "Текст песни не найден. Яндекс нам соврал (опять)!", delete_after=15, ephemeral=True)
            return

        embed = Embed(
            title=current_track['title'],
            description='**Текст песни**',
            color=0xfed42b,
        )
        text = await lyrics.fetch_lyrics_async()

        for subtext in text.split('\n\n'):
            embed.add_field(name='', value=subtext, inline=False)

        await interaction.respond(embed=embed, ephemeral=True)

class MyVibeButton(Button, VoiceExtension):
    def __init__(self, **kwargs):
        Button.__init__(self, **kwargs)
        VoiceExtension.__init__(self, None)
    
    async def callback(self, interaction: Interaction) -> None:
        logging.info('[MENU] My vibe button callback')

        if not await self.voice_check(interaction):
            return

        if not interaction.guild_id or not interaction.user:
            logging.warning('[MENU] No guild id or user in button callback')
            return
        
        member = cast(Member, interaction.user)
        channel = cast(VoiceChannel, interaction.channel)
        track = await self.db.get_track(interaction.guild_id, 'current')

        if len(channel.members) > 2 and not member.guild_permissions.manage_channels:
            logging.info(f"Starting vote for starting vibe in guild {interaction.guild_id}")

            if track:
                response_message = f"{member.mention} хочет запустить волну по треку **{track['title']}**.\n\n Выполнить действие?"
                vibe_type = 'track'
                vibe_id = track['id']
            else:
                response_message = f"{member.mention} хочет запустить станцию **Моя Волна**.\n\n Выполнить действие?"
                vibe_type = 'user'
                vibe_id = 'onyourwave'

            message = cast(Interaction, await self.respond(interaction, "info", response_message))
            response = await message.original_response()

            await response.add_reaction('✅')
            await response.add_reaction('❌')
            
            await self.db.update_vote(
                interaction.guild_id,
                response.id,
                {
                    'positive_votes': list(),
                    'negative_votes': list(),
                    'total_members': len(channel.members),
                    'action': 'vibe_station',
                    'vote_content': [vibe_type, vibe_id, interaction.user.id]
                }
            )
            return

        if track:
            logging.info(f"[MENU] Playing vibe for track '{track["id"]}'")
            res = await self.update_vibe(
                interaction,
                'track',
                track['id']
            )
        else:
            logging.info('[MENU] Playing station user:onyourwave')
            res = await self.update_vibe(
                interaction,
                'user',
                'onyourwave'
            )

        if not res:
            logging.info('[MENU] Failed to start the vibe')
            await self.respond(interaction, "error", "Не удалось запустить **Мою Волну**. Возможно, у вас нет подписки на Яндекс Музыку.", ephemeral=True)

        if (next_track := await self.db.get_track(interaction.guild_id, 'next')):
            await self.play_track(interaction, next_track, button_callback=True)

class MyVibeSelect(Select, VoiceExtension):
    def __init__(self, *args,  **kwargs):
        super().__init__(*args, **kwargs)
        VoiceExtension.__init__(self, None)
    
    async def callback(self, interaction: Interaction) -> None:
        logging.info('[MENU] My vibe select callback')

        if not await self.voice_check(interaction):
            return

        if not interaction.user:
            logging.warning('[MENU] No user in select callback')
            return

        custom_id = interaction.custom_id
        if custom_id not in ('diversity', 'mood', 'lang'):
            logging.error(f'[MENU] Unknown custom_id: {custom_id}')
            return

        if not interaction.data:
            logging.warning('[MENU] No data in select callback')
            return
        
        data_values = cast(list[str] | None, interaction.data.get('values'))
        if not data_values or data_values[0] not in (
            'fun', 'active', 'calm', 'sad', 'all',
            'favorite', 'popular', 'discover', 'default',
            'not-russian', 'russian', 'without-words', 'any'
        ):
            logging.error(f'[MENU] Unknown data_value: {data_values}')
            return

        logging.info(f"[MENU] Settings option '{custom_id}' updated to '{data_values[0]}'")
        await self.users_db.update(interaction.user.id, {f'vibe_settings.{custom_id}': data_values[0]})
        
        view = await MyVibeSettingsView(interaction).init()
        view.disable_all_items()
        await interaction.edit(view=view)

        await self.update_vibe(interaction, 'user', 'onyourwave', update_settings=True)
        view.enable_all_items()
        await interaction.edit(view=view)

class MyVibeSettingsView(View, VoiceExtension):
    def __init__(self, interaction: Interaction, *items: Item, timeout: float | None = 360, disable_on_timeout: bool = True):
        View.__init__(self, *items, timeout=timeout, disable_on_timeout=disable_on_timeout)
        VoiceExtension.__init__(self, None)
        self.interaction = interaction

    async def init(self) -> Self:
        if not self.interaction.user:
            logging.warning('[MENU] No user in settings view')
            return self

        settings = (await self.users_db.get_user(self.interaction.user.id, projection={'vibe_settings'}))['vibe_settings']

        diversity_settings = settings['diversity']
        diversity = [
            SelectOption(label='Любое', value='default'),
            SelectOption(label='Любимое', value='favorite', default=diversity_settings == 'favorite'),
            SelectOption(label='Незнакомое', value='discover', default=diversity_settings == 'discover'),
            SelectOption(label='Популярное', value='popular', default=diversity_settings == 'popular')
        ]

        mood_settings = settings['mood']
        mood = [
            SelectOption(label='Любое', value='all'),
            SelectOption(label='Бодрое', value='active', default=mood_settings == 'active'),
            SelectOption(label='Весёлое', value='fun', default=mood_settings == 'fun'),
            SelectOption(label='Спокойное', value='calm', default=mood_settings == 'calm'),
            SelectOption(label='Грустное', value='sad', default=mood_settings == 'sad')
        ]

        lang_settings = settings['lang']
        lang = [
            SelectOption(label='Любое', value='any'),
            SelectOption(label='Русский', value='russian', default=lang_settings == 'russian'),
            SelectOption(label='Иностранный', value='not-russian', default=lang_settings == 'not-russian'),
            SelectOption(label='Без слов', value='without-words', default=lang_settings == 'without-words')
        ]

        feel_select = MyVibeSelect(
            ComponentType.string_select,
            placeholder='По характеру',
            options=diversity,
            row=0,
            custom_id='diversity'
        )
        mood_select = MyVibeSelect(
            ComponentType.string_select,
            placeholder='По настроению',
            options=mood,
            row=1,
            custom_id='mood'
        )
        lang_select = MyVibeSelect(
            ComponentType.string_select,
            placeholder='По языку',
            options=lang,
            row=2,
            custom_id='lang'
        )
        for select in [feel_select, mood_select, lang_select]:
            self.add_item(select)

        return self
    
    async def on_timeout(self) -> None:
        try:
            return await super().on_timeout()
        except HTTPException:
            pass
        self.stop()

class MyVibeSettingsButton(Button, VoiceExtension):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        VoiceExtension.__init__(self, None)

    async def callback(self, interaction: Interaction) -> None:
        logging.info('[MENU] My vibe settings button callback')
        if not await self.voice_check(interaction, check_vibe_privilage=True):
            return

        await self.respond(interaction, "info", "Настройки **Волны**", view=await MyVibeSettingsView(interaction).init(), ephemeral=True)

class AddToPlaylistSelect(Select, VoiceExtension):
    def __init__(self, ym_client: YMClient, *args, **kwargs):
        super().__init__(*args, **kwargs)
        VoiceExtension.__init__(self, None)
        self.ym_client = ym_client
        
    async def callback(self, interaction: Interaction):
        logging.info('[MENU] Add to playlist select callback')

        if not await self.voice_check(interaction):
            return

        if not interaction.guild_id or not interaction.data or 'values' not in interaction.data:
            logging.warning('[MENU] No data in select callback')
            return

        data_values = cast(list[str] | None, interaction.data.get('values'))
        logging.debug(f"[MENU] Add to playlist select callback: {data_values}")

        if not data_values:
            logging.warning('[MENU] No data in select callback')
            return

        kind, user_id = data_values[0].split(';')
        playlist = cast(Playlist, await self.ym_client.users_playlists(kind=kind, user_id=user_id))
        current_track = await self.db.get_track(interaction.guild_id, 'current')

        if not current_track:
            return

        tracks = [track.id for track in playlist.tracks]
        track_in_playlist = current_track['id'] in tracks
        
        if track_in_playlist:
            index = tracks.index(current_track['id'])
            res = await self.ym_client.users_playlists_delete_track(
                kind=f"{playlist.kind}",
                from_=index,
                to=index + 1,
                revision=playlist.revision or 1
            )
        else:
            res = await self.ym_client.users_playlists_insert_track(
                kind=f"{playlist.kind}",
                track_id=current_track['id'],
                album_id=current_track['albums'][0]['id'],
                revision=playlist.revision or 1
            )

        if not res:
            await self.respond(interaction, "error", "Что-то пошло не так. Попробуйте позже.", delete_after=15, ephemeral=True)
        elif track_in_playlist:
            await self.respond(interaction, "success", "🗑 Трек был удалён из плейлиста.", delete_after=15, ephemeral=True)
        else:
            await self.respond(interaction, "success", "📩 Трек был добавлен в плейлист.", delete_after=15, ephemeral=True)
            

class AddToPlaylistButton(Button, VoiceExtension):
    def __init__(self, **kwargs):
        Button.__init__(self, **kwargs)
        VoiceExtension.__init__(self, None)
    
    async def callback(self, interaction: Interaction):
        if not await self.voice_check(interaction) or not interaction.guild_id:
            return

        if not await self.db.get_track(interaction.guild_id, 'current'):
            await self.respond(interaction, "error", "Нет воспроизводимого трека.", delete_after=15, ephemeral=True)
            return

        if not (client := await self.init_ym_client(interaction)):
            await self.respond(interaction, "error", "Что-то пошло не так. Попробуйте позже.", delete_after=15, ephemeral=True)
            return

        if not (vc := await self.get_voice_client(interaction)) or not vc.is_playing:
            await self.respond(interaction, "error", "Нет воспроизводимого трека.", delete_after=15, ephemeral=True)
            return

        if not (playlists := await client.users_playlists_list()):
            await self.respond(interaction, "error", "У вас нет плейлистов.", delete_after=15, ephemeral=True)
            return

        view = View(
            AddToPlaylistSelect(
                client,
                ComponentType.string_select,
                placeholder='Выберите плейлист',
                options=[
                    SelectOption(
                        label=playlist.title or "Без названия",
                        value=f"{playlist.kind or "-1"};{playlist.uid}"
                    ) for playlist in playlists
                ]
            )
        )

        await interaction.respond(view=view, ephemeral=True, delete_after=360)


class MenuView(View, VoiceExtension):
    
    def __init__(self, ctx: ApplicationContext | Interaction | RawReactionActionEvent, *items: Item, timeout: float | None = 3600, disable_on_timeout: bool = False):
        View.__init__(self, *items, timeout=timeout, disable_on_timeout=disable_on_timeout)
        VoiceExtension.__init__(self, None)
        self.ctx = ctx

        self.repeat_button = ToggleButton(self, style=ButtonStyle.secondary, emoji='🔂', row=0, custom_id='repeat')
        self.shuffle_button = ToggleButton(self, style=ButtonStyle.secondary, emoji='🔀', row=0, custom_id='shuffle')
        self.play_pause_button = PlayPauseButton(style=ButtonStyle.primary, emoji='⏯', row=0)
        self.next_button = SwitchTrackButton(style=ButtonStyle.primary, emoji='⏭', row=0, custom_id='next')
        self.prev_button = SwitchTrackButton(style=ButtonStyle.primary, emoji='⏮', row=0, custom_id='previous')

        self.like_button = ReactionButton(self, style=ButtonStyle.secondary, emoji='❤️', row=1, custom_id='like')
        self.dislike_button = ReactionButton(self, style=ButtonStyle.secondary, emoji='💔', row=1, custom_id='dislike')
        self.lyrics_button = LyricsButton(style=ButtonStyle.secondary, emoji='📋', row=1)
        self.add_to_playlist_button = AddToPlaylistButton(style=ButtonStyle.secondary, emoji='📁', row=1)
        self.vibe_button = MyVibeButton(style=ButtonStyle.secondary, emoji='🌊', row=1)
        self.vibe_settings_button = MyVibeSettingsButton(style=ButtonStyle.success, emoji='🛠', row=1)
        
        self.current_vibe_button: MyVibeButton | MyVibeSettingsButton = self.vibe_button

    async def init(self, *, disable: bool = False) -> Self:
        await self.update(disable=disable)

        self.add_item(self.repeat_button)
        self.add_item(self.prev_button)
        self.add_item(self.play_pause_button)
        self.add_item(self.next_button)
        self.add_item(self.shuffle_button)
        self.add_item(self.like_button)
        self.add_item(self.dislike_button)
        self.add_item(self.lyrics_button)
        self.add_item(self.add_to_playlist_button)
        self.add_item(self.current_vibe_button)

        return self

    async def update(self, *, disable: bool = False) -> Self:
        if not self.ctx.guild_id:
            return self
        
        self.enable_all_items()

        self.guild = await self.db.get_guild(self.ctx.guild_id, projection={
            'repeat': 1, 'shuffle': 1, 'current_track': 1, 'current_viber_id': 1, 'vibing': 1, 'single_token_uid': 1
        })

        if self.guild['repeat']:
            self.repeat_button.style = ButtonStyle.success
        else:
            self.repeat_button.style = ButtonStyle.secondary

        if self.guild['shuffle']:
            self.shuffle_button.style = ButtonStyle.success
        else:
            self.shuffle_button.style = ButtonStyle.secondary

        current_track = self.guild['current_track']

        if not isinstance(self.ctx, RawReactionActionEvent) \
           and len(cast(VoiceChannel, self.ctx.channel).members) == 2 \
           and not self.guild['single_token_uid']:

            if current_track and str(current_track['id']) in [str(like.id) for like in await self.get_reacted_tracks(self.ctx, 'like')]:
                self.like_button.style = ButtonStyle.success
            else:
                self.like_button.style = ButtonStyle.secondary

            if current_track and str(current_track['id']) in [str(dislike.id) for dislike in await self.get_reacted_tracks(self.ctx, 'dislike')]:
                self.dislike_button.style = ButtonStyle.success
            else:
                self.dislike_button.style = ButtonStyle.secondary

        else:
            self.like_button.style = ButtonStyle.secondary
            self.dislike_button.style = ButtonStyle.secondary

        if not current_track:
            self.lyrics_button.disabled = True
            self.like_button.disabled = True
            self.dislike_button.disabled = True
            self.add_to_playlist_button.disabled = True
        elif not current_track['lyrics_available']:
            self.lyrics_button.disabled = True

        if self.guild['single_token_uid']:
            self.like_button.disabled = True
            self.dislike_button.disabled = True
            self.add_to_playlist_button.disabled = True

        if self.guild['vibing']:
            self.current_vibe_button = self.vibe_settings_button
        else:
            self.current_vibe_button = self.vibe_button

        if disable:
            self.disable_all_items()
        
        if self.timeout:
            self.__timeout_expiry = monotonic() + self.timeout

        return self
    
    async def on_timeout(self) -> None:
        logging.debug('[MENU] Menu timed out. Deleting menu message')
        if not self.ctx.guild_id:
            return

        if self.guild['current_menu']:
            await self.db.update(self.ctx.guild_id, {
                'current_menu': None, 'repeat': False, 'shuffle': False,
                'previous_tracks': [], 'next_tracks': [], 'votes': {},
                'vibing': False, 'current_viber_id': None
            })

            if (message := await self.get_menu_message(self.ctx, self.guild['current_menu'])):
                await message.delete()
                logging.debug('[MENU] Successfully deleted menu message')
            else:
                logging.debug('[MENU] No menu message found')

        self.stop()
