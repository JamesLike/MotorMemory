#!/bin/bash
# S3DF development launcher: uses an LCLS ana env (pyepics + PyQt5 backend).
# Start the fake IOC first in another shell:  ./run_dev.sh --ioc
h=$(dirname "$(readlink -f "$0")")
PY=${MP_PYTHON:-/sdf/group/lcls/ds/ana/sw/conda1/inst/envs/ana-4.0.68-py3/bin/python}
export EPICS_CA_AUTO_ADDR_LIST=${EPICS_CA_AUTO_ADDR_LIST:-NO}
export EPICS_CA_ADDR_LIST=${EPICS_CA_ADDR_LIST:-localhost}
if [ "$1" == "--ioc" ]; then
  shift
  exec "$PY" "$h/sim_ioc.py" --list-pvs "$@"
fi
exec "$PY" "$h/motormemory.py" "$@"
