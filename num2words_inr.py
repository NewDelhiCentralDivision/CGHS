"""Minimal Indian-numbering (lakh/crore) number-to-words for rupee amounts, no external deps."""

ONES = ['', 'One', 'Two', 'Three', 'Four', 'Five', 'Six', 'Seven', 'Eight', 'Nine',
        'Ten', 'Eleven', 'Twelve', 'Thirteen', 'Fourteen', 'Fifteen', 'Sixteen',
        'Seventeen', 'Eighteen', 'Nineteen']
TENS = ['', '', 'Twenty', 'Thirty', 'Forty', 'Fifty', 'Sixty', 'Seventy', 'Eighty', 'Ninety']

def _two_digit(n):
    if n < 20:
        return ONES[n]
    return (TENS[n // 10] + (' ' + ONES[n % 10] if n % 10 else '')).strip()

def _three_digit(n):
    if n < 100:
        return _two_digit(n)
    return ONES[n // 100] + ' Hundred' + (' ' + _two_digit(n % 100) if n % 100 else '')

def number_to_words_inr(n):
    n = int(round(n))
    if n == 0:
        return 'Zero'
    parts = []
    crore = n // 10000000; n %= 10000000
    lakh = n // 100000; n %= 100000
    thousand = n // 1000; n %= 1000
    hundred = n
    if crore: parts.append(_three_digit(crore) + ' Crore')
    if lakh: parts.append(_three_digit(lakh) + ' Lakh')
    if thousand: parts.append(_three_digit(thousand) + ' Thousand')
    if hundred: parts.append(_three_digit(hundred))
    return ' '.join(parts)

def rupees_in_words(amount):
    return f"Rupees {number_to_words_inr(amount)} Only"

if __name__ == '__main__':
    for v in [4220, 3720, 500, 1000000, 123456]:
        print(v, '->', rupees_in_words(v))
