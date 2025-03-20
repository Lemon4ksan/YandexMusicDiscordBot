from math import ceil
from typing import Any

from discord.ui import View, Button, Item
from discord import ApplicationContext, ButtonStyle, Interaction, Embed, HTTPException

from MusicBot.cogs.utils.voice_extension import VoiceExtension

def generate_queue_embed(page: int, tracks_list: list[dict[str, Any]]) -> Embed:
    count = 15 * page
    length = len(tracks_list)

    embed = Embed(
        title=f"Всего: {length}",
        color=0xfed42b,
    )
    embed.set_author(name="Очередь треков")
    embed.set_footer(text=f"Страница {page + 1} из {ceil(length / 15)}")

    for i, track in enumerate(tracks_list[count:count + 15], start=1 + count):
        if track['duration_ms']:
            duration_m = track['duration_ms'] // 60000
            duration_s = ceil(track['duration_ms'] / 1000) - duration_m * 60
            embed.add_field(name=f"{i} - {track['title']} - {duration_m}:{duration_s:02d}", value="", inline=False)

    return embed

class QueueNextButton(Button):
    def __init__(self, root:' QueueView', **kwargs):
        Button.__init__(self, **kwargs)
        self.root = root

    async def callback(self, interaction: Interaction) -> None:
        self.root.page += 1
        self.root.update()
        embed = generate_queue_embed(self.root.page, self.root.tracks)
        await interaction.edit(embed=embed, view=self.root)

class QueuePrevButton(Button):
    def __init__(self, root: 'QueueView', **kwargs):
        Button.__init__(self, **kwargs)
        self.root = root

    async def callback(self, interaction: Interaction) -> None:
        self.root.page -= 1
        self.root.update()
        embed = generate_queue_embed(self.root.page, self.root.tracks)
        await interaction.edit(embed=embed, view=self.root)

class QueueView(View, VoiceExtension):
    def __init__(
        self,
        ctx: ApplicationContext | Interaction,
        tracks: list[dict[str, Any]],
        *items: Item,
        timeout: float | None = 360,
        disable_on_timeout: bool = False
    ):
        View.__init__(self, *items, timeout=timeout, disable_on_timeout=disable_on_timeout)
        VoiceExtension.__init__(self, None)

        self.ctx = ctx
        self.tracks = tracks
        self.page = 0

        self.next_button = QueueNextButton(self, style=ButtonStyle.primary, emoji='▶️')
        self.prev_button = QueuePrevButton(self, style=ButtonStyle.primary, emoji='◀️', disabled=True)
        
        if not self.tracks[15:]:
            self.next_button.disabled = True

        self.prev_button.disabled = True

        self.add_item(self.prev_button)
        self.add_item(self.next_button)

    def update(self):
        count = 15 * self.page

        if self.tracks[15:]:
            self.next_button.disabled = False
        else:
            self.next_button.disabled = True

        if self.tracks[:count]:
            self.prev_button.disabled = False
        else:
            self.prev_button.disabled = True
    
    async def on_timeout(self) -> None:
        try:
            await super().on_timeout()
        except HTTPException:
            pass
        self.stop()
