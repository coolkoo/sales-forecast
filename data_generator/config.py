"""Static definitions for the synthetic dataset — KFC **Vietnam**.

One brand ("KFC") across 8 Vietnamese store locations, priced in VND, with
Vietnamese menu names, VN public holidays + retail driver days (Tết modelled as a
multi-week regime, plus the two Women's Days, Children's Day, Teachers' Day,
Mid-Autumn), and per-store open dates so the forecaster can learn the new-store
maturation ramp.

Everything here is the *ground-truth generative model* the forecaster/anomaly
detector must recover. Keep it declarative.
"""
from __future__ import annotations

import datetime as dt

START_DATE = dt.date(2024, 7, 1)
END_DATE = dt.date(2026, 6, 30)
CHECK_DETAIL_DAYS = 60
SEED = 20260708

DAYPARTS = ["lunch", "dinner", "late"]
RVC_CODE = {"breakfast": 11, "lunch": 12, "dinner": 13, "late": 14}

BRANDS = {
    "KFC": {"name": "KFC Vietnam", "format": "qsr_chicken",
            "dayparts": ["lunch", "dinner", "late"]},
}

# store_id, brand, region, timezone, lat, lon, volume_scale, annual_growth, opened
STORES = [
    ("KFC-HCM01", "KFC", "South",   "Asia/Ho_Chi_Minh", 10.78, 106.70, 1.35, 0.08, "2016-03-01"),  # HCMC Dist 1
    ("KFC-HCM07", "KFC", "South",   "Asia/Ho_Chi_Minh", 10.73, 106.72, 1.20, 0.07, "2018-06-01"),  # HCMC Dist 7
    ("KFC-HN01",  "KFC", "North",   "Asia/Ho_Chi_Minh", 21.03, 105.85, 1.25, 0.07, "2017-09-01"),  # Hanoi Hoan Kiem
    ("KFC-HN02",  "KFC", "North",   "Asia/Ho_Chi_Minh", 21.03, 105.79, 1.05, 0.06, "2019-11-01"),  # Hanoi Cau Giay
    ("KFC-DN01",  "KFC", "Central", "Asia/Ho_Chi_Minh", 16.05, 108.22, 1.00, 0.09, "2020-05-01"),  # Da Nang
    ("KFC-CT01",  "KFC", "South",   "Asia/Ho_Chi_Minh", 10.03, 105.78, 0.90, 0.08, "2021-08-01"),  # Can Tho
    ("KFC-HP01",  "KFC", "North",   "Asia/Ho_Chi_Minh", 20.86, 106.68, 0.95, 0.14, "2025-05-01"),  # Hai Phong — NEW (ramping)
    ("KFC-BH01",  "KFC", "South",   "Asia/Ho_Chi_Minh", 10.95, 106.82, 0.85, 0.18, "2025-12-15"),  # Bien Hoa — NEW (early ramp)
]

# Region climate in °F (model is unit-agnostic; 65°F baseline). VN = tropical.
REGION_CLIMATE = {
    "South":   (82, 6, 0.45),
    "North":   (75, 14, 0.38),
    "Central": (79, 9, 0.42),
}

# Menu — Vietnamese names, VND prices. bom lists (sku, qty_per). base_pop = units/day
# at a scale-1.0 store across dayparts. weather = (temp_beta, rain_beta).
MENU = {
    "KFC": [
        ("KFC-ORIG-2PC",  "Gà Rán Truyền Thống (2 miếng)", "chicken",   74000, ["lunch", "dinner", "late"], 120, (-0.02, 0.03), [("CHICKEN-BONEIN", 2), ("BREADING", 0.15), ("OIL", 0.10)]),
        ("KFC-CRISPY-2PC","Gà Giòn Cay (2 miếng)",         "chicken",   74000, ["lunch", "dinner", "late"], 95,  (-0.02, 0.03), [("CHICKEN-BONEIN", 2), ("BREADING", 0.18), ("OIL", 0.12)]),
        ("KFC-COMGA",     "Cơm Gà Rán",                    "rice",      59000, ["lunch", "dinner"],         115, (-0.04, 0.02), [("CHICKEN-BONEIN", 1), ("RICE", 1), ("BREADING", 0.1), ("OIL", 0.08)]),
        ("KFC-8PC-BUCKET","Xô Gà 8 Miếng",                 "chicken",  279000, ["lunch", "dinner"],         42,  (0.0, 0.05),   [("CHICKEN-BONEIN", 8), ("BREADING", 0.6), ("OIL", 0.4)]),
        ("KFC-12PC-BUCKET","Xô Gà 12 Miếng",               "chicken",  389000, ["dinner"],                  18,  (0.0, 0.06),   [("CHICKEN-BONEIN", 12), ("BREADING", 0.9), ("OIL", 0.6)]),
        ("KFC-TENDERS-3", "Gà Rán Không Xương (3 miếng)",  "chicken",   69000, ["lunch", "dinner", "late"], 92,  (0.0, 0.0),    [("CHICKEN-TENDER", 3), ("BREADING", 0.1), ("OIL", 0.08)]),
        ("KFC-POPCORN",   "Gà Popcorn",                    "chicken",   55000, ["lunch", "dinner", "late"], 70,  (0.02, 0.03),  [("POPCORN-CHICKEN", 0.3), ("BREADING", 0.08), ("OIL", 0.07)]),
        ("KFC-WINGS-6",   "Cánh Gà Cay (6 miếng)",         "chicken",   79000, ["dinner", "late"],          60,  (0.02, 0.05),  [("CHICKEN-WING", 6), ("BREADING", 0.15), ("OIL", 0.12)]),
        ("KFC-NASHVILLE", "Gà Cay Nashville",              "chicken",   85000, ["lunch", "dinner", "late"], 45,  (0.0, 0.02),   [("CHICKEN-TENDER", 3), ("BREADING", 0.1), ("OIL", 0.08), ("NASHVILLE-SAUCE", 0.05)]),
        ("KFC-SANDWICH",  "Burger Gà",                     "sandwich",  59000, ["lunch", "dinner", "late"], 110, (0.03, -0.02), [("CHICKEN-FILET", 1), ("BRIOCHE-BUN", 1), ("PICKLE", 0.03), ("OIL", 0.08)]),
        ("KFC-SPICY-SAND","Burger Gà Cay",                 "sandwich",  59000, ["lunch", "dinner", "late"], 75,  (0.03, -0.02), [("CHICKEN-FILET", 1), ("BRIOCHE-BUN", 1), ("PICKLE", 0.03), ("OIL", 0.08)]),
        ("KFC-CHICK-LITTLE","Burger Gà Nhỏ",               "sandwich",  29000, ["lunch", "dinner", "late"], 55,  (0.02, 0.0),   [("CHICKEN-FILET-SM", 1), ("SLIDER-BUN", 1), ("PICKLE", 0.02)]),
        ("KFC-FAMOUS-BOWL","Bát Cơm Gà Sốt",               "rice",      65000, ["lunch", "dinner"],         65,  (-0.06, 0.05), [("POPCORN-CHICKEN", 0.15), ("MASHED-POTATO", 0.3), ("CORN-KERNEL", 0.1), ("GRAVY", 0.1), ("CHEESE", 0.1)]),
        ("KFC-POT-PIE",   "Bánh Pie Gà",                   "entree",    65000, ["lunch", "dinner"],         35,  (-0.16, 0.08), [("POT-PIE-SHELL", 1), ("CHICKEN-DICED", 0.2), ("VEG-MIX", 0.15), ("GRAVY", 0.15)]),
        ("KFC-MASHED",    "Khoai Tây Nghiền",              "side",       25000, ["lunch", "dinner", "late"], 130, (-0.10, 0.06), [("MASHED-POTATO", 0.3), ("GRAVY", 0.1)]),
        ("KFC-SLAW",      "Salad Bắp Cải",                 "side",       25000, ["lunch", "dinner"],         85,  (0.10, -0.05), [("COLESLAW-MIX", 0.3)]),
        ("KFC-BISCUIT",   "Bánh Biscuit",                  "side",       19000, ["lunch", "dinner", "late"], 120, (-0.03, 0.03), [("BISCUIT", 1)]),
        ("KFC-MAC",       "Mì Ống Phô Mai",                "side",       29000, ["lunch", "dinner", "late"], 70,  (-0.06, 0.04), [("MAC-CHEESE", 0.3)]),
        ("KFC-FRIES",     "Khoai Tây Chiên",               "side",       29000, ["lunch", "dinner", "late"], 140, (0.01, 0.03),  [("FRIES", 1), ("OIL", 0.05)]),
        ("KFC-CORN",      "Bắp Ngô",                       "side",       22000, ["lunch", "dinner"],         45,  (0.08, -0.03), [("CORN-COB", 1)]),
        ("KFC-SODA",      "Nước Ngọt",                     "beverage",   22000, ["lunch", "dinner", "late"], 200, (0.15, -0.06), [("SODA-SYRUP", 0.05), ("CUP", 1)]),
        ("KFC-SWEET-TEA", "Trà Đá",                        "beverage",   18000, ["lunch", "dinner", "late"], 110, (0.20, -0.08), [("TEA", 0.03), ("CUP", 1)]),
        ("KFC-LEMONADE",  "Nước Chanh",                    "beverage",   27000, ["lunch", "dinner", "late"], 55,  (0.22, -0.10), [("LEMONADE-MIX", 0.05), ("CUP", 1)]),
        ("KFC-WATER",     "Nước Suối",                     "beverage",   15000, ["lunch", "dinner", "late"], 35,  (0.15, -0.05), [("WATER-BOTTLE", 1)]),
        ("KFC-COOKIE",    "Bánh Quy Socola",               "dessert",    15000, ["lunch", "dinner", "late"], 40,  (-0.03, 0.03), [("COOKIE", 1)]),
        ("KFC-CAKE",      "Bánh Trứng Bồ Đào Nha",         "dessert",    22000, ["dinner"],                  30,  (-0.04, 0.03), [("CAKE", 1)]),
    ],
}

# Ingredient costs in VND: unit_cost, uom, shelf_life_days, lead_time_days, frozen, thaw_hours, cook_yield.
INGREDIENTS = {
    "CHICKEN-BONEIN":  (13000, "each", 7, 2, False, 0, 0.85),
    "CHICKEN-TENDER":  (9500, "each", 120, 3, True, 6, 0.88),
    "CHICKEN-WING":    (7000, "each", 120, 3, True, 6, 0.80),
    "CHICKEN-FILET":   (18000, "each", 90, 3, True, 5, 0.86),
    "CHICKEN-FILET-SM":(8500, "each", 90, 3, True, 4, 0.86),
    "POPCORN-CHICKEN": (78000, "kg", 120, 3, True, 4, 0.90),
    "CHICKEN-DICED":   (72000, "kg", 90, 3, True, 4, 0.90),
    "BREADING":        (19000, "kg", 365, 7, False, 0, 1.0),
    "OIL":             (48000, "l", 90, 7, False, 0, 1.0),
    "NASHVILLE-SAUCE": (60000, "l", 60, 5, False, 0, 1.0),
    "BRIOCHE-BUN":     (7000, "each", 10, 2, False, 0, 1.0),
    "SLIDER-BUN":      (3500, "each", 10, 2, False, 0, 1.0),
    "PICKLE":          (36000, "kg", 60, 3, False, 0, 1.0),
    "MASHED-POTATO":   (28000, "kg", 30, 4, False, 0, 1.0),
    "GRAVY":           (24000, "l", 14, 4, False, 0, 1.0),
    "COLESLAW-MIX":    (22000, "kg", 10, 2, False, 0, 0.90),
    "RICE":            (6000, "serving", 365, 7, False, 0, 1.0),
    "BISCUIT":         (6000, "each", 180, 3, True, 1, 1.0),
    "MAC-CHEESE":      (38000, "kg", 90, 4, True, 2, 1.0),
    "FRIES":           (13000, "serving", 180, 4, True, 0, 0.92),
    "CORN-COB":        (11000, "each", 60, 4, True, 2, 0.95),
    "CORN-KERNEL":     (24000, "kg", 180, 5, True, 1, 1.0),
    "VEG-MIX":         (19000, "kg", 14, 3, True, 1, 0.95),
    "POT-PIE-SHELL":   (14000, "each", 180, 5, True, 2, 1.0),
    "CHEESE":          (92000, "kg", 45, 4, False, 0, 1.0),
    "SODA-SYRUP":      (95000, "l", 365, 7, False, 0, 1.0),
    "TEA":             (2000, "serving", 365, 10, False, 0, 1.0),
    "LEMONADE-MIX":    (72000, "l", 180, 7, False, 0, 1.0),
    "CUP":             (1500, "each", 730, 10, False, 0, 1.0),
    "WATER-BOTTLE":    (5000, "each", 365, 10, False, 0, 1.0),
    "COOKIE":          (7000, "each", 60, 5, True, 1, 0.95),
    "CAKE":            (9000, "each", 90, 5, True, 3, 1.0),
}

DOW_FACTOR = {"qsr_chicken": [0.85, 0.83, 0.90, 0.98, 1.22, 1.35, 1.28]}
DAYPART_SHARE = {"qsr_chicken": {"breakfast": 0.0, "lunch": 0.40, "dinner": 0.46, "late": 0.14}}


# ---------------------------------------------------------------------------
# VN holidays + retail driver days. Tết is a multi-week regime (pre-Tết spike →
# quiet during the holiday as people travel home → rebound).
# ---------------------------------------------------------------------------
def _h(y, m, d):
    return dt.date(y, m, d)

def _tet(day1: dt.date) -> dict:
    out = {}
    for off, mult, name in [(-6, 1.12, "Trước Tết"), (-5, 1.12, "Trước Tết"), (-4, 1.15, "Trước Tết"),
                            (-3, 1.15, "Trước Tết"), (-2, 1.10, "Trước Tết"), (-1, 0.90, "Giao thừa"),
                            (0, 0.48, "Tết"), (1, 0.48, "Tết"), (2, 0.55, "Tết"), (3, 0.62, "Tết"),
                            (4, 0.72, "Tết"), (5, 0.82, "Sau Tết"), (6, 0.88, "Sau Tết")]:
        out[day1 + dt.timedelta(days=off)] = {"qsr_chicken": mult, "name": name}
    return out

HOLIDAYS: dict = {}
for _d1 in (_h(2024, 2, 10), _h(2025, 1, 29), _h(2026, 2, 17)):   # Tết Nguyên Đán
    HOLIDAYS.update(_tet(_d1))
for _dt_date, _mult, _name in [
    (_h(2024, 3, 8), 1.30, "Quốc tế Phụ nữ"), (_h(2025, 3, 8), 1.30, "Quốc tế Phụ nữ"), (_h(2026, 3, 8), 1.30, "Quốc tế Phụ nữ"),
    (_h(2024, 4, 18), 1.15, "Giỗ Tổ Hùng Vương"), (_h(2025, 4, 7), 1.15, "Giỗ Tổ Hùng Vương"), (_h(2026, 4, 26), 1.15, "Giỗ Tổ Hùng Vương"),
    (_h(2024, 4, 30), 1.22, "Giải phóng miền Nam"), (_h(2025, 4, 30), 1.22, "Giải phóng miền Nam"), (_h(2026, 4, 30), 1.22, "Giải phóng miền Nam"),
    (_h(2024, 5, 1), 1.20, "Quốc tế Lao động"), (_h(2025, 5, 1), 1.20, "Quốc tế Lao động"), (_h(2026, 5, 1), 1.20, "Quốc tế Lao động"),
    (_h(2024, 6, 1), 1.42, "Quốc tế Thiếu nhi"), (_h(2025, 6, 1), 1.42, "Quốc tế Thiếu nhi"), (_h(2026, 6, 1), 1.42, "Quốc tế Thiếu nhi"),
    (_h(2024, 9, 2), 1.22, "Quốc khánh"), (_h(2025, 9, 2), 1.22, "Quốc khánh"), (_h(2026, 9, 2), 1.22, "Quốc khánh"),
    (_h(2024, 9, 17), 1.25, "Tết Trung Thu"), (_h(2025, 10, 6), 1.25, "Tết Trung Thu"), (_h(2026, 9, 25), 1.25, "Tết Trung Thu"),
    (_h(2024, 10, 20), 1.30, "Phụ nữ Việt Nam"), (_h(2025, 10, 20), 1.30, "Phụ nữ Việt Nam"), (_h(2026, 10, 20), 1.30, "Phụ nữ Việt Nam"),
    (_h(2024, 11, 20), 1.22, "Nhà giáo Việt Nam"), (_h(2025, 11, 20), 1.22, "Nhà giáo Việt Nam"), (_h(2026, 11, 20), 1.22, "Nhà giáo Việt Nam"),
    (_h(2024, 12, 25), 1.20, "Giáng sinh"), (_h(2025, 12, 25), 1.20, "Giáng sinh"), (_h(2026, 12, 25), 1.20, "Giáng sinh"),
    (_h(2025, 2, 14), 1.22, "Valentine"), (_h(2026, 2, 14), 1.22, "Valentine"),
]:
    HOLIDAYS.setdefault(_dt_date, {"qsr_chicken": _mult, "name": _name})

# Scheduled promotions. (scope, target, item_id, start, end, discount_pct, expected_lift).
PROMOS = [
    ("brand", "KFC", "KFC-8PC-BUCKET", _h(2024, 9, 1),  _h(2024, 9, 30), 0.15, 1.6),
    ("brand", "KFC", "KFC-COMGA",      _h(2024, 10, 1), _h(2024, 10, 28), 0.20, 1.7),
    ("brand", "KFC", "KFC-WINGS-6",    _h(2025, 1, 15), _h(2025, 1, 27), 0.25, 1.9),
    ("brand", "KFC", "KFC-TENDERS-3",  _h(2025, 3, 1),  _h(2025, 3, 21), 0.20, 1.55),
    ("brand", "KFC", "KFC-NASHVILLE",  _h(2025, 4, 1),  _h(2025, 4, 30), 0.10, 1.8),
    ("store", "KFC-HN01", "KFC-12PC-BUCKET", _h(2025, 6, 1), _h(2025, 6, 20), 0.20, 1.7),
    ("brand", "KFC", "KFC-FAMOUS-BOWL",_h(2025, 8, 1),  _h(2025, 8, 24), 0.20, 1.75),
    ("brand", "KFC", "KFC-SPICY-SAND", _h(2025, 10, 6), _h(2025, 10, 31), 0.20, 1.6),
    ("brand", "KFC", "KFC-8PC-BUCKET", _h(2025, 12, 20),_h(2026, 1, 10), 0.15, 1.5),
    ("store", "KFC-HCM01", "KFC-POPCORN", _h(2026, 3, 1), _h(2026, 3, 20), 0.25, 1.65),
    ("brand", "KFC", "KFC-COMGA",      _h(2026, 5, 1),  _h(2026, 5, 25), 0.20, 1.6),
    ("brand", "KFC", "KFC-8PC-BUCKET", _h(2026, 6, 1),  _h(2026, 6, 15), 0.15, 1.55),
]
