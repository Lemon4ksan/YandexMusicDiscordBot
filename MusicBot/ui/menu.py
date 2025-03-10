import logging
from typing import Self, cast

from discord.ui import View, Button, Item, Select
from discord import (
    Interaction, ApplicationContext, RawReactionActionEvent,
    VoiceChannel, ButtonStyle, Embed, ComponentType, SelectOption, Member, HTTPException
)

import yandex_music.exceptions
from yandex_music import TrackLyrics, Playlist, ClientAsync as YMClient
from MusicBot.cogs.utils.voice_extension import VoiceExtension, menu_views

class ToggleButton(Button, VoiceExtension):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        VoiceExtension.__init__(self, None)
    
    async def callback(self, interaction: Interaction) -> None:
        callback_type = interaction.custom_id
        if callback_type not in ('repeat', 'shuffle'):
            raise ValueError(f"Invalid callback type: '{callback_type}'")
        
        logging.info(f'[MENU] {callback_type.capitalize()} button callback')
        
        if not (gid := interaction.guild_id) or not interaction.user:
            logging.warning('[MENU] Failed to get guild ID.')
            await interaction.respond("❌ Что-то пошло не так. Попробуйте снова.", delete_after=15, ephemeral=True)
            return
        
        if not await self.voice_check(interaction, check_vibe_privilage=True):
            return

        guild = await self.db.get_guild(gid)
        member = cast(Member, interaction.user)
        channel = cast(VoiceChannel, interaction.channel)

        if len(channel.members) > 2 and not member.guild_permissions.manage_channels:
            logging.info(f"[MENU] User {interaction.user.id} started vote to pause/resume track in guild {gid}")
            
            action = "выключить" if guild[callback_type] else "включить"
            task = "перемешивание треков" if callback_type == 'shuffle' else "повтор трека"
            message = cast(Interaction, await interaction.respond(f"{member.mention} хочет {action} {task}.\n\nВыполнить действие?", delete_after=60))
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

        if not await self.update_menu_view(interaction, button_callback=True):
            await interaction.respond("❌ Что-то пошло не так. Попробуйте снова.", delete_after=15, ephemeral=True)

class PlayPauseButton(Button, VoiceExtension):
    def __init__(self, **kwargs):
        Button.__init__(self, **kwargs)
        VoiceExtension.__init__(self, None)
    
    async def callback(self, interaction: Interaction) -> None:
        logging.info('[MENU] Play/Pause button callback...')
        if not await self.voice_check(interaction, check_vibe_privilage=True):
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
            message = cast(Interaction, await interaction.respond(f"{member.mention} хочет {task} проигрывание.\n\nВыполнить действие?", delete_after=60))
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
        
        try:
            embed = interaction.message.embeds[0]
        except IndexError:
            await interaction.respond("❌ Нет воспроизводимого трека.", delete_after=15, ephemeral=True)
            return

        if vc.is_paused():
            vc.resume()
            embed.remove_footer()
        else:
            vc.pause()
            embed.set_footer(text='Приостановлено')

        await interaction.edit(embed=embed)

class SwitchTrackButton(Button, VoiceExtension):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        VoiceExtension.__init__(self, None)
    
    async def callback(self, interaction: Interaction) -> None:
        callback_type = interaction.custom_id
        if callback_type not in ('next', 'previous'):
            raise ValueError(f"Invalid callback type: '{callback_type}'")

        if not (gid := interaction.guild_id) or not interaction.user:
            logging.warning(f"[MENU] {callback_type.capitalize()} track button callback without guild id or user")
            return

        logging.info(f'[MENU] {callback_type.capitalize()} track button callback')

        if not await self.voice_check(interaction, check_vibe_privilage=True):
            return

        tracks_type = callback_type + '_tracks'
        guild = await self.db.get_guild(gid, projection={tracks_type: 1, 'vote_switch_track': 1})

        if not guild[tracks_type]:
            logging.info(f"[MENU] No tracks in '{tracks_type}' list in guild {gid}")
            await interaction.respond(f"❌ Нет треков в {'очереди' if callback_type == 'next' else 'истории'}.", delete_after=15, ephemeral=True)
            return

        member = cast(Member, interaction.user)
        channel = cast(VoiceChannel, interaction.channel)

        if guild['vote_switch_track'] and len(channel.members) > 2 and not member.guild_permissions.manage_channels:
            logging.info(f"[MENU] User {interaction.user.id} started vote to skip track in guild {gid}")

            task = "пропустить текущий трек" if callback_type == 'next' else "вернуться к предыдущему треку"
            message = cast(Interaction, await interaction.respond(f"{member.mention} хочет {task}.\n\nВыполнить переход?", delete_after=60))
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
            title = await self.next_track(interaction, button_callback=True)
        else:
            title = await self.previous_track(interaction, button_callback=True)

        if not title:
            await interaction.respond(f"❌ Что-то пошло не так. Попробуйте позже.", delete_after=15, ephemeral=True)

class ReactionButton(Button, VoiceExtension):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        VoiceExtension.__init__(self, None)
    
    async def callback(self, interaction: Interaction):
        callback_type = interaction.custom_id
        if callback_type not in ('like', 'dislike'):
            raise ValueError(f"Invalid callback type: '{callback_type}'")

        logging.info(f'[MENU] {callback_type.capitalize()} button callback')

        if not await self.voice_check(interaction) or not (gid := interaction.guild_id):
            return

        if not (vc := await self.get_voice_client(interaction)) or not vc.is_playing:
            await interaction.respond("❌ Нет воспроизводимого трека.", delete_after=15, ephemeral=True)

        channel = cast(VoiceChannel, interaction.channel)
        res = await self.react_track(interaction, callback_type)

        if callback_type == 'like' and res[0]:
            await self._update_menu_views_dict(interaction)
            await interaction.edit(view=menu_views[gid])
            await interaction.respond(
                f"✅ Трек был {'добавлен в понравившиеся.' if res[1] == 'added' else 'удалён из понравившихся.'}",
                delete_after=15, ephemeral=True
            )

        elif callback_type == 'dislike' and res[0]:

            if len(channel.members) == 2 and not await self.next_track(interaction, vc=vc, button_callback=True):
                await interaction.respond("✅ Воспроизведение приостановлено. Нет треков в очереди.", delete_after=15)

            await self._update_menu_views_dict(interaction)
            await interaction.edit(view=menu_views[gid])
            await interaction.respond(
                f"✅ Трек был {'добавлен в дизлайки.' if res[1] == 'added' else 'удалён из дизлайков.'}",
                delete_after=15, ephemeral=True
            )

        else:
            logging.debug(f"[VC_EXT] Failed to get {callback_type} tracks")
            await interaction.respond("❌ Операция не удалась. Попробуйте позже.", delete_after=15, ephemeral=True)

class LyricsButton(Button, VoiceExtension):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        VoiceExtension.__init__(self, None)
        
    async def callback(self, interaction: Interaction) -> None:
        logging.info('[MENU] Lyrics button callback...')

        if not await self.voice_check(interaction) or not interaction.guild_id or not interaction.user:
            return
        
        client = await self.init_ym_client(interaction)
        if not client:
            return

        current_track = await self.db.get_track(interaction.guild_id, 'current')
        if not current_track:
            logging.debug('[MENU] No current track found')
            return

        try:
            lyrics = cast(TrackLyrics, await client.tracks_lyrics(current_track['id']))
        except yandex_music.exceptions.NotFoundError:
            logging.debug('[MENU] Lyrics not found')
            await interaction.respond("❌ Текст песни не найден. Яндекс нам соврал (опять)!", delete_after=15, ephemeral=True)
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
                _type = 'track'
                _id = track['id']
            else:
                response_message = f"{member.mention} хочет запустить станцию **Моя Волна**.\n\n Выполнить действие?"
                _type = 'user'
                _id = 'onyourwave'

            message = cast(Interaction, await interaction.respond(response_message))
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
                    'vote_content': [_type, _id, interaction.user.id]
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
            await interaction.respond('❌ Не удалось запустить "Мою Волну". Возможно, у вас нет подписки на Яндекс Музыку.', ephemeral=True)

        next_track = await self.db.get_track(interaction.guild_id, 'next')
        if next_track:
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

        await interaction.respond('Настройки "Моей Волны"', view=await MyVibeSettingsView(interaction).init(), ephemeral=True)

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
            await interaction.respond('❌ Что-то пошло не так. Попробуйте позже.', delete_after=15, ephemeral=True)
        elif track_in_playlist:
            await interaction.respond('🗑 Трек был удалён из плейлиста.', delete_after=15, ephemeral=True)
        else:
            await interaction.respond('📩 Трек был добавлен в плейлист.', delete_after=15, ephemeral=True)
            

class AddToPlaylistButton(Button, VoiceExtension):
    def __init__(self, **kwargs):
        Button.__init__(self, **kwargs)
        VoiceExtension.__init__(self, None)
    
    async def callback(self, interaction: Interaction):
        if not await self.voice_check(interaction) or not interaction.guild_id:
            return

        current_track = await self.db.get_track(interaction.guild_id, 'current')
        if not current_track:
            await interaction.respond('❌ Нет воспроизводимого трека.', delete_after=15, ephemeral=True)
            return

        client = await self.init_ym_client(interaction)
        if not client:
            await interaction.respond('❌ Что-то пошло не так. Попробуйте позже.', delete_after=15, ephemeral=True)
            return

        if not (vc := await self.get_voice_client(interaction)) or not vc.is_playing:
            await interaction.respond("❌ Нет воспроизводимого трека.", delete_after=15, ephemeral=True)
            return

        playlists = await client.users_playlists_list()
        if not playlists:
            await interaction.respond('❌ У вас нет плейлистов.', delete_after=15, ephemeral=True)
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

        self.repeat_button = ToggleButton(style=ButtonStyle.secondary, emoji='🔂', row=0, custom_id='repeat')
        self.shuffle_button = ToggleButton(style=ButtonStyle.secondary, emoji='🔀', row=0, custom_id='shuffle')
        self.play_pause_button = PlayPauseButton(style=ButtonStyle.primary, emoji='⏯', row=0)
        self.next_button = SwitchTrackButton(style=ButtonStyle.primary, emoji='⏭', row=0, custom_id='next')
        self.prev_button = SwitchTrackButton(style=ButtonStyle.primary, emoji='⏮', row=0, custom_id='previous')
        
        self.like_button = ReactionButton(style=ButtonStyle.secondary, emoji='❤️', row=1, custom_id='like')
        self.dislike_button = ReactionButton(style=ButtonStyle.secondary, emoji='💔', row=1, custom_id='dislike')
        self.lyrics_button = LyricsButton(style=ButtonStyle.secondary, emoji='📋', row=1)
        self.add_to_playlist_button = AddToPlaylistButton(style=ButtonStyle.secondary, emoji='📁', row=1)
        self.vibe_button = MyVibeButton(style=ButtonStyle.secondary, emoji='🌊', row=1)
        self.vibe_settings_button = MyVibeSettingsButton(style=ButtonStyle.success, emoji='🛠', row=1)

    async def init(self, *, disable: bool = False) -> Self:
        if not self.ctx.guild_id:
            return self

        self.guild = await self.db.get_guild(self.ctx.guild_id, projection={'repeat': 1, 'shuffle': 1, 'current_track': 1, 'current_menu': 1, 'vibing': 1})
    
        if self.guild['repeat']:
            self.repeat_button.style = ButtonStyle.success
        if self.guild['shuffle']:
            self.shuffle_button.style = ButtonStyle.success
        
        current_track = self.guild['current_track']
        likes = await self.get_likes(self.ctx)

        self.add_item(self.repeat_button)
        self.add_item(self.prev_button)
        self.add_item(self.play_pause_button)
        self.add_item(self.next_button)
        self.add_item(self.shuffle_button)
        
        if not isinstance(self.ctx, RawReactionActionEvent) and len(cast(VoiceChannel, self.ctx.channel).members) == 2:
            if likes and current_track and str(current_track['id']) in [str(like.id) for like in likes]:
                self.like_button.style = ButtonStyle.success

        if not current_track:
            self.lyrics_button.disabled = True
            self.like_button.disabled = True
            self.dislike_button.disabled = True
            self.add_to_playlist_button.disabled = True
        elif not current_track['lyrics_available']:
            self.lyrics_button.disabled = True

        self.add_item(self.like_button)
        self.add_item(self.dislike_button)
        self.add_item(self.lyrics_button)
        self.add_item(self.add_to_playlist_button)

        if self.guild['vibing']:
            self.add_item(self.vibe_settings_button)
        else:
            self.add_item(self.vibe_button)

        if disable:
            self.disable_all_items()

        return self

    async def on_timeout(self) -> None:
        logging.debug('[MENU] Menu timed out. Deleting menu message')
        if not self.ctx.guild_id:
            return
        
        if self.guild['current_menu']:
            await self.stop_playing(self.ctx)
            await self.db.update(self.ctx.guild_id, {'current_menu': None, 'previous_tracks': [], 'vibing': False})

            message = await self.get_menu_message(self.ctx, self.guild['current_menu'])
            if message:
                await message.delete()
                logging.debug('[MENU] Successfully deleted menu message')
            else:
                logging.debug('[MENU] No menu message found')

            self.stop()
