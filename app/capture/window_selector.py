"""
Companion - Wayland Window Selector

Uses the XDG Desktop Portal ScreenCast API through D-Bus.

Important:
- No X11 window enumeration
- No unrestricted screen capture
- User explicitly chooses the window
"""

import asyncio
import logging
import uuid

from dbus_next.aio import MessageBus
from dbus_next import Message, Variant
from dbus_next.constants import MessageType


# ============================================================
# Logging
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger("companion.capture")


# ============================================================
# XDG Desktop Portal constants
# ============================================================

BUS_NAME = "org.freedesktop.portal.Desktop"

PORTAL_PATH = "/org/freedesktop/portal/desktop"

SCREENCAST_INTERFACE = "org.freedesktop.portal.ScreenCast"

REQUEST_INTERFACE = "org.freedesktop.portal.Request"


# ============================================================
# WindowSelector
# ============================================================

class WindowSelector:

    # --------------------------------------------------------
    # Initialization
    # --------------------------------------------------------

    def __init__(self):
        self.bus = None
        self.session_handle = None

    # --------------------------------------------------------
    # Connect to D-Bus
    # --------------------------------------------------------

    async def connect(self):
        """Connect to the user's D-Bus session."""

        self.bus = await MessageBus(
            negotiate_unix_fd=True
        ).connect()

        logger.info("Connected to D-Bus")
        logger.info("Connected to XDG Desktop Portal")

    # --------------------------------------------------------
    # Generic D-Bus call
    # --------------------------------------------------------

    async def call(self, member, signature="", body=None):
        """Send a direct D-Bus method call."""

        if body is None:
            body = []

        message = Message(
            destination=BUS_NAME,
            path=PORTAL_PATH,
            interface=SCREENCAST_INTERFACE,
            member=member,
            signature=signature,
            body=body,
        )

        reply = await self.bus.call(message)

        if reply.message_type == MessageType.ERROR:
            raise RuntimeError(
                f"D-Bus error: {reply.error_name}: {reply.body}"
            )

        return reply.body

    # --------------------------------------------------------
    # Wait for portal response
    # --------------------------------------------------------

    async def wait_for_response(self, request_path):
        """
        Wait for the XDG portal Request.Response signal.
        """

        loop = asyncio.get_running_loop()

        future = loop.create_future()

        def handler(message):

            if (
                message.message_type == MessageType.SIGNAL
                and message.interface == REQUEST_INTERFACE
                and message.member == "Response"
                and message.path == request_path
            ):

                if not future.done():
                    future.set_result(message.body)

                return True

            return False

        self.bus.add_message_handler(handler)

        try:
            response, results = await future

        finally:
            self.bus.remove_message_handler(handler)

        if response != 0:
            raise RuntimeError(
                f"Portal request failed with response code: {response}"
            )

        return results

    # --------------------------------------------------------
    # Create ScreenCast session
    # --------------------------------------------------------

    async def create_session(self):
        """Create a ScreenCast session."""

        handle_token = (
            f"companion_{uuid.uuid4().hex}"
        )

        session_token = (
            f"session_{uuid.uuid4().hex}"
        )

        options = {
            "handle_token": Variant(
                "s",
                handle_token,
            ),

            "session_handle_token": Variant(
                "s",
                session_token,
            ),
        }

        logger.info(
            "Creating ScreenCast session..."
        )

        result = await self.call(
            "CreateSession",
            "a{sv}",
            [options],
        )

        request_path = result[0]

        logger.info(
            "Portal request created: %s",
            request_path,
        )

        results = await self.wait_for_response(
            request_path
        )

        self.session_handle = (
            results["session_handle"].value
        )

        logger.info(
            "ScreenCast session created: %s",
            self.session_handle,
        )

        return self.session_handle

    # --------------------------------------------------------
    # Select ONE application window
    # --------------------------------------------------------

    async def select_window(self):
        """Request the user to select one application window."""

        if self.session_handle is None:
            await self.create_session()

        handle_token = (
            f"select_{uuid.uuid4().hex}"
        )

        options = {

            # 1 = monitor
            # 2 = window
            #
            # Companion requests WINDOW only.

            "types": Variant(
                "u",
                2,
            ),

            # Only one source.

            "multiple": Variant(
                "b",
                False,
            ),

            "handle_token": Variant(
                "s",
                handle_token,
            ),
        }

        logger.info(
            "Requesting window selection..."
        )

        result = await self.call(
            "SelectSources",
            "oa{sv}",
            [
                self.session_handle,
                options,
            ],
        )

        request_path = result[0]

        logger.info(
            "Waiting for portal selection..."
        )

        results = await self.wait_for_response(
            request_path
        )

        logger.info(
            "Window selection accepted."
        )

        return results

    # --------------------------------------------------------
    # Start capture
    # --------------------------------------------------------

    async def start_capture(self):
        """Start the authorized screen-capture session."""

        handle_token = (
            f"start_{uuid.uuid4().hex}"
        )

        options = {
            "handle_token": Variant(
                "s",
                handle_token,
            )
        }

        logger.info(
            "Starting screen capture..."
        )

        result = await self.call(
            "Start",
            "osa{sv}",
            [
                self.session_handle,
                "",
                options,
            ],
        )

        request_path = result[0]

        logger.info(
            "Waiting for capture authorization..."
        )

        results = await self.wait_for_response(
            request_path
        )

        streams = results.get("streams")

        logger.info(
            "Capture started successfully."
        )

        logger.info(
            "PipeWire streams: %s",
            streams,
        )

        return streams

    # --------------------------------------------------------
    # Open portal-authorized PipeWire remote
    # --------------------------------------------------------

    async def open_pipewire_remote(self):
        """Open the portal-authorized PipeWire remote."""

        logger.info(
            "Opening portal PipeWire remote..."
        )

        result = await self.call(
            "OpenPipeWireRemote",
            "oa{sv}",
            [
                self.session_handle,
                {},
            ],
        )

        fd = result[0]

        logger.info(
            "Portal PipeWire remote opened. FD=%s",
            fd,
        )

        return fd

    # --------------------------------------------------------
    # Close session
    # --------------------------------------------------------

    async def close(self):
        """Close the portal session."""

        if self.session_handle is None:
            return

        message = Message(
            destination=BUS_NAME,
            path=self.session_handle,
            interface="org.freedesktop.portal.Session",
            member="Close",
        )

        await self.bus.call(message)

        logger.info(
            "Portal session closed."
        )


# ============================================================
# Main
# ============================================================

async def main():

    selector = WindowSelector()

    try:

        print()
        print("==========================================")
        print("        COMPANION WINDOW SELECTOR")
        print("==========================================")
        print()

        # ----------------------------------------------------
        # Connect
        # ----------------------------------------------------

        await selector.connect()

        print(
            "Creating Wayland capture session..."
        )
        print()

        # ----------------------------------------------------
        # Create session
        # ----------------------------------------------------

        await selector.create_session()

        print(
            "Opening window selection..."
        )
        print()

        print(
            "Select ONE application window."
        )
        print()

        # ----------------------------------------------------
        # Select window
        # ----------------------------------------------------

        await selector.select_window()

        print()
        print(
            "Window source selected."
        )

        # ----------------------------------------------------
        # Start capture
        # ----------------------------------------------------

        print(
            "Starting capture..."
        )
        print()

        streams = await selector.start_capture()

        # ----------------------------------------------------
        # Open PipeWire remote
        # ----------------------------------------------------

        pipewire_fd = (
            await selector.open_pipewire_remote()
        )

        # ----------------------------------------------------
        # Success
        # ----------------------------------------------------

        print()
        print("==========================================")
        print("       ✅ CAPTURE STARTED")
        print("==========================================")
        print()

        print(
            f"PipeWire streams: {streams}"
        )

        print(
            f"PipeWire FD: {pipewire_fd}"
        )

        print()
        print(
            "Capture session is alive."
        )

        print(
            "Press Ctrl+C to stop."
        )

        print()

        # ----------------------------------------------------
        # Keep session alive
        # ----------------------------------------------------

        while True:
            await asyncio.sleep(1)

    except Exception as exc:

        logger.exception(
            "Window selection failed"
        )

        print()
        print(
            f"❌ Error: {exc}"
        )
        print()

    finally:

        await selector.close()


# ============================================================
# Entry point
# ============================================================

if __name__ == "__main__":

    asyncio.run(main())