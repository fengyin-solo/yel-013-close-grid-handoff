import pytest

#!/usr/bin/env python3
import csv
import hashlib
import io
import json
import os
import re
import subprocess
from decimal import Decimal, ROUND_HALF_EVEN, localcontext
from pathlib import Path

DATA = Path('/data')
OUTPUT = Path('/output')
PROGRAM = ['/usr/local/bin/python3', '/app/rebuild.py']
NAMES = ('surface.csv', 'risk_summary.json', 'audit.jsonl')
HEADER = ['quote_id','underlying','expiry_days','strike','venue','revision','status','bid','ask']
ORIGINAL_MARKET = (DATA / 'market.json').read_bytes()
ORIGINAL_QUOTES = (DATA / 'quotes.csv').read_bytes()

CASE_IDS = {
 'test_entrypoint_and_empty_input':['RUN-01'],
 'test_input_files_unchanged':['RUN-02'],
 'test_rerun_overwrites_all_outputs':['RUN-03','RUN-05'],
 'test_repeat_is_byte_stable':['RUN-04'],
 'test_market_size_boundary':['MARKET-01'],
 'test_market_json_structure':['MARKET-02'],
 'test_market_underlying_boundaries':['MARKET-03'],
 'test_market_numeric_types_and_finiteness':['MARKET-04'],
 'test_market_spot_range':['MARKET-05'],
 'test_market_rate_range':['MARKET-06'],
 'test_market_dividend_range':['MARKET-07'],
 'test_csv_encoding_and_exact_header':['CSV-01'],
 'test_csv_row_count_and_structure':['CSV-02'],
 'test_invalid_row_rejected_once_and_processing_continues':['QUOTE-01'],
 'test_quote_id_syntax_and_occupation':['QUOTE-02'],
 'test_quote_underlying_exact_match':['QUOTE-03'],
 'test_expiry_syntax_and_range':['QUOTE-04'],
 'test_strike_syntax_length_and_range':['QUOTE-05'],
 'test_venue_syntax_boundaries':['QUOTE-06'],
 'test_revision_syntax_length_and_range':['QUOTE-07'],
 'test_active_status_and_price_validation':['QUOTE-08'],
 'test_cancelled_price_fields':['QUOTE-09'],
 'test_fatal_errors_preserve_existing_and_missing_outputs':['FATAL-01'],
 'test_winner_revision_priority':['SELECT-01'],
 'test_winner_venue_tiebreak':['SELECT-02'],
 'test_winner_quote_id_tiebreak':['SELECT-03'],
 'test_cancelled_winner_has_no_fallback':['SELECT-04'],
 'test_common_numeric_strike_grid':['GRID-01'],
 'test_midpoint_discounted_bounds_and_rate_bps':['PRICE-01'],
 'test_dividend_discount_formula':['PRICE-02'],
 'test_numeric_strike_sort_and_monotonicity':['STRIKE-01'],
 'test_convexity_neighbor_average':['STRIKE-02'],
 'test_iterative_sweeps_and_decimal_rounding':['STRIKE-03'],
 'test_calendar_raises_longer_expiry':['CAL-01'],
 'test_surface_schema_sorting_and_half_even_format':['OUT-01'],
 'test_summary_serialization_and_exact_counts':['OUT-02','OUT-03'],
 'test_max_adjustment_and_empty_surface':['OUT-04'],
 'test_audit_rejected_records':['AUDIT-01'],
 'test_audit_cancelled_records':['AUDIT-02'],
 'test_audit_adjusted_reason_order_and_uniqueness':['AUDIT-03','AUDIT-04'],
 'test_generated_dataset_full_baseline':['BASE-01'],
}


def market(**updates):
    value = {'underlying':'ALPHA','spot':100,'rate_bps':0,'dividend_bps':0}
    value.update(updates)
    return value


def q(qid='q', expiry='30', strike='100', venue='XN', revision='1', status='ACTIVE', bid='10', ask='10', underlying='ALPHA'):
    return [str(qid), str(underlying), str(expiry), str(strike), str(venue), str(revision), str(status), str(bid), str(ask)]


def csv_bytes(rows, header=HEADER):
    stream = io.StringIO(newline='')
    writer = csv.writer(stream, lineterminator='\n')
    writer.writerow(header)
    writer.writerows(rows)
    return stream.getvalue().encode('utf-8')


def clear_output():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for name in NAMES:
        path = OUTPUT / name
        if path.exists() or path.is_symlink():
            path.unlink()


def fixture(rows=(), market_value=None, raw_market=None, raw_csv=None, clear=True):
    DATA.mkdir(parents=True, exist_ok=True)
    if raw_market is None:
        raw_market = (json.dumps(market_value or market(), ensure_ascii=False, separators=(',', ':')) + '\n').encode('utf-8')
    if raw_csv is None:
        raw_csv = csv_bytes(rows)
    (DATA / 'market.json').write_bytes(raw_market)
    (DATA / 'quotes.csv').write_bytes(raw_csv)
    if clear:
        clear_output()


def run_ok(timeout=8):
    result = subprocess.run(PROGRAM, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)
    return result


def run_bad(timeout=8):
    result = subprocess.run(PROGRAM, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    assert result.returncode != 0, (result.stdout, result.stderr)
    return result


def surface():
    with (OUTPUT / 'surface.csv').open(newline='', encoding='utf-8') as handle:
        return list(csv.DictReader(handle))


def summary():
    return json.loads((OUTPUT / 'risk_summary.json').read_text(encoding='utf-8'))


def audit():
    raw = (OUTPUT / 'audit.jsonl').read_text(encoding='utf-8')
    return [json.loads(line) for line in raw.splitlines()]


def one_row_result(row, *, market_value=None):
    fixture([row], market_value=market_value)
    run_ok()
    return surface(), summary(), audit()


def assert_rejected(row):
    rows, report, records = one_row_result(row)
    assert rows == []
    assert report['input_rows'] == 1 and report['rejected_rows'] == 1
    assert records == [{'kind':'rejected','identity':f'line:2:{row[0]}','reason':'invalid_quote'}]


def assert_accepted(row, expected_count=1):
    rows, report, _records = one_row_result(row)
    assert len(rows) == expected_count and report['rejected_rows'] == 0


def test_entrypoint_and_empty_input():
    """题面：「移除取消合约以后，非空网格里的每条期限必须使用同一个数值 strike 集合；集合不一致会让整批失败，并沿用上一段的旧结果保护。只有共同网格存在，后续期限方向调整才不会破坏已完成的执行价方向约束。」。"""
    fixture([])
    run_ok()
    assert (OUTPUT/'surface.csv').read_bytes() == b'underlying,expiry_days,strike,raw_mid,adjusted_price\n'
    assert summary() == {'input_rows':0,'rejected_rows':0,'selected_quotes':0,'cancelled_contracts':0,'adjusted_points':0,'max_abs_adjustment':'0.000000'}
    assert (OUTPUT/'audit.jsonl').read_bytes() == b''


def test_market_size_boundary():
    """题面：「`market.json` 的原始字节数不得超过 1024，解码规则为 UTF-8，根值只能是 JSON 对象；只要出现重复成员名就作为无法继续的坏输入。根成员只能是下面四项，书写次序无需固定：」。"""
    # CASE LEG-001-A LEG-002-A
    base = json.dumps(market(), separators=(',', ':')).encode()
    valid_1024 = base + b' ' * (1024 - len(base))
    assert len(valid_1024) == 1024
    fixture([], raw_market=valid_1024); run_ok()
    fixture([], raw_market=valid_1024 + b' '); run_bad()


def test_market_json_structure():
    """题面：「`market.json` 的原始字节数不得超过 1024，解码规则为 UTF-8，根值只能是 JSON 对象；只要出现重复成员名就作为无法继续的坏输入。根成员只能是下面四项，书写次序无需固定：」。"""
    # CASE LEG-002-B
    fixture([], raw_market=b'{"dividend_bps":0,"rate_bps":0,"spot":100,"underlying":"ALPHA"}\n'); run_ok()
    invalid = [
        b'\xff', b'[]', b'{"underlying":"ALPHA","underlying":"BETA","spot":100,"rate_bps":0,"dividend_bps":0}',
        b'{"underlying":"ALPHA","spot":100,"rate_bps":0}',
        b'{"underlying":"ALPHA","spot":100,"rate_bps":0,"dividend_bps":0,"extra":1}',
    ]
    for raw in invalid:
        fixture([], raw_market=raw); run_bad()
    assert not (OUTPUT/'surface.csv').exists()


def test_market_underlying_boundaries():
    """题面：「`market.json` 的原始字节数不得超过 1024，解码规则为 UTF-8，根值只能是 JSON 对象；只要出现重复成员名就作为无法继续的坏输入。根成员只能是下面四项，书写次序无需固定：」。"""
    for value in ('A', 'A234567890123456'):
        fixture([], market_value=market(underlying=value)); run_ok()
    for value in ('', 'alpha', 'A2345678901234567', '_ALPHA'):
        fixture([], market_value=market(underlying=value)); run_bad()
    assert not (OUTPUT/'surface.csv').exists()


def test_market_numeric_types_and_finiteness():
    """题面：「`market.json` 的原始字节数不得超过 1024，解码规则为 UTF-8，根值只能是 JSON 对象；只要出现重复成员名就作为无法继续的坏输入。根成员只能是下面四项，书写次序无需固定：」。"""
    for key in ('spot','rate_bps','dividend_bps'):
        legal = market(**{key: Decimal('0.5') if key != 'spot' else Decimal('1.5')})
        raw = ('{"underlying":"ALPHA","spot":%s,"rate_bps":%s,"dividend_bps":%s}' %
               tuple(str(legal[k]) if k != 'underlying' else legal[k] for k in ('spot','rate_bps','dividend_bps'))).encode()
        fixture([], raw_market=raw); run_ok()
        for token in ('true','"1"','null','NaN','Infinity'):
            fields = {'spot':'100','rate_bps':'0','dividend_bps':'0'}; fields[key] = token
            raw = f'{{"underlying":"ALPHA","spot":{fields["spot"]},"rate_bps":{fields["rate_bps"]},"dividend_bps":{fields["dividend_bps"]}}}'.encode()
            fixture([], raw_market=raw); run_bad()
    assert not (OUTPUT/'surface.csv').exists()


def test_market_spot_range():
    """题面：「`market.json` 的原始字节数不得超过 1024，解码规则为 UTF-8，根值只能是 JSON 对象；只要出现重复成员名就作为无法继续的坏输入。根成员只能是下面四项，书写次序无需固定：」。"""
    # BOUNDARY-SPOT-ACCEPT
    for value in (0.000001, 1_000_000):
        fixture([], market_value=market(spot=value)); run_ok()
    # BOUNDARY-SPOT-REJECT
    for value in (0, -0.000001, 1_000_000.000001):
        fixture([], market_value=market(spot=value)); run_bad()
    assert not (OUTPUT/'surface.csv').exists()


def test_market_rate_range():
    """题面：「`market.json` 的原始字节数不得超过 1024，解码规则为 UTF-8，根值只能是 JSON 对象；只要出现重复成员名就作为无法继续的坏输入。根成员只能是下面四项，书写次序无需固定：」。"""
    # CASE LEG-006-A
    for value in (-100_000, 100_000):
        fixture([], market_value=market(rate_bps=value)); run_ok()
    for value in (-100_000.000001, 100_000.000001):
        fixture([], market_value=market(rate_bps=value)); run_bad()
    assert not (OUTPUT/'surface.csv').exists()


def test_market_dividend_range():
    """题面：「`market.json` 的原始字节数不得超过 1024，解码规则为 UTF-8，根值只能是 JSON 对象；只要出现重复成员名就作为无法继续的坏输入。根成员只能是下面四项，书写次序无需固定：」。"""
    for value in (-100_000, 100_000):
        fixture([], market_value=market(dividend_bps=value)); run_ok()
    for value in (-100_000.000001, 100_000.000001):
        fixture([], market_value=market(dividend_bps=value)); run_bad()
    assert not (OUTPUT/'surface.csv').exists()


def test_csv_encoding_and_exact_header():
    """题面：「解码成功不等于业务通过。单行内容不合法时，只向审计写入一次拒绝，继续检查后续行，并让该行退出胜者竞争：」。"""
    # CASE LEG-001-B LEG-003-A LEG-004-A LEG-005-A LEG-008-A LEG-010-A LEG-011-A LEG-014-A LEG-016-A LEG-017-A
    fixture([]); run_ok()
    fixture(raw_csv=b'\xff', rows=[]); run_bad()
    for header in (HEADER[::-1], HEADER[:-1], HEADER + ['extra']):
        fixture([], raw_csv=csv_bytes([], header)); run_bad()
    assert not (OUTPUT/'surface.csv').exists()


def test_csv_row_count_and_structure():
    """题面：「解码成功不等于业务通过。单行内容不合法时，只向审计写入一次拒绝，继续检查后续行，并让该行退出胜者竞争：」。"""
    # CASE LEG-003-B LEG-004-B LEG-005-B LEG-009-A LEG-010-B LEG-011-B LEG-014-B LEG-016-B LEG-017-B
    fixture([]); run_ok()
    rows = [q(f'q{i}', strike=100) for i in range(500)]
    fixture(rows); run_ok(); assert summary()['input_rows'] == 500
    fixture(rows + [q('q500')]); run_bad()
    for raw in (b'quote_id,underlying,expiry_days,strike,venue,revision,status,bid,ask\na,"broken\n',
                csv_bytes([]) + b'a,ALPHA,30\n',
                csv_bytes([]) + b'a,ALPHA,30,100,XN,1,ACTIVE,1,1,extra\n'):
        fixture(raw_csv=raw); run_bad()


def test_invalid_row_rejected_once_and_processing_continues():
    """题面：「移除取消合约以后，非空网格里的每条期限必须使用同一个数值 strike 集合；集合不一致会让整批失败，并沿用上一段的旧结果保护。只有共同网格存在，后续期限方向调整才不会破坏已完成的执行价方向约束。」。"""
    bad = q('bad', expiry='0', venue='bad', bid='-1', ask='x')
    fixture([bad, q('good', expiry='30', strike='100', bid='7', ask='9')])
    run_ok()
    assert summary()['rejected_rows'] == 1
    assert [(r['expiry_days'], r['raw_mid']) for r in surface()] == [('30','8.000000')]
    assert audit()[0] == {'kind':'rejected','identity':'line:2:bad','reason':'invalid_quote'}


def test_quote_id_syntax_and_occupation():
    """题面：「把 `(underlying, expiry_days, strike)` 看成一个合约键，再把键相同的合法记录按 `(revision, venue, quote_id)` 做稳定字典序竞争，最终只保留最大记录。失败记录不能因为最大记录撤单而递补。最大记录若标为 `CANCELLED`，此键离开网格，并进入撤单审计。」。"""
    assert_accepted(q('a'))
    assert_accepted(q('A' * 64))
    for value in ('', 'a b', 'é', 'a' * 65): assert_rejected(q(value))
    fixture([q('occupied', expiry='0'), q('occupied', expiry='30')]); run_ok()
    assert summary()['rejected_rows'] == 2 and summary()['selected_quotes'] == 0


def test_quote_underlying_exact_match():
    """题面：「把 `(underlying, expiry_days, strike)` 看成一个合约键，再把键相同的合法记录按 `(revision, venue, quote_id)` 做稳定字典序竞争，最终只保留最大记录。失败记录不能因为最大记录撤单而递补。最大记录若标为 `CANCELLED`，此键离开网格，并进入撤单审计。」。"""
    assert_accepted(q('a', underlying='ALPHA'))
    assert_rejected(q('b', underlying='Alpha'))
    assert_rejected(q('c', underlying='BETA'))
    assert surface() == [] and summary()['rejected_rows'] == 1


def test_expiry_syntax_and_range():
    """题面：「移除取消合约以后，非空网格里的每条期限必须使用同一个数值 strike 集合；集合不一致会让整批失败，并沿用上一段的旧结果保护。只有共同网格存在，后续期限方向调整才不会破坏已完成的执行价方向约束。」。"""
    # CASE LEG-006-B
    for value in ('1','01','3650'): assert_accepted(q('a'+value, expiry=value))
    for index, value in enumerate(('0','3651','+1','１','1.0','')): assert_rejected(q(f'x{index}', expiry=value))
    assert surface() == [] and summary()['rejected_rows'] == 1


def test_strike_syntax_length_and_range():
    """题面：「移除取消合约以后，非空网格里的每条期限必须使用同一个数值 strike 集合；集合不一致会让整批失败，并沿用上一段的旧结果保护。只有共同网格存在，后续期限方向调整才不会破坏已完成的执行价方向约束。」。"""
    # CASE LEG-007-A LEG-012-A LEG-013-A
    for index, value in enumerate(('0.000001','1e2','1000000','00000000000000000000000000000001')):
        assert len(value) <= 32; assert_accepted(q(f'a{index}', strike=value))
    invalid = ('0','-1','1000000.000001','NaN','Infinity','１','1_0','0.00000000000000000000000000000001')
    assert len(invalid[-1]) == 34
    for index, value in enumerate(invalid): assert_rejected(q(f'x{index}', strike=value))


def test_venue_syntax_boundaries():
    """题面：「移除取消合约以后，非空网格里的每条期限必须使用同一个数值 strike 集合；集合不一致会让整批失败，并沿用上一段的旧结果保护。只有共同网格存在，后续期限方向调整才不会破坏已完成的执行价方向约束。」。"""
    # CASE LEG-007-B LEG-008-B LEG-012-B LEG-013-B
    for value in ('AA','ABCDEFGH'): assert_accepted(q('a'+value, venue=value))
    for index, value in enumerate(('A','ABCDEFGHI','Xn','12','ÄA')): assert_rejected(q(f'x{index}', venue=value))
    assert surface() == [] and summary()['rejected_rows'] == 1


def test_revision_syntax_length_and_range():
    """题面：「移除取消合约以后，非空网格里的每条期限必须使用同一个数值 strike 集合；集合不一致会让整批失败，并沿用上一段的旧结果保护。只有共同网格存在，后续期限方向调整才不会破坏已完成的执行价方向约束。」。"""
    # CASE LEG-009-B
    for value in ('0','0000000001','1000000000'): assert_accepted(q('a'+value[-3:], revision=value))
    for index, value in enumerate(('-1','+1','１','10000000000','1000000001','1.0','')): assert_rejected(q(f'x{index}', revision=value))
    assert surface() == [] and summary()['rejected_rows'] == 1


def test_active_status_and_price_validation():
    """题面：「移除取消合约以后，非空网格里的每条期限必须使用同一个数值 strike 集合；集合不一致会让整批失败，并沿用上一段的旧结果保护。只有共同网格存在，后续期限方向调整才不会破坏已完成的执行价方向约束。」。"""
    for index, pair in enumerate((('0','0'),('0','1000000'),('.5','1e0'))): assert_accepted(q(f'a{index}', bid=pair[0], ask=pair[1]))
    bad = [('UNKNOWN','1','1'),('ACTIVE','NaN','1'),('ACTIVE','1','Infinity'),('ACTIVE','-1','1'),
           ('ACTIVE','2','1'),('ACTIVE','1','1000000.000001'),('ACTIVE','0.00000000000000000000000000000001','1')]
    assert len(bad[-1][1]) == 34
    for index, (status,bid,ask) in enumerate(bad): assert_rejected(q(f'x{index}', status=status, bid=bid, ask=ask))


def test_cancelled_price_fields():
    """题面：「移除取消合约以后，非空网格里的每条期限必须使用同一个数值 strike 集合；集合不一致会让整批失败，并沿用上一段的旧结果保护。只有共同网格存在，后续期限方向调整才不会破坏已完成的执行价方向约束。」。"""
    rows, report, _ = one_row_result(q('c', status='CANCELLED', bid='', ask=''))
    assert rows == [] and report['cancelled_contracts'] == 1 and report['rejected_rows'] == 0
    assert_rejected(q('x1', status='CANCELLED', bid='1', ask=''))
    assert_rejected(q('x2', status='CANCELLED', bid='', ask='1'))


def test_fatal_errors_preserve_existing_and_missing_outputs():
    """题面：「移除取消合约以后，非空网格里的每条期限必须使用同一个数值 strike 集合；集合不一致会让整批失败，并沿用上一段的旧结果保护。只有共同网格存在，后续期限方向调整才不会破坏已完成的执行价方向约束。」。"""
    def check(raw_market=None, raw_csv=None, rows=()):
        fixture(rows, raw_market=raw_market, raw_csv=raw_csv)
        (OUTPUT/'surface.csv').write_bytes(b'OLD-SURFACE\x00')
        (OUTPUT/'audit.jsonl').write_bytes(b'OLD-AUDIT\n')
        summary_path = OUTPUT/'risk_summary.json'
        summary_path.unlink(missing_ok=True)
        run_bad()
        return ((OUTPUT/'surface.csv').read_bytes(),
                (OUTPUT/'audit.jsonl').read_bytes(), summary_path.exists())
    expected = (b'OLD-SURFACE\x00', b'OLD-AUDIT\n', False)
    assert check(raw_market=b'{}') == expected
    assert check(raw_csv=csv_bytes([]) + b'bad,row\n') == expected
    assert check(rows=[q('a',30,100),q('b',60,100),q('c',60,120)]) == expected


def test_winner_revision_priority():
    """题面：「移除取消合约以后，非空网格里的每条期限必须使用同一个数值 strike 集合；集合不一致会让整批失败，并沿用上一段的旧结果保护。只有共同网格存在，后续期限方向调整才不会破坏已完成的执行价方向约束。」。"""
    fixture([q('high', bid='8', ask='8', revision='10'), q('low', bid='2', ask='2', revision='9')])
    run_ok(); assert surface()[0]['raw_mid'] == '8.000000'


def test_winner_venue_tiebreak():
    """题面：「移除取消合约以后，非空网格里的每条期限必须使用同一个数值 strike 集合；集合不一致会让整批失败，并沿用上一段的旧结果保护。只有共同网格存在，后续期限方向调整才不会破坏已完成的执行价方向约束。」。"""
    fixture([q('z-low', venue='AA', bid='2', ask='2'), q('a-high', venue='ZZ', bid='8', ask='8')])
    run_ok(); assert surface()[0]['raw_mid'] == '8.000000'


def test_winner_quote_id_tiebreak():
    """题面：「把 `(underlying, expiry_days, strike)` 看成一个合约键，再把键相同的合法记录按 `(revision, venue, quote_id)` 做稳定字典序竞争，最终只保留最大记录。失败记录不能因为最大记录撤单而递补。最大记录若标为 `CANCELLED`，此键离开网格，并进入撤单审计。」。"""
    fixture([q('a', bid='2', ask='2'), q('z', bid='8', ask='8')])
    run_ok(); assert surface()[0]['raw_mid'] == '8.000000'


def test_cancelled_winner_has_no_fallback():
    """题面：「移除取消合约以后，非空网格里的每条期限必须使用同一个数值 strike 集合；集合不一致会让整批失败，并沿用上一段的旧结果保护。只有共同网格存在，后续期限方向调整才不会破坏已完成的执行价方向约束。」。"""
    fixture([q('old', revision='1', bid='9', ask='9'), q('new', revision='2', status='CANCELLED', bid='', ask='')])
    run_ok()
    assert surface() == [] and summary()['cancelled_contracts'] == 1
    assert audit() == [{'kind':'cancelled','identity':'ALPHA|30|100.000000','reason':'winning_revision_cancelled'}]


def test_common_numeric_strike_grid():
    """题面：「移除取消合约以后，非空网格里的每条期限必须使用同一个数值 strike 集合；集合不一致会让整批失败，并沿用上一段的旧结果保护。只有共同网格存在，后续期限方向调整才不会破坏已完成的执行价方向约束。」。"""
    fixture([q('a',30,'100.0'),q('b',60,'1e2')]); run_ok(); assert len(surface()) == 2
    fixture([q('a',30,100),q('b',60,100),q('c',60,120)]); run_bad()


def test_midpoint_discounted_bounds_and_rate_bps():
    """题面：「移除取消合约以后，非空网格里的每条期限必须使用同一个数值 strike 集合；集合不一致会让整批失败，并沿用上一段的旧结果保护。只有共同网格存在，后续期限方向调整才不会破坏已完成的执行价方向约束。」。"""
    fixture([q('lo',365,90,bid='0',ask='0')], market_value=market(rate_bps=1000))
    run_ok(); row=surface()[0]
    with localcontext() as ctx:
        ctx.prec=60
        lower=max(Decimal(0), Decimal(100)-Decimal(90)*Decimal('-.1').exp())
    assert row['raw_mid']=='0.000000' and row['adjusted_price']==f'{lower.quantize(Decimal(".000001"),rounding=ROUND_HALF_EVEN):f}'
    fixture([q('mid',365,100,bid='10',ask='14')], market_value=market(rate_bps=1000)); run_ok()
    assert surface()[0]['raw_mid']=='12.000000' and surface()[0]['adjusted_price']=='12.000000'
    fixture([q('hi',365,110,bid='200',ask='200')], market_value=market(rate_bps=1000)); run_ok()
    assert surface()[0]['adjusted_price']=='100.000000'

def test_dividend_discount_formula():
    """题面：「移除取消合约以后，非空网格里的每条期限必须使用同一个数值 strike 集合；集合不一致会让整批失败，并沿用上一段的旧结果保护。只有共同网格存在，后续期限方向调整才不会破坏已完成的执行价方向约束。」。"""
    fixture([q('a',365,100,bid='200',ask='200')], market_value=market(dividend_bps=1000))
    run_ok()
    with localcontext() as ctx:
        ctx.prec=60; expected=Decimal(100)*Decimal('-.1').exp()
    assert surface()[0]['adjusted_price'] == f'{expected.quantize(Decimal(".000001"),rounding=ROUND_HALF_EVEN):f}'


def test_numeric_strike_sort_and_monotonicity():
    """题面：「移除取消合约以后，非空网格里的每条期限必须使用同一个数值 strike 集合；集合不一致会让整批失败，并沿用上一段的旧结果保护。只有共同网格存在，后续期限方向调整才不会破坏已完成的执行价方向约束。」。"""
    fixture([q('right',30,'10',bid='1',ask='1'),q('left',30,'2',bid='.8',ask='.8')], market_value=market(spot=1))
    run_ok(); rows=surface()
    assert [(r['strike'],r['adjusted_price']) for r in rows] == [('2.000000','0.800000'),('10.000000','0.800000')]
    assert audit()[-1] == {'kind':'adjusted','identity':'ALPHA|30|10.000000','reason':'monotonicity'}


def test_convexity_neighbor_average():
    """题面：「移除取消合约以后，非空网格里的每条期限必须使用同一个数值 strike 集合；集合不一致会让整批失败，并沿用上一段的旧结果保护。只有共同网格存在，后续期限方向调整才不会破坏已完成的执行价方向约束。」。"""
    fixture([q('a',30,100,bid='10',ask='10'),q('b',30,120,bid='9',ask='9'),q('c',30,140,bid='2',ask='2')])
    run_ok(); assert [r['adjusted_price'] for r in surface()] == ['10.000000','6.000000','2.000000']
    assert audit()[-1] == {'kind':'adjusted','identity':'ALPHA|30|120.000000','reason':'convexity'}


def test_iterative_sweeps_and_decimal_rounding():
    """题面：「移除取消合约以后，非空网格里的每条期限必须使用同一个数值 strike 集合；集合不一致会让整批失败，并沿用上一段的旧结果保护。只有共同网格存在，后续期限方向调整才不会破坏已完成的执行价方向约束。」。"""
    fixture([q('a',30,100,bid='1',ask='1'),q('b',30,110,bid='1',ask='1'),q('c',30,120,bid='1',ask='1'),q('d',30,130,bid='0',ask='0')])
    run_ok(); values=[r['adjusted_price'] for r in surface()]
    assert values == ['1.000000','0.666667','0.333333','0.000000']
    nums=[Decimal(x) for x in values]
    assert all(2*nums[i] <= nums[i-1]+nums[i+1]+Decimal('.000001') for i in (1,2))


def test_calendar_raises_longer_expiry():
    """题面：「移除取消合约以后，非空网格里的每条期限必须使用同一个数值 strike 集合；集合不一致会让整批失败，并沿用上一段的旧结果保护。只有共同网格存在，后续期限方向调整才不会破坏已完成的执行价方向约束。」。"""
    rows=[]
    for expiry, prices in ((30,(10,8,6)),(90,(9,7,5))):
        for index,(strike,price) in enumerate(zip((100,120,140),prices)): rows.append(q(f'q{expiry}-{index}',expiry,strike,bid=price,ask=price))
    fixture(rows); run_ok(); groups={}
    for row in surface(): groups.setdefault(row['expiry_days'],[]).append(row['adjusted_price'])
    assert groups == {'30':['10.000000','8.000000','6.000000'],'90':['10.000000','8.000000','6.000000']}
    assert [x['reason'] for x in audit()] == ['calendar','calendar','calendar']


def test_surface_schema_sorting_and_half_even_format():
    """题面：「移除取消合约以后，非空网格里的每条期限必须使用同一个数值 strike 集合；集合不一致会让整批失败，并沿用上一段的旧结果保护。只有共同网格存在，后续期限方向调整才不会破坏已完成的执行价方向约束。」。"""
    fixture([q('d',90,120,bid='2.0000005',ask='2.0000005'),q('c',90,80,bid='4',ask='4'),
             q('b',30,120,bid='1.2345565',ask='1.2345565'),q('a',30,80,bid='3',ask='3')])
    run_ok(); raw=(OUTPUT/'surface.csv').read_text(); rows=surface()
    assert raw.splitlines()[0] == ','.join(('underlying','expiry_days','strike','raw_mid','adjusted_price'))
    assert [(r['expiry_days'],r['strike']) for r in rows] == [('30','80.000000'),('30','120.000000'),('90','80.000000'),('90','120.000000')]
    assert all(re.fullmatch(r'\d+\.\d{6}', r[key]) for r in rows for key in ('strike','raw_mid','adjusted_price'))
    assert rows[1]['raw_mid'] == '1.234556'


def test_summary_serialization_and_exact_counts():
    """题面：「`/output/risk_summary.json` 是 UTF-8 紧凑 JSON，以单个换行收尾。根键按下面代码块的顺序输出：」。"""
    # CASE LEG-015-A
    rows=[q('bad',expiry='0'), q('c1',30,100,revision=1,bid=4,ask=4), q('c2',30,100,revision=2,bid=6,ask=6),
          q('c3',30,100,revision=3,bid=8,ask=8), q('can',30,90,status='CANCELLED',bid='',ask=''),
          q('right',30,120,bid=12,ask=12)]
    fixture(rows); run_ok(); raw=(OUTPUT/'risk_summary.json').read_bytes(); report=summary()
    assert raw == b'{"input_rows":6,"rejected_rows":1,"selected_quotes":2,"cancelled_contracts":1,"adjusted_points":1,"max_abs_adjustment":"4.000000"}\n'
    assert list(report) == ['input_rows','rejected_rows','selected_quotes','cancelled_contracts','adjusted_points','max_abs_adjustment']
    assert all(type(report[key]) is int and report[key] >= 0 for key in list(report)[:5])


def test_max_adjustment_and_empty_surface():
    """题面：「移除取消合约以后，非空网格里的每条期限必须使用同一个数值 strike 集合；集合不一致会让整批失败，并沿用上一段的旧结果保护。只有共同网格存在，后续期限方向调整才不会破坏已完成的执行价方向约束。」。"""
    # CASE LEG-015-B
    fixture([q('a',30,100,bid=10,ask=10),q('b',30,120,bid=12,ask=12),q('c',30,140,bid=20,ask=20)])
    run_ok(); assert summary()['max_abs_adjustment'] == '10.000000'
    fixture([]); run_ok(); assert summary()['max_abs_adjustment'] == '0.000000'


def test_audit_rejected_records():
    """题面：「`/output/risk_summary.json` 是 UTF-8 紧凑 JSON，以单个换行收尾。根键按下面代码块的顺序输出：」。"""
    fixture([q('z bad',expiry=30),q('ok',expiry=30),q('a',expiry=0)])
    run_ok(); assert audit() == [
        {'kind':'rejected','identity':'line:2:z bad','reason':'invalid_quote'},
        {'kind':'rejected','identity':'line:4:a','reason':'invalid_quote'}]


def test_rejected_released_rejected_transition():
    """题面：「解码成功不等于业务通过。单行内容不合法时，只向审计写入一次拒绝，继续检查后续行，并让该行退出胜者竞争：」。"""
    fixture([q('bad',expiry=0),q('live',30,100,bid=8,ask=8),q('also-bad',expiry=4000)])
    run_ok()
    transitions = ["rejected", "released", "rejected"]
    assert transitions == ["rejected", "released", "rejected"]
    assert [record['kind'] for record in audit()] == ["rejected", "rejected"]
    assert [row['strike'] for row in surface()] == ["100.000000"]


def test_audit_cancelled_records():
    """题面：「`/output/risk_summary.json` 是 UTF-8 紧凑 JSON，以单个换行收尾。根键按下面代码块的顺序输出：」。"""
    fixture([q('z',90,120,status='CANCELLED',bid='',ask=''),q('a',30,100,status='CANCELLED',bid='',ask='')])
    run_ok(); assert audit() == [
        {'kind':'cancelled','identity':'ALPHA|30|100.000000','reason':'winning_revision_cancelled'},
        {'kind':'cancelled','identity':'ALPHA|90|120.000000','reason':'winning_revision_cancelled'}]


def test_audit_adjusted_reason_order_and_uniqueness():
    """题面：「underlying,expiry_days,strike,raw_mid,adjusted_price」。"""
    rows=[]
    for expiry, prices in ((30,(8,7,6)),(90,(10,200,0))):
        for index,(strike,price) in enumerate(zip((100,120,140),prices)): rows.append(q(f'q{expiry}-{index}',expiry,strike,bid=price,ask=price))
    fixture(rows); run_ok(); records=audit()
    target=[x for x in records if x['identity']=='ALPHA|90|120.000000']
    assert target == [{'kind':'adjusted','identity':'ALPHA|90|120.000000','reason':'bounds+monotonicity+convexity+calendar'}]
    adjusted=[x['identity'] for x in records if x['kind']=='adjusted']
    assert adjusted == sorted(adjusted, key=lambda x:(int(x.split('|')[1]),Decimal(x.split('|')[2])))
    assert len(adjusted)==len(set(adjusted))
    assert all(list(x)==['kind','identity','reason'] for x in records)
    raw=(OUTPUT/'audit.jsonl').read_bytes()
    assert raw.endswith(b'\n') and not raw.endswith(b'\n\n') and b' ' not in raw


def test_input_files_unchanged():
    """题面：「移除取消合约以后，非空网格里的每条期限必须使用同一个数值 strike 集合；集合不一致会让整批失败，并沿用上一段的旧结果保护。只有共同网格存在，后续期限方向调整才不会破坏已完成的执行价方向约束。」。"""
    # CASE LEG-018-A
    rows=[q('a',30,100,bid=8,ask=8),q('b',30,120,bid=12,ask=12)]
    fixture(rows)
    before=[hashlib.sha256((DATA/name).read_bytes()).hexdigest() for name in ('market.json','quotes.csv')]
    run_ok()
    after=[hashlib.sha256((DATA/name).read_bytes()).hexdigest() for name in ('market.json','quotes.csv')]
    assert before == after


def test_rerun_overwrites_all_outputs():
    """题面：「underlying,expiry_days,strike,raw_mid,adjusted_price」。"""
    # CASE LEG-018-B
    rows=[q('bad',expiry=0),q('can',30,90,status='CANCELLED',bid='',ask=''),q('a',30,100,bid=8,ask=8),q('b',30,120,bid=12,ask=12)]
    fixture(rows); run_ok()
    assert all((OUTPUT/name).is_file() for name in NAMES) and audit()
    fixture([q('fresh',90,100,bid=3,ask=3)], clear=False); run_ok()
    assert surface() == [{'underlying':'ALPHA','expiry_days':'90','strike':'100.000000','raw_mid':'3.000000','adjusted_price':'3.000000'}]
    assert summary() == {'input_rows':1,'rejected_rows':0,'selected_quotes':1,'cancelled_contracts':0,'adjusted_points':0,'max_abs_adjustment':'0.000000'}
    assert audit() == [] and all((OUTPUT/name).is_file() for name in NAMES)


def test_repeat_is_byte_stable():
    """题面：「没有改动的输入再跑一遍，三份产物应完全同字节。输入发生变化时，旧曲面、旧汇总和旧审计都必须被本次结果整体替换。」。"""
    rows=[q('bad',expiry=0),q('can',30,90,status='CANCELLED',bid='',ask=''),q('a',30,100,bid=8,ask=8),q('b',30,120,bid=12,ask=12)]
    fixture(rows); run_ok()
    first=[(OUTPUT/name).read_bytes() for name in NAMES]
    run_ok()
    assert first == [(OUTPUT/name).read_bytes() for name in NAMES]

def test_cross_core_combination():
    """题面：「移除取消合约以后，非空网格里的每条期限必须使用同一个数值 strike 集合；集合不一致会让整批失败，并沿用上一段的旧结果保护。只有共同网格存在，后续期限方向调整才不会破坏已完成的执行价方向约束。」。"""
    rows=[q('invalid',30,100,venue='bad'),q('old',30,100,revision=1,bid=1,ask=1),q('new',30,100,revision=2,bid=10,ask=10),
          q('m',30,120,bid=12,ask=12),q('r',30,140,bid=0,ask=0),
          q('l2',90,100,bid=5,ask=5),q('m2',90,120,bid=4,ask=4),q('r2',90,140,bid=3,ask=3)]
    fixture(rows); run_ok(); values={}
    for row in surface(): values[(row['expiry_days'],row['strike'])]=Decimal(row['adjusted_price'])
    assert summary()['rejected_rows']==1 and values[('30','100.000000')]==Decimal(10)
    for expiry in ('30','90'):
        v=[values[(expiry,f'{k:.6f}')] for k in (100,120,140)]
        assert v[0]>=v[1]>=v[2] and 2*v[1]<=v[0]+v[2]
    assert all(values[('90',f'{k:.6f}')]>=values[('30',f'{k:.6f}')] for k in (100,120,140))


def test_generated_dataset_full_baseline():
    """题面：「移除取消合约以后，非空网格里的每条期限必须使用同一个数值 strike 集合；集合不一致会让整批失败，并沿用上一段的旧结果保护。只有共同网格存在，后续期限方向调整才不会破坏已完成的执行价方向约束。」。"""
    fixture(raw_market=ORIGINAL_MARKET, raw_csv=ORIGINAL_QUOTES); run_ok()
    expected_surface = ('underlying,expiry_days,strike,raw_mid,adjusted_price\n'
      'ALPHA,30,80.000000,20.200000,20.200000\nALPHA,30,100.000000,7.100000,7.100000\n'
      'ALPHA,30,120.000000,8.100000,7.100000\nALPHA,90,80.000000,19.100000,20.368422\n'
      'ALPHA,90,100.000000,6.100000,7.100000\nALPHA,90,120.000000,4.100000,7.100000\n')
    expected_summary = '{"input_rows":9,"rejected_rows":1,"selected_quotes":6,"cancelled_contracts":1,"adjusted_points":4,"max_abs_adjustment":"3.000000"}\n'
    expected_audit = ('{"kind":"rejected","identity":"line:10:q-bad","reason":"invalid_quote"}\n'
      '{"kind":"cancelled","identity":"ALPHA|180|100.000000","reason":"winning_revision_cancelled"}\n'
      '{"kind":"adjusted","identity":"ALPHA|30|120.000000","reason":"monotonicity"}\n'
      '{"kind":"adjusted","identity":"ALPHA|90|80.000000","reason":"bounds"}\n'
      '{"kind":"adjusted","identity":"ALPHA|90|100.000000","reason":"calendar"}\n'
      '{"kind":"adjusted","identity":"ALPHA|90|120.000000","reason":"calendar"}\n')
    assert (OUTPUT/'surface.csv').read_text() == expected_surface
    assert (OUTPUT/'risk_summary.json').read_text() == expected_summary
    assert (OUTPUT/'audit.jsonl').read_text() == expected_audit


CASE_IDS_BY_TEST = {'test_entrypoint_and_empty_input': [], 'test_market_size_boundary': ['LEG-001-A', 'LEG-002-A'], 'test_market_json_structure': ['LEG-002-B'], 'test_market_underlying_boundaries': [], 'test_market_numeric_types_and_finiteness': [], 'test_market_spot_range': [], 'test_market_rate_range': ['LEG-006-A'], 'test_market_dividend_range': [], 'test_csv_encoding_and_exact_header': ['LEG-001-B', 'LEG-003-A', 'LEG-004-A', 'LEG-005-A', 'LEG-008-A', 'LEG-010-A', 'LEG-011-A', 'LEG-014-A', 'LEG-016-A', 'LEG-017-A'], 'test_csv_row_count_and_structure': ['LEG-003-B', 'LEG-004-B', 'LEG-005-B', 'LEG-009-A', 'LEG-010-B', 'LEG-011-B', 'LEG-014-B', 'LEG-016-B', 'LEG-017-B'], 'test_invalid_row_rejected_once_and_processing_continues': [], 'test_quote_id_syntax_and_occupation': [], 'test_quote_underlying_exact_match': [], 'test_expiry_syntax_and_range': ['LEG-006-B'], 'test_strike_syntax_length_and_range': ['LEG-007-A', 'LEG-012-A', 'LEG-013-A'], 'test_venue_syntax_boundaries': ['LEG-007-B', 'LEG-008-B', 'LEG-012-B', 'LEG-013-B'], 'test_revision_syntax_length_and_range': ['LEG-009-B'], 'test_active_status_and_price_validation': [], 'test_cancelled_price_fields': [], 'test_fatal_errors_preserve_existing_and_missing_outputs': [], 'test_winner_revision_priority': [], 'test_winner_venue_tiebreak': [], 'test_winner_quote_id_tiebreak': [], 'test_cancelled_winner_has_no_fallback': [], 'test_common_numeric_strike_grid': [], 'test_midpoint_discounted_bounds_and_rate_bps': [], 'test_dividend_discount_formula': [], 'test_numeric_strike_sort_and_monotonicity': [], 'test_convexity_neighbor_average': [], 'test_iterative_sweeps_and_decimal_rounding': [], 'test_calendar_raises_longer_expiry': [], 'test_surface_schema_sorting_and_half_even_format': [], 'test_summary_serialization_and_exact_counts': ['LEG-015-A'], 'test_max_adjustment_and_empty_surface': ['LEG-015-B'], 'test_audit_rejected_records': [], 'test_rejected_released_rejected_transition': [], 'test_audit_cancelled_records': [], 'test_audit_adjusted_reason_order_and_uniqueness': [], 'test_input_files_unchanged': ['LEG-018-A'], 'test_rerun_overwrites_all_outputs': ['LEG-018-B'], 'test_repeat_is_byte_stable': [], 'test_cross_core_combination': [], 'test_generated_dataset_full_baseline': []}

@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if call.when == 'call':
        for case_id in CASE_IDS_BY_TEST.get(item.name, []):
            print(('PASS ' if report.passed else 'FAIL ') + case_id, flush=True)

if __name__ == "__main__":
    TESTS = [(name, value) for name, value in list(globals().items()) if name.startswith('test_') and callable(value)]
    selected = os.environ.get('TEST_FILTER')
    failures = 0
    executed = 0
    for name, function in TESTS:
        if selected and selected not in (name, *CASE_IDS.get(name, [])):
            continue
        executed += 1
        try:
            function()
            print('PASS ' + name, flush=True)
            for case_id in CASE_IDS.get(name, []):
                print('PASS ' + case_id, flush=True)
        except Exception as exc:
            failures += 1
            print('FAIL ' + name + ': ' + repr(exc), flush=True)
            for case_id in CASE_IDS.get(name, []):
                print('FAIL ' + case_id + ': ' + repr(exc), flush=True)
    if selected and executed == 0:
        print('ERROR unknown TEST_FILTER: ' + selected, flush=True)
        failures += 1
    raise SystemExit(1 if failures else 0)
