#!/usr/bin/bash
set +x

for f in clips/*; do
  ./combine.sh 321.mp4 $f out/$f
done
