#!/usr/bin/env python3
"""장 시작/마감 변동성·방향성 전수 — HL 1h(n=74) + 5m(n~12 파일럿).
KR 09:00 개장 / 15:30 마감. 24h perp는 방향만 → '변동성 먹기'=경계 예측가능 방향성분.
사전등록: t≥2.5(다중검정 ~12개) → 후보. 비용 4.5bps/side."""
import json,datetime,math,statistics
KST=datetime.timezone(datetime.timedelta(hours=9)); FEE=0.00045
def kst(ms): return datetime.datetime.fromtimestamp(ms/1000,KST)
def st(x):
    n=len(x);m=sum(x)/n;sd=statistics.pstdev(x)
    return sum(x)*100,m*100,(m/(sd/math.sqrt(n)) if sd>0 else 0),100*sum(1 for v in x if v>0)/n,n
def line(nm,x):
    if not x: print(f"  {nm:<30} n=  0 (트리거 없음)"); return
    tot,avg,t,hit,n=st(x); fl=' <<<' if abs(t)>=2.5 else (' <' if t>0 else '')
    print(f"  {nm:<30} n={n:>3} tot={tot:>+7.2f}% avg={avg:>+.3f}% t={t:>+5.2f} hit={hit:>4.0f}%{fl}")

def load1h(tag):
    d=json.load(open(f"experiments/perp/hl_{tag}.json"))
    by={}
    for b in d["klines"]:
        t=kst(int(b["t"])); by.setdefault(t.strftime("%Y-%m-%d"),{})[t.hour]=(float(b["o"]),float(b["c"]))
    out=[]
    for dt in sorted(by):
        h=by[dt]; wd=datetime.date.fromisoformat(dt).weekday()
        if wd>=5: continue
        if 9 in h and 14 in h and 10 in h: out.append((dt,h))
    return out

print("="*88)
print("PART A — 1시간봉 105일 (n=74): 개장시간/마감시간 방향성 + ORB")
for tag in ["SMSN","SKHX"]:
    S=load1h(tag)
    openH=[]; closeH=[]; gap_then_open=[]; openH_then_rest=[]; rest=[]
    openL=[]; openS=[]; closeL=[]; closeS=[]
    orb=[]; open_fade_rest=[]
    for i,(dt,h) in enumerate(S):
        o9,c9=h[9]                       # 09:00-10:00 (개장시간)
        oh=c9/o9-1                       # open-hour return
        # rest of day: 10:00 -> 15:00(or last avail <=15)
        last=max(k for k in h if 10<=k<=15)
        c_last=h[last][1]; r_rest=c_last/c9-1
        # close hour: 14:00->15:00
        ch=h[14][1]/h[14][0]-1
        openL.append(oh-2*FEE); openS.append(-oh-2*FEE)
        closeL.append(ch-2*FEE); closeS.append(-ch-2*FEE)
        rest.append(r_rest)
        openH.append(oh); closeH.append(ch)
        # ORB: open-hour 방향으로 rest 진입
        sd=1 if oh>0 else -1
        orb.append(sd*r_rest-2*FEE)
        open_fade_rest.append(-sd*r_rest-2*FEE)
        # overnight gap -> open hour (gap continuation/fade)
        if i>0:
            pdt,ph=S[i-1]; plast=max(k for k in ph if 10<=k<=15)
            gap=o9/ph[plast][1]-1     # prev close(15) -> today open(09)
            sg=1 if gap>0 else -1
            gap_then_open.append(sg*oh-2*FEE)        # gap 방향이 개장시간에 지속?
        openH_then_rest.append((oh,r_rest))
    print(f"\n--- {tag} (n_session={len(S)}) ---")
    line("개장시간 롱 (09-10시)",openL); line("개장시간 숏",openS)
    line("마감시간 롱 (14-15시)",closeL); line("마감시간 숏",closeS)
    line("ORB: 개장시간방향→장중잔여",orb); line("개장fade→장중잔여",open_fade_rest)
    line("오버나잇갭방향→개장시간 지속",gap_then_open)
    # corr open-hour vs rest-of-day
    a=[x[0] for x in openH_then_rest]; b=[x[1] for x in openH_then_rest]
    n=len(a);ma=sum(a)/n;mb=sum(b)/n
    va=sum((x-ma)**2 for x in a);vb=sum((x-mb)**2 for x in b)
    cov=sum((a[i]-ma)*(b[i]-mb) for i in range(n))
    c=cov/math.sqrt(va*vb) if va>0 and vb>0 else 0
    print(f"  corr(개장시간, 장중잔여)={c:+.3f} (음수=개장과열되돌림 / 양수=추세지속)")

print("\n"+"="*88)
print("PART B — 5분봉 17.5일 (n~12 파일럿): 시간대별 변동성 + 개장레인지 + 마감옥션")
for tag in ["SMSN","SKHX"]:
    d=json.load(open(f"experiments/perp/hl_{tag}_5m.json"))["klines"]
    bars=[(kst(int(b["t"])),float(b["o"]),float(b["h"]),float(b["l"]),float(b["c"])) for b in d]
    # vol profile: mean |ret| per HH:MM bucket (KR session 09:00-15:30 + 인접)
    buck={}
    prev=None
    for t,o,hi,lo,c in bars:
        if prev:
            r=abs(math.log(c/prev)) if prev>0 else 0
            buck.setdefault((t.hour,t.minute),[]).append(r)
        prev=c
    print(f"\n--- {tag} 시간대별 평균|5m수익| (bp) — 변동성 집중 구간 ---")
    keys=sorted(buck)
    # show KR-relevant hours 08:30-16:00, every 30min
    show=[(h,m) for (h,m) in keys if (8<=h<=16) and m%30==0]
    prof=[(f"{h:02d}:{m:02d}", 1e4*sum(buck[(h,m)])/len(buck[(h,m)])) for h,m in show]
    mx=max(v for _,v in prof)
    for lab,v in prof:
        bar='█'*int(40*v/mx)
        star=' ★' if (lab in('09:00','09:30','15:00','15:30')) else ''
        print(f"    {lab}  {v:>5.1f}bp {bar}{star}")
    # 세션별 개장레인지(09:00-09:30) breakout, 마감옥션(15:00-15:30 drift)
    byd={}
    for t,o,hi,lo,c in bars:
        if t.weekday()>=5: continue
        byd.setdefault(t.strftime("%Y-%m-%d"),[]).append((t,o,hi,lo,c))
    orb=[]; auction=[]; auction_mom=[]
    for dt in sorted(byd):
        rows=byd[dt]
        orng=[r for r in rows if (9,0)<=(r[0].hour,r[0].minute)<(9,30)]
        aft =[r for r in rows if (9,30)<=(r[0].hour,r[0].minute)<=(15,0)]
        clo =[r for r in rows if (15,0)<=(r[0].hour,r[0].minute)<=(15,30)]
        morning=[r for r in rows if (9,30)<=(r[0].hour,r[0].minute)<(15,0)]
        if orng and aft and len(aft)>1:
            hi=max(r[2] for r in orng); lo=min(r[3] for r in orng)
            entry=aft[0][1]; ex=aft[-1][4]
            # breakout: 진입가가 레인지상단 위면 롱, 하단 아래면 숏, 아니면 패스
            if entry>hi: orb.append(ex/entry-1-2*FEE)
            elif entry<lo: orb.append(-(ex/entry-1)-2*FEE)
        if clo and len(clo)>1 and morning:
            adrift=clo[-1][4]/clo[0][1]-1            # 15:00->15:30 옥션 drift
            auction.append(adrift)
            mdir=1 if (morning[-1][4]/morning[0][1]-1)>0 else -1
            auction_mom.append(mdir*adrift-2*FEE)    # 오전방향이 옥션에 지속?
    line("ORB 30분 breakout→15:00",orb)
    line("마감옥션 15:00-15:30 always-long",[a-2*FEE for a in auction])
    line("오전방향→마감옥션 지속",auction_mom)

print("\n사전등록 t≥2.5='<<<'. PART B는 n~12 파일럿(descriptive). 비용 4.5bps/side. 펀딩 무시(장중<6h).")
