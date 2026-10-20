from pathlib import Path

import pandas as pd
import streamlit as st


NUMERIC_COLUMNS = ("vCPUs", "MemoryGB", "GPUs", "MaxDataDiskCount")
DATA_PATH = Path(__file__).resolve().parent / "data" / "vm_skus_all_regions.parquet"


@st.cache_data
def load_data() -> pd.DataFrame:
    df = pd.read_parquet(DATA_PATH)
    for column in NUMERIC_COLUMNS:
        if column in df.columns:
            df[column] = pd.to_numeric(df[column], errors="coerce")
    return df


def distinct_values(values: pd.Series) -> str:
    return ", ".join(sorted({str(value) for value in values.dropna()}))


def summarize_families(df: pd.DataFrame) -> pd.DataFrame:
    data = df.copy()
    for column in NUMERIC_COLUMNS:
        data[column] = pd.to_numeric(data[column], errors="coerce")
    return data.groupby("Family", sort=True).agg(
        **{
            "Unique SKU Count": ("SkuName", "nunique"),
            "Region Count": ("Region", "nunique"),
            "CPU Architecture(s)": ("CpuArchitectureType", distinct_values),
            "Min vCPU": ("vCPUs", "min"),
            "Max vCPU": ("vCPUs", "max"),
            "Min GPU": ("GPUs", "min"),
            "Max GPU": ("GPUs", "max"),
            "Min Memory (GB)": ("MemoryGB", "min"),
            "Max Memory (GB)": ("MemoryGB", "max"),
            "Min Data Disk": ("MaxDataDiskCount", "min"),
            "Max Data Disk": ("MaxDataDiskCount", "max"),
            "Premium IO": ("PremiumIO", distinct_values),
        }
    ).reset_index()


def distinct_delimited_values(values: pd.Series) -> str:
    return ", ".join(sorted({
        item.strip()
        for value in values.dropna()
        for item in str(value).split(",")
        if item.strip()
    }))


def summarize_regions(df: pd.DataFrame) -> pd.DataFrame:
    data = df.copy()
    for column in ("vCPUs", "MemoryGB", "GPUs"):
        data[column] = pd.to_numeric(data[column], errors="coerce")
    data["_ZonedSkuName"] = data["SkuName"].where(
        data["AvailabilityZones"].str.contains(r"[^,\s]", na=False)
    )
    aggregations = {
        "VM SKU Count": ("SkuName", "nunique"),
        "Family Count": ("Family", "nunique"),
        "Availability Zones": ("AvailabilityZones", distinct_delimited_values),
        "SKUs with Zone Data": ("_ZonedSkuName", "nunique"),
        "CPU Architecture(s)": ("CpuArchitectureType", distinct_values),
        "Max vCPU": ("vCPUs", "max"),
        "Max Memory (GB)": ("MemoryGB", "max"),
        "Max GPU": ("GPUs", "max"),
    }
    for label, column in (
        ("Premium IO", "PremiumIO"),
        ("Accelerated Networking", "AcceleratedNetworkingEnabled"),
        ("Ultra SSD", "UltraSSDAvailable"),
        ("Ephemeral OS Disk", "EphemeralOSDiskSupported"),
        ("Hyper-V Generations", "HyperVGenerations"),
        ("Tiers", "Tier"),
    ):
        if column in data.columns:
            aggregations[label] = (column, distinct_delimited_values)

    summary = data.groupby("Region", sort=True).agg(**aggregations).reset_index()
    summary.insert(
        4,
        "Zone Count",
        summary["Availability Zones"].map(
            lambda zones: len(zones.split(", ")) if zones else pd.NA
        ).astype("Int64"),
    )
    summary["Availability Zones"] = summary["Availability Zones"].replace("", "Not reported")
    return summary


def summarize_matrix(df: pd.DataFrame) -> pd.DataFrame:
    cpu_bounds = [2 ** exponent for exponent in range(1, 11)]
    memory_bounds = [2 ** exponent for exponent in range(2, 15)]
    cpu_labels = [
        "1-2",
        *[f"{lower + 1}-{upper}" for lower, upper in zip(cpu_bounds, cpu_bounds[1:])],
        ">1024",
    ]
    memory_labels = [
        "<=4",
        *[f">{lower}-{upper}" for lower, upper in zip(memory_bounds, memory_bounds[1:])],
        ">16384",
    ]
    data = df[["SkuName", "vCPUs", "MemoryGB"]].copy()
    for source, target, bounds, labels in (
        ("vCPUs", "vCPU range", cpu_bounds, cpu_labels),
        ("MemoryGB", "Memory range (GB)", memory_bounds, memory_labels),
    ):
        values = pd.to_numeric(data[source], errors="coerce").replace(
            [float("inf"), float("-inf")], float("nan")
        )
        data[target] = pd.cut(values, bins=[0, *bounds, float("inf")], labels=labels)
    matrix = (
        data.groupby(["vCPU range", "Memory range (GB)"], observed=False)["SkuName"]
        .nunique().unstack(fill_value=0)
    )
    matrix.index = pd.Index(cpu_labels, name="vCPU range")
    matrix.columns = pd.Index(memory_labels, name="Memory range (GB)")
    return matrix.astype("int64")
