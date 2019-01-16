#!/bin/bash

# Determines which version of python we should run the kernel with, by checking
# which version of python the toolchain's lldb is built for.

if [ -d /swift-tensorflow-toolchain/usr/lib/python2.7/site-packages/lldb ]
then
    echo "python2"
else
    echo "python3"
fi
