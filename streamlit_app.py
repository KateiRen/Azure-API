from pathlib import Path

import streamlit as st


st.set_page_config(
    page_title="Azure VM SKU Explorer",
    page_icon=":material/cloud:",
    layout="wide",
)

pages_dir = Path(__file__).resolve().parent / "app_pages"
page = st.navigation([
    st.Page(
        pages_dir / "sku_browser.py",
        title="SKU browser",
        icon=":material/search:",
        default=True,
    ),
    st.Page(
        pages_dir / "family_summary.py",
        title="Family summary",
        icon=":material/table_chart:",
    ),
    st.Page(
        pages_dir / "region_summary.py",
        title="Region Summary",
        icon=":material/public:",
    ),
    st.Page(
        pages_dir / "matrix_view.py",
        title="Matrix View",
        icon=":material/grid_on:",
    ),
])
page.run()
