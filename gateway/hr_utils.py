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
    """Vráti zónu 0–4 (Myzone-kompatibilné)."""
    pct = hr / max_hr * 100
    if pct < 50:
        return 0
    if pct < 60:
        return 1
    if pct < 70:
        return 2
    if pct < 80:
        return 3
    return 4


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


ZONE_COLORS = {
    0: "#555555",   # šedá
    1: "#1a5fa8",   # modrá
    2: "#1a8c3e",   # zelená
    3: "#b8820a",   # žltá
    4: "#a01820",   # červená
}

ZONE_LABELS = {
    0: "Gray",
    1: "Blue",
    2: "Green",
    3: "Yellow",
    4: "Red",
}
