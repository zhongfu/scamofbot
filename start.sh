#!/bin/sh
screen -dmS tg.scamofbot sh -c 'echo "ctrl+a d to detach"; while true; do python3.8 -m app; sleep 300; done'
