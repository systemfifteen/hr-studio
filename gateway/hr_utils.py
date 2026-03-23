from datetime import datetime


def calc_max_hr(birth_year: int, gender: str = None) -> int:
    """Tanaka formula — presnejšia ako 220-vek."""
    age = datetime.now().year - birth_year
    if gender and gender.upper() == "F":
        mhr = 206 - (0.88 * age)
    else:
        mhr = 208 - (0.7 * age)
    return round(mhr)


def calc_zone(hr: int, max_hr: int) -> int:
    """Vráti zónu 0–5 (Myzone-kompatibilné).
    0=pod 50% (sivá, % červenou), 1=Gray(50-59%), 2=Blue(60-69%),
    3=Green(70-79%), 4=Yellow(80-89%), 5=Red(≥90%)
    """
    pct = hr / max_hr * 100
    if pct < 50:
        return 0
    if pct < 60:
        return 1
    if pct < 70:
        return 2
    if pct < 80:
        return 3
    if pct < 90:
        return 4
    return 5


def calc_calories(hr: int, weight_kg: float, age: int,
                  gender: str, duration_min: float) -> float:
    """
    Keytel formula pre odhad kalórií z HR.
    Potrebuje váhu, vek a pohlavie pre presnosť.
    Vráti kcal za dané obdobie (duration_min).
    """
    if not weight_kg:
        return 0.0

    if gender and gender.upper() == "F":
        cal_per_min = (
            (-20.4022 + (0.4472 * hr)
             - (0.1263 * weight_kg)
             + (0.074 * age)) / 4.184
        )
    else:
        cal_per_min = (
            (-55.0969 + (0.6309 * hr)
             + (0.1988 * weight_kg)
             + (0.2017 * age)) / 4.184
        )

    return round(max(0.0, cal_per_min * duration_min), 1)


def calc_watts(hr: int, weight_kg: float, age: int, gender: str) -> int:
    """
    Odhad mechanického výkonu (W) z HR cez Keytel + cyklická efektivita 25%.
    1 kcal/min = 69.78 W (metabolicky), × 0.25 = mechanické watty.
    """
    if not weight_kg:
        return 0

    if gender and gender.upper() == "F":
        cal_per_min = (
            (-20.4022 + (0.4472 * hr)
             - (0.1263 * weight_kg)
             + (0.074 * age)) / 4.184
        )
    else:
        cal_per_min = (
            (-55.0969 + (0.6309 * hr)
             + (0.1988 * weight_kg)
             + (0.2017 * age)) / 4.184
        )

    watts = max(0.0, cal_per_min) * 69.78 * 0.25
    return round(watts)


ZONE_COLORS = {
    0: "#2a2a2a",   # tmavosivá — pod 50%
    1: "#555555",   # sivá      — 50-59%
    2: "#1a5fa8",   # modrá     — 60-69%
    3: "#1a8c3e",   # zelená    — 70-79%
    4: "#b8820a",   # žltá      — 80-89%
    5: "#a01820",   # červená   — ≥90%
}

ZONE_LABELS = {
    0: "Rest",
    1: "Gray",
    2: "Blue",
    3: "Green",
    4: "Yellow",
    5: "Red",
}
