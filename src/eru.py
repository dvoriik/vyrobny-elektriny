"""Stažení a načtení dat ERÚ: datové sady technologických energetických zařízení (TEZ)
a seznam držitelů licencí, skupina 11 (výroba elektřiny)."""
import re, datetime as dt
from pathlib import Path
import requests
import pandas as pd
from lxml import etree

BASE = 'https://eru.gov.cz'
LISTING = BASE + '/zpristupnena-data'
UA = {'User-Agent': 'vyrobny-elektriny/1.0 (open data pipeline)'}


def _get(url, **kw):
    r = requests.get(url, headers=UA, timeout=kw.pop('timeout', 120), **kw)
    r.raise_for_status()
    return r


def _ddmmyyyy(s):
    return dt.date(int(s[4:]), int(s[2:4]), int(s[:2]))


def find_latest(tez_date=None):
    """Najde nejnovější TEZ a k němu nejbližší seznam licencí. Vrací dict s URL a daty."""
    html = _get(LISTING).text
    tez = {_ddmmyyyy(m): '/' + p for p, m in re.findall(r'href="/(datove-sady-technologickych-energetickych-zarizeni-ke-dni-(\d{8})[^"]*)"', html)}
    lic = {_ddmmyyyy(m): '/' + p for p, m in re.findall(r'href="/(seznam-drzitelu-licenci-uznani-opravneni-podnikat-ke-dni-(\d{8})[^"]*)"', html)}
    if not tez or not lic:
        raise RuntimeError('Na stránce ERÚ jsem nenašel odkazy na datové sady (změnila se struktura stránky?)')
    td = tez_date or max(tez)
    if td not in tez:
        raise RuntimeError(f'TEZ ke dni {td} na stránce není')
    ld = min(lic, key=lambda d: (abs((d - td).days), -d.toordinal()))
    tez_page = _get(BASE + tez[td]).text
    lic_page = _get(BASE + lic[ld]).text
    tez_file = re.search(r'href="([^"]*tez-sk-11-[^"]+\.xml)"', tez_page)
    lic_file = re.search(r'href="([^"]*lic11[^"]+\.xml)"', lic_page)
    if not tez_file or not lic_file:
        raise RuntimeError('Na stránce datové sady chybí soubor pro skupinu 11')
    return {'tez_date': td, 'tez_url': BASE + tez_file.group(1), 'tez_page': BASE + tez[td],
            'lic_date': ld, 'lic_url': BASE + lic_file.group(1), 'lic_page': BASE + lic[ld],
            'format_note': re.sub(r'.*ke-dni-\d{8}-?', '', tez[td]) or None}


def download(url, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        with requests.get(url, headers=UA, timeout=300, stream=True) as r:
            r.raise_for_status()
            tmp = dest.with_suffix('.part')
            with open(tmp, 'wb') as f:
                for ch in r.iter_content(1 << 20):
                    f.write(ch)
            tmp.rename(dest)
    return dest


def read_licences(path):
    """Seznam licencí skupiny 11 -> DataFrame (jeden řádek na verzi licence)."""
    root = etree.parse(str(path), etree.XMLParser(huge_tree=True)).getroot()
    df = pd.DataFrame([dict(e.attrib) for e in root])
    anon = df.subjekt_IC.str.fullmatch(r'-+')
    df['fyzicka_osoba'] = anon
    df['stav'] = df.version_status.map(lambda s: 'Aktivní' if s.startswith('Aktiv') else ('Zaniklá' if s.startswith('Zanik') else 'Zrušená'))
    return df


def _kids(e):
    return {c.tag: (c.text or '') for c in e if len(c) == 0}


def read_tez(path):
    """TEZ skupiny 11 -> (licence, provozovny, zdroje, katastry)."""
    root = etree.parse(str(path), etree.XMLParser(huge_tree=True)).getroot()
    L, P, O, C = [], [], [], []
    for lt in root.iter('LicTable'):
        l = _kids(lt); L.append(l); lid = l['LicId']
        for pe in lt.iter('PremiseElec'):
            p = _kids(pe)
            td = pe.find('TechnicalData')
            if td is not None:
                p.update({'TD_' + k: v for k, v in _kids(td).items()})
            p['LicId'] = lid
            P.append(p)
            for o in pe.iter('PremiseElecOutput'):
                d = _kids(o); d['PremiseElecId'] = p['PremiseElecId']; d['LicId'] = lid; O.append(d)
            for c in pe.iter('TEZCadaster'):
                d = _kids(c); d['PremiseElecId'] = p['PremiseElecId']; d['LicId'] = lid; C.append(d)
    L, P, O, C = map(pd.DataFrame, (L, P, O, C))
    O['OutputElec'] = pd.to_numeric(O.OutputElec, errors='coerce').fillna(0)
    O['OutputTherm'] = pd.to_numeric(O.OutputTherm, errors='coerce').fillna(0)
    return L, P, O, C
