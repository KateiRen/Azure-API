import pandas as pd
import streamlit as st


PRICING_FILTERS = (
    ("With pricing", "retail price", "with_pricing"),
    ("With 1Y RI", "1Y RI", "with_1y_ri"),
    ("With 3Y RI", "3Y RI", "with_3y_ri"),
)


def pricing_checkboxes(df: pd.DataFrame, key_prefix: str) -> list[str]:
    st.sidebar.markdown("**Pricing availability**")
    required_prices = []
    for label, column, suffix in PRICING_FILTERS:
        key = f"{key_prefix}_{suffix}"
        available = column in df.columns
        if not available:
            st.session_state[key] = False
        if st.sidebar.checkbox(
            label,
            key=key,
            disabled=not available,
            help=(
                f"Require a numeric {column} value, including zero. All checked price "
                "types must be present."
                if available else
                f"The saved dataset has no {column} column. Fetch prices using launcher option 2."
            ),
        ):
            required_prices.append(column)
    return required_prices


def filter_pricing(df: pd.DataFrame, required_prices: list[str]) -> pd.DataFrame:
    filtered = df
    for column in required_prices:
        filtered = filtered[pd.to_numeric(filtered[column], errors="coerce").notna()]
    return filtered
