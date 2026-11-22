# animepahe-dl

Download anime from [animepahe](https://animepahe.pw) in the terminal, several episodes at once, at full line speed.

It uses animepahe's direct download links (the same single MP4 file the site's download button gives you) rather than the in-browser stream, which is split into many small pieces and rate limited. Cloudflare cookies are read straight from your browser, so there is nothing to copy by hand.

Forked from [KevCui/animepahe-dl](https://github.com/KevCui/animepahe-dl) and rewritten in Python.

## Requirements

- Python 3.10+
- [curl_cffi](https://github.com/lexiforest/curl_cffi) for browser-like HTTP, and [yt-dlp](https://github.com/yt-dlp/yt-dlp), used only to read your browser's cookies:

  ```sh
  python3 -m venv .venv
  .venv/bin/pip install -r requirements.txt
  ```

- Optional: [fzf](https://github.com/junegunn/fzf) to pick between search results. Without it you get a numbered list.

## Setup

1. Open https://animepahe.pw in your browser and let the Cloudflare check pass.
2. Create `config.json` next to `animepahe.py`:

   ```json
   {
     "ua": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
     "browser": "chrome"
   }
   ```

   - `ua`: your browser's exact user agent (search "what is my user agent" in that browser). Cloudflare ties its cookie to it, so update it after browser updates.
   - `browser`: where to read cookies from, in yt-dlp's `BROWSER[+KEYRING][:PROFILE]` format. Defaults to `chrome`. Supported: `brave`, `chrome`, `chromium`, `edge`, `firefox`, `opera`, `safari`, `vivaldi`, `whale`. A Chromium fork not on that list works through its profile directory, for example `chromium:~/.config/ardia`.

## Usage

```
.venv/bin/python animepahe.py [query] [--id ID] [-e EPISODES] [-a {jpn,eng}] [-r RES] [-j JOBS] [-o DIR] [--links] [-d]
```

| Option | Meaning |
|---|---|
| `query` | anime title to search for |
| `--id ID` | Anime ID from the animepahe URL (`/anime/<ID>`), skips the search |
| `-e EPISODES` | `1`, `1,3-5`, or `*` for all. Asks if omitted |
| `-a {jpn,eng}` | audio, default `jpn`. Episodes without it are skipped, never swapped for the other audio |
| `-r RES` | maximum resolution such as `720`. Default is the highest. If that resolution is missing, the next lower one is used |
| `-j JOBS` | episodes downloaded at once, default 4 |
| `-o DIR` | output directory, default current directory |
| `--links` | print the direct MP4 links instead of downloading |
| `-d` | debug logging |

Files are saved as `<DIR>/<Title>/<episode>.mp4`, with `/` and whitespace in the title replaced by `_` and the episode number zero-padded (`01.mp4`, `12.5.mp4`). Finished episodes are skipped on the next run, and interrupted ones resume from their `.part` file.

### Examples

```sh
# search, pick with fzf, then choose episodes interactively
.venv/bin/python animepahe.py frieren

# all episodes in English audio at most 720p, 8 at a time
.venv/bin/python animepahe.py frieren -e '*' -a eng -r 720 -j 8

# by Anime ID, only print links
.venv/bin/python animepahe.py --id a2295fa7-8eaa-d274-a475-6a06be0cfa5a -e 1-3 --links
```

### When Cloudflare blocks a request

The script pauses and prints the exact link that was blocked. Open it in the browser named in `config.json`, wait for the page to load, then press Enter. The script re-reads the cookies and carries on. If it keeps happening, your `ua` probably no longer matches the browser.

## Tests

```sh
.venv/bin/python test_animepahe.py
```

## Disclaimer

The purpose of this script is to download anime episodes in order to watch them later in case when Internet is not available. Please do NOT copy or distribute downloaded anime episodes to any third party. Watch them and delete them afterwards. Please use this script at your own responsibility.
