"""进程级维护闸门（备份恢复期间使用）。

恢复会覆盖数据库与业务文件，必须阻止其它业务 API 在恢复窗口内读写，
避免请求落到"半个恢复"状态。锁与闸门均为进程内作用域；本项目按单进程
部署，若未来改为多 worker，需要替换为跨进程协调机制。
"""

from __future__ import annotations

import threading

_lock = threading.Lock()
_active = False


def enter_maintenance() -> None:
    """进入维护状态；恢复入口在拿到恢复互斥锁后调用。"""
    global _active
    with _lock:
        _active = True


def leave_maintenance() -> None:
    """退出维护状态；恢复入口必须在 finally 中调用。"""
    global _active
    with _lock:
        _active = False


def is_maintenance() -> bool:
    """当前进程是否处于维护状态。"""
    with _lock:
        return _active
