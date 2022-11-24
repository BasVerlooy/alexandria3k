#
# Alexandria3k Crossref bibliographic metadata processing
# Copyright (C) 2022  Diomidis Spinellis
# SPDX-License-Identifier: GPL-3.0-or-later
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
"""Performance module test"""

import io
import sys
import unittest

sys.path.append("src/alexandria3k")

from debug import Debug
from perf import Perf

class TestPerf(unittest.TestCase):
    def test_no_output(self):

        p = Perf()
        d = Debug()
        f = io.StringIO()
        d.set_output(f)

        p.print("One message")
        self.assertRegex(f.getvalue(), r"^$")

    def test_output(self):

        p = Perf()
        d = Debug()
        d.set_flags(["perf"])
        f = io.StringIO()
        d.set_output(f)

        p.print("phase 1")
        self.assertRegex(f.getvalue(), r"phase 1")
