\
from __future__ import annotations
import re
from unidecode import unidecode

def normalize_name(name: str) -> str:
    name = unidecode(name or "").upper()
    name = re.sub(r"\s+", " ", name).strip()
    return name

def parse_brl_money(value: str):
    """
    Convert "1.415,89" or "1415,89" or "1 415,89" to float 1415.89
    Returns None if it can't parse.
    """
    if value is None:
        return None
    s = str(value).strip()
    s = s.replace("\xa0", " ").replace(" ", "")
    # keep digits, dot, comma
    s = re.sub(r"[^0-9\.,\-]", "", s)
    if s == "":
        return None
    # if comma exists, assume comma decimal
    if "," in s:
        s = s.replace(".", "")
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None

def safe_contains(hay: str, needle: str) -> bool:
    return (needle in (hay or "").upper())
