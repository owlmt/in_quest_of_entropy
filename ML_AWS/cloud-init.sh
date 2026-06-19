#!/bin/bash
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y bpftrace fio iperf3 stress-ng python3 python3-pandas
echo READY > /var/log/rng-setup-done
