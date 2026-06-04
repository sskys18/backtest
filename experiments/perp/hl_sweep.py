#!/usr/bin/env python3
"""Hyperliquid xyz:SMSN/SKHX 24h perp 롱숏 전수 — 105일 history, n~70 세션.
KR세션=KST 평일 09:00-15:00(15:30 근사). 오버나잇=15:00->익영업일 09:00(주말관통).
비용 taker 4.5bps/side, 펀딩=HL 1h 실현이력(롱=지불,숏=수취), 보유시간 부과.
이제 PREDICTIVE 검증 가능(n충분). 상관/t-stat 포함."""
import json, datetime, math, statistics
KST=datetime.timezone(datetime.timedelta(hours=9))
FEE=0.00045
def kst(ms): return datetime.datetime.fromtimestamp(ms/1000,KST)

def load(tag):
    d=json.load(open(f"experiments/perp/hl_{tag}.json"))
    bars=[(int(b["t"]),float(b["o"]),float(b["c"])) for b in d["klines"]]
    fund=[(int(f["time"]),float(f["fundingRate"])) for f in d["funding"]]
    return bars,fund

def fcost(fund,t0,t1,side):
    return side*sum(fr for ft,fr in fund if t0<ft<=t1)

def sessions(bars):
    byd={}
    for ms,o,c in bars:
        t=kst(ms); byd.setdefault(t.strftime("%Y-%m-%d"),[]).append((ms,o,c,t))
    out=[]
    for d in sorted(byd):
        rows=byd[d]
        if rows[0][3].weekday()>=5: continue        # skip Sat/Sun (KR closed)
        op=[r for r in rows if r[3].hour==9]
        cl=[r for r in rows if r[3].hour==15] or [r for r in rows if 9<=r[3].hour<=15]
        if not op or not cl: continue
        out.append(dict(date=d,open_ms=op[0][0],open_px=op[0][1],
                        close_ms=cl[-1][0],close_px=cl[-1][2],wd=rows[0][3].weekday()))
    return out

def stats(rs):
    """rs=list of net returns. return tot%,avg%,hit%,sharpe,t"""
    n=len(rs); tot=sum(rs); avg=tot/n
    sd=statistics.pstdev(rs) if n>1 else 0
    sharpe=avg/sd*math.sqrt(252) if sd>0 else 0    # daily->annual rough
    t=avg/(sd/math.sqrt(n)) if sd>0 else 0
    hit=100*sum(1 for x in rs if x>0)/n
    return tot*100,avg*100,hit,sharpe,t

def corr(a,b):
    n=len(a); ma=sum(a)/n; mb=sum(b)/n
    va=sum((x-ma)**2 for x in a); vb=sum((x-mb)**2 for x in b)
    cov=sum((a[i]-ma)*(b[i]-mb) for i in range(n))
    return cov/math.sqrt(va*vb) if va>0 and vb>0 else 0

def run(tag):
    bars,fund=load(tag); S=sessions(bars)
    print(f"\n{'='*82}\n{tag}  1h bars={len(bars)} 세션={len(S)} [{S[0]['date']}..{S[-1]['date']}]")
    R={}
    def add(k,v): R.setdefault(k,[]).append(v)
    intr_series=[]; on_series=[]
    for i,s in enumerate(S):
        intr=s['close_px']/s['open_px']-1
        for side,tg in [(1,'L'),(-1,'S')]:
            add(f"intraday_{tg}", side*intr-2*FEE-fcost(fund,s['open_ms'],s['close_ms'],side))
        if i+1<len(S):
            nx=S[i+1]; on=nx['open_px']/s['close_px']-1
            intr_series.append(intr); on_series.append(on)
            for side,tg in [(1,'L'),(-1,'S')]:
                add(f"overnight_{tg}", side*on-2*FEE-fcost(fund,s['close_ms'],nx['open_ms'],side))
            sf=-1 if intr>0 else 1   # FADE intraday over the overnight
            add("ON_FADE_intra", sf*on-2*FEE-fcost(fund,s['close_ms'],nx['open_ms'],sf))
            sm=1 if intr>0 else -1
            add("ON_MOM_intra", sm*on-2*FEE-fcost(fund,s['close_ms'],nx['open_ms'],sm))
            # 24h
            full=nx['open_px']/s['open_px']-1
            for side,tg in [(1,'L'),(-1,'S')]:
                add(f"hold24h_{tg}", side*full-2*FEE-fcost(fund,s['open_ms'],nx['open_ms'],side))
        if i>0:
            pv=S[i-1]; gap=s['open_px']/pv['close_px']-1
            sf=-1 if gap>0 else 1
            add("intra_FADE_ON", sf*intr-2*FEE-fcost(fund,s['open_ms'],s['close_ms'],sf))
            sm=1 if gap>0 else -1
            add("intra_MOM_ON", sm*intr-2*FEE-fcost(fund,s['open_ms'],s['close_ms'],sm))
    print(f"  {'strategy':<16} {'n':>3} {'net%':>8} {'avg%':>7} {'hit%':>5} {'Shrp':>6} {'t':>6}")
    for k in ['intraday_L','intraday_S','overnight_L','overnight_S','hold24h_L','hold24h_S',
              'ON_FADE_intra','ON_MOM_intra','intra_FADE_ON','intra_MOM_ON']:
        if k not in R: continue
        tot,avg,hit,sh,t=stats(R[k])
        fl=' <<' if t>1.5 else (' <' if tot>0 else '')
        print(f"  {k:<16} {len(R[k]):>3} {tot:>+8.2f} {avg:>+7.3f} {hit:>5.0f} {sh:>+6.2f} {t:>+6.2f}{fl}")
    # the decisive thesis test: corr(intraday move, subsequent overnight move)
    c=corr(intr_series,on_series)
    print(f"  >>> corr(장중move, 직후 오버나잇move) = {c:+.3f}  (음수=오버슈트되돌림=fade근거, n={len(intr_series)})")

for tag in ["SMSN","SKHX"]:
    run(tag)
print("\n비용 4.5bps/side, 펀딩 HL 1h실현. t>1.5 '<<'. 단일종목·다중검정 미보정 → t 인플레 감안.")
