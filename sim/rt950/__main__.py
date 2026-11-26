"""python3 -m sim.rt950: the RT-950 SURF workbench on SoapyFang (sim/rt950/workbench.py), after
the same check as sim.soapyfang's for what the machine might lack. SPDX-License-Identifier: MIT"""
import sys

from sim.soapyfang.__main__ import check

check(sys.argv[1:])

from .workbench import main  # noqa: E402

main()
