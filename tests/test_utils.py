\
from src.utils import parse_brl_money, normalize_name

def test_money():
    assert parse_brl_money("1.415,89") == 1415.89
    assert parse_brl_money("1518,00") == 1518.00
    assert parse_brl_money("1 518,00") == 1518.00

def test_name():
    assert normalize_name("José  da   Silva") == "JOSE DA SILVA"
