from typing import TypedDict, TypeAlias, Literal

VibeSettingsOptions: TypeAlias = Literal[
    'active', 'fun', 'calm', 'sad', 'all',
    'favorite', 'discover', 'popular', 'default',
    'russian', 'not-russian', 'without-words', 'any',
]

class User(TypedDict, total=False):  # Don't forget to change base.py if you add a new field
    ym_token: str | None
    vibe_batch_id: str | None
    vibe_type: Literal['track', 'album', 'artist', 'playlist', 'user'] | None
    vibe_id: str | int | None
    vibe_settings: dict[Literal['mood', 'diversity', 'lang'], VibeSettingsOptions]

class ExplicitUser(TypedDict):
    _id: int
    ym_token: str | None
    vibe_batch_id: str | None
    vibe_type: Literal['track', 'album', 'artist', 'playlist', 'user'] | None
    vibe_id: str | int | None
    vibe_settings: dict[Literal['mood', 'diversity', 'lang'], VibeSettingsOptions]
