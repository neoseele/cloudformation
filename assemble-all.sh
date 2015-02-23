#!/bin/bash

PATH=`dirname $0`

for d in *.d; do $PATH/template-util.py -a $d; done

