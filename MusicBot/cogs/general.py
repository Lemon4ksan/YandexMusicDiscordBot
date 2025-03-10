import logging
from typing import Literal
from asyncio import gather

import discord
from discord.ext.commands import Cog

from yandex_music.exceptions import UnauthorizedError
from yandex_music import ClientAsync as YMClient

from MusicBot.ui import ListenView
from MusicBot.database import BaseUsersDatabase, BaseGuildsDatabase
from MusicBot.cogs.utils import generate_item_embed

users_db = BaseUsersDatabase()

def setup(bot):
    bot.add_cog(General(bot))

async def get_search_suggestions(ctx: discord.AutocompleteContext) -> list[str]:
    if not ctx.interaction.user or not ctx.value or not (100 > len(ctx.value) > 2):
        return []

    uid = ctx.interaction.user.id
    token = await users_db.get_ym_token(uid)
    if not token:
        logging.info(f"[GENERAL] User {uid} has no token")
        return []

    try:
        client = await YMClient(token).init()
    except UnauthorizedError:
        logging.info(f"[GENERAL] User {uid} provided invalid token")
        return []

    content_type = ctx.options['тип']
    search = await client.search(ctx.value)
    if not search:
        logging.warning(f"[GENERAL] Failed to search for '{ctx.value}' for user {uid}")
        return []

    logging.debug(f"[GENERAL] Searching for '{ctx.value}' for user {uid}")

    if content_type not in ('Трек', 'Альбом', 'Артист', 'Плейлист'):
        logging.error(f"[GENERAL] Invalid content type '{content_type}' for user {uid}")
        return []

    if content_type == 'Трек' and search.tracks is not None:
        res = [f"{item.title} {f"({item.version})" if item.version else ''} - {", ".join(item.artists_name())}" for item in search.tracks.results]
    elif content_type == 'Альбом' and search.albums is not None:
        res = [f"{item.title} - {", ".join(item.artists_name())}" for item in search.albums.results]
    elif content_type == 'Артист' and search.artists is not None:
        res = [f"{item.name}" for item in search.artists.results]
    elif content_type == 'Плейлист' and search.playlists is not None:
        res = [f"{item.title}" for item in search.playlists.results]
    else:
        logging.info(f"[GENERAL] Failed to get content type '{content_type}' with name '{ctx.value}' for user {uid}")
        return []

    return res[:100]

async def get_user_playlists_suggestions(ctx: discord.AutocompleteContext) -> list[str]:
    if not ctx.interaction.user or not ctx.value or not (100 > len(ctx.value) > 2):
        return []

    uid = ctx.interaction.user.id
    token = await users_db.get_ym_token(uid)
    if not token:
        logging.info(f"[GENERAL] User {uid} has no token")
        return []

    try:
        client = await YMClient(token).init()
    except UnauthorizedError:
        logging.info(f"[GENERAL] User {uid} provided invalid token")
        return []

    logging.debug(f"[GENERAL] Searching for '{ctx.value}' for user {uid}")
    try:
        playlists_list = await client.users_playlists_list()
    except Exception as e:
        logging.error(f"[GENERAL] Failed to get playlists for user {uid}: {e}")
        return []

    return [playlist.title for playlist in playlists_list if playlist.title and ctx.value in playlist.title][:100]

class General(Cog):

    def __init__(self, bot: discord.Bot):
        self.bot = bot
        self.db = BaseGuildsDatabase()
        self.users_db = users_db

    account = discord.SlashCommandGroup("account", "Команды, связанные с аккаунтом.")

    @discord.slash_command(description="Получить информацию о командах YandexMusic.")
    @discord.option(
        "command",
        description="Название команды.",
        type=discord.SlashCommandOptionType.string,
        required=False
    )
    async def help(self, ctx: discord.ApplicationContext, command: str = 'all') -> None:
        logging.info(f"[GENERAL] Help command invoked by {ctx.user.id} for command '{command}'")

        embed = discord.Embed(
            title='Помощь',
            color=0xfed42b
        )
        embed.set_author(name='YandexMusic')
        embed.description = '__Использование__\n'

        if command == 'all':
            embed.description = (
                "Этот бот позволяет слушать музыку из вашего аккаунта Яндекс Музыки.\n"
                "Зарегистрируйте свой токен с помощью /login. Его можно получить [здесь](https://github.com/MarshalX/yandex-music-api/discussions/513).\n"
                "Для получения помощи по конкретной команде, введите /help <команда>.\n"
                "Для изменения настроек необходимо иметь права управления каналами на сервере.\n\n"
                "**Присоединяйтесь к нашему [серверу сообщества](https://discord.gg/TgnW8nfbFn)!**"
            )
            embed.add_field(
                name='__Основные команды__',
                value="""`account`
                `find`
                `help`
                `queue`
                `settings`
                `voice`"""
            )
            embed.set_footer(text='©️ Bananchiki')
        elif command == 'account':
            embed.description += (
                "Ввести токен Яндекс Музыки. Его можно получить [здесь](https://github.com/MarshalX/yandex-music-api/discussions/513).\n"
                "```/account login <token>```\n"
                "Удалить токен из базы данных бота.\n```/account remove```\n"
                "Получить ваш плейлист.\n```/account playlist <название>```\n"
                "Получить плейлист «Мне нравится».\n```/account likes```\n"
                "Получить ваши рекомендации.\n```/account recommendations <тип>```\n"
            )
        elif command == 'find':
            embed.description += (
                "Вывести информацию о треке (по умолчанию), альбоме, авторе или плейлисте. Позволяет добавить музыку в очередь. "
                "В названии можно уточнить автора через «-». Возвращается лучшее совпадение.\n```/find <тип> <название>```"
            )
        elif command == 'help':
            embed.description += (
                "Вывести список всех команд или информацию по конкретной команде.\n```/help <команда>```\n"
            )
        elif command == 'queue':
            embed.description += (
                "Получить очередь треков. По 15 элементов на страницу.\n```/queue get```\n"
                "Очистить очередь треков и историю прослушивания. Доступно только если вы единственный в голосовом канале "
                "или имеете разрешение управления каналом.\n```/queue clear```\n"
            )
        elif command == 'settings':
            embed.description += (
                "`Примечание`: Только пользователи с разрешением управления каналом могут менять настройки.\n\n"
                "Получить текущие настройки.\n```/settings show```\n"
                "Переключить параметр настроек.\n```/settings toggle <параметр>```\n"
            )
        elif command == 'voice':
            embed.description += (
                "`Примечание`: Доступность меню и Моей Волны зависит от настроек сервера.\n\n"
                "Присоединить бота в голосовой канал.\n```/voice join```\n"
                "Заставить бота покинуть голосовой канал.\n ```/voice leave```\n"
                "Прервать проигрывание, удалить историю, очередь и текущий плеер.\n ```/voice stop```\n"
                "Создать меню проигрывателя. \n```/voice menu```\n"
                "Запустить станцию. Без уточнения станции, запускает Мою Волну.\n```/voice vibe <название станции>```"
            )
        else:
            await ctx.respond('❌ Неизвестная команда.', delete_after=15, ephemeral=True)
            return

        await ctx.respond(embed=embed, ephemeral=True)
    
    @account.command(description="Ввести токен Яндекс Музыки.")
    @discord.option("token", type=discord.SlashCommandOptionType.string, description="Токен.")
    async def login(self, ctx: discord.ApplicationContext, token: str) -> None:
        logging.info(f"[GENERAL] Login command invoked by user {ctx.author.id} in guild {ctx.guild_id}")
        try:
            client = await YMClient(token).init()
        except UnauthorizedError:
            logging.info(f"[GENERAL] Invalid token provided by user {ctx.author.id}")
            await ctx.respond('❌ Недействительный токен.', delete_after=15, ephemeral=True)
            return

        if not client.me or not client.me.account:
            logging.warning(f"[GENERAL] Failed to get user info for user {ctx.author.id}")
            await ctx.respond('❌ Не удалось получить информацию о пользователе.', delete_after=15, ephemeral=True)
            return

        await self.users_db.update(ctx.author.id, {'ym_token': token})
        await ctx.respond(f'✅ Привет, {client.me.account.first_name}!', delete_after=15, ephemeral=True)

        logging.info(f"[GENERAL] User {ctx.author.id} logged in successfully")
    
    @account.command(description="Удалить токен из базы данных бота.")
    async def remove(self, ctx: discord.ApplicationContext) -> None:
        logging.info(f"[GENERAL] Remove command invoked by user {ctx.author.id} in guild {ctx.guild_id}")
        if not await self.users_db.get_ym_token(ctx.user.id):
            logging.info(f"[GENERAL] No token found for user {ctx.author.id}")
            await ctx.respond('❌ Токен не указан.', delete_after=15, ephemeral=True)
            return

        await self.users_db.update(ctx.user.id, {'ym_token': None})
        await ctx.respond(f'✅ Токен был удалён.', delete_after=15, ephemeral=True)
        logging.info(f"[GENERAL] Token removed for user {ctx.author.id}")

    @account.command(description="Получить плейлист «Мне нравится»")
    async def likes(self, ctx: discord.ApplicationContext) -> None:
        logging.info(f"[GENERAL] Likes command invoked by user {ctx.author.id} in guild {ctx.guild_id}")

        token = await self.users_db.get_ym_token(ctx.user.id)
        if not token:
            logging.info(f"[GENERAL] No token found for user {ctx.user.id}")
            await ctx.respond("❌ Укажите токен через /account login.", delete_after=15, ephemeral=True)
            return

        try:
            client = await YMClient(token).init()
        except UnauthorizedError:
            logging.info(f"[GENERAL] Invalid token for user {ctx.user.id}")
            await ctx.respond('❌ Неверный токен. Укажите новый через /account login.', delete_after=15, ephemeral=True)
            return

        try:
            likes = await client.users_likes_tracks()
        except UnauthorizedError:
            logging.warning(f"[GENERAL] Unknown token error for user {ctx.user.id}")
            await ctx.respond("❌ Произошла неизвестная ошибка при попытке получения лайков. Пожалуйста, сообщите об этом разработчику.", delete_after=15, ephemeral=True)
            return

        if likes is None:
            logging.info(f"[GENERAL] Failed to fetch likes for user {ctx.user.id}")
            await ctx.respond('❌ Что-то пошло не так. Повторите попытку позже.', delete_after=15, ephemeral=True)
            return
        elif not likes:
            logging.info(f"[GENERAL] Empty likes for user {ctx.user.id}")
            await ctx.respond('❌ У вас нет треков в плейлисте «Мне нравится».', delete_after=15, ephemeral=True)
            return

        await ctx.defer()  # Sometimes it takes a while to fetch all tracks, so we defer the response
        real_tracks = await gather(*[track_short.fetch_track_async() for track_short in likes.tracks], return_exceptions=True)
        tracks = [track for track in real_tracks if not isinstance(track, BaseException)]  # Can't fetch user tracks

        await ctx.respond(embed=await generate_item_embed(tracks), view=ListenView(tracks))
        logging.info(f"[GENERAL] Successfully generated likes message for user {ctx.user.id}")
    
    @account.command(description="Получить ваши рекомендации.")
    @discord.option(
        'тип',
        parameter_name='content_type',
        description="Вид рекомендаций.",
        type=discord.SlashCommandOptionType.string,
        choices=['Премьера', 'Плейлист дня', 'Дежавю']
    )
    async def recommendations(
        self,
        ctx: discord.ApplicationContext,
        content_type: Literal['Премьера', 'Плейлист дня', 'Дежавю']
    )-> None:
        # NOTE: Recommendations can be accessed by using /find, but it's more convenient to have it in separate command.
        logging.debug(f"[GENERAL] Recommendations command invoked by user {ctx.user.id} in guild {ctx.guild_id} for type '{content_type}'")

        token = await self.users_db.get_ym_token(ctx.user.id)
        if not token:
            await ctx.respond("❌ Укажите токен через /account login.", delete_after=15, ephemeral=True)
            return

        try:
            client = await YMClient(token).init()
        except UnauthorizedError:
            logging.info(f"[GENERAL] User {ctx.user.id} provided invalid token")
            await ctx.respond('❌ Неверный токен. Укажите новый через /account login.', delete_after=15, ephemeral=True)
            return

        search = await client.search(content_type, type_='playlist')
        if not search or not search.playlists:
            logging.info(f"[GENERAL] Failed to fetch recommendations for user {ctx.user.id}")
            await ctx.respond('❌ Что-то пошло не так. Повторите попытку позже.', delete_after=15, ephemeral=True)
            return

        playlist = search.playlists.results[0]
        if playlist is None:
            logging.info(f"[GENERAL] Failed to fetch recommendations for user {ctx.user.id}")
            await ctx.respond('❌ Что-то пошло не так. Повторите попытку позже.', delete_after=15, ephemeral=True)

        tracks = await playlist.fetch_tracks_async()
        if not tracks:
            logging.info(f"[GENERAL] User {ctx.user.id} search for '{content_type}' returned no tracks")
            await ctx.respond("❌ Пустой плейлист.", delete_after=15, ephemeral=True)
            return

        await ctx.respond(embed=await generate_item_embed(playlist), view=ListenView(playlist))

    @account.command(description="Получить ваш плейлист.")
    @discord.option(
        "запрос",
        parameter_name='name',
        description="Название плейлиста.",
        type=discord.SlashCommandOptionType.string,
        autocomplete=discord.utils.basic_autocomplete(get_user_playlists_suggestions)
    )
    async def playlist(self, ctx: discord.ApplicationContext, name: str) -> None:
        logging.info(f"[GENERAL] Playlist command invoked by user {ctx.user.id} in guild {ctx.guild_id}")

        token = await self.users_db.get_ym_token(ctx.user.id)
        if not token:
            logging.info(f"[GENERAL] No token found for user {ctx.user.id}")
            await ctx.respond("❌ Укажите токен через /account login.", delete_after=15, ephemeral=True)
            return

        try:
            client = await YMClient(token).init()
        except UnauthorizedError:
            logging.info(f"[GENERAL] User {ctx.user.id} provided invalid token")
            await ctx.respond('❌ Неверный токен. Укажите новый через /account login.', delete_after=15, ephemeral=True)
            return

        try:
            playlists = await client.users_playlists_list()
        except UnauthorizedError:
            logging.warning(f"[GENERAL] Unknown token error for user {ctx.user.id}")
            await ctx.respond("❌ Произошла неизвестная ошибка при попытке получения плейлистов. Пожалуйста, сообщите об этом разработчику.", delete_after=15, ephemeral=True)
            return

        playlist = next((playlist for playlist in playlists if playlist.title == name), None)
        if not playlist:
            logging.info(f"[GENERAL] User {ctx.user.id} playlist '{name}' not found")
            await ctx.respond("❌ Плейлист не найден.", delete_after=15, ephemeral=True)
            return

        tracks = await playlist.fetch_tracks_async()
        if not tracks:
            logging.info(f"[GENERAL] User {ctx.user.id} playlist '{name}' is empty")
            await ctx.respond("❌ Плейлист пуст.", delete_after=15, ephemeral=True)
            return

        await ctx.respond(embed=await generate_item_embed(playlist), view=ListenView(playlist))

    @discord.slash_command(description="Найти контент и отправить информацию о нём. Возвращается лучшее совпадение.")
    @discord.option(
        "тип",
        parameter_name='content_type',
        description="Тип контента для поиска.",
        type=discord.SlashCommandOptionType.string,
        choices=['Трек', 'Альбом', 'Артист', 'Плейлист'],
    )
    @discord.option(
        "запрос",
        parameter_name='name',
        description="Название контента для поиска (По умолчанию трек).",
        type=discord.SlashCommandOptionType.string,
        autocomplete=discord.utils.basic_autocomplete(get_search_suggestions)
    )
    async def find(
        self,
        ctx: discord.ApplicationContext,
        content_type: Literal['Трек', 'Альбом', 'Артист', 'Плейлист'],
        name: str
    ) -> None:
        logging.info(f"[GENERAL] Find command invoked by user {ctx.user.id} in guild {ctx.guild_id} for '{content_type}' with name '{name}'")

        token = await self.users_db.get_ym_token(ctx.user.id)
        if not token:
            logging.info(f"[GENERAL] No token found for user {ctx.user.id}")
            await ctx.respond("❌ Укажите токен через /account login.", delete_after=15, ephemeral=True)
            return

        try:
            client = await YMClient(token).init()
        except UnauthorizedError:
            logging.info(f"[GENERAL] User {ctx.user.id} provided invalid token")
            await ctx.respond("❌ Недействительный токен. Если это не так, попробуйте ещё раз.", delete_after=15, ephemeral=True)
            return

        search_result = await client.search(name, nocorrect=True)
        if not search_result:
            logging.warning(f"Failed to search for '{name}' for user {ctx.user.id}")
            await ctx.respond("❌ Что-то пошло не так. Повторите попытку позже.", delete_after=15, ephemeral=True)
            return

        if content_type == 'Трек':
            content = search_result.tracks
        elif content_type == 'Альбом':
            content = search_result.albums
        elif content_type == 'Артист':
            content = search_result.artists
        else:
            content = search_result.playlists

        if not content:
            logging.info(f"[GENERAL] User {ctx.user.id} search for '{name}' returned no results")
            await ctx.respond("❌ По запросу ничего не найдено.", delete_after=15, ephemeral=True)
            return

        result = content.results[0]
        await ctx.respond(embed=await generate_item_embed(result), view=ListenView(result))

        logging.info(f"[GENERAL] Successfully generated '{content_type}' message for user {ctx.author.id}")
