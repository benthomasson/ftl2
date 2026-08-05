#!/bin/bash
set -e

mkdir -p /home/ftl2/.ssh
chmod 700 /home/ftl2/.ssh

if [ -f /authorized_keys ]; then
    cp /authorized_keys /home/ftl2/.ssh/authorized_keys
fi

if [ -n "$SSH_PUBLIC_KEY" ]; then
    echo "$SSH_PUBLIC_KEY" >> /home/ftl2/.ssh/authorized_keys
fi

if [ -f /home/ftl2/.ssh/authorized_keys ]; then
    chmod 600 /home/ftl2/.ssh/authorized_keys
    chown -R ftl2:ftl2 /home/ftl2/.ssh
fi

exec /usr/sbin/sshd -D -e
