import unittest

import hud


class WindowTargetTests(unittest.TestCase):
    def test_real_electron_main_window_is_accepted(self):
        # Shape/style observed on the Windows desktop; no title/localization dependency.
        self.assertTrue(hud.is_main_window("Chrome_WidgetWin_1", 0x15070000, None, "ChatGPT.exe"))
        self.assertTrue(hud.is_main_window("Chrome_WidgetWin_1", 0x15070000, None, "Codex.exe"))

    def test_same_process_about_dialog_and_owned_windows_are_excluded(self):
        self.assertFalse(hud.is_main_window("#32770", 0x94C800C4, None, "ChatGPT.exe"))
        self.assertFalse(hud.is_main_window("Chrome_WidgetWin_1", 0x15070000, 42, "ChatGPT.exe"))
        self.assertFalse(hud.is_main_window("Chrome_WidgetWin_1", 0x14C00000, None, "ChatGPT.exe"))

    def test_modal_disabled_main_is_a_valid_anchor_but_other_apps_are_excluded(self):
        self.assertTrue(hud.is_main_window("Chrome_WidgetWin_1", 0x1D070000, None, "ChatGPT.exe"))
        self.assertFalse(hud.is_main_window("Chrome_WidgetWin_1", 0x15070000, None, "Other.exe"))

    def test_narrow_windows_never_push_the_bar_outside_the_title_area(self):
        self.assertIsNone(hud.hud_geometry((800, 700, 1475, 1330), 525, 49, 1.75))
        self.assertIsNone(hud.hud_geometry((0, 0, 1600, 40), 525, 49, 1))
        self.assertEqual(hud.hud_geometry((0, 0, 2194, 1186), 525, 49, 1.75), (525, 49, 1398, 12))

    def test_negative_monitor_coordinates_preserve_dpi_placement(self):
        geometry = hud.hud_geometry((-2560, -300, 0, 1140), 600, 56, 2)
        self.assertEqual(geometry, (600, 56, -910, -286))


if __name__ == "__main__":
    unittest.main()
