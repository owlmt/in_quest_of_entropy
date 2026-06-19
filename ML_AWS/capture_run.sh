#!/bin/bash
# Reproduce the capture on a real Linux host (needs root for bpftrace). Usage: ./capture_run.sh [seconds]
DUR=${1:-300}; URL='https://speed.cloudflare.com/__down?bytes=1000000000'
echo 1 | sudo tee /sys/block/nvme0n1/queue/add_random >/dev/null
for i in $(seq 1 16); do ( e=$((SECONDS+DUR+5)); while [ $SECONDS -lt $e ]; do curl -s -o /dev/null "$URL"; done ) & done
fio --name=load --filename=./fio.dat --size=2G --rw=randrw --bs=4k --iodepth=32 --numjobs=4 \
    --time_based --runtime=$((DUR+5)) --direct=1 >/tmp/fio.log 2>&1 &
stress-ng --timer 8 --timeout $((DUR+5))s >/tmp/stress.log 2>&1 &
sleep 3
sudo timeout $DUR bpftrace capture.bt > raw_events.csv 2>/tmp/bt.err
wait 2>/dev/null
python3 bt_to_csv.py raw_events.csv rng_hw.csv
