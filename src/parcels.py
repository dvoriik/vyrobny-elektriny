import re
TOK=re.compile(r'(?P<st>(?:st(?:avební)?\.?\s*(?:p(?:arc)?\.?\s*)?(?:č\.?\s*)?)|(?:p\.?\s*č\.?\s*(?:st\.?\s*)?)|(?:par(?:c)?\.?\s*č\.?\s*(?:st\.?\s*)?))?(?P<num>\d{1,5}(?:/\d{1,4})?)',re.I)
def parse(note):
    out=[]
    if not note: return out
    s=note.replace('\xa0',' ')
    # split on separators; carry 'st.' context within a segment
    for seg in re.split(r'[;,]|\ba\b|\+',s):
        segst=bool(re.search(r'\bst\.|\bstavební|\bst\s+\d',seg,re.I))
        for m in TOK.finditer(seg):
            pre=(m.group('st') or '').lower()
            st='st' in pre or (segst and not pre.startswith('p'))
            num=m.group('num'); k,_,p=num.partition('/')
            out.append((st,int(k),int(p) if p else None))
    return out
