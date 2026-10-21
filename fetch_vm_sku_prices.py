"""Enrich saved VM SKUs with public Linux/base-compute hourly USD prices."""

import argparse
from datetime import datetime, timezone
import math
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import time
from urllib.parse import urlparse

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


API_URL = "https://prices.azure.com/api/retail/prices"
PRICE_COLUMNS = ("retail price", "1Y RI", "3Y RI")
TERM_HOURS = {"1 Year": ("1Y RI", 8760), "3 Years": ("3Y RI", 26280)}
DEFAULT_INPUT = Path(__file__).resolve().parent / "data" / "vm_skus_all_regions.parquet"
METER_FIELDS = (
    "armRegionName", "armSkuName", "serviceName", "productName", "productId",
    "skuName", "skuId", "meterName", "meterId", "type", "reservationTerm",
    "effectiveStartDate", "retailPrice", "currencyCode", "unitOfMeasure",
    "tierMinimumUnits", "isPrimaryMeterRegion",
)
DIAGNOSTIC_COLUMNS = (
    "Region", "SkuName", "PriceColumn", "Status", "Reason", "ExistingPriceRetained",
    "CandidateHourlyPrice", *METER_FIELDS,
)
PRICING_RULE_VERSION = "2"
PROVENANCE_COLUMNS = (
    "Region", "SkuName", "PriceColumn", "Price", "Status", "SourceStatus",
    "FetchedAt", "LastCheckedAt", "RuleVersion", "Warning", "EquivalentOfferCount",
    *METER_FIELDS,
)


def key(value: str) -> str:
    return value.strip().casefold()


def validate_data(data: pd.DataFrame) -> None:
    for column in ("Region", "SkuName"):
        if column not in data:
            raise ValueError(f"Input Parquet is missing required column {column!r}.")
        if not data[column].map(lambda value: isinstance(value, str) and bool(value.strip())).all():
            raise ValueError(f"Input column {column!r} contains missing or invalid identifiers.")
    if data.empty:
        raise ValueError("Input Parquet contains no VM SKUs.")


def create_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=5, backoff_factor=1, status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",), respect_retry_after_header=True,
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def fetch_region_prices(
    session: requests.Session, region: str, *, allow_nonprimary: bool = False,
) -> list[dict]:
    escaped_region = region.replace("'", "''")
    params = {
        "api-version": "2023-01-01-preview",
        "currencyCode": "'USD'",
        "meterRegion": "'primary'",
        "$filter": f"serviceName eq 'Virtual Machines' and armRegionName eq '{escaped_region}'",
    }
    if allow_nonprimary:
        del params["meterRegion"]
    url = API_URL
    seen = set()
    items = []
    page = 0
    while url:
        parsed = urlparse(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "prices.azure.com"
            or parsed.port not in (None, 443)
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError(f"Unexpected pricing API page URL: {url}")
        if url in seen:
            raise ValueError(f"Pricing API repeated a page for {region}.")
        seen.add(url)
        response = session.get(url, params=params, timeout=(15, 90))
        response.raise_for_status()
        payload = response.json()
        if (
            not isinstance(payload, dict)
            or not isinstance(payload.get("Items"), list)
            or "NextPageLink" not in payload
        ):
            raise ValueError(f"Invalid pricing API response for {region}: expected Items and NextPageLink.")
        if not all(isinstance(item, dict) for item in payload["Items"]):
            raise ValueError(f"Invalid pricing API item for {region}.")
        items.extend(payload["Items"])
        page += 1
        print(f"  Page {page}: {len(payload['Items']):,} meters ({len(items):,} total)", flush=True)
        next_page = payload.get("NextPageLink")
        if next_page is not None and not isinstance(next_page, str):
            raise ValueError(f"Invalid NextPageLink for {region}.")
        url = next_page
        params = None
    return items


def evaluate_meter(
    item: dict, region: str, sku: str, now: datetime, *, allow_nonprimary: bool = False,
) -> tuple[str | None, float | None, datetime | None, str]:
    for field, expected, reason in (
        ("serviceName", "Virtual Machines", "Not a Virtual Machines meter"),
        ("currencyCode", "USD", "Currency is not USD"),
        ("unitOfMeasure", "1 Hour", "Unit is not 1 Hour"),
    ):
        if item.get(field) != expected:
            return None, None, None, reason
    if item.get("isPrimaryMeterRegion") is not True:
        if not allow_nonprimary:
            return None, None, None, "Not a primary meter"
        if item.get("isPrimaryMeterRegion") is not False:
            return None, None, None, "Unknown primary-meter flag"
    product = item.get("productName")
    meter = item.get("meterName")
    sku_label = item.get("skuName")
    if not all(isinstance(value, str) and value.strip() for value in (product, meter, sku_label)):
        raise ValueError(f"Incomplete meter names for {region}/{sku}.")
    labels = f"{product} {meter} {sku_label}".casefold()
    if re.search(r"cloud\s*services", labels):
        return None, None, None, "Excluded offer: cloud services"
    if re.search(r"\bwin\b", labels):
        return None, None, None, "Excluded offer: windows"
    for excluded in ("windows", "spot", "low priority"):
        if excluded in labels:
            return None, None, None, f"Excluded offer: {excluded}"
    if item.get("type") == "Consumption":
        column, divisor = "retail price", 1
    elif item.get("type") == "Reservation" and item.get("reservationTerm") in TERM_HOURS:
        column, divisor = TERM_HOURS[item["reservationTerm"]]
    else:
        return None, None, None, "Unsupported price type or reservation term"
    try:
        tier = float(item["tierMinimumUnits"])
        price = float(item["retailPrice"])
        effective = datetime.fromisoformat(item["effectiveStartDate"].replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(f"Invalid price metadata for {region}/{sku}/{column}.") from error
    if not math.isfinite(tier) or not math.isfinite(price) or price < 0 or effective.tzinfo is None:
        raise ValueError(f"Invalid price metadata for {region}/{sku}/{column}.")
    if tier != 0:
        return column, None, effective, "Nonzero tierMinimumUnits"
    if effective > now:
        return column, None, effective, "Price is not yet effective"
    return column, price / divisor, effective, ""


def select_prices(
    items: list[dict], region: str, skus: set[str], now: datetime,
    *, diagnostics: list[dict] | None = None, provenance: list[dict] | None = None,
    allow_nonprimary: bool = False,
) -> tuple[dict[tuple[str, str], float], int]:
    candidates: dict[tuple[str, str], tuple[datetime, set[float]]] = {}
    nonprimary_candidates: dict[tuple[str, str], tuple[datetime, set[float]]] = {}
    observations: dict[str, list[tuple[dict, str | None, float | None, datetime | None, str]]] = {}
    for item in items:
        item_region = item.get("armRegionName")
        sku = item.get("armSkuName")
        if not isinstance(item_region, str) or key(item_region) != key(region):
            continue
        if not isinstance(sku, str) or key(sku) not in skus:
            continue
        column, hourly, effective, reason = evaluate_meter(
            item, region, sku, now, allow_nonprimary=allow_nonprimary,
        )
        if diagnostics is not None or not reason:
            observations.setdefault(key(sku), []).append((item, column, hourly, effective, reason))
        if column is None or hourly is None or effective is None or reason:
            continue
        candidate_key = (key(sku), column)
        pool = candidates if item["isPrimaryMeterRegion"] is True else nonprimary_candidates
        previous = pool.get(candidate_key)
        if previous is None or effective > previous[0]:
            pool[candidate_key] = (effective, {hourly})
        elif effective == previous[0]:
            previous[1].add(hourly)
    nonprimary_selections = set()
    for candidate_key, candidate in nonprimary_candidates.items():
        if candidate_key not in candidates:
            candidates[candidate_key] = candidate
            nonprimary_selections.add(candidate_key)
    prices = {}
    ambiguous = 0
    for candidate_key, (effective, values) in candidates.items():
        if len(values) == 1:
            price = next(iter(values))
            prices[candidate_key] = price
            sku, column = candidate_key
            primary = candidate_key not in nonprimary_selections
            sources = [
                row[0] for row in observations[sku]
                if row[1] == column and row[3] == effective and not row[4]
                and row[0].get("isPrimaryMeterRegion") is primary
            ]
            source = min(sources, key=lambda item: tuple(
                str(item.get(field) or "") for field in ("productId", "skuId", "meterId", "productName")
            ))
            warning = "Zero price: verify before using for cost estimates" if price == 0 else ""
            status = "fresh_primary" if primary else "fresh_nonprimary"
            if not primary:
                print(f"  Nonprimary fallback: {region}/{sku}/{column} = {price:.8g} USD/hour", flush=True)
            if provenance is not None:
                provenance.append({
                    "Region": region, "SkuName": sku, "PriceColumn": column, "Price": price,
                    "Status": status, "SourceStatus": status,
                    "FetchedAt": now.isoformat(), "LastCheckedAt": now.isoformat(),
                    "RuleVersion": PRICING_RULE_VERSION, "Warning": warning,
                    "EquivalentOfferCount": len({
                        tuple(item.get(field) for field in METER_FIELDS) for item in sources
                    }),
                    **{field: source.get(field) for field in METER_FIELDS},
                })
            if warning:
                print(f"  WARNING: {region}/{sku}/{column}: {warning}", flush=True)
                if diagnostics is not None:
                    diagnostics.append({
                        "Region": region, "SkuName": sku, "PriceColumn": column,
                        "Status": "zero_price_verify", "Reason": warning,
                        "CandidateHourlyPrice": price,
                        **{field: source.get(field) for field in METER_FIELDS},
                    })
        else:
            ambiguous += 1
            print(
                f"  WARNING: conflicting current prices for {region}/{candidate_key[0]}/"
                f"{candidate_key[1]}; skipping this API price.",
                flush=True,
            )
    if diagnostics is not None:
        for sku in sorted(skus):
            for column in PRICE_COLUMNS:
                if (sku, column) in prices:
                    continue
                candidate = candidates.get((sku, column))
                status = "conflict" if candidate is not None else "no_eligible_price"
                rows = observations.get(sku, [])
                if candidate is not None:
                    primary = (sku, column) not in nonprimary_selections
                    rows = [
                        row for row in rows
                        if row[1] == column and row[3] == candidate[0] and not row[4]
                        and row[0].get("isPrimaryMeterRegion") is primary
                    ]
                if not rows:
                    diagnostics.append({
                        "Region": region, "SkuName": sku, "PriceColumn": column,
                        "Status": "no_api_match",
                        "Reason": "No exact region/armSkuName match in downloaded API records",
                    })
                for item, source_column, hourly, _, reason in rows:
                    diagnostics.append({
                        "Region": region, "SkuName": sku, "PriceColumn": column,
                        "Status": status,
                        "Reason": (
                            "Different prices at the latest eligible effective date"
                            if candidate is not None else
                            reason or f"Eligible only for {source_column}, not {column}"
                        ),
                        "CandidateHourlyPrice": hourly,
                        **{field: item.get(field) for field in METER_FIELDS},
                    })
    return prices, ambiguous


def choose_preserve_existing(data: pd.DataFrame) -> bool:
    existing = [column for column in PRICE_COLUMNS if column in data]
    if not existing:
        return False
    print(f"Existing pricing columns: {', '.join(existing)}")
    print("1. Delete and recreate all pricing columns; unmatched prices will be blank.")
    print("2. Reprice all API matches, including populated values; keep unmatched prices (default).")
    while True:
        try:
            answer = input("Choose pricing mode [1/2, default 2]: ").strip().casefold()
        except EOFError as error:
            raise ValueError("No pricing mode received. Existing files were kept; nothing fetched.") from error
        if answer == "1":
            return False
        if answer in ("", "2"):
            return True
        print("Please choose 1 or 2.")


def enrich_prices(
    data: pd.DataFrame, session: requests.Session, *, preserve_existing: bool = False,
    diagnostics: list[dict] | None = None,
    provenance: list[dict] | None = None,
    previous_provenance: dict[tuple[str, str, str, float], dict] | None = None,
    allow_nonprimary: bool = False,
) -> tuple[pd.DataFrame, int]:
    validate_data(data)
    result = data.copy()
    for column in PRICE_COLUMNS:
        if preserve_existing and column in result:
            result[column] = result[column].astype(float)
        else:
            result[column] = float("nan")
    region_keys = data["Region"].map(key)
    sku_keys = data["SkuName"].map(key)
    regions = sorted(region_keys.unique())
    ambiguous = 0
    for number, region in enumerate(regions, 1):
        mask = region_keys == region
        skus = set(sku_keys[mask])
        print(f"[{number}/{len(regions)}] {region}: looking up {len(skus):,} SKUs", flush=True)
        items = fetch_region_prices(session, region, allow_nonprimary=allow_nonprimary)
        now = datetime.now(timezone.utc)
        selected_provenance: list[dict] = []
        diagnostic_start = len(diagnostics) if diagnostics is not None else 0
        prices, conflicts = select_prices(
            items, region, skus, now, diagnostics=diagnostics,
            provenance=selected_provenance if provenance is not None else None,
            allow_nonprimary=allow_nonprimary,
        )
        ambiguous += conflicts
        for column in PRICE_COLUMNS:
            mapping = {sku: price for (sku, name), price in prices.items() if name == column}
            new_prices = sku_keys[mask].map(mapping)
            if preserve_existing:
                new_prices = new_prices.fillna(result.loc[mask, column])
            result.loc[mask, column] = new_prices
        sku_labels = dict(zip(sku_keys[mask], data.loc[mask, "SkuName"]))
        if provenance is not None:
            region_label = data.loc[mask, "Region"].iloc[0]
            for row in selected_provenance:
                row["Region"] = region_label
                row["SkuName"] = sku_labels[row["SkuName"]]
            provenance.extend(selected_provenance)
            if preserve_existing:
                retained_values = result.loc[mask, ["SkuName", *PRICE_COLUMNS]].drop_duplicates()
                for sku_label, *values in retained_values.itertuples(index=False, name=None):
                    for column, value in zip(PRICE_COLUMNS, values):
                        if (key(sku_label), column) in prices or pd.isna(value):
                            continue
                        price = float(value)
                        if not math.isfinite(price) or price < 0:
                            raise ValueError(f"Invalid retained price for {region}/{sku_label}/{column}.")
                        old = (previous_provenance or {}).get((region, key(sku_label), column, price))
                        row = dict(old or {})
                        warning = "Retained price; not verified by current API scan"
                        if old is None:
                            warning += "; original source and fetch time unknown"
                        if price == 0:
                            warning += "; Zero price: verify before using for cost estimates"
                            print(f"  WARNING: {region}/{sku_label}/{column}: {warning}", flush=True)
                        row.update({
                            "Region": region_label, "SkuName": sku_label, "PriceColumn": column,
                            "Price": price, "Status": "retained",
                            "SourceStatus": row.get("SourceStatus", "unknown"),
                            "LastCheckedAt": now.isoformat(), "Warning": warning,
                        })
                        provenance.append(row)
        if diagnostics is not None:
            retained = {
                (sku, column)
                for column in PRICE_COLUMNS
                for sku in sku_keys[mask & result[column].notna()].unique()
                if (sku, column) not in prices
            } if preserve_existing else set()
            for row in diagnostics[diagnostic_start:]:
                row["ExistingPriceRetained"] = (row["SkuName"], row["PriceColumn"]) in retained
                row["SkuName"] = sku_labels[row["SkuName"]]
        found = sku_keys[mask].map({
            sku: price for (sku, column), price in prices.items() if column == "retail price"
        }).notna().sum()
        print(f"  Matched retail prices: {found:,}/{int(mask.sum()):,} rows", flush=True)
    return result, ambiguous


def load_provenance(path: Path) -> dict[tuple[str, str, str, float], dict]:
    report = pd.read_csv(path, dtype={"RuleVersion": str}, float_precision="round_trip")
    missing = set(PROVENANCE_COLUMNS) - set(report.columns)
    if missing:
        raise ValueError(f"Provenance report {path} is missing columns: {', '.join(sorted(missing))}.")
    result = {}
    for row in report.drop_duplicates().to_dict("records"):
        region, sku, column, price = row["Region"], row["SkuName"], row["PriceColumn"], float(row["Price"])
        if (
            not isinstance(region, str) or not region.strip()
            or not isinstance(sku, str) or not sku.strip()
            or column not in PRICE_COLUMNS or not math.isfinite(price) or price < 0
        ):
            raise ValueError(f"Invalid price identity in provenance report {path}.")
        identity = (key(region), key(sku), column, price)
        if identity in result:
            raise ValueError(f"Conflicting provenance records for {identity} in {path}.")
        result[identity] = row
    return result


def write_diagnostics(rows: list[dict], path: Path) -> int:
    report = pd.DataFrame(rows, columns=DIAGNOSTIC_COLUMNS).drop_duplicates()
    with tempfile.NamedTemporaryFile(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False,
    ) as temporary:
        temporary_path = Path(temporary.name)
    try:
        report.to_csv(temporary_path, index=False)
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)
    return len(report)


def write_exports(
    data: pd.DataFrame, parquet: Path, csv: Path, *,
    provenance: list[dict] | None = None, provenance_path: Path | None = None,
) -> None:
    if (provenance is None) != (provenance_path is None):
        raise ValueError("Provenance rows and output path must be provided together.")
    targets = [parquet, csv]
    if provenance_path is not None:
        targets.append(provenance_path)
    temporary_paths = []
    keep_backups: set[Path] = set()

    def temporary_for(target: Path) -> Path:
        with tempfile.NamedTemporaryFile(
            dir=target.parent, prefix=f".{target.name}.", suffix=".tmp", delete=False,
        ) as temporary:
            path = Path(temporary.name)
        temporary_paths.append(path)
        return path

    try:
        staged = [temporary_for(target) for target in targets]
        data.to_parquet(staged[0], index=False, engine="pyarrow")
        data.to_csv(staged[1], index=False)
        if provenance is not None:
            pd.DataFrame(provenance, columns=PROVENANCE_COLUMNS).drop_duplicates().to_csv(
                staged[2], index=False,
            )
        backups = []
        for target in targets:
            backup = temporary_for(target) if target.exists() else None
            if backup is not None:
                shutil.copy2(target, backup)
            backups.append(backup)
        replaced = []
        try:
            for index, target in enumerate(targets):
                os.replace(staged[index], target)
                replaced.append(index)
        except OSError as error:
            rollback_errors = []
            for index in reversed(replaced):
                backup = backups[index]
                try:
                    if backup is None:
                        targets[index].unlink(missing_ok=True)
                    else:
                        os.replace(backup, targets[index])
                except OSError as rollback_error:
                    if backup is not None:
                        keep_backups.add(backup)
                    rollback_errors.append(
                        f"{targets[index]}: {rollback_error}; original backup: {backup}"
                    )
            if rollback_errors:
                raise OSError(f"Export and rollback failed: {'; '.join(rollback_errors)}") from error
            raise
    finally:
        for path in temporary_paths:
            if path not in keep_backups:
                path.unlink(missing_ok=True)


def print_summary(data: pd.DataFrame, ambiguous: int, elapsed: float) -> None:
    unique = data.assign(
        Region=data["Region"].map(key), SkuName=data["SkuName"].map(key),
    ).drop_duplicates(["Region", "SkuName"])
    print("\nSUMMARY (Linux/base compute, USD per hour)")
    print("Coverage reflects final saved prices, including any retained existing values.")
    print(f"Input/output rows: {len(data):,}")
    print(f"Unique regional SKUs: {len(unique):,}")
    print(f"Distinct SKU names: {unique['SkuName'].nunique():,}")
    for column in PRICE_COLUMNS:
        found = unique[column].notna()
        print(f"{column}: {int(found.sum()):,}/{len(unique):,} ({found.mean():.2%}) regional SKUs")
    retail = unique["retail price"].notna()
    retail_count = int(retail.sum())
    print(f"Regional SKUs without retail price: {len(unique) - retail_count:,}")
    print(f"Distinct SKU names with retail in at least one region: {unique.loc[retail, 'SkuName'].nunique():,}")
    for column in ("1Y RI", "3Y RI"):
        count = int((retail & unique[column].notna()).sum())
        percentage = f"{count / retail_count:.2%}" if retail_count else "N/A"
        print(f"{column} among retail-matched regional SKUs: {count:,}/{retail_count:,} ({percentage})")
    print(f"Conflicting API price cells skipped: {ambiguous:,}")
    print(f"Elapsed: {elapsed:.1f} seconds")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input", type=Path, default=DEFAULT_INPUT,
        help="Parquet to update in place; the sibling .csv is regenerated from it.",
    )
    parser.add_argument(
        "--allow-nonprimary", action="store_true",
        help="Allow unambiguous exact-match nonprimary prices only when no eligible primary offer exists.",
    )
    args = parser.parse_args(argv)
    parquet = args.input.resolve()
    if parquet.suffix.casefold() != ".parquet":
        parser.error("--input must have a .parquet extension.")
    csv = parquet.with_suffix(".csv")
    diagnostics_path = parquet.with_name(f"{parquet.stem}_pricing_diagnostics.csv")
    provenance_path = parquet.with_name(f"{parquet.stem}_pricing_provenance.csv")
    started = time.monotonic()
    print("Pricing: Linux/base compute, USD/hour; excluding Windows, Cloud Services, Spot, Low Priority and DevTest.")
    print(
        "Meter policy: prefer primary; allow explicitly labelled nonprimary fallback."
        if args.allow_nonprimary else "Meter policy: primary only (nonprimary fallback disabled)."
    )
    print("RI hourly rates = full-term API retailPrice / 8,760 (1Y) or 26,280 (3Y).")
    print(f"Will update: {parquet}\n             {csv}", flush=True)
    try:
        data = pd.read_parquet(parquet)
        validate_data(data)
        preserve_existing = choose_preserve_existing(data)
        previous_provenance = (
            load_provenance(provenance_path)
            if preserve_existing and provenance_path.exists() else None
        )
        print(
            "Mode: update found prices and preserve unmatched existing values."
            if preserve_existing else "Mode: recreate pricing columns.",
            flush=True,
        )
        diagnostics: list[dict] = []
        provenance: list[dict] = []
        with create_session() as session:
            enriched, ambiguous = enrich_prices(
                data, session, preserve_existing=preserve_existing, diagnostics=diagnostics,
                provenance=provenance, previous_provenance=previous_provenance,
                allow_nonprimary=args.allow_nonprimary,
            )
        diagnostic_count = write_diagnostics(diagnostics, diagnostics_path)
        print(f"Diagnostics written: {diagnostics_path}", flush=True)
        write_exports(
            enriched, parquet, csv, provenance=provenance, provenance_path=provenance_path,
        )
    except (OSError, ValueError, requests.RequestException) as error:
        print(f"ERROR: Pricing enrichment failed: {error}", file=sys.stderr, flush=True)
        return 1
    print_summary(enriched, ambiguous, time.monotonic() - started)
    print(f"Diagnostic rows: {diagnostic_count:,}")
    print(f"Diagnostics: {diagnostics_path}")
    print(f"Provenance: {provenance_path}")
    audit = pd.DataFrame(provenance, columns=PROVENANCE_COLUMNS).drop_duplicates()
    print(f"Stored price sources: {audit['Status'].value_counts().to_dict()}")
    print(f"Zero price warnings: {int(audit['Price'].eq(0).sum()):,}")
    print(f"Updated: {parquet}\n         {csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
