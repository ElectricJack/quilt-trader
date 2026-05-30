"""Register the v1 FMP dataset catalog.

Importing this module registers DatasetSpecs into the module-level registry.
The coordinator's lifespan imports this module once at startup.
"""
from coordinator.services.datasets.registry import DatasetSpec, Pagination, register


# Free-tier FMP only exposes the firehose. By-name and by-symbol filtering
# endpoints (/stable/house-trades-by-name, /stable/house-trades) are paid-tier.
# To get a specific politician's trades on free tier, download this firehose
# and filter in-algo via lastName / firstName.
register(DatasetSpec(
    name="fmp.house_disclosures",
    provider="fmp",
    endpoint_path="/stable/house-latest",
    event_date_column="transactionDate",
    knowledge_date_column="disclosureDate",
    symbol_keyed=False,
    id_columns=("disclosureDate", "transactionDate", "firstName", "lastName",
                "symbol", "amount", "type"),
    columns={
        "symbol": "str", "firstName": "str", "lastName": "str",
        "office": "str", "district": "str", "owner": "str",
        "transactionDate": "date", "disclosureDate": "date",
        "assetDescription": "str", "assetType": "str",
        "type": "str", "amount": "str",
        "capitalGainsOver200USD": "str", "comment": "str", "link": "str",
    },
    # FMP free tier caps `limit` at 5 and rejects `page>0` for this endpoint.
    # The adapter catches the resulting 402 and treats it as end-of-data so
    # the job completes cleanly with whatever was reachable. Paid plans can
    # raise page_size and continue paginating.
    pagination=Pagination.PAGE, page_size=5,
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
