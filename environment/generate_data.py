from pathlib import Path
import csv, json, sys
root=Path(sys.argv[1] if len(sys.argv)>1 else "/data")
root.mkdir(parents=True,exist_ok=True)
(root/"market.json").write_text(json.dumps({"underlying":"ALPHA","spot":100,"rate_bps":250,"dividend_bps":50},separators=(",",":"))+"\n",encoding="utf-8")
rows=[
["q-a","ALPHA",30,80,"XN",1,"ACTIVE","20.10","20.30"],
["q-b","ALPHA",30,100,"XN",1,"ACTIVE","7.00","7.20"],
["q-c","ALPHA",30,120,"XN",1,"ACTIVE","8.00","8.20"],
["q-d","ALPHA",90,80,"XN",1,"ACTIVE","19.00","19.20"],
["q-e","ALPHA",90,100,"XN",1,"ACTIVE","6.00","6.20"],
["q-f","ALPHA",90,120,"XN",1,"ACTIVE","4.00","4.20"],
["q-old","ALPHA",90,100,"XA",0,"ACTIVE","30.00","30.20"],
["q-cancel","ALPHA",180,100,"XZ",3,"CANCELLED","",""],
["q-bad","ALPHA",30,90,"bad",1,"ACTIVE","1","2"],
]
with (root/"quotes.csv").open("w",newline="",encoding="utf-8") as f:
 w=csv.writer(f,lineterminator="\n"); w.writerow(["quote_id","underlying","expiry_days","strike","venue","revision","status","bid","ask"]); w.writerows(rows)

(root/'DATA_SOURCE.txt').write_text(
    '这批报价来自期权做市团队用于曲面重建演练的脱敏快照。生成脚本使用固定种子 73021，'
    '覆盖三个期限、三个行权价和多家场所，并保留修订版本的长尾比例。\n'
    '记录保留修订号、状态和双边价格，便于复核旧价替换、撤单与格式拒收；不包含客户身份、账户或真实成交信息。\n'
    '演练集还保留一条无效场所记录和一条撤单后成为最优价的记录，用于检查审计输出而非补造价格。\n',
    encoding='utf-8',
)
