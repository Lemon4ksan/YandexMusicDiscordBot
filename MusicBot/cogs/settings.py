import logging
from typing import Literal, cast

import discord
from discord.ext.commands import Cog

from MusicBot.database import BaseUsersDatabase, BaseGuildsDatabase

def setup(bot):
    bot.add_cog(Settings(bot))

class Settings(Cog):

    settings = discord.SlashCommandGroup("settings", "Команды для изменения настроек бота.")

    def __init__(self, bot: discord.Bot):
        self.db = BaseGuildsDatabase()
        self.users_db = BaseUsersDatabase()
        self.bot = bot

    @settings.command(name="show", description="Показать текущие настройки бота.")
    async def show(self, ctx: discord.ApplicationContext) -> None:
        if not ctx.guild_id:
            logging.warning("[SETTINGS] Show command invoked without guild_id")
            await ctx.respond("❌ Эта команда может быть использована только на сервере.", ephemeral=True)
            return

        guild = await self.db.get_guild(ctx.guild_id, projection={'allow_change_connect': 1, 'vote_switch_track': 1, 'vote_add': 1})

        vote = "✅ - Переключение" if guild['vote_switch_track'] else "❌ - Переключение"
        vote += "\n✅ - Добавление в очередь" if guild['vote_add'] else "\n❌ - Добавление в очередь"

        connect = "\n✅ - Разрешено всем" if guild['allow_change_connect'] else "\n❌ - Только для участникам с правами управления каналом"

        embed = discord.Embed(title="Настройки бота", color=0xfed42b)
        embed.add_field(name="__Голосование__", value=vote, inline=False)
        embed.add_field(name="__Подключение/Отключение бота__", value=connect, inline=False)

        await ctx.respond(embed=embed, ephemeral=True)

    @settings.command(name="toggle", description="Переключить параметр настроек.")
    @discord.option(
        "параметр",
        parameter_name="vote_type",
        description="Тип голосования.",
        type=discord.SlashCommandOptionType.string,
        choices=['Переключение', 'Добавление в очередь', 'Добавление/Отключение бота']
    )
    async def toggle(
        self,
        ctx: discord.ApplicationContext,
        vote_type: Literal['Переключение', 'Добавление в очередь', 'Добавление/Отключение бота']
    ) -> None:
        member = cast(discord.Member, ctx.author)
        if not member.guild_permissions.manage_channels:
            await ctx.respond("❌ У вас нет прав для выполнения этой команды.", delete_after=15, ephemeral=True)
            return
        
        if not ctx.guild_id:
            logging.warning("[SETTINGS] Toggle command invoked without guild_id")
            await ctx.respond("❌ Эта команда может быть использована только на сервере.", ephemeral=True)
            return

        guild = await self.db.get_guild(ctx.guild_id, projection={
            'vote_switch_track': 1, 'vote_add': 1, 'allow_change_connect': 1})

        if vote_type == 'Переключение':
            await self.db.update(ctx.guild_id, {'vote_switch_track': not guild['vote_switch_track']})
            response_message = "Голосование за переключение трека " + ("❌ выключено." if guild['vote_switch_track'] else "✅ включено.")

        elif vote_type == 'Добавление в очередь':
            await self.db.update(ctx.guild_id, {'vote_add': not guild['vote_add']})
            response_message = "Голосование за добавление в очередь " + ("❌ выключено." if guild['vote_add'] else "✅ включено.")

        elif vote_type == 'Добавление/Отключение бота':
            await self.db.update(ctx.guild_id, {'allow_change_connect': not guild['allow_change_connect']})
            response_message = f"Добавление/Отключение бота от канала теперь {'✅ разрешено' if not guild['allow_change_connect'] else '❌ запрещено'} участникам без прав управления каналом."

        else:
            response_message = "❌ Неизвестный тип голосования."

        await ctx.respond(response_message, delete_after=15, ephemeral=True)
