from typing import TypedDict, Literal, Any

class MessageVotes(TypedDict):
    positive_votes: list[int]
    negative_votes: list[int]
    total_members: int
    action: Literal[
        'next', 'play/pause', 'stop', 'repeat', 'shuffle', 'previous', 'add_track',
        'add_album', 'add_artist', 'add_playlist', 'vibe_station', 'clear_queue'
    ]
    vote_content: Any | None

class Guild(TypedDict, total=False):
    next_tracks: list[dict[str, Any]]
    previous_tracks: list[dict[str, Any]]
    current_track: dict[str, Any] | None
    current_menu: int | None
    is_stopped: bool
    always_allow_menu: bool
    allow_change_connect: bool
    vote_switch_track: bool
    vote_add: bool
    shuffle: bool
    repeat: bool
    votes: dict[str, MessageVotes]
    vibing: bool
    current_viber_id: int | None

class ExplicitGuild(TypedDict):
    _id: int
    next_tracks: list[dict[str, Any]]
    previous_tracks: list[dict[str, Any]]
    current_track: dict[str, Any] | None
    current_menu: int | None
    is_stopped: bool  # Prevents the `after` callback of play_track
    always_allow_menu: bool
    allow_change_connect: bool
    vote_switch_track: bool
    vote_add: bool
    shuffle: bool
    repeat: bool
    votes: dict[str, MessageVotes]
    vibing: bool
    current_viber_id: int | None
