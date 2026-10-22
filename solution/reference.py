#!/usr/bin/env python3
import csv
import io
import json
import re
import sys
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN, localcontext
from pathlib import Path

DATA = Path('/data')
OUTPUT = Path('/output')
HEADER = ['quote_id', 'underlying', 'expiry_days', 'strike', 'venue', 'revision', 'status', 'bid', 'ask']
QID_RE = re.compile(r'[A-Za-z0-9._-]{1,64}\Z')
UNDERLYING_RE = re.compile(r'[A-Z][A-Z0-9_]{0,15}\Z')
VENUE_RE = re.compile(r'[A-Z]{2,8}\Z')
DIGITS_RE = re.compile(r'[0-9]+\Z')
DECIMAL_RE = re.compile(r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z')
Q12 = Decimal('0.000000000001')
Q6 = Decimal('0.000001')
REASON_ORDER = ('bounds', 'monotonicity', 'convexity', 'calendar')


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key')
        result[key] = value
    return result


def reject_constant(_value):
    raise ValueError('non-finite JSON number')


def parse_market():
    raw = (DATA / 'market.json').read_bytes()
    if len(raw) > 1024:
        raise ValueError('market too large')
    text = raw.decode('utf-8')
    market = json.loads(text, object_pairs_hook=unique_object, parse_int=Decimal,
                        parse_float=Decimal, parse_constant=reject_constant)
    if not isinstance(market, dict) or set(market) != {'underlying', 'spot', 'rate_bps', 'dividend_bps'}:
        raise ValueError('invalid market keys')
    underlying = market['underlying']
    if not isinstance(underlying, str) or UNDERLYING_RE.fullmatch(underlying) is None:
        raise ValueError('invalid underlying')
    numbers = []
    for key in ('spot', 'rate_bps', 'dividend_bps'):
        value = market[key]
        if isinstance(value, bool) or not isinstance(value, Decimal) or not value.is_finite():
            raise ValueError('invalid market number')
        numbers.append(value)
    spot, rate_bps, dividend_bps = numbers
    if not (Decimal(0) < spot <= Decimal(1_000_000)):
        raise ValueError('spot out of range')
    if not (Decimal(-100_000) <= rate_bps <= Decimal(100_000)):
        raise ValueError('rate out of range')
    if not (Decimal(-100_000) <= dividend_bps <= Decimal(100_000)):
        raise ValueError('dividend out of range')
    return market, spot, rate_bps, dividend_bps


def parse_text_decimal(text, *, max_length):
    if not isinstance(text, str) or len(text) > max_length or not text.isascii() or DECIMAL_RE.fullmatch(text) is None:
        raise ValueError('invalid decimal syntax')
    try:
        value = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError('invalid decimal') from exc
    if not value.is_finite():
        raise ValueError('non-finite decimal')
    return value


def parse_quotes(expected_underlying):
    raw = (DATA / 'quotes.csv').read_bytes()
    text = raw.decode('utf-8')
    reader = csv.reader(io.StringIO(text, newline=''), strict=True)
    try:
        header = next(reader)
    except StopIteration as exc:
        raise ValueError('missing CSV header') from exc
    if header != HEADER:
        raise ValueError('invalid CSV header')
    rows, rejected, occupied_ids = [], [], set()
    previous_end_line = reader.line_num
    record_count = 0
    try:
        for values in reader:
            start_line = previous_end_line + 1
            previous_end_line = reader.line_num
            record_count += 1
            if record_count > 500:
                raise ValueError('too many CSV rows')
            if len(values) != len(HEADER):
                raise ValueError('wrong CSV field count')
            row = dict(zip(HEADER, values))
            quote_id = row['quote_id']
            try:
                if QID_RE.fullmatch(quote_id) is None:
                    raise ValueError('invalid quote id')
                if quote_id in occupied_ids:
                    raise ValueError('duplicate quote id')
                occupied_ids.add(quote_id)
                if row['underlying'] != expected_underlying:
                    raise ValueError('underlying mismatch')
                if DIGITS_RE.fullmatch(row['expiry_days']) is None:
                    raise ValueError('invalid expiry syntax')
                expiry = int(row['expiry_days'])
                if not 1 <= expiry <= 3650:
                    raise ValueError('expiry out of range')
                strike = parse_text_decimal(row['strike'], max_length=32)
                if not Decimal(0) < strike <= Decimal(1_000_000):
                    raise ValueError('strike out of range')
                if VENUE_RE.fullmatch(row['venue']) is None:
                    raise ValueError('invalid venue')
                if len(row['revision']) > 10 or DIGITS_RE.fullmatch(row['revision']) is None:
                    raise ValueError('invalid revision syntax')
                revision = int(row['revision'])
                if not 0 <= revision <= 1_000_000_000:
                    raise ValueError('revision out of range')
                status = row['status']
                if status not in {'ACTIVE', 'CANCELLED'}:
                    raise ValueError('invalid status')
                if status == 'ACTIVE':
                    bid = parse_text_decimal(row['bid'], max_length=32)
                    ask = parse_text_decimal(row['ask'], max_length=32)
                    if not Decimal(0) <= bid <= ask <= Decimal(1_000_000):
                        raise ValueError('invalid market width')
                else:
                    if row['bid'] != '' or row['ask'] != '':
                        raise ValueError('cancelled prices must be empty')
                    bid = ask = None
                rows.append({'quote_id': quote_id, 'underlying': row['underlying'], 'expiry': expiry,
                             'strike': strike, 'venue': row['venue'], 'revision': revision,
                             'status': status, 'bid': bid, 'ask': ask})
            except (ValueError, InvalidOperation):
                rejected.append((start_line, quote_id))
    except csv.Error as exc:
        raise ValueError('invalid CSV structure') from exc
    return record_count, rows, rejected


def round12(value):
    return value.quantize(Q12, rounding=ROUND_HALF_EVEN)


def format6(value):
    return format(value.quantize(Q6, rounding=ROUND_HALF_EVEN), 'f')


def build_outputs(market, spot, rate_bps, dividend_bps, input_rows, valid_rows, rejected):
    winners = {}
    for row in valid_rows:
        key = (row['underlying'], row['expiry'], row['strike'])
        rank = (row['revision'], row['venue'], row['quote_id'])
        if key not in winners or rank > winners[key][0]:
            winners[key] = (rank, row)
    cancelled, active = [], {}
    for key, (_rank, row) in winners.items():
        if row['status'] == 'CANCELLED':
            cancelled.append(key)
        else:
            active[key] = row
    if active:
        grids = {}
        for underlying, expiry, strike in active:
            grids.setdefault((underlying, expiry), set()).add(strike)
        expected = None
        for group in sorted(grids):
            grid = grids[group]
            if expected is None:
                expected = grid
            elif grid != expected:
                raise ValueError('inconsistent strike grid')

    prices, raw_mids, reasons = {}, {}, {}
    with localcontext() as context:
        context.prec = 80
        for key, row in active.items():
            years = Decimal(row['expiry']) / Decimal(365)
            midpoint = (row['bid'] + row['ask']) / Decimal(2)
            discounted_spot = spot * (-dividend_bps / Decimal(10_000) * years).exp()
            discounted_strike = row['strike'] * (-rate_bps / Decimal(10_000) * years).exp()
            lower = max(Decimal(0), discounted_spot - discounted_strike)
            upper = discounted_spot
            bounded = min(max(midpoint, lower), upper)
            point_reasons = set()
            if bounded != midpoint:
                bounded = round12(bounded)
                point_reasons.add('bounds')
            prices[key], raw_mids[key], reasons[key] = bounded, midpoint, point_reasons

        for underlying, expiry in sorted({(u, e) for u, e, _k in prices}):
            keys = sorted((key for key in prices if key[:2] == (underlying, expiry)), key=lambda key: key[2])
            while True:
                changed = False
                for index in range(1, len(keys)):
                    left, right = keys[index - 1], keys[index]
                    if prices[right] > prices[left]:
                        new_value = round12(prices[left])
                        if new_value != prices[right]:
                            prices[right] = new_value
                            reasons[right].add('monotonicity')
                            changed = True
                for index in range(1, len(keys) - 1):
                    left, middle, right = keys[index - 1], keys[index], keys[index + 1]
                    cap = (prices[left] + prices[right]) / Decimal(2)
                    if prices[middle] > cap:
                        new_value = round12(cap)
                        if new_value != prices[middle]:
                            prices[middle] = new_value
                            reasons[middle].add('convexity')
                            changed = True
                if not changed:
                    break

        for underlying, strike in sorted({(u, k) for u, _e, k in prices}):
            keys = sorted((key for key in prices if key[0] == underlying and key[2] == strike), key=lambda key: key[1])
            for index in range(1, len(keys)):
                short, long = keys[index - 1], keys[index]
                if prices[long] < prices[short]:
                    new_value = round12(prices[short])
                    if new_value != prices[long]:
                        prices[long] = new_value
                        reasons[long].add('calendar')

    ordered_points = sorted(prices, key=lambda key: (key[0], key[1], key[2]))
    surface_lines = ['underlying,expiry_days,strike,raw_mid,adjusted_price']
    for key in ordered_points:
        surface_lines.append(','.join((key[0], str(key[1]), format6(key[2]),
                                       format6(raw_mids[key]), format6(prices[key]))))
    surface_bytes = ('\n'.join(surface_lines) + '\n').encode('utf-8')
    adjusted = [key for key in ordered_points if prices[key] != raw_mids[key]]
    max_adjustment = max((abs(prices[key] - raw_mids[key]) for key in ordered_points), default=Decimal(0))
    summary = {'input_rows': input_rows, 'rejected_rows': len(rejected),
               'selected_quotes': len(ordered_points), 'cancelled_contracts': len(cancelled),
               'adjusted_points': len(adjusted), 'max_abs_adjustment': format6(max_adjustment)}
    summary_bytes = (json.dumps(summary, ensure_ascii=False, separators=(',', ':')) + '\n').encode('utf-8')
    audit_records = []
    for line_number, quote_id in rejected:
        audit_records.append({'kind': 'rejected', 'identity': f'line:{line_number}:{quote_id}', 'reason': 'invalid_quote'})
    for key in sorted(cancelled, key=lambda item: (item[0], item[1], item[2])):
        audit_records.append({'kind': 'cancelled', 'identity': f'{key[0]}|{key[1]}|{format6(key[2])}',
                              'reason': 'winning_revision_cancelled'})
    for key in adjusted:
        ordered_reasons = '+'.join(reason for reason in REASON_ORDER if reason in reasons[key])
        audit_records.append({'kind': 'adjusted', 'identity': f'{key[0]}|{key[1]}|{format6(key[2])}',
                              'reason': ordered_reasons})
    audit_bytes = ''.join(json.dumps(record, ensure_ascii=False, separators=(',', ':')) + '\n'
                          for record in audit_records).encode('utf-8')
    return surface_bytes, summary_bytes, audit_bytes


def main():
    market, spot, rate_bps, dividend_bps = parse_market()
    input_rows, valid_rows, rejected = parse_quotes(market['underlying'])
    payloads = build_outputs(market, spot, rate_bps, dividend_bps, input_rows, valid_rows, rejected)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for name, payload in zip(('surface.csv', 'risk_summary.json', 'audit.jsonl'), payloads):
        (OUTPUT / name).write_bytes(payload)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(f'error: {exc}', file=sys.stderr)
        raise SystemExit(1)
