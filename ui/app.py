import logging

from config.settings import setup_logging

# Initialize centralized logging (file + stdout) before anything else
setup_logging()
logger = logging.getLogger(__name__)

import streamlit as st

def setup_page_config():
    """Configures the main Streamlit page settings."""
    st.set_page_config(
        page_title="NSE Stock Screener",
        page_icon="📈",
        layout="wide",
        initial_sidebar_state="expanded"
    )

def render_sidebar():
    """Renders global navigation and filters in the sidebar."""
    st.sidebar.title("Navigation & Filters")
    # TODO: Insert logic here for global date pickers or index selections
    pass

def main():
    """Main application entry point."""
    try:
        setup_page_config()
        render_sidebar()
        
        st.title("NSE Quantitative Screener")
        st.markdown("Welcome to the engine. Select a page from the sidebar to view the dashboard, run screeners, or view raw tables.")
        
        # TODO: Insert logic here to load high-level system metrics (e.g., last DB update date)
        
    except Exception as e:
        logger.error(f"Application crashed: {e}")
        st.error("An unexpected error occurred. Please check the logs.")

if __name__ == "__main__":
    main()