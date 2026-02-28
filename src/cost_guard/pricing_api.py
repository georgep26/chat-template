"""Fetch list prices from AWS Pricing API with fallback to config defaults."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

LOG = logging.getLogger(__name__)

# AWS Pricing API is only available in us-east-1
PRICING_API_REGION = "us-east-1"

# Product groups / filters for get_products (location added per-region where needed).
_LAMBDA_GROUPS = ("AWS-Lambda-Requests", "AWS-Lambda-Duration")


def _location_for_region(region: str) -> str:
    """Map AWS region code to Pricing API location string."""
    # https://docs.aws.amazon.com/general/latest/gr/rande.html
    loc = {
        "us-east-1": "US East (N. Virginia)",
        "us-east-2": "US East (Ohio)",
        "us-west-1": "US West (N. California)",
        "us-west-2": "US West (Oregon)",
        "af-south-1": "Africa (Cape Town)",
        "ap-east-1": "Asia Pacific (Hong Kong)",
        "ap-south-1": "Asia Pacific (Mumbai)",
        "ap-southeast-1": "Asia Pacific (Singapore)",
        "ap-southeast-2": "Asia Pacific (Sydney)",
        "ap-northeast-1": "Asia Pacific (Tokyo)",
        "ap-northeast-2": "Asia Pacific (Seoul)",
        "ap-northeast-3": "Asia Pacific (Osaka)",
        "ca-central-1": "Canada (Central)",
        "eu-central-1": "EU (Frankfurt)",
        "eu-west-1": "EU (Ireland)",
        "eu-west-2": "EU (London)",
        "eu-west-3": "EU (Paris)",
        "eu-north-1": "EU (Stockholm)",
        "eu-south-1": "EU (Milan)",
        "me-south-1": "Middle East (Bahrain)",
        "sa-east-1": "South America (Sao Paulo)",
    }
    return loc.get(region, "US East (N. Virginia)")


def _extract_on_demand_price(price_item: dict[str, Any]) -> float | None:
    """Get OnDemand USD price from a single PriceList item. Returns None if not found."""
    try:
        terms = price_item.get("terms") or {}
        on_demand = terms.get("OnDemand") or {}
        for _term_id, term_detail in on_demand.items():
            dims = (term_detail.get("priceDimensions") or {}).values()
            for dim in dims:
                ppu = dim.get("pricePerUnit") or {}
                usd = ppu.get("USD")
                if usd is not None:
                    return float(usd)
    except (TypeError, ValueError, KeyError):
        pass
    return None


def _extract_on_demand_price_tiered(price_item: dict[str, Any], usage: float = 0.0) -> float | None:
    """
    Get OnDemand USD price for tiered dimensions (e.g. Lambda GB-s).
    Uses the tier that contains `usage`; if usage is 0 or unknown, returns Tier 1 (lowest tier).
    """
    try:
        terms = price_item.get("terms") or {}
        on_demand = terms.get("OnDemand") or {}
        for _term_id, term_detail in on_demand.items():
            dims = term_detail.get("priceDimensions") or {}
            # Sort by beginRange so Tier 1 (0) is first; use the tier that contains usage
            def begin_range(dim: dict) -> float:
                try:
                    return float(dim.get("beginRange", 0))
                except (TypeError, ValueError):
                    return 0.0

            sorted_dims = sorted(dims.values(), key=begin_range)
            for dim in sorted_dims:
                try:
                    start = float(dim.get("beginRange", 0))
                    end_str = dim.get("endRange", "Inf")
                    end = float(end_str) if end_str != "Inf" else float("inf")
                    if start <= usage < end:
                        ppu = dim.get("pricePerUnit") or {}
                        usd = ppu.get("USD")
                        if usd is not None:
                            return float(usd)
                except (TypeError, ValueError):
                    continue
            # No tier contained usage; return first (Tier 1) for estimates
            if sorted_dims:
                ppu = sorted_dims[0].get("pricePerUnit") or {}
                usd = ppu.get("USD")
                if usd is not None:
                    return float(usd)
    except (TypeError, ValueError, KeyError):
        pass
    return None


def _fetch_products_first_price(
    client: Any,
    service_code: str,
    filters: list[dict[str, str]],
    max_pages: int = 3,
) -> float | None:
    """Call get_products with filters and return the first OnDemand USD price found."""
    try:
        paginator = client.get_paginator("get_products")
        page_count = 0
        for page in paginator.paginate(
            ServiceCode=service_code,
            Filters=filters,
            FormatVersion="aws_v1",
            MaxResults=10,
        ):
            for item_str in page.get("PriceList") or []:
                item = json.loads(item_str)
                price = _extract_on_demand_price(item)
                if price is not None:
                    return price
            page_count += 1
            if page_count >= max_pages or "NextToken" not in page or not page["NextToken"]:
                break
    except Exception as e:  # pragma: no cover
        LOG.warning("Pricing API fetch failed for %s %s: %s", service_code, filters, e)
    return None


def _fetch_lambda_prices(client: Any, location: str) -> dict[str, float]:
    """Fetch Lambda request and GB-second prices for a location. Returns empty dict on failure."""
    result: dict[str, float] = {}
    filters_base = [
        {"Type": "TERM_MATCH", "Field": "location", "Value": location},
    ]
    for group in _LAMBDA_GROUPS:
        if "Request" in group:
            price = _fetch_products_first_price(
                client,
                "AWSLambda",
                filters_base + [{"Type": "TERM_MATCH", "Field": "group", "Value": group}],
                max_pages=1,
            )
            if price is not None:
                result["lambda_request_per_million"] = (
                    price if price >= 0.001 else price * 1_000_000.0
                )
        else:
            # Duration (GB-s): API returns tiered pricing; use Tier 1 (0–6B GB-s) to match calculator
            try:
                paginator = client.get_paginator("get_products")
                for page in paginator.paginate(
                    ServiceCode="AWSLambda",
                    Filters=filters_base + [{"Type": "TERM_MATCH", "Field": "group", "Value": group}],
                    FormatVersion="aws_v1",
                    MaxResults=5,
                ):
                    for item_str in page.get("PriceList") or []:
                        item = json.loads(item_str)
                        price = _extract_on_demand_price_tiered(item, usage=0.0)
                        if price is not None:
                            result["lambda_gb_second"] = price
                            break
                    if "lambda_gb_second" in result:
                        break
                    if "NextToken" not in page or not page["NextToken"]:
                        break
            except Exception as e:  # pragma: no cover
                LOG.warning("Pricing API fetch failed for Lambda Duration: %s", e)
    return result


def _fetch_cloudwatch_logs_prices(client: Any, location: str) -> dict[str, float]:
    """Fetch CloudWatch Logs data ingestion $/GB. Fills both ingest key names for config/estimate."""
    result: dict[str, float] = {}
    # AmazonCloudWatch: productFamily "Data Payload" or "Logs"; group varies (CW-Logs-Ingestion, etc.)
    filter_sets = [
        [{"Type": "TERM_MATCH", "Field": "location", "Value": location}, {"Type": "TERM_MATCH", "Field": "productFamily", "Value": "Data Payload"}, {"Type": "TERM_MATCH", "Field": "group", "Value": "CW-Logs-Ingestion"}],
        [{"Type": "TERM_MATCH", "Field": "location", "Value": location}, {"Type": "TERM_MATCH", "Field": "productFamily", "Value": "Logs"}, {"Type": "TERM_MATCH", "Field": "group", "Value": "CW-Logs-Ingestion"}],
        [{"Type": "TERM_MATCH", "Field": "location", "Value": location}, {"Type": "TERM_MATCH", "Field": "productFamily", "Value": "Data Payload"}],
    ]
    for filters in filter_sets:
        price = _fetch_products_first_price(client, "AmazonCloudWatch", filters, max_pages=2)
        if price is not None:
            result["cloudwatch_logs_ingest_gb"] = price
            result["cloudwatch_logs_ingestion_gb"] = price
            return result
    return result


def _fetch_aurora_serverless_prices(client: Any, location: str) -> dict[str, float]:
    """Fetch Aurora Serverless ACU $/hour."""
    result: dict[str, float] = {}
    # AmazonRDS: Aurora Serverless v2 ACU-hour; productFamily can be "Serverless" or "Aurora Serverless"
    for product_family in ("Serverless", "Aurora Serverless", "Database-Usage"):
        price = _fetch_products_first_price(
            client,
            "AmazonRDS",
            [
                {"Type": "TERM_MATCH", "Field": "location", "Value": location},
                {"Type": "TERM_MATCH", "Field": "productFamily", "Value": product_family},
            ],
            max_pages=5,
        )
        if price is not None:
            # API may return per-ACU-hour; if value is tiny might be per-second
            if price < 0.01 and price > 0:
                price = price * 3600.0  # assume per-second
            result["aurora_serverless_acu_hour"] = price
            return result
    return result


def _fetch_vpc_endpoint_prices(client: Any, location: str) -> dict[str, float]:
    """Fetch VPC Interface Endpoint data processing $/GB and hourly $/hr."""
    result: dict[str, float] = {}
    base_loc = [{"Type": "TERM_MATCH", "Field": "location", "Value": location}]
    # Data processing $/GB: AmazonEC2 Vpc Endpoint
    for filters in (
        base_loc + [{"Type": "TERM_MATCH", "Field": "productFamily", "Value": "Vpc Endpoint"}, {"Type": "TERM_MATCH", "Field": "group", "Value": "VpcEndpoint-DataProcessing"}],
        base_loc + [{"Type": "TERM_MATCH", "Field": "productFamily", "Value": "Vpc Endpoint"}],
    ):
        price = _fetch_products_first_price(client, "AmazonEC2", filters, max_pages=2)
        if price is not None:
            result["interface_vpc_endpoint_data_gb"] = price
            break
    # Hourly $/hr: VpcEndpoint-Hours
    hourly_filters = base_loc + [{"Type": "TERM_MATCH", "Field": "productFamily", "Value": "Vpc Endpoint"}, {"Type": "TERM_MATCH", "Field": "group", "Value": "VpcEndpoint-Hours"}]
    hourly = _fetch_products_first_price(client, "AmazonEC2", hourly_filters, max_pages=2)
    if hourly is not None:
        result["interface_vpc_endpoint_hourly"] = hourly
    return result


def fetch_pricing_with_metadata(
    default_pricing: dict[str, float],
    region: str = "us-east-1",
    at_time: datetime | None = None,
) -> tuple[dict[str, float], dict[str, dict[str, str]]]:
    """
    Fetch pricing from AWS Pricing API where possible; fill rest from defaults.

    Returns:
        (rates, metadata): rates dict key -> USD value; metadata key -> {source, at}.
        source is "pricing_api" or "default_stored_price"; at is ISO8601 timestamp.
    """
    at_time = at_time or datetime.now(tz=UTC)
    at_str = at_time.strftime("%Y-%m-%dT%H:%M:%SZ")
    default_meta = {"source": "default_stored_price", "at": at_str}

    rates: dict[str, float] = {}
    metadata: dict[str, dict[str, str]] = {}

    try:
        import boto3
        client = boto3.client("pricing", region_name=PRICING_API_REGION)
        location = _location_for_region(region)
        api_prices = _fetch_lambda_prices(client, location)
        for fetcher in (
            _fetch_cloudwatch_logs_prices,
            _fetch_aurora_serverless_prices,
            _fetch_vpc_endpoint_prices,
        ):
            try:
                more = fetcher(client, location)
                for k, v in more.items():
                    api_prices[k] = v
            except Exception as e:  # pragma: no cover
                LOG.warning("Pricing fetcher %s failed: %s", fetcher.__name__, e)
    except Exception as e:  # pragma: no cover
        LOG.warning("Pricing API unavailable, using defaults: %s", e)
        api_prices = {}

    for key in default_pricing:
        default_val = default_pricing[key]
        if key in api_prices:
            rates[key] = api_prices[key]
            metadata[key] = {"source": "pricing_api", "at": at_str}
        else:
            rates[key] = default_val
            metadata[key] = default_meta

    return rates, metadata
