#!/bin/bash

# Copyright (C) 2012 Google Inc.

# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.

# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# General Public License for more details.

# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA
# 02110-1301, USA.

# This is an offline testing script for most/all of the htools
# programs, checking basic command line functionality.

# Optional argument that specifies the test files to run. If not
# specified, then all tests are run.
#
# For example, a value of 'balancing' runs the file
# 'shelltests/htools-balancing.test'.  Multiple files can be specified
# using shell notation, for example, '{balancing,basic}'.
TESTS=${1:-*}

set -e
set -o pipefail

. $(dirname $0)/cli-tests-defs.sh

echo Running offline htools tests

export T=`mktemp -d`
trap 'rm -rf $T' EXIT
trap 'echo FAIL to build test files' ERR
echo Using $T as temporary dir

echo -n Generating hspace simulation data for hinfo and hbal...
# this cluster spec should be fine
./test/hs/hspace --simu p,4,8T,64g,16 -S $T/simu-onegroup \
  --disk-template drbd -l 8 -v -v -v >/dev/null 2>&1
echo OK

echo -n Generating hinfo and hbal test files for multi-group...
./test/hs/hspace --simu p,4,8T,64g,16 --simu p,4,8T,64g,16 \
  -S $T/simu-twogroups --disk-template drbd -l 8 >/dev/null 2>&1
echo OK

echo -n Generating test files for rebalancing...
# we generate a cluster with two node groups, one with unallocable
# policy, then we change all nodes from this group to the allocable
# one, and we check for rebalancing
FROOT="$T/simu-rebal-orig"
./test/hs/hspace --simu u,4,8T,64g,16 --simu p,4,8T,64g,16 \
  -S $FROOT --disk-template drbd -l 8 >/dev/null 2>&1
for suffix in standard tiered; do
  RELOC="$T/simu-rebal-merged.$suffix"
  # this relocates the nodes
  sed -re 's/^(node-.*|fake-uuid-)-02(|.*)/\1-01\2/' \
    < $FROOT.$suffix > $RELOC
done
export BACKEND_BAL_STD="-t$T/simu-rebal-merged.standard"
export BACKEND_BAL_TIER="-t$T/simu-rebal-merged.tiered"
echo OK

# For various tests
export BACKEND_DYNU="-t $T/simu-onegroup.standard"
export BACKEND_EXCL="-t $T/simu-onegroup.standard"

echo -n Generating data files for IAllocator checks...
for evac_mode in primary-only secondary-only all; do
  sed -e 's/"evac_mode": "all"/"evac_mode": "'${evac_mode}'"/' \
    -e 's/"spindles": [0-9]\+,//' \
    < $TESTDATA_DIR/hail-node-evac.json \
    > $T/hail-node-evac.json.$evac_mode
done
for bf in hail-alloc-drbd hail-alloc-invalid-twodisks hail-alloc-twodisks \
  hail-change-group hail-node-evac hail-reloc-drbd hail-alloc-spindles; do
  f=$bf.json
  sed -e 's/"exclusive_storage": false/"exclusive_storage": true/' \
    < $TESTDATA_DIR/$f > $T/$f.excl-stor
  sed -e 's/"exclusive_storage": false/"exclusive_storage": true/' \
    -e 's/"spindles": [0-9]\+,//' \
    < $TESTDATA_DIR/$f > $T/$f.fail-excl-stor
done
echo OK

echo -n Checking file-based RAPI...
mkdir -p $T/hscan
export RAPI_URL="file://$TESTDATA_DIR/rapi"
./test/hs/hscan -d $T/hscan/ -p -v -v $RAPI_URL >/dev/null 2>&1
# check that we file parsing is correct, i.e. hscan saves correct text
# files, and is idempotent (rapi+text == rapi); more is tested in
# shelltest later
RAPI_TXT="$(ls $T/hscan/*.data|head -n1)"
./test/hs/hinfo -p --print-instances -m $RAPI_URL > $T/hscan/direct.hinfo 2>&1
./test/hs/hinfo -p --print-instances -t $RAPI_TXT > $T/hscan/fromtext.hinfo 2>&1
echo OK

echo Running shelltest...

shelltest $SHELLTESTARGS \
  ${TOP_SRCDIR:-.}/test/hs/shelltests/htools-$TESTS.test