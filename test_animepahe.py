"""Self-check for the parsing and selection logic. Run: .venv/bin/python test_animepahe.py"""
from animepahe import (decode_kwik, episode_label, folder_name, parse_browser, parse_episodes, parse_links,
                       pick_link)

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
    assert folder_name("Frieren: Beyond Journey's End") == "Frieren:_Beyond_Journey's_End"
    assert folder_name('A/B  C') == 'A_B__C'


def test_parse_browser():
    assert parse_browser('chrome') == ('chrome', None, None)
    assert parse_browser('chromium+kwallet6:/tmp/x') == ('chromium', '/tmp/x', 'KWALLET6')


if __name__ == '__main__':
    for name, fn in list(globals().items()):
        if name.startswith('test_'):
            fn()
            print('ok', name)
