#!/usr/bin/env python3
"""Compile the BlackLemon Studios folder structure into data/data.js.

Run:  python3 scripts/build.py

Download counter (per app product): drop a `download.json` (preferred) or the
legacy `counter.json` into the product folder, e.g. Inside/products/nexus/download.json:

    { "baseline": 74 }

The build reads the REAL download counts of that app's GitHub release assets,
anchors them so the number reads `baseline` right now, and grows it as real
downloads come in afterwards. Set GITHUB_TOKEN in the environment for higher
rate limits (optional). With no network/token it falls back to the baseline so
the site still builds. Re-run the build (locally or via the GitHub Action in
.github/workflows/build.yml) to refresh the live counts.
"""
import os, re, json
import urllib.request, urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMG = ('.jpg', '.jpeg', '.png', '.webp', '.gif')
VID = ('.mp4', '.webm', '.mov')
FONT = ('.ttf', '.otf', '.woff2', '.woff')
GH_TOKEN = os.environ.get('GITHUB_TOKEN', '').strip()

# Products pinned to the top of their availability group (Coming Soon list).
PIN_TOP = ('revisionary',)


def rel(p):
    return os.path.relpath(p, ROOT).replace('\\', '/')


def rj(p):
    if not os.path.isfile(p):
        return {}
    try:
        return json.load(open(p, encoding='utf-8'))
    except Exception as e:
        print('  ! bad json', rel(p), e)
        return {}


def ints(v, d=0):
    try:
        return int(re.sub(r'[^0-9-]', '', str(v)))
    except Exception:
        return d


def okey(n):
    m = re.search(r'(\d+)', os.path.splitext(n)[0])
    return (0, int(m.group(1)), n.lower()) if m else (1, 0, n.lower())


def find_font_file(folder, font_value):
    """A dropped-in title font file, if any. Prefers one whose name matches the
    `font` field; otherwise the first font file found."""
    files = [f for f in sorted(os.listdir(folder))
             if f.lower().endswith(FONT) and os.path.isfile(os.path.join(folder, f))]
    if not files:
        return ''
    base = os.path.splitext(str(font_value or ''))[0].strip().lower()
    for f in files:
        if os.path.splitext(f)[0].lower() == base:
            return rel(os.path.join(folder, f))
    return rel(os.path.join(folder, files[0]))


def stem(folder, name):
    if not os.path.isdir(folder):
        return ''
    for f in os.listdir(folder):
        if os.path.splitext(f)[0].lower() == name.lower():
            return rel(os.path.join(folder, f))
    return ''


def section_items(folder):
    out = []
    if not os.path.isdir(folder):
        return out
    for fn in sorted(os.listdir(folder), key=okey):
        fp = os.path.join(folder, fn)
        ext = os.path.splitext(fn)[1].lower()
        if not os.path.isfile(fp):
            continue
        if ext in IMG:
            out.append({'type': 'image', 'src': rel(fp)})
        elif ext in VID:
            out.append({'type': 'video', 'src': rel(fp)})
        elif ext == '.txt':
            out.append({'type': 'text', 'text': open(fp, encoding='utf-8', errors='replace').read().strip()})
    return out


# ---------- image luminance (for the auto-contrast header) ----------
def image_top_dark(path):
    """True if the TOP band of an image (where the floating header sits) is dark.
    Returns None when it can't be measured so the caller can default to light."""
    if not path:
        return None
    full = os.path.join(ROOT, path)
    if not os.path.isfile(full):
        return None
    try:
        from PIL import Image
    except Exception:
        return None
    try:
        im = Image.open(full).convert('RGB')
        w, h = im.size
        band = im.crop((0, 0, w, max(1, int(h * 0.30)))).resize((48, 16))
        raw = band.tobytes()  # packed RGB
        n = len(raw) // 3
        if not n:
            return None
        total = sum(0.299 * raw[i] + 0.587 * raw[i + 1] + 0.114 * raw[i + 2]
                    for i in range(0, n * 3, 3))
        return (total / n) < 120
    except Exception as e:
        print('  ! luminance failed', path, e)
        return None


# ---------- download counter ----------
def gh_json(url):
    req = urllib.request.Request(url, headers={
        'Accept': 'application/vnd.github+json',
        'User-Agent': 'blacklemon-build',
    })
    if GH_TOKEN:
        req.add_header('Authorization', 'Bearer ' + GH_TOKEN)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode('utf-8'))


def repo_download_total(urls):
    """Sum download_count of every release asset across the repos these GitHub
    release URLs point to. Returns an int, or None if it can't be fetched."""
    repos = []
    for u in urls:
        m = re.match(r'https?://github\.com/([^/]+)/([^/]+)/releases/', u or '')
        if m:
            pair = (m.group(1), m.group(2))
            if pair not in repos:
                repos.append(pair)
    if not repos:
        return None
    total = 0
    try:
        for owner, repo in repos:
            data = gh_json('https://api.github.com/repos/%s/%s/releases?per_page=100' % (owner, repo))
            for release in data:
                for asset in release.get('assets', []):
                    total += int(asset.get('download_count') or 0)
        return total
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError, TypeError) as e:
        print('  ! download-count fetch failed: ' + str(e))
        return None


def build_counter(cfg, urls, prev):
    """baseline = the number to show right now; it then grows with real downloads.
    Raising the baseline in download.json always takes effect, and the live
    growth already accumulated is carried forward so the number never drops."""
    baseline = ints(cfg.get('baseline'), 0)
    prev = prev or {}
    prev_anchor = prev.get('anchor')
    prev_downloads = prev.get('downloads')
    prev_baseline = prev.get('baseline')
    live = repo_download_total([u for u in urls if u])
    if live is not None:
        anchor = prev_anchor if isinstance(prev_anchor, int) else live
        downloads = baseline + max(0, live - anchor)
    else:
        # No live data this run: honour the (possibly raised) baseline and
        # carry forward any growth recorded on top of the previous baseline.
        anchor = prev_anchor if isinstance(prev_anchor, int) else None
        carried = 0
        if isinstance(prev_downloads, int) and isinstance(prev_baseline, int):
            carried = max(0, prev_downloads - prev_baseline)
        downloads = baseline + carried
    return {'baseline': baseline, 'anchor': anchor, 'downloads': downloads}


def load_prev_counters():
    """Read the previously built counters so live counts only ever grow."""
    p = os.path.join(ROOT, 'data', 'data.js')
    if not os.path.isfile(p):
        return {}
    try:
        txt = open(p, encoding='utf-8').read()
        i, j = txt.find('{'), txt.rfind('}')
        data = json.loads(txt[i:j + 1])
        return {pr.get('slug'): pr.get('counter')
                for pr in data.get('products', []) if pr.get('counter')}
    except Exception:
        return {}


def counter_cfg(folder):
    """download.json is the canonical name; counter.json kept as a fallback."""
    for name in ('download.json', 'counter.json'):
        p = os.path.join(folder, name)
        if os.path.isfile(p):
            return rj(p)
    return None


# ---------- products ----------
def build_product(slug, folder, prev_counters):
    d = rj(os.path.join(folder, 'text.json'))
    t = (d.get('type') or 'website').lower()
    header = stem(folder, 'header')
    e = dict(slug=slug, name=d.get('name') or slug, type=t, font=d.get('font', ''),
             fontWeight=d.get('fontWeight', ''), accent=d.get('accent') or '#1b1b1b',
             status=d.get('status', 'Available Now'), disclaimer=d.get('disclaimer', ''),
             order=ints(d.get('order'), 0) if d.get('order') not in (None, '') else None,
             header=header, grid=stem(folder, 'grid'), video=stem(folder, 'video'))
    e['available'] = 'yes' if e['status'] == 'Available Now' else 'no'
    e['headerDark'] = bool(image_top_dark(header))
    e['fontFile'] = find_font_file(folder, e['font'])
    urls = []
    if t == 'app':
        m = d.get('mac') or {}
        e['mac'] = {'silicon': m.get('silicon', ''), 'intel': m.get('intel', '')}
        e['windows'] = d.get('windows', '')
        urls = [e['mac'].get('silicon', ''), e['mac'].get('intel', ''), e['windows']]
    else:
        e['url'] = d.get('url', '')
        e['button'] = d.get('button', '')
    cfg = counter_cfg(folder)
    if cfg is not None:
        e['counter'] = build_counter(cfg, urls, (prev_counters or {}).get(slug))
    layout = d.get('layout') or ['Highlights', 'Look Closer', 'And so much more']
    e['content'] = [{'name': s, 'items': section_items(os.path.join(folder, s))}
                    for s in layout if os.path.isdir(os.path.join(folder, s))]
    return e


def order_key(p):
    """Available Now first, then Coming Soon; pinned slugs top their group."""
    avail_rank = 0 if p.get('available') == 'yes' else 1
    pin = 0 if p.get('slug') in PIN_TOP else 1
    order_val = p['order'] if isinstance(p.get('order'), int) else 10 ** 6
    return (avail_rank, pin, order_val, (p.get('name') or p.get('slug') or '').lower())


def build_products(prev_counters):
    base = os.path.join(ROOT, 'Inside', 'products')
    out = []
    if os.path.isdir(base):
        for s in sorted(os.listdir(base)):
            f = os.path.join(base, s)
            if os.path.isdir(f) and not s.startswith('.'):
                out.append(build_product(s, f, prev_counters))
    out.sort(key=order_key)
    return out


def build_blogs():
    base = os.path.join(ROOT, 'blogs')
    out = []
    if not os.path.isdir(base):
        return out
    for s in sorted(os.listdir(base)):
        f = os.path.join(base, s)
        if not os.path.isdir(f) or s.startswith('.'):
            continue
        d = rj(os.path.join(f, 'blog.json'))
        imgs = [rel(os.path.join(f, x)) for x in sorted(os.listdir(f), key=okey)
                if os.path.splitext(x)[1].lower() in IMG]
        out.append(dict(slug=s, title=d.get('title') or s, author=d.get('author', ''),
                        date=d.get('date', ''), cover=imgs[0] if imgs else '',
                        images=imgs, body=d.get('body', '')))
    return out


def build_events():
    base = os.path.join(ROOT, 'Inside', 'events')
    out = []
    if not os.path.isdir(base):
        return out
    for fn in sorted(os.listdir(base), key=okey):
        fp = os.path.join(base, fn)
        ext = os.path.splitext(fn)[1].lower()
        if not os.path.isfile(fp):
            continue
        if ext in IMG:
            out.append({'type': 'image', 'src': rel(fp)})
        elif ext in VID:
            out.append({'type': 'video', 'src': rel(fp)})
    return out


def esc_html(s):
    return s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def md_to_html(md):
    """Minimal markdown -> HTML for the legal docs."""
    def inline(t):
        t = esc_html(t)
        t = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', t)
        t = re.sub(r'(?<!_)_([^_]+)_(?!_)', r'<em>\1</em>', t)
        return t
    out = []
    lines = md.split('\n')
    i = 0
    while i < len(lines):
        ln = lines[i].rstrip()
        if not ln.strip():
            i += 1
            continue
        if ln.startswith('### '):
            out.append('<h3>' + inline(ln[4:].strip()) + '</h3>')
        elif ln.startswith('## '):
            out.append('<h2>' + inline(ln[3:].strip()) + '</h2>')
        elif ln.startswith('# '):
            out.append('<h1>' + inline(ln[2:].strip()) + '</h1>')
        elif ln.lstrip().startswith(('- ', '* ')):
            items = []
            while i < len(lines) and lines[i].lstrip().startswith(('- ', '* ')):
                items.append('<li>' + inline(lines[i].lstrip()[2:].strip()) + '</li>')
                i += 1
            out.append('<ul>' + ''.join(items) + '</ul>')
            continue
        else:
            buf = [ln]
            i += 1
            while i < len(lines) and lines[i].strip() and not lines[i].lstrip().startswith(('#', '- ', '* ')):
                buf.append(lines[i].rstrip())
                i += 1
            out.append('<p>' + inline(' '.join(buf)) + '</p>')
            continue
        i += 1
    return '\n'.join(out)


def read_legal(name):
    p = os.path.join(ROOT, 'legal', name + '.md')
    if not os.path.isfile(p):
        return {'title': name.title(), 'html': ''}
    md = open(p, encoding='utf-8').read()
    m = re.search(r'^#\s+(.+)$', md, re.M)
    return {'title': (m.group(1).strip() if m else name.title()), 'html': md_to_html(md)}


def main():
    ins = os.path.join(ROOT, 'Inside')
    prev_counters = load_prev_counters()
    rv = rj(os.path.join(ROOT, 'data', 'reviews.json'))
    st = rv.get('stats') or {}
    cnt = ints(st.get('count'), len(rv.get('reviews', [])))
    ssum = ints(st.get('sum'), sum(ints(r.get('rating')) for r in rv.get('reviews', [])))
    hdr = stem(ins, 'logo')
    data = dict(
        intro=dict(zoom=stem(ins, 'Zoom'), lemon=stem(ins, 'lemon'), logo=hdr,
                   featured=rj(os.path.join(ins, 'header', 'text.json')).get('product', '')),
        products=build_products(prev_counters),
        contact={k: v for k, v in rj(os.path.join(ROOT, 'Footer', 'contact.json')).items()
                 if isinstance(v, str) and v.strip()},
        blogs=build_blogs(),
        reviews=rv.get('reviews', []),
        reviewStats=dict(count=cnt, sum=ssum, average=(round(ssum / cnt, 2) if cnt else 0)),
        events=build_events(),
        greeting={'text': rj(os.path.join(ins, 'greeting', 'text.json')).get('text', ''),
                  'logo': stem(ins, 'logo')},
        about={'signature': stem(ins, 'about-signature')},
        legal=dict(terms=read_legal('terms'), privacy=read_legal('privacy')),
        kitUid='4c7df81a89',
    )
    os.makedirs(os.path.join(ROOT, 'data'), exist_ok=True)
    open(os.path.join(ROOT, 'data', 'data.js'), 'w', encoding='utf-8').write(
        'window.BLACKLEMON_DATA = ' + json.dumps(data, indent=2, ensure_ascii=False) + ';\n')
    dl = [(p['slug'], p['counter']['downloads']) for p in data['products'] if p.get('counter')]
    print('Built data/data.js: products=%d blogs=%d contacts=%d reviews=%d/%d avg=%s featured=%s' % (
        len(data['products']), len(data['blogs']), len(data['contact']),
        len(data['reviews']), cnt, data['reviewStats']['average'], data['intro']['featured']))
    print('  order:', [p['slug'] + ('*' if p['available'] == 'yes' else '') for p in data['products']])
    if dl:
        print('  counters:', dl)


if __name__ == '__main__':
    main()
