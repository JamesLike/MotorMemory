#!/bin/bash
# Beamline launcher: same PCDS conda environment that camviewer uses
# (pyca / psp.Pv backend).
h=$(dirname "$(readlink -f "$0")")
export PCDS_CONDA_VER=5.1.1
source /cds/group/pcds/pyps/conda/pcds_conda ""
echo "Launching MotorMemory from $h with: $@"
exec python "$h/motormemory.py" "$@"
