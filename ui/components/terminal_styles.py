"""
Terminal Styling & UI Components
================================
Provides custom CSS and reusable UI components for an ultra-clean,
production-grade Bloomberg / TradingView dark-mode terminal with vibrant accents.
"""

from __future__ import annotations

import streamlit as st


def apply_terminal_theme() -> None:
    """Injects high-grade dark mode financial terminal styles with vibrant accents."""
    terminal_css = """
    <style>
    /* Global App Container */
    .stApp {
        background-color: #080c14;
        color: #f1f5f9;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    }

    /* Main block padding */
    .block-container {
        padding-top: 1.5rem;
        padding-bottom: 2.5rem;
        padding-left: 2rem;
        padding-right: 2rem;
        max-width: 100%;
    }

    /* Sidebar Styling */
    section[data-testid="stSidebar"] {
        background: linear-gradient(180deg, #0d131f 0%, #090e17 100%);
        border-right: 1px solid #1e293b;
    }
    section[data-testid="stSidebar"] .stMarkdown h1,
    section[data-testid="stSidebar"] .stMarkdown h2,
    section[data-testid="stSidebar"] .stMarkdown h3 {
        color: #38bdf8;
        font-size: 1.05rem;
        letter-spacing: 0.06em;
        text-transform: uppercase;
        font-weight: 700;
    }

    /* Terminal Header Bar */
    .terminal-header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        background: linear-gradient(90deg, #0f172a 0%, #172033 100%);
        border: 1px solid #1e293b;
        border-left: 4px solid #38bdf8;
        border-radius: 8px;
        padding: 14px 22px;
        margin-bottom: 20px;
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
    }
    .terminal-title {
        font-size: 1.35rem;
        font-weight: 800;
        letter-spacing: -0.01em;
        color: #f8fafc;
        margin: 0;
        display: flex;
        align-items: center;
        gap: 8px;
    }
    .terminal-subtitle {
        font-size: 0.82rem;
        color: #94a3b8;
        margin-top: 4px;
        font-family: ui-monospace, "SF Mono", Menlo, Monaco, Consolas, monospace;
        letter-spacing: 0.04em;
    }
    .terminal-badge {
        display: inline-flex;
        align-items: center;
        gap: 6px;
        padding: 4px 12px;
        border-radius: 6px;
        font-size: 0.75rem;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 0.08em;
    }
    .badge-live {
        background: rgba(16, 185, 129, 0.15);
        color: #10b981;
        border: 1px solid rgba(16, 185, 129, 0.4);
        box-shadow: 0 0 10px rgba(16, 185, 129, 0.2);
    }
    .badge-demo {
        background: rgba(245, 158, 11, 0.15);
        color: #f59e0b;
        border: 1px solid rgba(245, 158, 11, 0.4);
    }

    /* Metric Cards - Multi-Color Glowing Tops */
    div[data-testid="stMetric"] {
        background: #0f1626;
        border: 1px solid #1e293b;
        border-radius: 8px;
        padding: 12px 16px;
        box-shadow: 0 4px 8px rgba(0, 0, 0, 0.25);
        transition: transform 0.15s ease, border-color 0.15s ease;
    }
    div[data-testid="stMetric"]:hover {
        border-color: #38bdf8;
        transform: translateY(-1px);
    }
    div[data-testid="stMetricLabel"] {
        font-size: 0.78rem;
        color: #94a3b8;
        text-transform: uppercase;
        letter-spacing: 0.06em;
        font-weight: 600;
    }
    div[data-testid="stMetricValue"] {
        font-size: 1.45rem;
        font-weight: 800;
        color: #f8fafc;
        font-family: ui-monospace, "SF Mono", Menlo, Monaco, Consolas, monospace;
    }

    /* Filter Ribbon & Parameter Badges (Zero Verbose Text) */
    .filter-ribbon {
        display: flex;
        flex-wrap: wrap;
        gap: 8px;
        margin-bottom: 18px;
        align-items: center;
    }
    .filter-chip {
        display: inline-flex;
        align-items: center;
        gap: 5px;
        padding: 5px 11px;
        border-radius: 6px;
        font-size: 0.78rem;
        font-weight: 600;
        font-family: ui-monospace, "SF Mono", Menlo, Monaco, Consolas, monospace;
        letter-spacing: 0.02em;
    }
    .chip-cyan {
        background: rgba(0, 240, 255, 0.1);
        color: #00f0ff;
        border: 1px solid rgba(0, 240, 255, 0.35);
    }
    .chip-green {
        background: rgba(16, 185, 129, 0.1);
        color: #10b981;
        border: 1px solid rgba(16, 185, 129, 0.35);
    }
    .chip-amber {
        background: rgba(245, 158, 11, 0.1);
        color: #f59e0b;
        border: 1px solid rgba(245, 158, 11, 0.35);
    }
    .chip-purple {
        background: rgba(168, 85, 247, 0.1);
        color: #c084fc;
        border: 1px solid rgba(168, 85, 247, 0.35);
    }
    .chip-rose {
        background: rgba(244, 63, 94, 0.1);
        color: #fb7185;
        border: 1px solid rgba(244, 63, 94, 0.35);
    }

    /* Sidebar Index Cards & Strategy Cards */
    .sidebar-header-box {
        background: linear-gradient(135deg, rgba(14, 165, 233, 0.15) 0%, rgba(30, 41, 59, 0.4) 100%);
        border: 1px solid rgba(56, 189, 248, 0.25);
        border-radius: 8px;
        padding: 12px 14px;
        margin-bottom: 12px;
    }
    .sidebar-brand-title {
        font-size: 1.1rem;
        font-weight: 800;
        letter-spacing: 0.04em;
        color: #f8fafc;
        display: flex;
        align-items: center;
        gap: 8px;
        margin: 0;
    }
    .sidebar-brand-subtitle {
        font-size: 0.72rem;
        color: #94a3b8;
        font-family: ui-monospace, "SF Mono", Menlo, Monaco, Consolas, monospace;
        margin-top: 3px;
        text-transform: uppercase;
        letter-spacing: 0.05em;
    }
    .sidebar-card {
        background: #0f172a;
        border: 1px solid #1e293b;
        border-radius: 6px;
        padding: 8px 12px;
        margin: 6px 0;
        font-size: 0.8rem;
    }
    .index-badge-card {
        background: #0f172a;
        border-left: 3px solid #38bdf8;
        padding: 6px 10px;
        border-radius: 4px;
        margin: 6px 0;
        font-size: 0.8rem;
        color: #cbd5e1;
    }

    /* Buttons */
    .stButton > button {
        background: linear-gradient(180deg, #1e293b 0%, #0f172a 100%);
        color: #38bdf8;
        border: 1px solid #38bdf8;
        border-radius: 6px;
        font-weight: 700;
        padding: 0.45rem 1.2rem;
        transition: all 0.2s ease-in-out;
    }
    .stButton > button:hover {
        background: #0284c7;
        color: #ffffff;
        border-color: #38bdf8;
        box-shadow: 0 0 14px rgba(56, 189, 248, 0.4);
    }

    /* Dataframe formatting */
    .stDataFrame {
        border: 1px solid #1e293b;
        border-radius: 8px;
        overflow: hidden;
    }
    </style>
    """
    st.markdown(terminal_css, unsafe_allow_html=True)
    render_sidebar_controls()


def render_terminal_header(title: str, subtitle: str, status_text: str, is_live: bool = True) -> None:
    """Renders the top Bloomberg-style header bar with connection badges."""
    badge_class = "badge-live" if is_live else "badge-demo"
    html = f"""
    <div class="terminal-header">
        <div>
            <h1 class="terminal-title">⚡ {title}</h1>
            <div class="terminal-subtitle">{subtitle}</div>
        </div>
        <div>
            <span class="terminal-badge {badge_class}">● {status_text}</span>
        </div>
    </div>
    """
    st.markdown(html, unsafe_allow_html=True)


def render_sidebar_controls() -> None:
    """Render unified production-grade sidebar telemetry and data pipeline triggers."""
    # Top branding box: modern trading terminal identity
    st.sidebar.markdown("""
    <div class="sidebar-header-box">
        <div class="sidebar-brand-title">📈 TRADING TERMINAL</div>
        <div class="sidebar-brand-subtitle">NSE Quantitative Screener</div>
    </div>
    """, unsafe_allow_html=True)

    # Market Data Pipeline trigger with safe incremental execution
    st.sidebar.markdown("#### 🔄 Ingestion Engine")
    if st.sidebar.button(
        "⚡ Sync Market Data",
        key="sidebar_etl_incremental_sync",
        disabled=st.session_state.get("is_updating_etl", False),
        width="stretch",
        help="Incrementally fetch latest Bhavcopy records and sync MySQL without full re-download",
    ):
        from src.etl.pipeline import BhavcopyETL
        st.session_state.is_updating_etl = True
        try:
            with st.spinner("Syncing latest market data..."):
                etl = BhavcopyETL()
                res = etl.run_incremental()
                st.session_state.etl_status = res
                # Invalidate in-memory session cache to display freshly updated records
                st.session_state.market_data_cache = None
        except Exception as exc:
            st.session_state.etl_status = {"status": "error", "message": str(exc)}
        finally:
            st.session_state.is_updating_etl = False
        st.rerun()

    # Proactive status notification handling (including offline mode guard)
    if st.session_state.get("etl_status"):
        status = st.session_state.etl_status.get("status")
        msg = st.session_state.etl_status.get("message", "")
        if status == "success":
            st.sidebar.success(f"✓ {msg}")
        elif status in ("up_to_date", "offline"):
            # Informational badge when already current or utilizing local parquet cache
            st.sidebar.info(f"ℹ {msg}")
        else:
            st.sidebar.error(f"✗ {msg}")

    st.sidebar.divider()

    # Tracked Universe Telemetry
    st.sidebar.markdown("#### 🎯 Tracked Universe")
    st.sidebar.markdown("""
    <div class="index-badge-card" style="border-left-color: #38bdf8;">
        <strong>NIFTY 100</strong> · Large Cap Core (100)
    </div>
    <div class="index-badge-card" style="border-left-color: #a855f7;">
        <strong>NIFTY Midcap 150</strong> · Growth Midcaps (150)
    </div>
    <div class="index-badge-card" style="border-left-color: #f59e0b;">
        <strong>NIFTY Smallcap 250</strong> · High-Beta Smallcaps (250)
    </div>
    """, unsafe_allow_html=True)

    st.sidebar.divider()

    # Quantitative Strategies Summary
    st.sidebar.markdown("#### 📐 Screener Strategies")
    st.sidebar.markdown("""
    <div class="sidebar-card">
        <span style="color:#00f0ff; font-weight:700;">🌊 Swing + Volume</span><br/>
        <span style="color:#94a3b8; font-size:0.75rem;">Breakout & Volume Expansion</span>
    </div>
    <div class="sidebar-card">
        <span style="color:#a855f7; font-weight:700;">🚀 Swing With Momentum</span><br/>
        <span style="color:#94a3b8; font-size:0.75rem;">Multi-Horizon Stage Growth</span>
    </div>
    """, unsafe_allow_html=True)

    st.sidebar.divider()
    st.sidebar.caption("NSE Screener Terminal v2.2 · Production")
