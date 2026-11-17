#!/bin/bash
# What a repeated -L or --gres asks for. Run on login (24.11.5) through
# `su - tester`. Jobs are held, read back with scontrol, then cancelled.
cd /work; scontrol show lic | head -2; sinfo -h -o "%N %G"
for a in "-L lic:1 -L lic:2" "-L lic:2 -L lic:1" "-L lic:1,lic:1" "--gres=gpu:1 --gres=gpu:0" "--gres=gpu:0 --gres=gpu:1"; do
  j=$(sbatch --parsable -H -o /dev/null $a --wrap true 2>&1)
  echo "[$a] -> $(scontrol show job $j 2>/dev/null | grep -o -E "Licenses=[^ ]*|TresPerNode=[^ ]*" | tr "\n" " ")"
  scancel $j 2>/dev/null
done
