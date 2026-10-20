from contextlib import nullcontext

import pandas as pd
import streamlit as st


FAMILY_VERSIONS = ("Other", "v2", "v3", "v4", "v5", "v6", "v7")


def save_family_versions(key_prefix: str) -> None:
    st.session_state["selected_family_versions"] = [
        version for version in FAMILY_VERSIONS
        if st.session_state[f"{key_prefix}_{version.lower()}"]
    ]


def family_version_checkboxes(key_prefix: str, *, horizontal: bool = True) -> list[str]:
    st.markdown("**Family versions**")
    shared_versions = st.session_state.setdefault(
        "selected_family_versions", list(FAMILY_VERSIONS)
    )
    selected_versions = []
    containers = (
        st.columns([2, 1, 1, 1, 1, 1, 1])
        if horizontal else [nullcontext() for _ in FAMILY_VERSIONS]
    )
    for column, version in zip(containers, FAMILY_VERSIONS):
        with column:
            key = f"{key_prefix}_{version.lower()}"
            st.session_state[key] = version in shared_versions
            if st.checkbox(
                version,
                key=key,
                on_change=save_family_versions,
                args=(key_prefix,),
                help="Families without a v2-v7 version in their name." if version == "Other" else None,
            ):
                selected_versions.append(version)
    return selected_versions


def filter_family_versions(df: pd.DataFrame, selected_versions: list[str]) -> pd.DataFrame:
    # Versions may precede qualifiers, such as v2Promo, v3_T4, or V6NDR.
    versions = (
        df["Family"].astype("string")
        .str.extract(r"(?i)(v[2-7])(?!\d)", expand=False)
        .str.lower().fillna("Other")
    )
    return df[versions.isin(selected_versions)]
