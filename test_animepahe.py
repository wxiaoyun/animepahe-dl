"""Self-check for the parsing and selection logic. Run: .venv/bin/python test_animepahe.py"""
from animepahe import (decode_kwik, episode_label, folder_name, parse_browser, parse_episodes, parse_links,
                       parse_streams, pick_link, unpack_packer)

BUTTONS = '''
<button type="button" data-src="https://kwik.cx/e/s360" data-fansub="SEV" data-resolution="360" data-audio="jpn" data-av1="0" class="dropdown-item">
<button type="button" data-src="https://kwik.cx/e/s1080" data-fansub="SEV" data-resolution="1080" data-audio="jpn" data-av1="0" class="dropdown-item active">
<button type="button" data-src="https://kwik.cx/e/av1" data-fansub="SEV" data-resolution="1080" data-audio="jpn" data-av1="1" class="dropdown-item">
<button type="button" data-src="https://kwik.cx/e/e720" data-fansub="SEV" data-resolution="720" data-audio="eng" data-av1="0" class="dropdown-item">
'''

PLAY = '''
<a href="https://pahe.win/aaaaa" target="_blank" class="dropdown-item">SEV &middot; 360p (44MB) <span class="badge badge-primary">BD</span></a>
<a href="https://pahe.win/bbbbb" target="_blank" class="dropdown-item">SEV &middot; 1080p (172MB) <span class="badge badge-primary">BD</span></a>
<a href="https://pahe.win/ccccc" target="_blank" class="dropdown-item">Other &middot; 1080p (180MB)</a>
<a href="https://pahe.win/ddddd" target="_blank" class="dropdown-item">SEV &middot; 720p (100MB) <span class="badge badge-primary">BD</span> <span class="badge badge-warning text-capitalize">eng</span></a>
'''


def encode_kwik(text, alphabet='pbLiOQKvg', offset=7, base=4):
    """Inverse of decode_kwik, in the same shape as the real page script."""
    def digits(n):
        out = ''
        while True:
            out = alphabet[n % base] + out
            n //= base
            if not n:
                return out
    h = ''.join(digits(b + offset) + alphabet[base] for b in text.encode('utf8'))
    return f'var x;eval(function(h,u,n,t,e,r){{...}}("{h}",68,"{alphabet}",{offset},{base},54))'


def test_decode_kwik():
    form = '<form action="https://kwik.cx/d/XyZ" method="POST"><input type="hidden" name="_token" value="tok123">é'
    assert decode_kwik(encode_kwik(form)) == ('https://kwik.cx/d/XyZ', 'tok123')


def test_parse_and_pick():
    links = parse_links(PLAY)
    assert [(l['fansub'], l['resolution'], l['audio']) for l in links] == [
        ('SEV', 360, 'jpn'), ('SEV', 1080, 'jpn'), ('Other', 1080, 'jpn'), ('SEV', 720, 'eng')]
    assert pick_link(links, 'jpn')['url'].endswith('bbbbb')           # highest, first fansub wins the tie
    assert pick_link(links, 'jpn', 720)['url'].endswith('aaaaa')      # next lower when 720 is missing
    assert pick_link(links, 'eng')['url'].endswith('ddddd')
    assert pick_link(links, 'eng', 480) is None                       # never switches audio


def test_streams_and_packer():
    streams = parse_streams(BUTTONS)
    assert [s['url'][-5:] for s in streams] == ['/s360', 's1080', '/e720']   # AV1 dropped
    assert pick_link(streams, 'jpn')['url'].endswith('s1080')
    assert pick_link(streams, 'eng', 1080)['url'].endswith('e720')
    # 12 words, base 10: tokens "0".."b" exercise multi-digit lookups ("10", "11")
    words = 'var|x|y|z|a|b|c|d|e|f|source|m3u8'.split('|')
    html = "eval(function(p,a,c,k,e,d){...}('0 10=\\\\'https://h/s.11\\\\';',10,12,'%s'.split('|'),0,{}))" % '|'.join(words)
    assert unpack_packer(html) == "var source=\\\\'https://h/s.m3u8\\\\';"


def test_parse_episodes():
    have = [1, 2, 3, 4, 5, 12.5, 13]
    assert parse_episodes('1,3-4', have) == ([1, 3, 4], [])
    assert parse_episodes('12-13, 99', have) == ([12.5, 13], ['99'])
    assert parse_episodes('*', have) == (sorted(have), [])
    assert parse_episodes('12.5', have) == ([12.5], [])


def test_labels_and_names():
    assert episode_label(1.0, 2) == '01'
    assert episode_label(12.5, 2) == '12.5'
    assert episode_label(7.0, 3) == '007'
    assert folder_name("Frieren: Beyond Journey's End") == "Frieren__Beyond_Journey's_End"
    assert folder_name('A/B  C') == 'A_B__C'
    assert folder_name('Re:Zero - Why? <a|b> "c" *d\\e.') == 'Re_Zero_-_Why___a_b___c___d_e'


def test_parse_browser():
    assert parse_browser('chrome') == ('chrome', None, None)
    assert parse_browser('chromium+kwallet6:/tmp/x') == ('chromium', '/tmp/x', 'KWALLET6')


def test_rate_limit_backoff():
    import animepahe

    class Resp:
        def __init__(self, code, headers=None):
            self.status_code, self.headers, self.text = code, headers or {}, ''

    def client(codes):
        c = object.__new__(animepahe.Client)
        c.gen, c.RATE_LIMIT_WAITS = 0, (5, 15)
        c.request_interval, c.pace_lock, c.next_slot = 0.0, __import__('threading').Lock(), 0.0
        queue = list(codes)
        c._session = lambda: type('S', (), {'request': lambda self, *a, **k: queue.pop(0)})()
        return c, queue

    slept, real_sleep = [], animepahe.time.sleep
    animepahe.time.sleep = slept.append
    try:
        c, _ = client([Resp(429, {'retry-after': '2'}), Resp(429), Resp(200)])
        assert c.request('GET', 'u', 'search').status_code == 200
        assert slept == [2, 15]                                  # Retry-After wins, then fixed backoff
        c, _ = client([Resp(429)] * 3)
        try:
            c.request('GET', 'u', 'search')
            raise AssertionError('expected RuntimeError')
        except RuntimeError as e:
            assert 'api_status=429' in str(e)                    # gives up after the last wait
    finally:
        animepahe.time.sleep = real_sleep


def test_download_rejects_non_mp4():
    import contextlib, os, tempfile
    from pathlib import Path
    import animepahe

    class Resp:
        def __init__(self, code, chunks, headers):
            self.status_code, self._chunks, self.headers = code, chunks, headers

        def iter_content(self):
            return iter(self._chunks)

    def client(code, chunks, headers):
        c = object.__new__(animepahe.Client)
        session = type('S', (), {'stream': lambda self, *a, **k: contextlib.nullcontext(Resp(code, chunks, headers))})
        c._session = lambda: session()
        return c

    progress = type('P', (), {'add': lambda self, n: None})()
    mp4 = b'\x00\x00\x00\x20ftypisom' + b'x' * 100
    with tempfile.TemporaryDirectory() as d:
        dest = Path(d) / '01.mp4'
        html = b'<html>ads</html>'
        try:
            client(200, [html], {'content-type': 'text/html', 'content-length': str(len(html))}).download(
                'https://ad.example/x', 'r', dest, progress)
            raise AssertionError('expected RuntimeError')
        except RuntimeError as e:
            assert 'not_mp4' in str(e)
        assert not dest.exists() and not dest.with_name('01.mp4.part').exists()   # nothing written

        chunks = [mp4[:3], mp4[3:5], mp4[5:]]                                     # header split over chunks
        client(200, chunks, {'content-length': str(len(mp4))}).download('u', 'r', dest, progress)
        assert dest.read_bytes() == mp4

        part = Path(d) / '02.mp4.part'
        part.write_bytes(mp4[:50])                                                # resume: no header check
        client(206, [mp4[50:]], {'content-length': str(len(mp4) - 50)}).download('u', 'r', Path(d) / '02.mp4', progress)
        assert (Path(d) / '02.mp4').read_bytes() == mp4


def test_request_pacing():
    import threading, time
    import animepahe
    c = animepahe.Client.__new__(animepahe.Client)
    c.request_interval, c.pace_lock, c.next_slot = 0.1, threading.Lock(), 0.0
    stamps = []
    t0 = time.monotonic()
    workers = [threading.Thread(target=lambda: (c._pace(), stamps.append(time.monotonic() - t0))) for _ in range(5)]
    for w in workers:
        w.start()
    for w in workers:
        w.join()
    stamps.sort()
    gaps = [b - a for a, b in zip(stamps, stamps[1:])]
    assert all(g >= 0.09 for g in gaps), gaps                 # 5 threads at once still go out ~0.1 s apart
    assert stamps[0] < 0.05                                   # first request is not delayed
    c.request_interval, c.next_slot = 0.0, 0.0
    t1 = time.monotonic(); c._pace(); c._pace()
    assert time.monotonic() - t1 < 0.05                       # 0 disables pacing


if __name__ == '__main__':
    for name, fn in list(globals().items()):
        if name.startswith('test_'):
            fn()
            print('ok', name)
