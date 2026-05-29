"""Register the v1 FMP dataset catalog.

Importing this module registers DatasetSpecs into the module-level registry.
The coordinator's lifespan imports this module once at startup.
"""
from coordinator.services.datasets.registry import DatasetSpec, Pagination, register


register(DatasetSpec(
    name="fmp.house_disclosures",
    provider="fmp",
    endpoint_path="/stable/house-latest",
    event_date_column="transactionDate",
    knowledge_date_column="disclosureDate",
    symbol_keyed=False,
    id_columns=("disclosureDate", "transactionDate", "name", "symbol", "amount", "type"),
    columns={
        "symbol": "str", "name": "str", "office": "str", "district": "str",
        "transactionDate": "date", "disclosureDate": "date",
        "amount": "str", "type": "str", "assetDescription": "str", "link": "str",
    },
    pagination=Pagination.PAGE, page_size=100,
))

register(DatasetSpec(
    name="fmp.senate_disclosures",
    provider="fmp",
    endpoint_path="/stable/senate-latest",
    event_date_column="transactionDate",
    knowledge_date_column="disclosureDate",
    symbol_keyed=False,
    id_columns=("disclosureDate", "transactionDate", "firstName", "lastName",
                "symbol", "amount", "type"),
    columns={
        "symbol": "str", "firstName": "str", "lastName": "str", "office": "str",
        "transactionDate": "date", "disclosureDate": "date",
        "amount": "str", "type": "str", "assetDescription": "str", "link": "str",
    },
    pagination=Pagination.PAGE, page_size=100,
))

register(DatasetSpec(
    name="fmp.insider_trading",
    provider="fmp",
    endpoint_path="/stable/insider-trading/search",
    event_date_column="transactionDate",
    knowledge_date_column="filingDate",
    symbol_keyed=True,
    id_columns=("filingDate", "transactionDate", "reportingName",
                "transactionType", "securitiesTransacted", "price"),
    columns={
        "symbol": "str", "reportingName": "str", "typeOfOwner": "str",
        "transactionType": "str", "securitiesTransacted": "int", "price": "float",
        "transactionDate": "date", "filingDate": "datetime",
        "securityName": "str", "link": "str",
    },
    pagination=Pagination.PAGE, page_size=100,
))

register(DatasetSpec(
    name="fmp.income_statement",
    provider="fmp",
    endpoint_path="/stable/income-statement",
    event_date_column="date",
    knowledge_date_column="acceptedDate",
    symbol_keyed=True,
    id_columns=("date", "acceptedDate", "period"),
    columns={
        "symbol": "str", "date": "date", "acceptedDate": "datetime",
        "fillingDate": "date",  # preserved (FMP's typo, informational)
        "period": "str", "calendarYear": "str", "cik": "str",
        "reportedCurrency": "str",
        "revenue": "float", "netIncome": "float",
        "eps": "float", "epsDiluted": "float",
    },
    pagination=Pagination.SINGLE,
))

register(DatasetSpec(
    name="fmp.earnings_calendar",
    provider="fmp",
    endpoint_path="/stable/earnings-calendar",
    event_date_column="date",
    knowledge_date_column=None,
    symbol_keyed=False,
    id_columns=("date", "symbol"),
    columns={
        "date": "date", "symbol": "str",
        "eps": "float", "epsEstimated": "float",
        "revenue": "float", "revenueEstimated": "float",
        "time": "str", "fiscalDateEnding": "date",
    },
    pagination=Pagination.DATE_RANGE, date_chunk_days=365,
))
