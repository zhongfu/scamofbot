#!/bin/sh
screen -dmS tg.scamofbot sh -c 'echo "ctrl+a d to detach"; while true; do python3 -m bot; sleep 300; done'
