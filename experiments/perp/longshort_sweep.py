#!/usr/bin/env python3
"""KR-perp(삼전/하닉) 24h 롱숏 전수 스윕 — 장중/오버나잇/24h × 롱/숏 × always/gap신호.
도구가 2일짜리(n=2~3세션)라 PREDICTIVE 아님 — DESCRIPTIVE(돈이 어느 윈도우/방향에 있었나).
비용: taker 5bps/side, 펀딩=실현이력(8h, 롱=지불 숏=수취), 보유구간 8h경계마다 부과."""
import json, datetime
KST = datetime.timezone(datetime.timedelta(hours=9))
FEE = 0.0005          # taker per side
def kst(ms): return datetime.datetime.fromtimestamp(ms/1000, KST)

def load(sym):
    d = json.load(open(f"experiments/perp/{sym}_1m.json"))
    bars = [(int(b[0]), float(b[1]), float(b[4])) for b in d["klines"]]  # (open_ms, open, close)
    fund = [(int(f["fundingTime"]), float(f["fundingRate"])) for f in d["funding"]]
    return bars, fund

def funding_cost(fund, t0, t1, side):
    """held [t0,t1], side +1 long/-1 short. long pays +rate, short receives. return cost fraction."""
    c = 0.0
    for ft, fr in fund:
        if t0 < ft <= t1:
            c += side * fr     # long: +fr cost; short: -fr (i.e. receives when fr>0)
    return c

def sessions(bars):
    """KR session = KST 09:00..15:30 (UTC 00:00..06:30). return list of dicts per date."""
    by_date = {}
    for ms, o, c in bars:
        t = kst(ms); d = t.strftime("%Y-%m-%d")
        by_date.setdefault(d, []).append((ms, o, c, t))
    out = []
    for d in sorted(by_date):
        rows = by_date[d]
        intr = [r for r in rows if (r[3].hour, r[3].minute) >= (9,0) and (r[3].hour, r[3].minute) <= (15,30)]
        if not intr: continue
        out.append(dict(date=d,
            open_ms=intr[0][0], open_px=intr[0][1],
            close_ms=intr[-1][0], close_px=intr[-1][2],
            all_rows=rows))
    return out

def run(sym):
    bars, fund = load(sym)
    S = sessions(bars)
    print(f"\n{'='*78}\n{sym}  bars={len(bars)} sessions={len(S)} "
          f"[{S[0]['date']}..{S[-1]['date']}]  funding pts={len(fund)}")
    # ---- per-session legs ----
    results = {}  # name -> list of (ret_net, ret_gross)
    def add(name, r_net, r_gross):
        results.setdefault(name, []).append((r_net, r_gross))

    for i, s in enumerate(S):
        intr_ret = s['close_px']/s['open_px'] - 1                      # 장중 (open->close)
        # overnight = this close -> next open
        nxt = S[i+1] if i+1 < len(S) else None
        # --- INTRADAY window, both sides ---
        for side, tag in [(+1,'L'),(-1,'S')]:
            g = side*intr_ret
            fc = funding_cost(fund, s['open_ms'], s['close_ms'], side)
            net = g - 2*FEE - fc
            add(f"intraday_always_{tag}", net, g)
        if nxt:
            on_ret = nxt['open_px']/s['close_px'] - 1                  # 오버나잇 close->next open
            for side, tag in [(+1,'L'),(-1,'S')]:
                g = side*on_ret
                fc = funding_cost(fund, s['close_ms'], nxt['open_ms'], side)
                net = g - 2*FEE - fc
                add(f"overnight_always_{tag}", net, g)
            # --- gap-FADE: at close, take OPPOSITE of intraday move, hold overnight ---
            side = -1 if intr_ret > 0 else +1
            g = side*on_ret; fc = funding_cost(fund, s['close_ms'], nxt['open_ms'], side)
            add("overnight_FADE_intraday", g-2*FEE-fc, g)
            # --- gap-MOMENTUM: same direction as intraday, hold overnight ---
            side = +1 if intr_ret > 0 else -1
            g = side*on_ret; fc = funding_cost(fund, s['close_ms'], nxt['open_ms'], side)
            add("overnight_MOM_intraday", g-2*FEE-fc, g)
            # --- 24h hold (open->next open) both sides ---
            full = nxt['open_px']/s['open_px'] - 1
            for side, tag in [(+1,'L'),(-1,'S')]:
                g=side*full; fc=funding_cost(fund, s['open_ms'], nxt['open_ms'], side)
                add(f"hold24h_always_{tag}", g-2*FEE-fc, g)
        # --- INTRADAY conditional on overnight gap (need prev overnight) ---
        if i>0:
            prev=S[i-1]; gap = s['open_px']/prev['close_px']-1
            # fade overnight gap during the day
            side = -1 if gap>0 else +1
            g=side*intr_ret; fc=funding_cost(fund, s['open_ms'], s['close_ms'], side)
            add("intraday_FADE_overnight", g-2*FEE-fc, g)
            side = +1 if gap>0 else -1
            g=side*intr_ret; fc=funding_cost(fund, s['open_ms'], s['close_ms'], side)
            add("intraday_MOM_overnight", g-2*FEE-fc, g)

    # ---- report ----
    print(f"  {'strategy':<26} {'n':>2}  {'net_tot%':>9} {'gross_tot%':>10} {'avg_net%':>9} {'hit%':>5}")
    order=['intraday_always_L','intraday_always_S','overnight_always_L','overnight_always_S',
           'hold24h_always_L','hold24h_always_S','overnight_FADE_intraday','overnight_MOM_intraday',
           'intraday_FADE_overnight','intraday_MOM_overnight']
    for name in order:
        if name not in results: continue
        rs=results[name]; n=len(rs)
        net=sum(r[0] for r in rs); gross=sum(r[1] for r in rs)
        avg=net/n*100; hit=100*sum(1 for r in rs if r[0]>0)/n
        flag=' <<' if net>0 else ''
        print(f"  {name:<26} {n:>2}  {net*100:>+9.2f} {gross*100:>+10.2f} {avg:>+9.2f} {hit:>5.0f}{flag}")

for sym in ["SAMSUNGUSDT","SKHYNIXUSDT"]:
    run(sym)
print("\n주의: n=2~3 세션. 부호는 DESCRIPTIVE(과거 어디에 돈이 있었나)지 PREDICTIVE 아님.")
print("펀딩=실현이력 6pt(8h). 비용 taker 5bps/side. KR세션=KST 09:00-15:30.")
