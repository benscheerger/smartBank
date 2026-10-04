import secrets
import webbrowser
from threading import Thread

from werkzeug.serving import make_server

from automation.console import (
    ConsoleController,
    create_console_app,
)
from automation.takeover_controls import QuietRequestHandler
from automation.terminal import run_cli


def main():
    controller = ConsoleController()
    token = secrets.token_hex(32)

    app = create_console_app(controller, token)

    server = make_server(
        "127.0.0.1",
        0,
        app,
        threaded=True,
        request_handler=QuietRequestHandler,
    )

    controller.origin = (
        f"http://127.0.0.1:{server.server_port}"
    )

    server_thread = Thread(
        target=server.serve_forever,
        daemon=True,
        name="console-server",
    )
    server_thread.start()

    print(f"Automation console: {controller.origin}")

    try:
        try:
            webbrowser.open(controller.origin)
        except webbrowser.Error:
            print("Open the console URL manually.")

        controller.run_forever()

    except KeyboardInterrupt:
        print("\nConsole stopped.")

    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)


if __name__ == "__main__":
    run_cli(main)
