import threading
import time
import webview
from textual_serve.server import Server

def run_textual():
    """Starts the Textual web server in the background using textual-serve."""
    print("[SERVER] Spinning up Textual web service on http://localhost:8000 ...")
    # This instructs the wrapper to execute your tui_app.py file as a web service
    server = Server("python app.py", host="localhost", port=8000)
    server.serve()

if __name__ == "__main__":
    # 1. Start the TUI server on a background thread
    tui_thread = threading.Thread(target=run_textual, daemon=True)
    tui_thread.start()

    # 2. Give the background server 2 seconds to initialize ports
    print("[MAIN] Waiting for local web socket...")
    time.sleep(2)

    # 3. Open the webpage inside a clean pywebview native window
    print("[MAIN] Launching pywebview view canvas...")
    webview.create_window(
        title="Textual Web App",
        url="http://localhost:8000",
        width=1000,
        height=700
    )
    
    # Start blocking main GUI thread
    webview.start()
