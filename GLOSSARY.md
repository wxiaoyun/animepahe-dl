# animepahe downloader

Downloads episodes of an anime from animepahe to local video files.

## Language

**Anime**:
A series listed on animepahe, with a title and a set of episodes.
_Avoid_: Show, series

**Anime ID**:
The opaque identifier animepahe assigns to an anime (the API calls it `session`).
_Avoid_: Slug, session, uuid

**Episode**:
One numbered installment of an anime, as listed by animepahe.

**Audio**:
The spoken language of an episode's video, either `jpn` (original, subtitled) or `eng` (dubbed).
_Avoid_: Language, dub/sub

**Resolution**:
The vertical pixel height of an episode's video, such as 360, 720 or 1080.
_Avoid_: Quality

**Fansub**:
The group credited with producing a given encode of an episode.

**Stream**:
The way of getting an episode as an in-browser video player feed, split into many small encrypted pieces.
_Avoid_: Embed, m3u8, playlist link

**Direct download**:
The way of getting an episode as a single complete video file, as offered by animepahe's download button.
_Avoid_: Download link, mp4 link
