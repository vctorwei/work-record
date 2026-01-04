
import streamlit.components.v1 as components
import json

def st_javascript(js_code):
    """
    Execute JS code and return the result.
    This is a minimal implementation of streamlit-javascript to avoid extra dependencies.
    """
    components.html(f"""
        <script>
            var result = eval("{js_code}");
            window.parent.postMessage({{
                isStreamlitMessage: true,
                type: "streamlit:setComponentValue",
                value: result
            }}, "*");
        </script>
    """, height=0, width=0)

