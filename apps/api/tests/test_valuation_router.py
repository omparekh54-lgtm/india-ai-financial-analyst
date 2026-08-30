from app.valuation.router import ValuationMethod, choose_valuation_methods


def test_bank_routes_to_book_value_methods() -> None:
    route = choose_valuation_methods("Private Sector Bank")

    assert route.sector_family == "bank"
    assert route.primary == [ValuationMethod.PB, ValuationMethod.RESIDUAL_INCOME]


def test_general_company_routes_to_dcf_and_pe() -> None:
    route = choose_valuation_methods("Information Technology", "IT Services")

    assert route.sector_family == "general_corporate"
    assert ValuationMethod.DCF in route.primary
    assert ValuationMethod.PE in route.primary
