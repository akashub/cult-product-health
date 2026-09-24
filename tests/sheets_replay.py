"""Replays an .xlsx as if it came from the Google Sheets API
(UNFORMATTED_VALUE + SERIAL_NUMBER): dates become serial numbers, blanks
become "", trailing blanks are trimmed so rows are uneven."""

from datetime import date, datetime

import openpyxl

from cultph.sources import GSheetSource

EPOCH = datetime(1899, 12, 30)


def _cell(v):
    if v is None:
        return ""
    if isinstance(v, datetime):
        return (v - EPOCH).total_seconds() / 86400
    if isinstance(v, date):
        return (datetime(v.year, v.month, v.day) - EPOCH).days
    if isinstance(v, float) and v.is_integer():
        return int(v)  # Sheets returns whole numbers as ints
    return v


class _WS:
    def __init__(self, rows):
        self.rows = rows

    def get_all_values(self, **kw):
        assert kw.get("value_render_option") == "UNFORMATTED_VALUE"
        return self.rows


class _Sheet:
    def __init__(self, tabs):
        self.tabs = tabs

    def worksheet(self, name):
        return _WS(self.tabs[name])


def replay(xlsx_path) -> GSheetSource:
    wb = openpyxl.load_workbook(xlsx_path, read_only=True, data_only=True)
    tabs = {}
    for ws in wb.worksheets:
        rows = []
        for r in ws.iter_rows(values_only=True):
            vals = [_cell(v) for v in r]
            while vals and vals[-1] == "":
                vals.pop()
            rows.append(vals)
        while rows and not rows[-1]:
            rows.pop()
        tabs[ws.title] = rows
    src = GSheetSource.__new__(GSheetSource)  # skip OAuth
    src._sh = _Sheet(tabs)
    return src
