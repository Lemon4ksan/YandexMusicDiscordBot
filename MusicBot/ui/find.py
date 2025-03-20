import logging
from typing import cast

import discord
from yandex_music import Track, Album, Artist, Playlist

from discord.ui import View, Button, Item
from discord import ButtonStyle, Interaction

from MusicBot.cogs.utils.voice_extension import VoiceExtension

class PlayButton(Button, VoiceExtension):
    def __init__(self, item: Track | Album | Artist | Playlist | list[Track], **kwargs):
        Button.__init__(self, **kwargs)
        VoiceExtension.__init__(self, None)
        self.item = item

    async def callback(self, interaction: Interaction) -> None:
        logging.debug(f"[FIND] Callback triggered for type: '{type(self.item).__name__}'")

        if not interaction.guild_id:
            logging.info("[FIND] No guild found in PlayButton callback")
            await self.respond(interaction, "error", "Эта команда доступна только на серверах.", ephemeral=True, delete_after=15)
            return
        
        if not await self.voice_check(interaction):
            return

        guild = await self.db.get_guild(interaction.guild_id, projection={'current_track': 1, 'current_menu': 1, 'vote_add': 1, 'vibing': 1})
        if guild['vibing']:
            await self.respond(interaction, "error", "Нельзя добавлять треки в очередь, пока запущена волна.", ephemeral=True, delete_after=15)
            return

        channel = cast(discord.VoiceChannel, interaction.channel)
        member = cast(discord.Member, interaction.user)

        if isinstance(self.item, Track):
            tracks = [self.item]
            action = 'add_track'
            vote_message = f"{member.mention} хочет добавить трек **{self.item.title}** в очередь.\n\n Голосуйте за добавление."
            response_message = f"Трек **{self.item.title}** был добавлен в очередь."

        elif isinstance(self.item, Album):
            album = await self.item.with_tracks_async()
            if not album or not album.volumes:
                logging.debug("[FIND] Failed to fetch album tracks in PlayButton callback")
                await self.respond(interaction, "error", "Не удалось получить треки альбома.", ephemeral=True, delete_after=15)
                return

            tracks = [track for volume in album.volumes for track in volume]
            action = 'add_album'
            vote_message = f"{member.mention} хочет добавить альбом **{self.item.title}** в очередь.\n\n Голосуйте за добавление."
            response_message = f"Альбом **{self.item.title}** был добавлен в очередь."

        elif isinstance(self.item, Artist):
            artist_tracks = await self.item.get_tracks_async()
            if not artist_tracks:
                logging.debug("[FIND] Failed to fetch artist tracks in PlayButton callback")
                await self.respond(interaction, "error", "Не удалось получить треки артиста.", ephemeral=True, delete_after=15)
                return

            tracks = artist_tracks.tracks.copy()
            action = 'add_artist'
            vote_message = f"{member.mention} хочет добавить треки от **{self.item.name}** в очередь.\n\n Голосуйте за добавление."
            response_message = f"Песни артиста **{self.item.name}** были добавлены в очередь."

        elif isinstance(self.item, Playlist):
            short_tracks = await self.item.fetch_tracks_async()
            if not short_tracks:
                logging.debug("[FIND] Failed to fetch playlist tracks in PlayButton callback")
                await self.respond(interaction, "error", "Не удалось получить треки из плейлиста.", ephemeral=True, delete_after=15)
                return

            tracks = [cast(Track, short_track.track) for short_track in short_tracks]
            action = 'add_playlist'
            vote_message = f"{member.mention} хочет добавить плейлист **{self.item.title}** в очередь.\n\n Голосуйте за добавление."
            response_message = f"Плейлист **{self.item.title}** был добавлен в очередь."

        elif isinstance(self.item, list):
            tracks = self.item.copy()
            if not tracks:
                logging.debug("[FIND] Empty tracks list in PlayButton callback")
                await self.respond(interaction, "error", "Не удалось получить треки.", ephemeral=True, delete_after=15)
                return

            action = 'add_playlist'
            vote_message = f"{member.mention} хочет добавить плейлист **Мне Нравится** в очередь.\n\n Голосуйте за добавление."
            response_message = f"Плейлист **«Мне нравится»** был добавлен в очередь."

        else:
            raise ValueError(f"Unknown item type: '{type(self.item).__name__}'")

        if guild['vote_add'] and len(channel.members) > 2 and not member.guild_permissions.manage_channels:
            logging.info(f"Starting vote for '{action}' (from PlayButton callback)")

            message = cast(discord.Interaction, await self.respond(interaction, "info", vote_message, delete_after=60))
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
                    'action': action,
                    'vote_content': [track.to_dict() for track in tracks]
                }
            )
            return

        if guild['current_menu']:
            await self.respond(interaction, "success", response_message, delete_after=15)
        elif not await self.send_menu_message(interaction, disable=True):
            await self.respond(interaction, "error", "Не удалось отправить сообщение.", ephemeral=True, delete_after=15)

        if guild['current_track']:
            logging.debug(f"[FIND] Adding tracks to queue")
            await self.db.modify_track(interaction.guild_id, tracks, 'next', 'extend')
        else:
            logging.debug(f"[FIND] Playing track")
            track = tracks.pop(0)
            await self.db.modify_track(interaction.guild_id, tracks, 'next', 'extend')
            if not await self.play_track(interaction, track):
                await self.respond(interaction, "error", "Не удалось воспроизвести трек.", ephemeral=True, delete_after=15)

        if interaction.message:
            await interaction.message.delete()
        else:
            logging.warning(f"[FIND] Interaction message is None")

class MyVibeButton(Button, VoiceExtension):
    def __init__(self, item: Track | Album | Artist | Playlist | list[Track], *args, **kwargs):
        Button.__init__(self, *args, **kwargs)
        VoiceExtension.__init__(self, None)
        self.item = item
    
    async def callback(self, interaction: discord.Interaction):
        logging.debug(f"[VIBE] Button callback for '{type(self.item).__name__}'")

        if not await self.voice_check(interaction):
            return

        if not interaction.guild_id or not interaction.user:
            logging.warning(f"[VIBE] Guild ID or user is None in button callback")
            return

        guild = await self.db.get_guild(interaction.guild_id, projection={'current_menu': 1, 'vibing': 1})
        if guild['vibing']:
            await self.respond(interaction, "error", "Волна уже запущена. Остановите её с помощью команды /voice stop.", ephemeral=True, delete_after=15)
            return

        track_type_map = {
            Track: 'track', Album: 'album', Artist: 'artist', Playlist: 'playlist', list: 'user'
        }

        if isinstance(self.item, Playlist):
            if not self.item.owner:
                logging.warning(f"[VIBE] Playlist owner is None")
                await self.respond(interaction, "error", "Не удалось получить информацию о плейлисте. Отсутствует владелец.", ephemeral=True, delete_after=15)
                return

            _id = self.item.owner.login + '_' + str(self.item.kind)
        elif not isinstance(self.item, list):
            _id = cast(int | str, self.item.id)
        else:
            _id = 'onyourwave'

        member = cast(discord.Member, interaction.user)
        channel = cast(discord.VoiceChannel, interaction.channel)
        
        if len(channel.members) > 2 and not member.guild_permissions.manage_channels:
            logging.info(f"Starting vote for starting vibe in guild {interaction.guild_id}")

            match self.item:
                case Track():
                    response_message = f"{member.mention} хочет запустить волну по треку **{self.item['title']}**.\n\n Выполнить действие?"
                case Album():
                    response_message = f"{member.mention} хочет запустить волну по альбому **{self.item['title']}**.\n\n Выполнить действие?"
                case Artist():
                    response_message = f"{member.mention} хочет запустить волну по исполнителю **{self.item['name']}**.\n\n Выполнить действие?"
                case Playlist():
                    response_message = f"{member.mention} хочет запустить волну по плейлисту **{self.item['title']}**.\n\n Выполнить действие?"
                case list():
                    response_message = f"{member.mention} хочет запустить станцию **Моя Волна**.\n\n Выполнить действие?"

            message = cast(discord.Interaction, await self.respond(interaction, "info", response_message))
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
                    'vote_content': [track_type_map[type(self.item)], _id, interaction.user.id]
                }
            )
            return

        if not guild['current_menu'] and not await self.send_menu_message(interaction, disable=True):
            await self.respond(interaction, "error", "Не удалось отправить сообщение.", ephemeral=True, delete_after=15)

        await self.update_vibe(interaction, track_type_map[type(self.item)], _id)

        if (next_track := await self.db.get_track(interaction.guild_id, 'next')):
            await self.play_track(interaction, next_track)

class ListenView(View):
    def __init__(self, item: Track | Album | Artist | Playlist | list[Track], *items: Item, timeout: float | None = 360, disable_on_timeout: bool = True):
        super().__init__(*items, timeout=timeout, disable_on_timeout=disable_on_timeout)
        logging.debug(f"[FIND] Creating view for type: '{type(item).__name__}'")

        if isinstance(item, Track):
            link_app = f"yandexmusic://album/{item.albums[0].id}/track/{item.id}"
            link_web = f"https://music.yandex.ru/album/{item.albums[0].id}/track/{item.id}"
        elif isinstance(item, Album):
            link_app = f"yandexmusic://album/{item.id}"
            link_web = f"https://music.yandex.ru/album/{item.id}"
        elif isinstance(item, Artist):
            link_app = f"yandexmusic://artist/{item.id}"
            link_web = f"https://music.yandex.ru/artist/{item.id}"
        elif isinstance(item, Playlist):
            link_app = f"yandexmusic://playlists/{item.playlist_uuid}"
            link_web = f"https://music.yandex.ru/playlists/{item.playlist_uuid}"
        elif isinstance(item, list):  # Can't open other person's likes
            self.add_item(PlayButton(item, label="Слушать в голосовом канале", style=ButtonStyle.gray))
            self.add_item(MyVibeButton(item, label="Моя Волна", style=ButtonStyle.gray, emoji="🌊", row=1))
            return

        self.button1: Button = Button(label="Слушать в приложении", style=ButtonStyle.gray, url=link_app, row=0)
        self.button2: Button = Button(label="Слушать в браузере", style=ButtonStyle.gray, url=link_web, row=0)
        self.button3: PlayButton = PlayButton(item, label="Слушать в голосовом канале", style=ButtonStyle.gray, row=0)
        self.button4: MyVibeButton = MyVibeButton(item, label="Моя Волна", style=ButtonStyle.gray, emoji="🌊", row=1)

        if item.available:
            # self.add_item(self.button1)  # Discord doesn't allow well formed URLs in buttons for some reason.
            self.add_item(self.button2)
            self.add_item(self.button3)
            self.add_item(self.button4)

    async def on_timeout(self) -> None:
        try:
            return await super().on_timeout()
        except discord.HTTPException:
            pass
        self.stop()
