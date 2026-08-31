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


def test_reit_and_invit_route_to_nav_and_yield() -> None:
    reit = choose_valuation_methods("Real Estate", "REIT")
    invit = choose_valuation_methods("Infrastructure", "InvIT")

    assert reit.sector_family == "reit_invit"
    assert invit.sector_family == "reit_invit"
    assert reit.primary == [ValuationMethod.NAV, ValuationMethod.DIVIDEND_YIELD]


def test_pre_profit_platform_routes_to_ev_sales() -> None:
    route = choose_valuation_methods("Technology", "Pre-profit internet platform")

    assert route.sector_family == "loss_making_growth"
    assert route.primary == [ValuationMethod.EV_SALES]
