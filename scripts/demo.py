#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""示例脚本：被 keybridge 的 script 动作调用。参数通过 sys.argv 传进来。"""
import datetime
import sys
from pathlib import Path

out = Path(__file__).with_name("_demo_out.txt")
stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
with out.open("a", encoding="utf-8") as f:
    f.write("[%s] 被调用，参数=%r\n" % (stamp, sys.argv[1:]))
print("demo.py 已执行，参数=%r" % (sys.argv[1:],))
