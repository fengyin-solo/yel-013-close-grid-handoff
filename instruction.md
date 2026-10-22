# 收盘估值快照锁版

终端锁版前，交易支持席要把各场所的合约版本收束成一份交接快照。明细来自 `/data/quotes.csv`，标的上下文来自 `/data/market.json`；重建入口仍为 `/app/rebuild.py`：

```bash
python3 /app/rebuild.py
```

验收输入由固定 seed `73021` 的演练快照产生，没有客户身份或真实成交。两个源文件只读，所有结果都必须从本次启动时磁盘上的内容重新推导。

## 标的市场对象

`market.json` 的原始字节数不得超过 1024，解码规则为 UTF-8，根值只能是 JSON 对象；只要出现重复成员名就作为无法继续的坏输入。根成员只能是下面四项，书写次序无需固定：

```text
underlying,spot,rate_bps,dividend_bps
```

`underlying` 须符合下一行的正则形式。其余成员须为有限 JSON 数字，布尔值不属于数字：`spot` 严格大于 0 且不超过 1000000，两个利率成员都落在 `[-100000,100000]`。利率以基点给出，进入指数公式前先除以 10000。

```text
[A-Z][A-Z0-9_]{0,15}
```

## 报价表与逐行准入

`quotes.csv` 的原始字节按 UTF-8 解码；第一条记录必须逐字等于下列九列标题。首行之后可有 0 至 500 条记录。若引号跨行未闭合，或任一记录的列数偏离标题，整批立即停止。

```text
quote_id,underlying,expiry_days,strike,venue,revision,status,bid,ask
```

解码成功不等于业务通过。单行内容不合法时，只向审计写入一次拒绝，继续检查后续行，并让该行退出胜者竞争：

- `quote_id` 须匹配下一代码块且长度在 1 到 64；只要格式合法，首次出现即占用该编号，之后的同名行无论前一行还有没有其他错误都按重复拒绝。
- `underlying` 必须与市场对象中的值逐字相同；`expiry_days` 只接受 ASCII 数字组成的非负写法，可带前导零，对应整数须在 1 到 3650。
- `strike` 是最多 32 个 ASCII 字符的有限十进制数，须满足 `0 < strike <= 1000000`；`venue` 只能匹配下一代码块。
- `revision` 由至多 10 个 ASCII 数字组成，允许前导零，对应整数位于 0 到 1000000000；`status` 仅取 `ACTIVE` 或 `CANCELLED`。
- 状态为 `ACTIVE` 时，`bid` 与 `ask` 两格都必须是长度至多 32 的有限 ASCII 十进制串，并按次序满足非负、bid 不高于 ask、ask 不高于 1000000；状态为 `CANCELLED` 时这两格反而必须留空。

```text
[A-Za-z0-9._-]
[A-Z]{2,8}
```

市场对象损坏、CSV 编码错误、表格结构损坏或数据行多于 500 条，都属于整批失败。整批失败要在触碰结果前以非零状态结束：三个已有结果逐字节保留，本来不存在的路径仍不可被创建。

## 胜者与风险网格

把 `(underlying, expiry_days, strike)` 看成一个合约键，再把键相同的合法记录按 `(revision, venue, quote_id)` 做稳定字典序竞争，最终只保留最大记录。失败记录不能因为最大记录撤单而递补。最大记录若标为 `CANCELLED`，此键离开网格，并进入撤单审计。

移除取消合约以后，非空网格里的每条期限必须使用同一个数值 strike 集合；集合不一致会让整批失败，并沿用上一段的旧结果保护。只有共同网格存在，后续期限方向调整才不会破坏已完成的执行价方向约束。

赢家为 `ACTIVE` 时，以两格价格的均值作为 `M=(bid+ask)/2`。把剩余寿命换算成年 `T=expiry_days/365` 后，分别求 `Sd=spot*exp(-dividend_bps/10000*T)` 和 `Kd=strike*exp(-rate_bps/10000*T)`；最终原始价就是 `M` 向闭区间 `[max(0,Sd-Kd),Sd]` 的投影。

计算全程采用十进制。任一曲面规则改写价格后，都要立即按十进制 ROUND_HALF_EVEN 保留 12 位小数。同一期限内先按数值 strike 递增处理：一轮 sweep 先自左向右消除上升，遇到右值高于左值时把右值压到左值；再自左向右处理离散凸性，内部点若违背 `2*C[i] <= C[i-1]+C[i+1]`，就将它降到左右邻点的均值。继续整轮 sweep，直到一整轮没有任何价格变化。随后按期限递增处理每个 strike，较长期不得低于紧邻的较短期限，否则把长期点抬到短期点。在共同网格前提下，这一步不会破坏已经稳定的单调与凸性。

## 结果文件

成功运行覆盖写出三个文件，旧批次记录不能残留。

曲面写到 `/output/surface.csv`，首行原文固定为下一代码块。记录按标的代码、到期天数、数值行权价这一顺序排列；三个数值列统一按 ROUND_HALF_EVEN 留六位小数。

```text
underlying,expiry_days,strike,raw_mid,adjusted_price
```

`/output/risk_summary.json` 是 UTF-8 紧凑 JSON，以单个换行收尾。根键按下面代码块的顺序输出：

```text
input_rows,rejected_rows,selected_quotes,cancelled_contracts,adjusted_points,max_abs_adjustment
```

前五项必须是非负整数。`input_rows` 数 CSV 数据行总数，`rejected_rows` 数拒绝行，`selected_quotes` 数写入曲面的 ACTIVE 胜者，`cancelled_contracts` 数撤单胜者，`adjusted_points` 数最终值与原始中价不同的点。`max_abs_adjustment` 取所有点 `abs(adjusted_price-raw_mid)` 的最大值，写成六位小数字符串；网格为空时写 `"0.000000"`。

`/output/audit.jsonl` 每行都是 UTF-8 紧凑 JSON，并以单个换行结束；每行根键按下面代码块的顺序排列。先按 CSV 物理行号记录拒绝项，`kind="rejected"`，identity 写成 `line:<CSV 中从 2 开始的物理行号>:<原始 quote_id>`，reason 固定为 `invalid_quote`。接着按 `(underlying, expiry_days, strike)` 递增记录撤单胜者，`kind` 为 `"cancelled"`，identity 为 `<underlying>|<expiry_days>|<六位小数 strike>`，reason 为 `winning_revision_cancelled`。最后仍按合约顺序记录每个最终价格发生改变的点，`kind` 为 `"adjusted"`，identity 使用同一格式，每个变化点只能出现一行；reason 按 `bounds,monotonicity,convexity,calendar` 的固定次序挑选该点实际触发项，再用 `+` 连接。

```text
kind,identity,reason
```

没有改动的输入再跑一遍，三份产物应完全同字节。输入发生变化时，旧曲面、旧汇总和旧审计都必须被本次结果整体替换。
