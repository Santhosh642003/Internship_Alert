"""Daily graduate search extension. Run alongside the existing radar.py."""
import argparse
import datetime as dt
import html
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlencode, urlparse, parse_qs
from urllib.request import Request, urlopen
from html.parser import HTMLParser
from radar import get_json

ROOT = Path(__file__).resolve().parent
STATE = ROOT / 'graduate_seen.json'

def clean(value):
    return ' '.join(re.sub(r'<[^>]+>', ' ', html.unescape(str(value or ''))).split())

def row(key,title,loc,desc,url,deadline=None):
    return dict(key=str(key),title=title,loc=loc,desc=clean(desc),url=url,deadline=deadline)

class StructuredPage(HTMLParser):
    def __init__(self):
        super().__init__()
        self.active=False
        self.blocks=[]
    def handle_starttag(self,tag,attrs):
        if tag=='script' and dict(attrs).get('type')=='application/ld+json':
            self.active=True
    def handle_endtag(self,tag):
        if tag=='script': self.active=False
    def handle_data(self,data):
        if self.active: self.blocks.append(json.loads(data))

def jsonld_nodes(obj):
    if isinstance(obj,list):
        for item in obj: yield from jsonld_nodes(item)
    if isinstance(obj,dict):
        if obj.get('@type')=='JobPosting': yield obj
        yield from jsonld_nodes(obj.get('@graph',[]))

def fetch(kind,board):
    if kind=='page':
        # Explicit employer-published JobPosting pages only, no crawling.
        parser=StructuredPage()
        with urlopen(Request(board,headers={'User-Agent':'GraduateRadar/1.0'}),timeout=25) as response:
            parser.feed(response.read().decode())
        nodes=list(jsonld_nodes(parser.blocks))
        if not nodes: raise ValueError('no JobPosting data; manual review')
        for j in nodes:
            yield row(j.get('url',board),j['title'],json.dumps(j.get('jobLocation','')),j.get('description'),j.get('url',board),j.get('validThrough'))
    elif kind=='greenhouse':
        for j in get_json(f'https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true')['jobs']:
            yield row(j['id'],j['title'],j['location']['name'],j['content'],j['absolute_url'])
    elif kind=='ashby':
        for j in get_json(f'https://api.ashbyhq.com/posting-api/job-board/{board}')['jobs']:
            if j.get('isListed',True):
                places=[j['location'], *[l.get('location','') for l in j.get('secondaryLocations',[])]]
                address=(j.get('address') or {}).get('postalAddress',{})
                places.extend(str(address.get(k) or '') for k in ('addressLocality','addressRegion','addressCountry'))
                yield row(j['jobUrl'],j['title'],', '.join(filter(None,places)),j['descriptionPlain'],j['jobUrl'])
    elif kind=='lever':
        offset=0
        while True:
            jobs=get_json(f'https://api.lever.co/v0/postings/{board}?mode=json&limit=100&skip={offset}')
            for j in jobs:
                body=' '.join([j.get('descriptionPlain',''),*(v['content'] for v in j.get('lists',[])),j.get('additionalPlain','')])
                yield row(j['id'],j['text'],j['categories'].get('location',''),body,j['hostedUrl'])
            if len(jobs)<100: break
            offset+=100
    elif kind=='workable':
        for j in get_json(f'https://www.workable.com/api/accounts/{board}?details=true')['jobs']:
            yield row(j['shortcode'],j['title'],', '.join(str(j.get(k) or '') for k in ('city','state','country')),str(j.get('description',''))+' '+str(j.get('requirements','')),j['url'])
    else: raise ValueError('unknown source')

def identity(url):
    parsed=urlparse(url)
    if parsed.hostname in ('www.stripe.com','stripe.com'):
        req=parse_qs(parsed.query).get('gh_jid',[None])[0] or parsed.path.rstrip('/').split('/')[-1]
        if req.isdigit(): return 'stripe:req:'+req
    return url.split('#')[0]

def classify(j,cfg):
    t=j['title'].lower()
    d=j['desc'].lower()
    if not any(word in t for word in cfg['keywords']) or not re.search(cfg['locations'],j['loc'],re.I): return
    if re.search(r'\b(senior|sr\.?|staff|principal|director|manager|intern(ship)?|phd)\b',t): return
    if not re.search(r'new grad|graduate|early career|entry.level|university grad|college grad|engineer i\b|\bncg\b|2027',t+' '+d[:2200]): return
    if re.search(r'immediate start|must return to school|must be a u\.?s\.? citizen|required.{0,12}security clearance|active.{0,12}security clearance|without (?:the need for )?(?:visa )?sponsorship|(?:cannot|unable to|do not|will not|not eligible for|no) (?:offer |provide )?(?:immigration |visa )?sponsor',d+' '+t): return
    if re.search(r'(?:ph\.?d\.?|doctorate) (?:is )?required|must (?:be pursuing|hold|have) (?:a |an )?(?:ph\.?d|doctorate)|requires? (?:a )?(?:ph\.?d|doctorate)|return(?:ing)? to (?:school|university)|(?:must|only).{0,25}(?:graduat.{0,8}december 2026|start.{0,10}january 2027)',d): return
    if j['deadline'] and j['deadline'][:10]<dt.date.today().isoformat(): return
    visa='UNVERIFIED; ask about OPT, E-Verify and future sponsorship'
    if re.search(r'we (?:do |can |will )?sponsor (?:and take over |employment|work|visa)|visa sponsorship is available',d):
        visa='Explicit positive text; confirm role and employer'
    matches=[s for s in cfg['skills'] if re.search(r'(?<!\w)'+re.escape(s)+r'(?!\w)',d,re.I)]
    version='Speech' if re.search('speech|asr|nlp|language processing',t) else ('Data' if re.search('data scien|analyst',t) else 'Industry')
    score=min(10,3+min(4,len(matches))+int('2027' in t+' '+d)+int(visa.startswith('Explicit'))+int(version=='Speech'))
    reason=', '.join(matches[:5]) or 'title match only'
    return score,f"{j['title']} | {j['loc']}\n{j['url']}\nFit {score}/10: {reason}. Visa: {visa}. Resume: {version}. Ask: graduate recruiter for this team."

def send(text):
    token=os.environ['TELEGRAM_BOT_TOKEN']
    chat=os.environ['TELEGRAM_CHAT_ID']
    payload=urlencode({'chat_id':chat,'text':text,'disable_web_page_preview':'true'}).encode()
    try:
        with urlopen(Request(f'https://api.telegram.org/bot{token}/sendMessage',data=payload),timeout=20) as r:
            if not json.load(r).get('ok'): raise RuntimeError()
    except Exception: raise RuntimeError('Telegram delivery failed; retry') from None

def run(cfg,dry):
    seen=set(json.loads(STATE.read_text())) if STATE.exists() else set()
    hits={}; failures=[]
    for company,kind,board in cfg['sources']:
        try:
            for j in fetch(kind,board):
                canonical=identity(j['url'])
                key=f"{kind}:{company}:{j['key']}"
                result=classify(j,cfg)
                if result and canonical not in seen and key not in seen:
                    hits[canonical]=(result[0],company+' | '+result[1],key)
        except Exception as error:
            failures.append(company+' ('+type(error).__name__+')')
    if failures: print('Sources requiring review:',', '.join(failures),file=sys.stderr)
    for url,(_,msg,key) in sorted(hits.items(),key=lambda x:-x[1][0])[:cfg['max_daily']]:
        if dry: print(msg); continue
        send(msg[:3950]); seen.update([url,key])
        temp=STATE.with_suffix('.tmp'); temp.write_text(json.dumps(sorted(seen))); temp.replace(STATE)
    if not hits and not dry: send('Graduate Radar: no new matches.'+(' Some sources failed.' if failures else ''))
    if failures and not dry: send('Sources requiring review: '+', '.join(failures))
    return bool(failures)

if __name__=='__main__':
    args=argparse.ArgumentParser()
    args.add_argument('--dry-run',action='store_true')
    args.add_argument('--config',default=ROOT/'graduate_config.json')
    opts=args.parse_args()
    sys.exit(int(run(json.loads(Path(opts.config).read_text()),opts.dry_run)))
