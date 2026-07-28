"""
CGHS rate matching engine.
Given a list of claimed line items (code and/or description + claimed amount),
matches each against the CGHS master rate table and computes the admissible
amount, flagging overcharges, no-matches, and likely duplicate billings.
"""
import json
from rapidfuzz import fuzz, process

def load_master(json_path):
    with open(json_path) as f:
        data = json.load(f)
    by_code = {r['code']: r for r in data}
    descriptions = {r['code']: r['description'] for r in data}
    return data, by_code, descriptions

def best_fuzzy_match(query, descriptions, score_cutoff=72):
    """descriptions: dict code -> description text"""
    if not query or not query.strip():
        return None, 0
    choices = descriptions
    result = process.extractOne(query, choices, scorer=fuzz.token_set_ratio, score_cutoff=score_cutoff)
    if result is None:
        return None, 0
    matched_text, score, code = result
    return code, score

def match_line_item(item, by_code, descriptions):
    """
    item: dict with optional 'code' and required 'description', 'claimed_amount'
    returns enriched dict with match info
    """
    code = item.get('code', '').strip().upper() if item.get('code') else ''
    matched_code = None
    match_method = None
    score = None

    if code and code in by_code:
        matched_code = code
        match_method = 'exact_code'
        score = 100
    else:
        # try fuzzy on description
        fm_code, fm_score = best_fuzzy_match(item.get('description',''), descriptions)
        if fm_code:
            matched_code = fm_code
            match_method = 'fuzzy_description'
            score = fm_score

    result = dict(item)
    if matched_code:
        rate_row = by_code[matched_code]
        cghs_rate = rate_row['rate_nabh']  # default to NABH; UI can toggle non-NABH
        result.update({
            'matched_code': matched_code,
            'matched_description': rate_row['description'],
            'cghs_rate_nabh': rate_row['rate_nabh'],
            'cghs_rate_non_nabh': rate_row['rate_non_nabh'],
            'match_method': match_method,
            'match_score': score,
            'admissible_amount': min(item.get('claimed_amount', 0), cghs_rate) if item.get('claimed_amount') is not None else cghs_rate,
        })
        claimed = item.get('claimed_amount', 0) or 0
        if claimed > cghs_rate:
            result['flag'] = 'OVERCHARGED'
        elif score is not None and score < 90:
            result['flag'] = 'REVIEW MATCH'
        else:
            result['flag'] = 'OK'
    else:
        result.update({
            'matched_code': None, 'matched_description': None,
            'cghs_rate_nabh': None, 'cghs_rate_non_nabh': None,
            'match_method': 'none', 'match_score': 0,
            'admissible_amount': None,
            'flag': 'NO MATCH - MANUAL REVIEW REQUIRED'
        })
    return result

def flag_duplicates(matched_items):
    """Mark items sharing the same matched_code as potential duplicate billing."""
    seen = {}
    for it in matched_items:
        c = it.get('matched_code')
        if c:
            seen.setdefault(c, []).append(it)
    for c, items in seen.items():
        if len(items) > 1:
            for it in items:
                if it['flag'] == 'OK':
                    it['flag'] = 'POSSIBLE DUPLICATE'
                else:
                    it['flag'] += ' + POSSIBLE DUPLICATE'
    return matched_items

def process_claim(line_items, master_json_path):
    _, by_code, descriptions = load_master(master_json_path)
    matched = [match_line_item(it, by_code, descriptions) for it in line_items]
    matched = flag_duplicates(matched)
    total_claimed = sum(it.get('claimed_amount') or 0 for it in matched)
    total_admissible = sum(it.get('admissible_amount') or 0 for it in matched if it.get('admissible_amount') is not None)
    return {
        'items': matched,
        'total_claimed': total_claimed,
        'total_admissible': total_admissible,
    }
