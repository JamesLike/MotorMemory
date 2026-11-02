#!/usr/bin/env python3
"""
Soft IOC with fake motor records for testing MotorMemory without hardware.

    python sim_ioc.py --prefix SIM:MMS: --count 3 --list-pvs

creates SIM:MMS:01 .. SIM:MMS:03, each with .VAL .RBV .DMOV .EGU .DESC
.LLM .HLM .RDBD .PREC.  Writing .VAL makes .RBV ramp toward it at
`speed` units/s with DMOV=0 while moving.
"""

from __future__ import annotations

import asyncio

from caproto import ChannelType
from caproto.server import PVGroup, SubGroup, ioc_arg_parser, pvproperty, run


class FakeMotor(PVGroup):
    VAL = pvproperty(name=".VAL", value=0.0, doc="setpoint", precision=3)
    RBV = pvproperty(name=".RBV", value=0.0, read_only=True, precision=3)
    DMOV = pvproperty(name=".DMOV", value=1, read_only=True)
    MOVN = pvproperty(name=".MOVN", value=0, read_only=True)
    EGU = pvproperty(name=".EGU", value="mm", dtype=ChannelType.STRING)
    DESC = pvproperty(name=".DESC", value="fake motor", dtype=ChannelType.STRING)
    LLM = pvproperty(name=".LLM", value=-100.0)
    HLM = pvproperty(name=".HLM", value=100.0)
    RDBD = pvproperty(name=".RDBD", value=0.005)
    PREC = pvproperty(name=".PREC", value=3)

    speed = 2.0  # units per second

    def __init__(self, *a, desc="fake motor", **kw):
        super().__init__(*a, **kw)
        self._task = None
        self._desc = desc

    @DESC.startup
    async def DESC(self, instance, async_lib):
        await instance.write(self._desc)

    @VAL.putter
    async def VAL(self, instance, value):
        if self._task is not None and not self._task.done():
            self._task.cancel()
        self._task = asyncio.ensure_future(self._move(value))
        return value

    async def _move(self, target):
        await self.DMOV.write(0)
        await self.MOVN.write(1)
        try:
            while abs(self.RBV.value - target) > 1e-9:
                step = self.speed * 0.05
                cur = self.RBV.value
                if abs(target - cur) <= step:
                    await self.RBV.write(target)
                    break
                await self.RBV.write(cur + step * (1 if target > cur else -1))
                await asyncio.sleep(0.05)
        finally:
            await self.DMOV.write(1)
            await self.MOVN.write(0)


def build(prefix: str, count: int) -> PVGroup:
    attrs = {}
    names = ["x", "y", "z", "rx", "ry", "rz"]
    for i in range(1, count + 1):
        attrs[f"m{i:02d}"] = SubGroup(
            FakeMotor, prefix=f"{i:02d}", desc=f"sim {names[(i - 1) % len(names)]} stage"
        )
    cls = type("SimIOC", (PVGroup,), attrs)
    return cls(prefix=prefix)


if __name__ == "__main__":
    import argparse
    import sys

    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--count", type=int, default=3)
    pre_args, rest = pre.parse_known_args()
    sys.argv = [sys.argv[0]] + rest
    ioc_options, run_options = ioc_arg_parser(default_prefix="SIM:MMS:", desc="fake motors")
    ioc = build(ioc_options["prefix"], pre_args.count)
    run(ioc.pvdb, **run_options)
