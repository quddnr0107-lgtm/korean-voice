"""Conservative legal-reading preparation and independent transcript gate.
No model training. No audio naturalness verdict from text tests.
Run: python3 quality.py --test
"""
import argparse, difflib, hashlib, json, re, unicodedata
from pathlib import Path

VERSION = 'legal-reading-quality/1'
DIGITS = '영일이삼사오육칠팔구'
COMPOUNDS = re.compile(r'공\s*[·ㆍ]\s*사(?=\s*(?:기업체|단체))')
ENUM = re.compile(r'(^|\n)([ \t]*)([1-9][0-9]{0,3})\.[ \t]+')
REFERENCES = re.compile(r'제\s*([1-9][0-9]{0,3})\s*([조항호])')

def number(n):
    n = int(n)
    if not 0 <= n < 10000:
        raise ValueError('supported numeral range: 0..9999')
    if n == 0:
        return '영'
    out = ''
    for value, unit in ((1000,'천'),(100,'백'),(10,'십'),(1,'')):
        q,n = divmod(n,value)
        if q:
            out += ('' if q == 1 and value > 1 else DIGITS[q]) + unit
    return out

def prepare(source):
    """Never overwrite source; preserve numbering, labels and lexical content.
    Number normalization is explicit, not a guarantee that TTS speaks it well.
    Unknown punctuation is retained and flagged for review.
    """
    text = unicodedata.normalize('NFC',source)
    text = COMPOUNDS.sub('공사',text)
    text = ENUM.sub(lambda m:m[1]+m[2]+'제'+m[3]+'호, ',text)
    text = re.sub(r'([①-⑳])[ \t]*',lambda m:'제'+str(ord(m[1])-ord('①')+1)+'항, ',text)
    text = REFERENCES.sub(lambda m:'제'+number(m[1])+m[2],text)
    flags = []
    if re.search(r'[0-9]',text):
        flags.append('review_remaining_numeric_notation')
    if re.search(r'[·ㆍ]',text):
        flags.append('review_remaining_middot_do_not_globally_split')
    if len(text) > 240:
        flags.append('review_long_segment_at_semantic_boundary')
    return dict(version=VERSION,source=source,spoken=text,review=flags,
                source_sha256=hashlib.sha256(source.encode()).hexdigest())

def canonical(text):
    text = prepare(text)['spoken']
    return ''.join(c for c in text if c.isalnum())

def transcript_gate(expected, recognized, critical=()):
    """ASR mismatch => review, never conclusive proof of a synthesis error.
    exact transcript match still requires listening for voice/style/boundaries.
    """
    a,b = canonical(expected),canonical(recognized)
    edits = [dict(kind=k,expected=a[i:j],recognized=b[x:y])
             for k,i,j,x,y in difflib.SequenceMatcher(None,a,b,autojunk=False).get_opcodes()
             if k != 'equal']
    missing = [p for p in critical if canonical(p) not in b]
    return dict(status='review' if edits or missing else 'transcript_match_listen_required',
                differences=edits,missing_critical=missing,perceptual_review_required=True)

def run_tests():
    # 9,999 ordinals × 3 notation variants × 4 lexical contexts =119,988.
    # Independent ordinal oracle uses positional digit lengths, not number().
    def oracle(n):
        out=''
        s=str(n)
        for i,c in enumerate(s):
            if c=='0':continue
            power=len(s)-i-1
            out += ('' if c=='1' and power else DIGITS[int(c)])+['','십','백','천'][power]
        return out
    contexts = [('공·사 기업체','공사 기업체'),('공ㆍ사 단체','공사 단체'),
                ('장교·준사관·부사관','장교·준사관·부사관'),
                ('자격을 가진 사람으로서 국가공무원법에 따라','자격을 가진 사람으로서 국가공무원법에 따라')]
    count=0; digest=hashlib.sha256()
    for n in range(1,10000):
        for form in (f'{n}. ',f'제{n}호, ',f'제 {n} 호, '):
            for src,want in contexts:
                original=form+src
                result=prepare(original)
                expected='제'+oracle(n)+'호, '+want
                assert result['source']==original
                assert result['spoken']==expected,(original,result,expected)
                assert prepare(result['spoken'])['spoken']==expected
                digest.update((original+'\0'+expected+'\n').encode());count+=1
    fixtures=[('① 대한민국','제일항, 대한민국'),('⑳ 대한민국','제이십항, 대한민국'),
              ('3.14는 소수다','3.14는 소수다'),('공·사 구분','공·사 구분'),
              ('제83조의3제2항제4호','제팔십삼조의3제이항제사호'),
              ('고용주\n2. 소집','고용주\n제이호, 소집')]
    for src,want in fixtures:assert prepare(src)['spoken']==want
    expected='제6호 의사 또는 치과의사 자격을 가진 사람으로서 국가공무원법에 따라'
    mutations=['제6호 의사 또는 치과의사 자격으로 서국가공무원법에 따라',
               expected.replace('제6호',''),expected+expected,
               expected.replace('치과의사','간호사')]
    for bad in mutations:assert transcript_gate(expected,bad)['status']=='review'
    assert transcript_gate(expected,expected)['status']=='transcript_match_listen_required'
    result=dict(version=VERSION,generated_cases=count,focused_regressions=len(fixtures),
                transcript_gate_cases=len(mutations)+1,failures=0,
                corpus_sha256=digest.hexdigest(),audio_cases_synthesized=0,
                scope='text preparation and transcript mismatch tests, NOT audio validation or model training')
    Path(__file__).with_name('test-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result,ensure_ascii=False))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--test',action='store_true')
    parser.add_argument('--source',type=Path)
    parser.add_argument('--recognized',type=Path)
    args=parser.parse_args()
    if args.test:run_tests()
    elif args.source and args.recognized:
        result=transcript_gate(args.source.read_text(),args.recognized.read_text())
        print(json.dumps(result,ensure_ascii=False,indent=2))
        raise SystemExit(2 if result['status']=='review' else 0)
    elif args.source:
        print(json.dumps(prepare(args.source.read_text()),ensure_ascii=False,indent=2))
    else:parser.print_help()

