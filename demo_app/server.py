from __future__ import annotations

from threading import Thread
from typing import Any, Literal

from werkzeug.serving import (
    BaseWSGIServer,
    WSGIRequestHandler,
    make_server,
)

from demo_app.app import create_app

DemoTargetUrl = Literal["http://127.0.0.1:8000/"]


class DemoServerStartError(RuntimeError):
    pass


class QuietDemoRequestHandler(WSGIRequestHandler):
    def log(
        self,
        type: str,
        message: str,
        *args: Any,
    ) -> None:
        pass


class DemoServer:
    url: DemoTargetUrl = "http://127.0.0.1:8000/"

    def __init__(
        self,
        *,
        dataset_id: str,
        notice_mode: str = "off",
    ):
        self.dataset_id = dataset_id
        self.notice_mode = notice_mode

        self._server: BaseWSGIServer | None = None
        self._thread: Thread | None = None

    def __enter__(self) -> DemoServer:
        app = create_app(
            dataset_id=self.dataset_id,
            notice_mode=self.notice_mode,
        )

        try:
            server = make_server(
                "127.0.0.1",
                8000,
                app,
                threaded=True,
                request_handler=QuietDemoRequestHandler,
            )
        except (OSError, SystemExit) as exc:
            raise DemoServerStartError(
                "Could not start the demo on port 8000. "
                "Stop any separately running Flask demo first."
            ) from exc

        thread = Thread(
            target=server.serve_forever,
            daemon=True,
            name="demo-server",
        )

        try:
            thread.start()
        except BaseException:
            server.server_close()
            raise

        self._server = server
        self._thread = thread

        return self

    def __exit__(self, exc_type, exc_value, traceback) -> Literal[False]:
        server = self._server
        thread = self._thread

        if server is None:
            return False

        try:
            server.shutdown()
        finally:
            server.server_close()

            if thread is not None:
                thread.join(timeout=2)

            self._server = None
            self._thread = None

        return False
