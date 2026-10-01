"""
Terminal Styling & UI Components
================================
Provides custom CSS and reusable UI components for an ultra-clean,
professional dark-mode financial terminal experience.
"""

import streamlit as st


def apply_terminal_theme() -> None:
    """Injects high-grade dark mode financial terminal styles."""
    terminal_css = """
    <style>
    /* Global App Container */
    .stApp {
        background-color: #0b0f17;
        color: #e6edf3;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    }

    /* Main block padding */
    .block-container {
        padding-top: 1.8rem;
        padding-bottom: 2.5rem;
        padding-left: 2.5rem;
        padding-right: 2.5rem;
        max-width: 100%;
    }

    /* Sidebar Styling */
    section[data-testid="stSidebar"] {
        background-color: #111622;
        border-right: 1px solid #1f2937;
    }
    section[data-testid="stSidebar"] .stMarkdown h1, 
    section[data-testid="stSidebar"] .stMarkdown h2, 
    section[data-testid="stSidebar"] .stMarkdown h3 {
        color: #58a6ff;
        font-size: 1.1rem;
        letter-spacing: 0.05em;
        text-transform: uppercase;
    }

    /* Terminal Header Bar */
    .terminal-header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        background: linear-gradient(90deg, #161f30 0%, #0d1524 100%);
        border: 1px solid #1f2e48;
        border-left: 4px solid #38bdf8;
        border-radius: 6px;
        padding: 14px 20px;
        margin-bottom: 22px;
    }
    .terminal-title {
        font-size: 1.4rem;
        font-weight: 700;
        letter-spacing: -0.02em;
        color: #f0f6fc;
        margin: 0;
    }
    .terminal-subtitle {
        font-size: 0.85rem;
        color: #8b949e;
        margin-top: 4px;
        font-family: ui-monospace, "SF Mono", Menlo, Monaco, Consolas, monospace;
    }
    .terminal-badge {
        display: inline-block;
        padding: 4px 10px;
        border-radius: 4px;
        font-size: 0.75rem;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.08em;
    }
    .badge-live {
        background-color: rgba(35, 134, 54, 0.2);
        color: #3fb950;
        border: 1px solid rgba(63, 185, 80, 0.4);
    }
    .badge-demo {
        background-color: rgba(210, 153, 34, 0.15);
        color: #d29922;
        border: 1px solid rgba(210, 153, 34, 0.4);
    }
    .badge-strategy {
        background-color: rgba(56, 189, 248, 0.15);
        color: #38bdf8;
        border: 1px solid rgba(56, 189, 248, 0.4);
    }

    /* Metric Cards */
    div[data-testid="stMetric"] {
        background: #111726;
        border: 1px solid #1e293b;
        border-radius: 8px;
        padding: 14px 18px;
        box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.2);
    }
    div[data-testid="stMetricLabel"] {
        font-size: 0.8rem;
        color: #94a3b8;
        text-transform: uppercase;
        letter-spacing: 0.06em;
        font-weight: 600;
    }
    div[data-testid="stMetricValue"] {
        font-size: 1.5rem;
        font-weight: 700;
        color: #f8fafc;
        font-family: ui-monospace, "SF Mono", Menlo, Monaco, Consolas, monospace;
    }

    /* Strategy Methodology Box */
    .strategy-box {
        background: #101726;
        border: 1px solid #1e2e4a;
        border-radius: 8px;
        padding: 18px 22px;
        margin-bottom: 24px;
    }
    .strategy-title {
        color: #38bdf8;
        font-size: 1.1rem;
        font-weight: 700;
        margin-bottom: 8px;
    }
    .rule-item {
        margin: 6px 0;
        font-size: 0.9rem;
        color: #cbd5e1;
        font-family: ui-monospace, "SF Mono", Menlo, Monaco, Consolas, monospace;
    }

    /* Buttons */
    .stButton > button {
        background: #1e293b;
        color: #38bdf8;
        border: 1px solid #38bdf8;
        border-radius: 6px;
        font-weight: 600;
        padding: 0.45rem 1.2rem;
        transition: all 0.2s ease-in-out;
    }
    .stButton > button:hover {
        background: #38bdf8;
        color: #0b0f17;
        border-color: #38bdf8;
        box-shadow: 0 0 12px rgba(56, 189, 248, 0.4);
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
