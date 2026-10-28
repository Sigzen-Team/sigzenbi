"""P6: client-2 system pages (P5 `system_page`) follow the OS dark setting. Copied to apps/sigzenbi/sigzenbi/tests/ by P6 Task 7."""
import re

from frappe.tests.utils import FrappeTestCase

from sigzenbi.utils import guided_fallback


def _dark_block(html):
	m = re.search(r"@media\s*\(\s*prefers-color-scheme\s*:\s*dark\s*\)\s*\{(.*?\})\s*\}", html, re.S)
	return m.group(1) if m else ""


class TestP6SystemDark(FrappeTestCase):
	def test_both_fallbacks_carry_a_dark_block(self):
		for configured in (True, False):
			block = _dark_block(guided_fallback("Your dashboard", configured))
			self.assertIn("#14161B", block.upper())  # ground
			self.assertIn("#1B1E24", block.upper())  # card surface
			self.assertIn("#ECEDEF", block.upper())  # text

	def test_light_look_unchanged(self):
		html = guided_fallback("Your dashboard", True)
		self.assertIn("#FAFAF8", html.upper())
		self.assertIn("is temporarily unavailable", html)
		self.assertIn("is not available yet", guided_fallback("Your dashboard", False))

	def test_reduced_motion_kept(self):
		self.assertIn("prefers-reduced-motion", guided_fallback("Your dashboard", True))
