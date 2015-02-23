#!/bin/bash

for d in *.d
do
  ../template-util.py -a $d
done

