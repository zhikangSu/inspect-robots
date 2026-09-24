#!/bin/bash
# 用法: conda activate lerobot && source /home/meow/R5/inspect_env.sh
# inspect-robots 的 arx_r5 适配器在 conda env lerobot（Python 3.12）里加载官方 SDK 所需的最小环境：
# 不 source 整个 ROS2 setup.bash（它会把 3.10 的 site-packages 塞进 PYTHONPATH），只给预编译库需要的两样东西。
export ARX_R5_PYTHON=/home/meow/R5/R5_official/py/ARX_R5_python          # 适配器由此 import bimanual（含 cpython-312 模块）
export AMENT_PREFIX_PATH=${AMENT_PREFIX_PATH:-/opt/ros/humble}           # libarx_r5_src.so 构造时检查这个变量
export LD_LIBRARY_PATH=$ARX_R5_PYTHON/bimanual/api/arx_r5_src:$ARX_R5_PYTHON/bimanual/api:/opt/ros/humble/lib:/usr/local/lib:$LD_LIBRARY_PATH
export INSPECT_ROBOTS_CONFIG=/home/meow/R5/inspect_r5.ini                # [embodiment.args] / [policy.args] 见该文件
