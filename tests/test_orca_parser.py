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

ORCA_WITH_RESOURCES = """
O   R   C   A
Program Version 6.0.0

INPUT FILE
|  1> ! B3LYP def2-SVP
|  2> %pal nprocs 12
|  3> end
|  4> %maxcore 4000
|  5> * xyz 0 1
|  6> H 0 0 0
|  7> H 0 0 1
|  8> *

           *        Program running with 12 parallel MPI-processes    *

FINAL SINGLE POINT ENERGY     -1.123456789
TOTAL RUN TIME: 0 days 0 hours 1 minutes 0 seconds
****ORCA TERMINATED NORMALLY****
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

ORCA_SA_CASSCF = """
O   R   C   A
Program Version 6.0.0

INPUT FILE
! CASSCF def2-SVP
%casscf
  nel 2
  norb 2
end
* xyz 0 1
H 0 0 0
H 0 0 1
*

--------------
CASSCF RESULTS
--------------

Final CASSCF energy       : -2804.586545359 Eh  -76316.6798 eV

---------------------------------------------
CAS-SCF STATES FOR BLOCK  0 MULT= 3 NROOTS= 1
---------------------------------------------

ROOT   0:  E=   -2804.5838615242 Eh
      1.00000 [     0]: 11


---------------------------------------------
CAS-SCF STATES FOR BLOCK  1 MULT= 1 NROOTS= 1
---------------------------------------------

ROOT   0:  E=   -2804.5892291946 Eh
      0.98881 [     1]: 11
      0.00590 [     2]: 02
      0.00529 [     0]: 20


-----------------------------
SA-CASSCF TRANSITION ENERGIES
------------------------------

FINAL SINGLE POINT ENERGY     -2804.586545359
TOTAL RUN TIME: 0 days 0 hours 5 minutes 0 seconds
****ORCA TERMINATED NORMALLY****
"""

ORCA_NEVPT2 = """
O   R   C   A
Program Version 6.0.0

INPUT FILE
! CASSCF SC-NEVPT2 def2-TZVP
%casscf
  nel 2
  norb 2
end
* xyz 0 1
H 0 0 0
H 0 0 1
*

 ===============================================================
                       NEVPT2 Results
 ===============================================================
   *********************
    MULT 1, ROOT 0
   *********************

  Class V0_ijab : 	 dE = -4.967481108940

 ---------------------------------------------------------------
 	 Total Energy Correction : dE = -5.22952406238801
 ---------------------------------------------------------------
 	 Reference  Energy       : E0 = -2804.58955126034061
 ---------------------------------------------------------------
 	 Total Energy (E0+dE)    : E  = -2809.81907532272862
 ---------------------------------------------------------------

--------------
TIMINGS NEVPT2
--------------

FINAL SINGLE POINT ENERGY     -2809.819075322729
TOTAL RUN TIME: 0 days 0 hours 10 minutes 0 seconds
****ORCA TERMINATED NORMALLY****
"""

ORCA_MRCI = """
O   R   C   A
Program Version 6.0.0

INPUT FILE
! MRCI def2-SVP
%mrci
  newblock 3 *
    nroots 1
  end
end
* xyz 0 1
H 0 0 0
H 0 0 1
*

----------
CI-RESULTS
----------

The threshold for printing is  0.30 percent

STATE   0:  Energy=  -2803.351419334 Eh RefWeight=  0.8871  0.00 eV      0.0 cm**-1
      0.0243  : h---h---[20]
      0.8478  : h---h---[11]
      0.0149  : h---h---[02]
      0.0046  : h---h 99[21]
Now choosing densities

FINAL SINGLE POINT ENERGY     -2803.351419333726
TOTAL RUN TIME: 0 days 0 hours 8 minutes 0 seconds
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

    def test_extracts_nprocs_and_maxcore(self) -> None:
        calc = self._parse(ORCA_WITH_RESOURCES)
        self.assertEqual(calc.nprocs, 12)
        self.assertEqual(calc.maxcore_mb, 4000)

    def test_sa_casscf_extracts_states_and_weights(self) -> None:
        calc = self._parse(ORCA_SA_CASSCF)
        self.assertAlmostEqual(calc.casscf_final_energy or 0.0, -2804.586545359)
        self.assertEqual(len(calc.casscf_states), 2)

        triplet, singlet = calc.casscf_states
        self.assertEqual(triplet.root, 0)
        self.assertEqual(triplet.block, 0)
        self.assertEqual(triplet.multiplicity, 3)
        self.assertAlmostEqual(triplet.energy, -2804.5838615242)
        self.assertEqual(len(triplet.weights), 1)
        self.assertAlmostEqual(triplet.weights[0].weight, 1.0)
        self.assertEqual(triplet.weights[0].occupation, "11")

        self.assertEqual(singlet.multiplicity, 1)
        self.assertEqual(len(singlet.weights), 3)
        self.assertAlmostEqual(singlet.weights[1].weight, 0.0059)
        self.assertEqual(singlet.weights[1].occupation, "02")

    def test_nevpt2_extracts_correction_and_total(self) -> None:
        calc = self._parse(ORCA_NEVPT2)
        self.assertEqual(len(calc.nevpt2_results), 1)
        result = calc.nevpt2_results[0]
        self.assertEqual(result.root, 0)
        self.assertEqual(result.multiplicity, 1)
        self.assertAlmostEqual(result.reference_energy, -2804.58955126034061)
        self.assertAlmostEqual(result.correction, -5.22952406238801)
        self.assertAlmostEqual(result.total_energy, -2809.81907532272862)

    def test_mrci_extracts_state_and_configuration_weights(self) -> None:
        calc = self._parse(ORCA_MRCI)
        self.assertEqual(len(calc.mrci_states), 1)
        state = calc.mrci_states[0]
        self.assertEqual(state.root, 0)
        self.assertAlmostEqual(state.energy, -2803.351419334)
        self.assertAlmostEqual(state.reference_weight or 0.0, 0.8871)
        self.assertEqual(len(state.weights), 4)
        self.assertEqual(state.weights[3].occupation, "h---h 99[21]")
        self.assertAlmostEqual(state.weights[1].weight, 0.8478)

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
