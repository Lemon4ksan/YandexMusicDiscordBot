from typing import TypedDict, TypeAlias, Literal

VibeSettingsOptions: TypeAlias = Literal[
    'active', 'fun', 'calm', 'sad', 'all',
    'favorite', 'discover', 'popular', 'default',
    'russian', 'not-russian', 'without-words', 'any',
]

class User(TypedDict, total=False):
    ym_token: str | None
    playlists:  list[tuple[str, int]]
    playlists_page: int
    queue_page: int
    vibe_batch_id: str | None
    vibe_type: Literal['track', 'album', 'artist', 'playlist', 'user'] | None
    vibe_id: str | int | None
    vibe_settings: dict[Literal['mood', 'diversity', 'lang'], VibeSettingsOptions]

class ExplicitUser(TypedDict):
    _id: int
    ym_token: str | None
    playlists: list[tuple[str, int]]  # name / tracks count
    playlists_page: int
    queue_page: int
    vibe_batch_id: str | None
    vibe_type: Literal['track', 'album', 'artist', 'playlist', 'user'] | None
    vibe_id: str | int | None
    vibe_settings: dict[Literal['mood', 'diversity', 'lang'], VibeSettingsOptions]
