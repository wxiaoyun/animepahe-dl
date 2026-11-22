#!/usr/bin/env python3
"""Download anime episodes from animepahe using its direct download links."""
import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from html import unescape
from pathlib import Path
from urllib.parse import quote

from curl_cffi import requests
from yt_dlp import YoutubeDL
from yt_dlp.cookies import extract_cookies_from_browser
from yt_dlp.networking.impersonate import ImpersonateTarget

HOST = 'https://animepahe.pw'
COOKIE_DOMAINS = ('animepahe', 'kwik', 'pahe.win')
CONFIG = Path(__file__).resolve().parent / 'config.json'

log = logging.getLogger('animepahe')


class Fatal(Exception):
    """Stops the whole run."""


# ---------- pure helpers (covered by test_animepahe.py) ----------

def parse_browser(spec):
    """Split a yt-dlp style BROWSER[+KEYRING][:PROFILE] spec."""
    m = re.fullmatch(r'(?P<name>[^+:]+)(?:\+(?P<keyring>[^:]+))?(?::(?P<profile>.+))?', spec.strip())
    if not m:
        raise Fatal(f'bad "browser" in config.json: {spec!r}')
    profile = m['profile'] and os.path.expanduser(m['profile'])
    return m['name'].lower(), profile, m['keyring'] and m['keyring'].upper()


def parse_links(play_html):
    """Direct download links on a play page, as dicts with url, fansub, resolution, audio."""
    links = []
    for url, body in re.findall(r'<a href="(https://pahe\.win/\w+)"[^>]*>(.*?)</a>', play_html, re.S):
        res = re.search(r'(\d+)p', body)
        audio = re.search(r'text-capitalize">\s*(\w+)\s*<', body)
        text = unescape(re.sub(r'<[^>]+>', ' ', body))
        links.append({
            'url': url,
            'fansub': text.split('·')[0].strip(),
            'resolution': int(res[1]) if res else 0,
            'audio': audio[1].lower() if audio else 'jpn',
        })
    return links


def parse_streams(play_html):
    """Stream buttons on a play page (non-AV1), in the same shape as parse_links."""
    streams = []
    for tag in re.findall(r'<button[^>]*data-src="https://kwik\.\w+/e/[^>]*>', play_html):
        attr = dict(re.findall(r'data-([\w-]+)="([^"]*)"', tag))
        if attr.get('av1') == '1':
            continue
        streams.append({
            'url': attr['src'],
            'fansub': attr.get('fansub', ''),
            'resolution': int(attr['resolution']) if attr.get('resolution', '').isdigit() else 0,
            'audio': attr.get('audio', 'jpn').lower(),
        })
    return streams


def unpack_packer(html):
    """Decode Dean Edwards' p.a.c.k.e.r. JS by word substitution, without running it."""
    m = re.search(r"\}\('(.*)',(\d+),(\d+),'(.*?)'\.split\('\|'\)", html, re.S)
    if not m:
        raise ValueError('page has no packed script')
    payload, base, count, words = m[1], int(m[2]), int(m[3]), m[4].split('|')
    digits = '0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ'

    def encode(n):
        return (encode(n // base) if n >= base else '') + digits[n % base]
    table = {encode(i): words[i] or encode(i) for i in range(count)}
    return re.sub(r'\b\w+\b', lambda w: table.get(w[0], w[0]), payload)


def pick_link(links, audio, max_resolution=None):
    """Highest resolution not above max_resolution in the wanted audio. Ties keep page order (first fansub)."""
    ok = [l for l in links if l['audio'] == audio and (max_resolution is None or l['resolution'] <= max_resolution)]
    return max(ok, key=lambda l: l['resolution'], default=None)


def decode_kwik(html):
    """Decode the obfuscated script on a kwik /f/ page into (form action, _token)."""
    m = re.search(r'\}\("(\w+)",\d+,"(\w+)",(\d+),(\d+),\d+\)\)', html)
    if not m:
        raise ValueError('kwik page has no encoded form')
    h, alphabet, offset, base = m[1], m[2], int(m[3]), int(m[4])
    chars = [chr(int(''.join(str(alphabet.index(c)) for c in part), base) - offset)
             for part in h.split(alphabet[base])[:-1]]
    form = ''.join(chars).encode('latin1').decode('utf8')
    action = re.search(r'action="([^"]+)"', form)
    token = re.search(r'name="_token" value="([^"]+)"', form)
    if not (action and token):
        raise ValueError('decoded kwik form has no action or _token')
    return action[1], token[1]


def parse_episodes(spec, available):
    """Pick episode numbers from `available` using "1,3-5,*" syntax. Returns (picked, unknown)."""
    have = sorted(set(available))
    picked, unknown = set(), []
    for part in filter(None, (p.strip() for p in spec.split(','))):
        if part == '*':
            picked.update(have)
        elif '-' in part:
            lo, hi = (float(x) for x in part.split('-', 1))
            picked.update(e for e in have if lo <= e <= hi)
        elif float(part) in have:
            picked.add(float(part))
        else:
            unknown.append(part)
    return sorted(picked), unknown


def episode_label(num, width):
    """Zero-padded episode number: 1 -> "01", 12.5 -> "12.5"."""
    whole, _, frac = f'{num:g}'.partition('.')
    return whole.zfill(width) + (f'.{frac}' if frac else '')


def folder_name(title):
    """Title as a folder name that also works on NTFS/exFAT drives: whitespace and \\/:*?"<>| become _,
    and trailing dots (which Windows filesystems reject) are dropped."""
    return re.sub(r'[\s\\/:*?"<>|]', '_', title).rstrip('.')


def is_challenge(r):
    return r.status_code in (403, 503) and (
        r.headers.get('cf-mitigated') == 'challenge' or 'Just a moment' in r.text)


# ---------- network ----------

class _YtdlpLogger:
    """Routes yt-dlp output into our log. yt-dlp warns once per undecryptable cookie (hundreds), and
    reports retries it recovers from as errors, so warnings go to debug and errors to warning. Real
    failures are logged by the caller."""

    def __init__(self, stage):
        self.stage = stage

    def debug(self, msg, *_, **__):
        log.debug('stage=%s %s', self.stage, msg)
    info = warning = debug

    def error(self, msg, *_, **__):
        log.warning('stage=%s %s', self.stage, msg)


class Client:
    """HTTP with browser cookies. One curl session per thread, since curl handles are not thread safe."""

    def __init__(self, ua, browser):
        self.ua, self.browser = ua, browser
        self.local = threading.local()
        self.lock = threading.Lock()
        # ponytail: one stream download at a time globally, the stream CDN answers 429 to concurrency.
        self.stream_lock = threading.Lock()
        self.gen = 0
        self.dead = False
        self.load_cookies()

    def load_cookies(self, level=logging.INFO):
        name, profile, keyring = parse_browser(self.browser)
        log.log(level, 'stage=cookies target=%s', self.browser)
        try:
            jar = extract_cookies_from_browser(name, profile, logger=_YtdlpLogger('cookies'), keyring=keyring)
        except Exception as e:
            raise Fatal(f'stage=cookies browser={self.browser} error={e}') from e
        self.cookies = [(c.domain, c.name, c.value) for c in jar if any(d in c.domain for d in COOKIE_DOMAINS)]
        log.log(level, 'stage=cookies status=loaded count=%d', len(self.cookies))
        if not any(n == 'cf_clearance' and 'animepahe' in d for d, n, _ in self.cookies):
            # yt-dlp skips cookies it cannot decrypt (e.g. keyring unavailable) without failing.
            log.warning('stage=cookies status=no_cf_clearance target=%s hint="open %s in that browser, '
                        'or check its keyring with -d"', self.browser, HOST)

    def _session(self):
        if getattr(self.local, 'gen', None) != self.gen:
            s = requests.Session(impersonate='chrome', headers={'User-Agent': self.ua})
            for domain, name, value in self.cookies:
                s.cookies.set(name, value, domain=domain)
            self.local.session, self.local.gen = s, self.gen
        return self.local.session

    def request(self, method, url, stage, **kw):
        for attempt in (1, 2):
            gen = self.gen
            log.info('stage=%s method=%s target=%s', stage, method, url)
            r = self._session().request(method, url, **kw)
            if not is_challenge(r):
                if r.status_code >= 400:
                    raise RuntimeError(f'stage={stage} target={url} api_status={r.status_code}')
                return r
            if attempt == 2:
                raise Fatal(f'stage={stage} target={url} status=still_blocked_by_cloudflare')
            self._solve(url, gen)

    def _solve(self, url, gen):
        """Ask the user to pass the Cloudflare check in their browser, then reload cookies once for all threads."""
        with self.lock:
            if self.dead:
                raise Fatal('stage=cloudflare status=aborted')
            if self.gen != gen:
                return  # another thread already refreshed cookies
            if not sys.stdin.isatty():
                self.dead = True
                raise Fatal(f'stage=cloudflare target={url} status=blocked, open it in your browser and re-run')
            print(f'\nCloudflare blocked: {url}\n'
                  f'Open that link in your browser ({self.browser}) until the page loads, then press Enter.\n'
                  f'(The browser can take about 30 s to save the new cookie, the script waits for it.)\n'
                  f'If this keeps happening, set "ua" in {CONFIG} to that browser\'s user agent.',
                  file=sys.stderr)
            try:
                input()
            except EOFError:
                self.dead = True
                raise Fatal('stage=cloudflare status=aborted')
            # Chromium writes cookies to disk in batches (about 20-30 s after the page loads), so poll.
            old, deadline = set(self.cookies), time.monotonic() + 60
            log.info('stage=cloudflare status=waiting_for_new_cookies timeout=60s')
            self.load_cookies(logging.DEBUG)
            while set(self.cookies) == old and time.monotonic() < deadline:
                time.sleep(3)
                self.load_cookies(logging.DEBUG)
            log.info('stage=cloudflare status=%s', 'cookies_changed' if set(self.cookies) != old else 'timeout')
            self.gen += 1

    def get_json(self, url, stage):
        r = self.request('GET', url, stage)
        try:
            return r.json()
        except ValueError as e:
            raise RuntimeError(f'stage={stage} target={url} error=not_json') from e

    def search(self, query):
        return self.get_json(f'{HOST}/api?m=search&q={quote(query)}', 'search').get('data', [])

    def title(self, anime_id):
        html = self.request('GET', f'{HOST}/anime/{anime_id}', 'anime_page').text
        m = re.search(r'<h1[^>]*>\s*<span[^>]*>(.*?)</span>', html, re.S)
        if not m:
            raise Fatal(f'stage=anime_page target={anime_id} error=title_not_found')
        return unescape(m[1]).strip()

    def episodes(self, anime_id):
        eps, page, last = [], 1, 1
        while page <= last:
            d = self.get_json(f'{HOST}/api?m=release&id={anime_id}&sort=episode_asc&page={page}', 'release')
            eps += d.get('data', [])
            last, page = d.get('last_page', 1), page + 1
        return eps

    def resolve(self, pahe_url):
        """pahe.win link -> direct MP4 URL."""
        r = self.request('GET', pahe_url, 'pahe')
        m = re.search(r'https://kwik\.\w+/f/\w+', r.text)
        if not m:
            raise RuntimeError(f'stage=pahe target={pahe_url} error=no_kwik_link')
        kwik = m[0]
        action, token = decode_kwik(self.request('GET', kwik, 'kwik_page', headers={'Referer': pahe_url}).text)
        r = self.request('POST', action, 'kwik_post', data={'_token': token},
                         headers={'Referer': kwik}, allow_redirects=False)
        if r.status_code != 302 or 'location' not in r.headers:
            raise RuntimeError(f'stage=kwik_post target={action} api_status={r.status_code} error=no_redirect')
        return r.headers['location'], kwik

    def stream_playlist(self, kwik_embed):
        """kwik /e/ embed link -> HLS playlist URL."""
        html = self.request('GET', kwik_embed, 'kwik_embed', headers={'Referer': f'{HOST}/'}).text
        m = re.search(r"https://[^'\"\\\s]+\.m3u8", unpack_packer(html))
        if not m:
            raise RuntimeError(f'stage=kwik_embed target={kwik_embed} error=no_m3u8')
        return m[0]

    def download_stream(self, playlist, dest, progress):
        """Fetch an HLS playlist into dest with yt-dlp. Slow, and serialized across threads."""
        seen = {'bytes': 0}

        def hook(d):
            now = d.get('downloaded_bytes') or 0
            progress.add(max(now - seen['bytes'], 0))
            seen['bytes'] = now

        opts = {
            'outtmpl': str(dest), 'quiet': True, 'noprogress': True, 'logger': _YtdlpLogger('stream_download'),
            'http_headers': {'Referer': 'https://kwik.cx/', 'User-Agent': self.ua},
            'impersonate': ImpersonateTarget.from_str('chrome'), 'progress_hooks': [hook],
        }
        with self.stream_lock:
            log.info('stage=stream_download method=GET target=%s', playlist)
            with YoutubeDL(opts) as ydl:
                if ydl.download([playlist]):
                    raise RuntimeError(f'stage=stream_download target={playlist} error=yt-dlp_failed')
        return seen['bytes']

    def download(self, url, referer, dest, progress):
        """Stream url into dest, resuming dest.part if present. Returns bytes written this run."""
        part = dest.with_name(dest.name + '.part')
        have = part.stat().st_size if part.exists() else 0
        headers = {'Referer': referer}
        if have:
            headers['Range'] = f'bytes={have}-'
        log.info('stage=download method=GET target=%s resume_from=%d', url.split('?')[0], have)
        with self._session().stream('GET', url, headers=headers) as r:
            if r.status_code == 416:  # nothing left to fetch
                part.rename(dest)
                return 0
            if r.status_code not in (200, 206):
                raise RuntimeError(f'stage=download api_status={r.status_code}')
            if r.status_code == 200:
                have = 0
            total = have + int(r.headers.get('content-length', 0))
            written = 0
            with open(part, 'ab' if have else 'wb') as f:
                for chunk in r.iter_content():
                    f.write(chunk)
                    written += len(chunk)
                    progress.add(len(chunk))
        if total and part.stat().st_size != total:
            raise RuntimeError(f'stage=download error=short_file got={part.stat().st_size} want={total}')
        part.rename(dest)
        return written


class Progress:
    """Combined download speed, logged every few seconds while anything downloads."""

    def __init__(self, every=5):
        self.lock, self.bytes, self.active, self.every = threading.Lock(), 0, 0, every
        self.stop = threading.Event()
        threading.Thread(target=self._report, daemon=True).start()

    def add(self, n):
        with self.lock:
            self.bytes += n

    def _report(self):
        last = 0
        while not self.stop.wait(self.every):
            with self.lock:
                done, active = self.bytes, self.active
            if active:
                log.info('stage=progress active=%d speed=%.0fMbit/s total=%.0fMB',
                         active, (done - last) * 8 / 1e6 / self.every, done / 1e6)
            last = done


# ---------- CLI ----------

def choose(titles):
    """Index of the user's pick among titles: fzf if installed, else a numbered prompt."""
    if len(titles) == 1:
        return 0
    if shutil.which('fzf'):
        lines = '\n'.join(f'{i}\t{t}' for i, t in enumerate(titles))
        r = subprocess.run(['fzf', '--delimiter=\t', '--with-nth=2..'], input=lines, capture_output=True, text=True)
        if r.returncode != 0:
            raise Fatal('no anime selected')
        return int(r.stdout.split('\t', 1)[0])
    for i, t in enumerate(titles, 1):
        print(f'{i:3}) {t}', file=sys.stderr)
    while True:
        s = input('Pick a number: ').strip()
        if s.isdigit() and 1 <= int(s) <= len(titles):
            return int(s) - 1


def load_config():
    try:
        cfg = json.loads(CONFIG.read_text())
    except (OSError, ValueError) as e:
        raise Fatal(f'stage=config target={CONFIG} error={e}. See README for the format.') from e
    if not cfg.get('ua'):
        raise Fatal(f'stage=config target={CONFIG} error="ua" is missing')
    return cfg


def _timed(label, method, link, dest, progress, fetch):
    """Run fetch() with start and done log lines and the active-download count."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    log.info('episode=%s status=start method=%s audio=%s resolution=%dp fansub=%s',
             label, method, link['audio'], link['resolution'], link['fansub'])
    with progress.lock:
        progress.active += 1
    t0 = time.monotonic()
    try:
        n = fetch()
    finally:
        with progress.lock:
            progress.active -= 1
    dt = max(time.monotonic() - t0, 1e-6)
    log.info('episode=%s status=done method=%s size=%.0fMB time=%.1fs speed=%.0fMbit/s target=%s',
             label, method, dest.stat().st_size / 1e6, dt, n * 8 / 1e6 / dt, dest)


def run_episode(client, ep, anime_id, out_dir, width, args, progress):
    """Direct download first. Stream when no direct link matches or the direct path fails (not Cloudflare)."""
    label = episode_label(float(ep['episode']), width)
    dest = out_dir / f'{label}.mp4'
    if dest.exists() and not args.links:
        log.info('episode=%s status=skipped reason=exists target=%s', label, dest)
        return True
    stage = 'mkdir'
    try:
        if not args.links:
            # Before any download attempt, so a local error (e.g. a name the filesystem rejects) is not
            # mistaken for a direct download failure and retried through the stream.
            out_dir.mkdir(parents=True, exist_ok=True)
        stage = 'play'
        play = client.request('GET', f'{HOST}/play/{anime_id}/{ep["session"]}', stage).text
        stage = 'select'
        links, streams = parse_links(play), parse_streams(play)
        link = pick_link(links, args.audio, args.resolution)
        if link:
            try:
                stage = 'resolve'
                url, referer = client.resolve(link['url'])
                if args.links:
                    print(url, flush=True)
                    return True
                stage = 'download'
                _timed(label, 'direct', link, dest, progress, lambda: client.download(url, referer, dest, progress))
                return True
            except Fatal:
                raise
            except Exception as e:
                log.warning('episode=%s stage=%s status=direct_failed error=%s fallback=stream', label, stage, e)

        stage = 'select'
        stream = pick_link(streams, args.audio, args.resolution)
        if not stream:
            have = sorted({f'{l["audio"]}/{l["resolution"]}p' for l in links + streams})
            log.warning('episode=%s stage=select status=no_match audio=%s resolution=%s available=%s',
                        label, args.audio, args.resolution or 'highest', ','.join(have) or 'none')
            return False
        if not link:
            log.warning('episode=%s stage=select status=no_direct_link fallback=stream', label)
        stage = 'stream_resolve'
        playlist = client.stream_playlist(stream['url'])
        if args.links:
            print(playlist, flush=True)
            return True
        stage = 'stream_download'
        # Own file name: yt-dlp's partial files must not mix with a direct download's .part.
        tmp = dest.with_name(f'{label}.stream.mp4')

        def fetch():
            n = client.download_stream(playlist, tmp, progress)
            tmp.rename(dest)
            return n
        _timed(label, 'stream', stream, dest, progress, fetch)
        return True
    except Fatal:
        raise
    except Exception as e:
        log.error('episode=%s stage=%s status=failed error=%s', label, stage, e)
        return False


def main(argv=None):
    p = argparse.ArgumentParser(description='Download anime from animepahe.')
    p.add_argument('query', nargs='?', help='anime title to search for')
    p.add_argument('--id', help='Anime ID (skips search)')
    p.add_argument('-e', '--episodes', help='episodes, e.g. "1,3-5" or "*" (asks if omitted)')
    p.add_argument('-a', '--audio', default='jpn', choices=['jpn', 'eng'], help='audio (default: jpn)')
    p.add_argument('-r', '--resolution', type=int, help='max resolution, e.g. 720 (default: highest)')
    p.add_argument('-j', '--jobs', type=int, default=4, help='episodes downloaded at once (default: 4)')
    p.add_argument('-o', '--out', help='output directory (default: "out_dir" in config.json, else .)')
    p.add_argument('--links', action='store_true', help='print direct MP4 links instead of downloading')
    p.add_argument('-d', '--debug', action='store_true', help='debug logging')
    args = p.parse_args(argv)
    if not (args.query or args.id):
        p.error('give a search query or --id')

    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO, stream=sys.stderr,
                        format='%(asctime)s %(levelname)s %(message)s', datefmt='%H:%M:%S')
    try:
        cfg = load_config()
        client = Client(cfg['ua'], cfg.get('browser', 'chrome'))
        if args.id:
            anime_id, title = args.id, client.title(args.id)
        else:
            results = client.search(args.query)
            if not results:
                raise Fatal(f'stage=search query={args.query!r} status=no_results')
            hit = results[choose([r['title'] for r in results])]
            anime_id, title = hit['session'], hit['title']
        log.info('stage=anime id=%s title=%s', anime_id, title)

        eps = client.episodes(anime_id)
        if not eps:
            raise Fatal(f'stage=release id={anime_id} status=no_episodes')
        by_num = {float(e['episode']): e for e in eps}
        spec = args.episodes
        if not spec:
            for e in eps:
                print(f'E{e["episode"]:<5} {e.get("created_at", "")}', file=sys.stderr)
            spec = input('Episodes to download (e.g. 1,3-5 or *): ')
        picked, unknown = parse_episodes(spec, by_num)
        for u in unknown:
            log.warning('stage=episodes episode=%s status=not_found', u)
        if not picked:
            raise Fatal('stage=episodes status=nothing_selected')

        width = max(2, len(str(int(max(by_num)))))
        out_dir = Path(args.out or cfg.get('out_dir') or '.').expanduser() / folder_name(title)
        progress = Progress()
        with ThreadPoolExecutor(max(1, args.jobs)) as pool:
            futures = [pool.submit(run_episode, client, by_num[n], anime_id, out_dir, width, args, progress)
                       for n in picked]
            try:
                ok = [f.result() for f in futures]
            except Fatal:
                client.dead = True
                pool.shutdown(cancel_futures=True)
                raise
            except KeyboardInterrupt:
                # Worker threads can't be interrupted. Exiting now is safe because .part files resume.
                log.warning('stage=summary status=interrupted')
                os._exit(130)
        progress.stop.set()
        failed = ok.count(False)
        log.info('stage=summary episodes=%d ok=%d failed=%d', len(ok), len(ok) - failed, failed)
        return 1 if failed else 0
    except Fatal as e:
        log.error('%s', e)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == '__main__':
    sys.exit(main())
