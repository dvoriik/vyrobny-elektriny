"""Odolné stahování velkých souborů: opakované pokusy a navázání přerušeného přenosu (HTTP Range)."""
import time
from pathlib import Path
import requests

UA = {'User-Agent': 'vyrobny-elektriny/1.0 (open data pipeline)'}


def download_file(url, dest: Path, session=None, retries=8, timeout=120, log=print):
    """Stáhne url do dest. Rozpracovaný přenos drží v dest.part a při výpadku spojení
    pokračuje od přijatých bajtů (pokud server podporuje Range), jinak začne znovu.
    Hotový soubor se přejmenuje na dest až po ověření velikosti."""
    dest = Path(dest); dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        return dest
    part = dest.with_name(dest.name + '.part')
    s = session or requests.Session()
    total = None
    for att in range(retries):
        have = part.stat().st_size if part.exists() else 0
        headers = dict(UA)
        if have:
            headers['Range'] = f'bytes={have}-'
        try:
            with s.get(url, headers=headers, timeout=timeout, stream=True) as r:
                if r.status_code == 416 and total and have >= total:
                    break
                r.raise_for_status()
                if have and r.status_code != 206:      # server Range nepodporuje -> od začátku
                    have = 0
                    part.unlink(missing_ok=True)
                if r.status_code == 206:
                    cr = r.headers.get('Content-Range', '')
                    total = int(cr.split('/')[-1]) if '/' in cr and cr.split('/')[-1].isdigit() else total
                else:
                    total = int(r.headers['Content-Length']) if r.headers.get('Content-Length', '').isdigit() else None
                with open(part, 'ab' if have else 'wb') as f:
                    for ch in r.iter_content(1 << 20):
                        f.write(ch)
            size = part.stat().st_size
            if total is None or size >= total:
                break
            raise IOError(f'neúplný přenos {size}/{total}')
        except (requests.RequestException, IOError) as e:
            if att == retries - 1:
                raise
            wait = min(60, 5 * (att + 1))
            log(f'  stahování {url.rsplit("/", 1)[-1]} přerušeno ({e.__class__.__name__}), pokus {att + 2}/{retries} za {wait} s')
            time.sleep(wait)
    part.rename(dest)
    return dest
