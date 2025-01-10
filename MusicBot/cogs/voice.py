from typing import cast

import discord
from discord.ext.commands import Cog

from yandex_music import Track, ClientAsync

from MusicBot.cogs.utils.find import process_track
from MusicBot.cogs.utils.voice import VoiceExtension
from MusicBot.cogs.utils.player import Player

def setup(bot: discord.Bot):
    bot.add_cog(Voice())

class Voice(Cog, VoiceExtension):
    
    voice = discord.SlashCommandGroup("voice", "Команды, связанные с голосовым каналом.", [1247100229535141899])
    queue = discord.SlashCommandGroup("queue", "Команды, связанные с очередью треков.", [1247100229535141899])
    track = discord.SlashCommandGroup("track", "Команды, связанные с текущим треком.", [1247100229535141899])
    
    @voice.command(name="menu", description="Toggle player menu. Available only if you're the only one in the vocie channel.")
    async def menu(self, ctx: discord.ApplicationContext) -> None:
        if not await self.voice_check(ctx):
            return
        current_track = self.db.get_track(ctx.guild.id, 'current')
        try:
            embed = await process_track(Track.de_json(current_track, client=ClientAsync()))  # type: ignore
            vc = self.get_voice_client(ctx)
            if not vc:
                return
            if not vc.is_paused():
                embed.set_footer(text='Приостановлено')
            else:
                embed.remove_footer()
        except AttributeError:
            embed = None
        interaction = cast(discord.Interaction, await ctx.respond(view=Player(ctx), embed=embed, delete_after=3600))
        response = await interaction.original_response()
        self.db.update(ctx.guild.id, {'current_player': response.id})
    
    @voice.command(name="join", description="Join the voice channel you're currently in.")
    async def join(self, ctx: discord.ApplicationContext) -> None:
        vc = self.get_voice_client(ctx)
        if vc is not None and vc.is_playing():
            await ctx.respond("❌ Бот уже находится в голосовом канале. Выключите его с помощью команды /voice leave.", delete_after=15, ephemeral=True)
        elif ctx.channel is not None and isinstance(ctx.channel, discord.VoiceChannel):
            await ctx.channel.connect(timeout=15)
            await ctx.respond("Подключение успешно!", delete_after=15, ephemeral=True)
        else:
            await ctx.respond("❌ Вы должны отправить команду в голосовом канале.", delete_after=15, ephemeral=True)
    
    @voice.command(description="Force the bot to leave the voice channel.")
    async def leave(self, ctx: discord.ApplicationContext) -> None:
        vc = self.get_voice_client(ctx)
        if await self.voice_check(ctx) and vc is not None:
            self.stop_playing(ctx)
            self.db.clear_history(ctx.guild.id)
            await vc.disconnect(force=True)
            await ctx.respond("Отключение успешно!", delete_after=15, ephemeral=True)
    
    @queue.command(description="Clear tracks queue and history.")
    async def clear(self, ctx: discord.ApplicationContext) -> None:
        if not await self.voice_check(ctx):
            return
        self.db.clear_history(ctx.guild.id)
        await ctx.respond("Очередь и история сброшены.", delete_after=15, ephemeral=True)
    
    @queue.command(description="Get tracks queue.")
    async def get(self, ctx: discord.ApplicationContext) -> None:
        if await self.voice_check(ctx):
            tracks_list = self.db.get_tracks_list(ctx.guild.id, 'next')
            embed = discord.Embed(
                title='Список треков',
                color=discord.Color.dark_purple()
            )
            for i, track in enumerate(tracks_list, start=1):
                embed.add_field(name=f"{i} - {track.get('title')}", value="", inline=False)
                if i == 25:
                    break
            await ctx.respond("", embed=embed, ephemeral=True)
    
    @track.command(description="Pause the current track.")
    async def pause(self, ctx: discord.ApplicationContext) -> None:
        vc = self.get_voice_client(ctx)
        if await self.voice_check(ctx) and vc is not None:
            if not vc.is_paused():
                self.pause_playing(ctx)
                await ctx.respond("Воспроизведение приостановлено.", delete_after=15, ephemeral=True)
            else:
                await ctx.respond("Воспроизведение уже приостановлено.", delete_after=15, ephemeral=True)
    
    @track.command(description="Resume the current track.")
    async def resume(self, ctx: discord.ApplicationContext) -> None:
        vc = self.get_voice_client(ctx)
        if await self.voice_check(ctx) and vc is not None:
            if vc.is_paused():
                self.resume_playing(ctx)
                await ctx.respond("Воспроизведение восстановлено.", delete_after=15, ephemeral=True)
            else:
                await ctx.respond("Воспроизведение не на паузе.", delete_after=15, ephemeral=True)
    
    @track.command(description="Stop the current track and clear the queue and history.")
    async def stop(self, ctx: discord.ApplicationContext) -> None:
        if await self.voice_check(ctx):
            self.db.clear_history(ctx.guild.id)
            self.stop_playing(ctx)
            await ctx.respond("Воспроизведение остановлено.", delete_after=15, ephemeral=True)
    
    @track.command(description="Switch to the next song in the queue.")
    async def next(self, ctx: discord.ApplicationContext) -> None:
        if await self.voice_check(ctx):
            gid = ctx.guild.id
            tracks_list = self.db.get_tracks_list(gid, 'next')
            if not tracks_list:
                await ctx.respond("Нет песенен в очереди.", delete_after=15, ephemeral=True)
                return
            self.db.update(gid, {'is_stopped': False})
            title = await self.next_track(ctx)
            if title is not None:
                await ctx.respond(f"Сейчас играет: **{title}**!", delete_after=15)
            else:
                await ctx.respond(f"Нет треков в очереди.", delete_after=15, ephemeral=True)
