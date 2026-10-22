#!/usr/bin/env python3
import csv,json,math
from pathlib import Path
D=Path('/data'); O=Path('/output'); O.mkdir(exist_ok=True)
m=json.loads((D/'market.json').read_text())
rows=list(csv.DictReader((D/'quotes.csv').open()))
seen={}; rejected=0; cancelled=0
for r in rows:
 try:
  key=(r['underlying'],int(r['expiry_days']),float(r['strike']))
  if key in seen: continue
  if r['status']=='CANCELLED': cancelled+=1; continue
  seen[key]=(float(r['bid'])+float(r['ask']))/2
 except Exception: rejected+=1
with (O/'surface.csv').open('w') as f:
 f.write('underlying,expiry_days,strike,raw_mid,adjusted_price\n')
 for (u,e,k),p in sorted(seen.items()): f.write(f'{u},{e},{k},{p:.6f},{p:.6f}\n')
(O/'risk_summary.json').write_text(json.dumps({'input_rows':len(rows),'rejected_rows':rejected,'selected_quotes':len(seen),'cancelled_contracts':cancelled,'adjusted_points':0,'max_abs_adjustment':'0.000000'})+'\n')
(O/'audit.jsonl').write_text('')
