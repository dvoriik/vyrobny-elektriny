"""RÚIAN (ČÚZK): územní prvky státu s definičními body a hranicemi, parcely po obcích."""
import io, json, zipfile, datetime as dt, collections, time, os
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
import pandas as pd
from lxml import etree
from pyproj import Transformer
from net import download_file

VDP = 'https://vdp.cuzk.gov.cz/vymenny_format/soucasna'
VF = 'urn:cz:isvs:ruian:schemas:VymennyFormatTypy:v1'
GML = 'http://www.opengis.net/gml/3.2'
PA = 'urn:cz:isvs:ruian:schemas:ParcelaIntTypy:v1'
KU = 'urn:cz:isvs:ruian:schemas:KatUzIntTypy:v1'
TR = Transformer.from_crs('EPSG:5514', 'EPSG:4326', always_xy=True)
UA = {'User-Agent': 'vyrobny-elektriny/1.0 (open data pipeline)'}


def _ln(t):
    return etree.QName(t).localname


def latest_date(max_back_days=70):
    """Datum nejnovějšího celostátního souboru (ČÚZK vydává k poslednímu dni měsíce)."""
    d = dt.date.today()
    for i in range(max_back_days):
        day = d - dt.timedelta(days=i)
        if (day + dt.timedelta(days=1)).day != 1:
            continue
        s = day.strftime('%Y%m%d')
        r = None
        for att in range(4):
            try:
                r = requests.head(f'{VDP}/{s}_ST_UKSG.xml.zip', headers=UA, timeout=30, allow_redirects=True); break
            except requests.RequestException:
                time.sleep(5 * (att + 1))
        if r is None:
            continue
        if r.status_code == 200:
            return s
    raise RuntimeError('Nenašel jsem aktuální soubor RÚIAN ST_UKSG')


def load_state(date, cache: Path):
    """Načte (a uloží do cache) kraje, okresy, obce, části obcí a k.ú. s body v WGS84 a hranice krajů/okresů."""
    out = cache / f'ruian_{date}'
    if (out / 'KatastralniUzemi.csv').exists():
        t = {k: pd.read_csv(out / f'{k}.csv', dtype=str) for k in ['KatastralniUzemi', 'Obec', 'CastObce', 'Okres', 'Vusc']}
        return t, json.load(open(out / 'hranice.json'))
    out.mkdir(parents=True, exist_ok=True)
    zpath = cache / f'{date}_ST_UKSG.xml.zip'
    download_file(f'{VDP}/{date}_ST_UKSG.xml.zip', zpath)
    z = zipfile.ZipFile(zpath)
    rows = collections.defaultdict(list); geoms = {}
    for ev, el in etree.iterparse(z.open(z.namelist()[0]), events=('end',), huge_tree=True):
        if etree.QName(el).namespace != VF or el.get(f'{{{GML}}}id') is None:
            continue
        kind = _ln(el.tag); r = {}
        for c in el:
            n = _ln(c.tag)
            if n in ('Kod', 'Nazev'):
                r[n] = c.text
            elif len(c) == 1 and len(c[0]) == 0 and _ln(c[0].tag) == 'Kod':
                r[n + '_Kod'] = c[0].text
            elif n == 'Geometrie':
                for g in c:
                    gn = _ln(g.tag)
                    if gn == 'DefinicniBod':
                        pos = g.find(f'.//{{{GML}}}pos')
                        if pos is not None:
                            x, y = map(float, pos.text.split()); lon, lat = TR.transform(x, y)
                            r['lon'] = round(lon, 6); r['lat'] = round(lat, 6)
                    elif gn.startswith('GeneralizovaneHranice') and kind in ('Okres', 'Vusc') and (kind, r.get('Kod')) not in geoms:
                        polys = []
                        for pl in g.iter(f'{{{GML}}}Polygon'):
                            rings = []
                            for pls in pl.iter(f'{{{GML}}}posList'):
                                v = list(map(float, pls.text.split()))
                                lo, la = TR.transform(v[0::2], v[1::2])
                                rings.append([[round(a, 5), round(b, 5)] for a, b in zip(lo, la)])
                            polys.append(rings)
                        geoms[(kind, r.get('Kod'))] = polys
        rows[kind].append(r)
        el.clear()
        while el.getprevious() is not None:
            del el.getparent()[0]
    t = {}
    for k in ['KatastralniUzemi', 'Obec', 'CastObce', 'Okres', 'Vusc']:
        t[k] = pd.DataFrame(rows[k]).astype(str).replace('nan', '')
        t[k].to_csv(out / f'{k}.csv', index=False)
    hr = {f'{k[0]}|{k[1]}': v for k, v in geoms.items()}
    json.dump(hr, open(out / 'hranice.json', 'w'))
    zpath.unlink()
    return {k: pd.read_csv(out / f'{k}.csv', dtype=str) for k in t}, hr


def _parse_obec(date, obec, want_kmen, tmpdir: Path, sess):
    url = f'{VDP}/{date}_OB_{obec}_UKSH.xml.zip'
    tmp = tmpdir / f'{obec}.zip'
    tmp.unlink(missing_ok=True)
    download_file(url, tmp, session=sess, retries=6, log=lambda *a: None)
    z = zipfile.ZipFile(tmp); res = []
    GID = f'{{{GML}}}id'; PTAG = f'{{{VF}}}Parcela'
    for ev, el in etree.iterparse(z.open(z.namelist()[0]), events=('end',), huge_tree=True):
        if el.get(GID) is None or not el.tag.startswith('{' + VF):
            continue
        if el.tag == PTAG:
            k = el.findtext(f'{{{PA}}}KatastralniUzemi/{{{KU}}}Kod')
            km = el.findtext(f'{{{PA}}}KmenoveCislo')
            if k in want_kmen and km and int(km) in want_kmen[k]:
                pod = el.findtext(f'{{{PA}}}PododdeleniCisla'); dr = el.findtext(f'{{{PA}}}DruhCislovaniKod')
                pos = el.find(f'{{{PA}}}Geometrie/{{{PA}}}DefinicniBod//{{{GML}}}pos')
                if pos is not None:
                    x, y = map(float, pos.text.split())
                    res.append((k, dr, int(km), int(pod) if pod else -1, x, y))
        el.clear()
        while el.getprevious() is not None:
            del el.getparent()[0]
    z.close(); tmp.unlink()
    return res


def parcel_points(date, wanted, ku_obec, cache: Path, workers=4, log=print, state: Path = None):
    """wanted: set of (ku, druh_or_None, kmen, pod_or_None) potřebných parcel.
    Cache: parcely.csv (nalezené body) + parcely_obce.csv (kdy byla obec naposledy stažena a s jakými kmenovými čísly).
    Obec se stahuje znovu jen tehdy, když potřebuje kmenové číslo, které při posledním stažení nehledala,
    nebo když od posledního stažení uplynulo víc než 365 dní."""
    state = state or cache
    pc = state / 'parcely.csv'; oc = state / 'parcely_obce.json'
    pts = pd.read_csv(pc, dtype={'ku': str, 'druh': str}) if pc.exists() else pd.DataFrame(columns=['ku', 'druh', 'kmen', 'pod', 'x', 'y'])
    seen = json.load(open(oc)) if oc.exists() else {}
    need = collections.defaultdict(lambda: collections.defaultdict(set))
    for ku, _, km, _ in wanted:
        ob = ku_obec.get(ku)
        if ob:
            need[ob][ku].add(km)
    today = dt.date.today()
    todo = []
    for ob, kus in need.items():
        s = seen.get(ob)
        fresh = s and (today - dt.date.fromisoformat(s['date'])).days <= 365
        if fresh and all(km in set(s['kmen'].get(ku, [])) for ku, kms in kus.items() for km in kms):
            continue
        if s:  # stáhnout i dříve hledaná čísla, ať cache o nic nepřijde
            for ku, kms in s['kmen'].items():
                need[ob][ku].update(kms)
        todo.append(ob)
    log(f'parcely: {len(need)} obcí potřeba, {len(todo)} ke stažení')
    tmpdir = cache / 'tmp'; tmpdir.mkdir(exist_ok=True)
    sess = requests.Session(); new = []; done = 0
    with ThreadPoolExecutor(workers) as ex:
        futs = {ex.submit(_parse_obec, date, ob, {k: set(v) for k, v in need[ob].items()}, tmpdir, sess): ob for ob in todo}
        for f in as_completed(futs):
            ob = futs[f]
            try:
                res = f.result()
            except Exception as e:
                log(f'  obec {ob}: chyba {e}'); continue
            new.append((ob, list(need[ob].keys()), res))
            seen[ob] = {'date': today.isoformat(), 'kmen': {k: sorted(v) for k, v in need[ob].items()}}
            done += 1
            if done % 250 == 0:
                log(f'  {done}/{len(todo)} obcí')
    if new:
        drop = {k for _, kus, _ in new for k in kus}
        pts = pts[~pts.ku.isin(drop)]
        add = pd.DataFrame([r for _, _, res in new for r in res], columns=['ku', 'druh', 'kmen', 'pod', 'x', 'y'])
        pts = pd.concat([pts, add], ignore_index=True)
        pts.to_csv(pc, index=False)
        json.dump(seen, open(oc, 'w'))
    return pts
