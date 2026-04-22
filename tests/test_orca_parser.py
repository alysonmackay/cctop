from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from cctop.models import Status
from cctop.orca import parse_orca


ORCA_DONE = """
O   R   C   A
Program Version 6.0.0

INPUT FILE
! B3LYP def2-SVP Opt Freq
* xyz 0 1
H 0 0 0
H 0 0 1
*

FINAL SINGLE POINT ENERGY     -1.123456789

VIBRATIONAL FREQUENCIES
   0:        102.33 cm**-1
   1:        240.12 cm**-1
NORMAL MODES

Final Gibbs free energy           ...     -1.100000
TOTAL RUN TIME: 0 days 1 hours 2 minutes 3 seconds
****ORCA TERMINATED NORMALLY****
"""

ORCA_IMAG = ORCA_DONE.replace("   0:        102.33", "   0:        -31.20")

ORCA_FAIL = """
O   R   C   A
Program Version 6.0.0
! PBE0 def2-TZVP Opt
* xyz 0 1
H 0 0 0
H 0 0 1
*
SCF NOT CONVERGED AFTER 500 CYCLES
ORCA finished by error termination
TOTAL RUN TIME: 0 days 0 hours 4 minutes 5 seconds
"""

ORCA_RUNNING = """
O   R   C   A
Program Version 6.0.0
! PBE0 def2-TZVP Opt
* xyz 0 1
H 0 0 0
H 0 0 1
*
"""

ORCA_DLPNO_SINGLE_POINT = """
O   R   C   A
Program Version 6.0.0

INPUT FILE
! DLPNO-CCSD(T) def2-TZVPP TightSCF
%mdci
  MaxIter 200
end
* xyz 0 1
C 0 0 0
O 0 0 1.2
*

Maximum number of iterations          ...     200
FINAL SINGLE POINT ENERGY     -113.292884931
TOTAL RUN TIME: 0 days 3 hours 21 minutes 9 seconds
****ORCA TERMINATED NORMALLY****
"""

ORCA_MAX_ITER_REACHED = """
O   R   C   A
Program Version 6.0.0
! B3LYP def2-SVP Opt
* xyz 0 1
H 0 0 0
H 0 0 1
*
Maximum number of geometry iterations has been reached
FINAL SINGLE POINT ENERGY     -1.123456789
TOTAL RUN TIME: 0 days 1 hours 2 minutes 3 seconds
****ORCA TERMINATED NORMALLY****
"""

ORCA_CONVERGED_OPT_WITH_INTERMEDIATE_NOT_CONVERGED = """
O   R   C   A
Program Version 6.0.0

INPUT FILE
! B3LYP def2-SVP Opt Freq
* xyz 0 1
H 0 0 0
H 0 0 1
*

GEOMETRY OPTIMIZATION CYCLE   1
Geometry optimization has not yet converged.
FINAL SINGLE POINT ENERGY     -1.010000000

GEOMETRY OPTIMIZATION CYCLE   2
Geometry optimization has not yet converged.
FINAL SINGLE POINT ENERGY     -1.120000000

GEOMETRY OPTIMIZATION CYCLE   3
THE OPTIMIZATION HAS CONVERGED
FINAL SINGLE POINT ENERGY     -1.123456789

VIBRATIONAL FREQUENCIES
   0:        102.33 cm**-1
   1:        240.12 cm**-1
NORMAL MODES

TOTAL RUN TIME: 0 days 1 hours 2 minutes 3 seconds
****ORCA TERMINATED NORMALLY****
"""

ORCA_TS_WITH_MULTIPLE_FREQUENCY_BLOCKS = """
O   R   C   A
Program Version 6.0.0

INPUT FILE
! B3LYP def2-SVP OptTS Freq
* xyz 0 1
H 0 0 0
H 0 0 1
*

VIBRATIONAL FREQUENCIES
   0:       -421.50 cm**-1
   1:         88.10 cm**-1
NORMAL MODES

FINAL SINGLE POINT ENERGY     -1.120000000

VIBRATIONAL FREQUENCIES
   0:       -187.25 cm**-1
   1:         95.40 cm**-1
NORMAL MODES

TOTAL RUN TIME: 0 days 1 hours 2 minutes 3 seconds
****ORCA TERMINATED NORMALLY****
"""


class OrcaParserTest(unittest.TestCase):
    def test_done_output(self) -> None:
        calc = self._parse(ORCA_DONE)
        self.assertEqual(calc.status, Status.DONE)
        self.assertEqual(calc.version, "6.0.0")
        self.assertEqual(calc.method, "B3LYP")
        self.assertEqual(calc.basis, "def2-SVP")
        self.assertEqual(calc.charge, 0)
        self.assertEqual(calc.multiplicity, 1)
        self.assertAlmostEqual(calc.final_energy or 0.0, -1.123456789)
        self.assertAlmostEqual(calc.gibbs_energy or 0.0, -1.1)
        self.assertEqual(calc.imaginary_frequency_count, 0)
        self.assertEqual(calc.runtime_seconds, 3723)

    def test_imaginary_frequency_is_suspicious(self) -> None:
        calc = self._parse(ORCA_IMAG)
        self.assertEqual(calc.status, Status.SUSPICIOUS)
        self.assertEqual(calc.imaginary_frequency_count, 1)
        self.assertAlmostEqual(calc.lowest_frequency or 0.0, -31.2)

    def test_error_output_failed(self) -> None:
        calc = self._parse(ORCA_FAIL)
        self.assertEqual(calc.status, Status.FAILED)
        self.assertEqual(calc.warning_count, 2)

    def test_recent_incomplete_output_is_running(self) -> None:
        calc = self._parse(ORCA_RUNNING)
        self.assertEqual(calc.status, Status.RUNNING)

    def test_dlpno_single_point_maxiter_setting_is_done(self) -> None:
        calc = self._parse(ORCA_DLPNO_SINGLE_POINT)
        self.assertEqual(calc.status, Status.DONE)
        self.assertEqual(calc.method, "DLPNO-CCSD(T)")
        self.assertEqual(calc.basis, "def2-TZVPP")
        self.assertEqual(calc.warning_count, 0)

    def test_reached_max_iterations_is_suspicious(self) -> None:
        calc = self._parse(ORCA_MAX_ITER_REACHED)
        self.assertEqual(calc.status, Status.SUSPICIOUS)
        self.assertEqual(calc.warning_count, 1)

    def test_intermediate_geometry_not_yet_converged_is_done(self) -> None:
        calc = self._parse(ORCA_CONVERGED_OPT_WITH_INTERMEDIATE_NOT_CONVERGED)
        self.assertEqual(calc.status, Status.DONE)
        self.assertEqual(calc.warning_count, 0)

    def test_uses_latest_frequency_block(self) -> None:
        calc = self._parse(ORCA_TS_WITH_MULTIPLE_FREQUENCY_BLOCKS)
        self.assertEqual(calc.status, Status.SUSPICIOUS)
        self.assertEqual(calc.imaginary_frequency_count, 1)
        self.assertAlmostEqual(calc.lowest_frequency or 0.0, -187.25)

    def _parse(self, content: str):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "orca.out"
            path.write_text(content)
            return parse_orca(path)


if __name__ == "__main__":
    unittest.main()
