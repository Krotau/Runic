from __future__ import annotations

import unittest

from runic.interactive.install_status import (
    InstallPhase,
    InstallPhaseState,
    InstallStatusUpdate,
    encode_install_status,
    format_install_line,
    is_install_status_log,
    parse_install_status,
)


class TestInteractiveInstallStatus(unittest.TestCase):
    def test_encode_and_parse_round_trip(self) -> None:
        update = InstallStatusUpdate(
            phase=InstallPhase.DOWNLOADING,
            state=InstallPhaseState.ACTIVE,
            detail="pulling manifest",
            progress=0.5,
        )

        encoded = encode_install_status(update)

        self.assertTrue(is_install_status_log(encoded))
        self.assertEqual(update, parse_install_status(encoded))

    def test_parse_returns_none_for_plain_logs(self) -> None:
        self.assertIsNone(parse_install_status("pulling manifest"))

    def test_format_install_line_renders_connecting_done_copy(self) -> None:
        update = InstallStatusUpdate(
            phase=InstallPhase.CONNECTING,
            state=InstallPhaseState.DONE,
        )

        self.assertEqual("connecting.... connected!", format_install_line(update))

    def test_format_install_line_renders_progress_bar_for_downloads(self) -> None:
        update = InstallStatusUpdate(
            phase=InstallPhase.DOWNLOADING,
            state=InstallPhaseState.ACTIVE,
            progress=0.5,
        )

        self.assertEqual("downloading... [#######_______] 50%", format_install_line(update, width=14))
